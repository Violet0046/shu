import json

import httpx
import pytest

from shu_grading.config import Settings
from shu_grading.models.base import ChatMessage, ImageInput
from shu_grading.models.doubao import DoubaoProvider
from shu_grading.models.factory import create_provider
from shu_grading.models.tracing import normalize_usage
from test_web_workflow import RESULT, detail, process, submit, task, web


def payload(status='completed'):
    return {'id':'resp-test','model':'doubao-seed-2-1-lite-260915','status':status,
            'output':[{'type':'reasoning','summary':[{'text':'do-not-persist-thinking'}]},
                      {'type':'message','content':[{'type':'output_text','text':json.dumps(RESULT)}]}],
            'usage':{'input_tokens':1500,'output_tokens':1200,'total_tokens':2700,
                     'input_tokens_details':{'cached_tokens':200},
                     'output_tokens_details':{'reasoning_tokens':0}}}


async def test_responses_wire_format_image_detail_and_no_thinking():
    provider=DoubaoProvider(api_key='ark-test-key')
    calls=[]
    def respond(request):
        calls.append(request)
        assert str(request.url)=='https://ark.cn-beijing.volces.com/api/v3/responses'
        assert request.headers['authorization']=='Bearer ark-test-key'
        body=json.loads(request.content)
        assert body['model']=='doubao-seed-2-1-lite-260915'
        assert body['thinking']=={'type':'disabled'}
        assert body['max_output_tokens']==7000
        assert body['stream'] is False and body['store'] is False
        assert not {'tools','response_format','messages','reasoning_effort','max_tokens'} & body.keys()
        assert body['input'][0]['role']=='system'
        image=body['input'][1]['content'][1]
        assert image=={'type':'input_image','image_url':'data:image/png;base64,AA==','detail':'high'}
        return httpx.Response(200,json=payload(),headers={'x-request-id':'request-test'})
    await provider.aclose()
    provider.client=httpx.AsyncClient(base_url='https://ark.cn-beijing.volces.com/api/v3/',
        headers={'Authorization':'Bearer ark-test-key'},transport=httpx.MockTransport(respond))
    try:
        response=await provider.chat([ChatMessage('system','只输出 JSON'),ChatMessage('user','阅卷',
            [ImageInput(base64_data='AA==',media_type='image/png')])],json_mode=True,max_tokens=7000)
        assert response.finish_reason=='stop'
        assert json.loads(response.content)['total_score']==40
        assert response.raw['_request_id']=='request-test'
        assert 'do-not-persist-thinking' not in json.dumps(response.raw)
        usage=normalize_usage('doubao',response.raw['usage'],response.usage)
        assert usage['input_tokens']==1500 and usage['cached_tokens']==200
        assert usage['reasoning_tokens']==0 and len(calls)==1
    finally:
        await provider.aclose()


@pytest.mark.parametrize('kind',['complete','length','empty','rate_limit','unauthorized'])
def test_ark_grading_trace_and_failure_handling(web,kind):
    client,app,_=web
    calls=[]
    def respond(request):
        calls.append(request)
        body=payload()
        if kind=='length':
            body.update(status='incomplete',incomplete_details={'reason':'max_output_tokens'},output=[])
        if kind=='empty':
            body['output']=[]
        if kind in ('rate_limit','unauthorized'):
            return httpx.Response(429 if kind=='rate_limit' else 401,json={'error':{'message':'test failure'}},
                                  headers={'x-request-id':'error-request'})
        return httpx.Response(200,json=body,headers={'x-request-id':'success-request'})
    def factory():
        provider=DoubaoProvider(api_key='ark-test-key')
        # No requests were made on the original client; close it on its owning loop.
        original=provider.client
        provider.client=httpx.AsyncClient(base_url='https://ark.cn-beijing.volces.com/api/v3/',transport=httpx.MockTransport(respond))
        original_close=provider.aclose
        async def close():
            await original_close()
            await original.aclose()
        provider.aclose=close
        return provider
    app.state.services.registry.resolve('essay').provider_factory=factory
    tid=task(client);submit(client,tid);process(client,app)
    assert len(calls)==1  # No hidden HTTP retries and no JSON repair on empty/truncated output.
    call=app.state.store.one('SELECT * FROM model_calls')
    params=json.loads(call['parameters'])
    assert params['thinking']=='disabled' and params['image_detail']=='high' and params['api']=='responses'
    job=app.state.store.one('SELECT * FROM grading_jobs')
    if kind=='complete':
        assert detail(client,tid)['submissions'][0]['status']=='ready'
        assert call['cached_tokens']==200 and call['reasoning_tokens']==0
    else:
        assert job['status']==('pending' if kind=='rate_limit' else 'failed')
    if kind in ('rate_limit','unauthorized'):
        assert call['status']=='failed' and call['request_id']=='error-request'
    if kind=='length':
        assert call['finish_reason']=='length'


async def test_factory_uses_ark_credentials_not_old_minimax_settings():
    settings=Settings(ai_provider='doubao',ai_model='doubao-seed-2-1-lite-260915',
        ark_api_key='ark-test-key',openai_api_key='old-minimax-key',openai_base_url='https://minimax.invalid/v1')
    provider=create_provider(settings)
    try:
        assert isinstance(provider,DoubaoProvider)
        assert str(provider.client.base_url)=='https://ark.cn-beijing.volces.com/api/v3/'
        assert provider.client.headers['authorization']=='Bearer ark-test-key'
    finally:
        await provider.aclose()
    with pytest.raises(ValueError,match='ARK_API_KEY'):
        create_provider(Settings(ai_provider='doubao',openai_api_key='old-minimax-key'))
