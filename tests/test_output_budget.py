import json

import pytest

from shu_grading.models.base import ChatResponse
from shu_grading.models.openai import OpenAIProvider
from test_web_workflow import FakeProvider, RESULT, detail, process, submit, task, web


@pytest.mark.parametrize('content,finish_reason,expected', [
    ('', 'length', '模型输出达到上限'),
    ('{"transcription":"半篇文章', 'length', '模型输出达到上限'),
    ('', 'stop', '模型未返回评分正文'),
    (' \n ', None, '模型未返回评分正文'),
])
def test_empty_or_truncated_output_never_triggers_repair_or_automatic_retry(web,content,finish_reason,expected):
    client,app,_=web
    class ExhaustedProvider(FakeProvider):
        model_name='MiniMax-M3.1-Flash'
        async def chat(self,messages,**kwargs):
            self.calls.append(messages)
            return ChatResponse(content=content,finish_reason=finish_reason,
                raw={'usage':{'completion_tokens':6000,'prompt_tokens':5351,
                             'completion_tokens_details':{'reasoning_tokens':5998}}})
    provider=ExhaustedProvider()
    app.state.services.registry.resolve('essay').provider_factory=lambda:provider
    tid=task(client);submit(client,tid);process(client,app)
    assert len(provider.calls)==1
    row=detail(client,tid)['submissions'][0]
    assert row['status']=='failed' and expected in row['error']
    assert row['ai_result'] is None
    trace=app.state.store.one('SELECT * FROM model_calls')
    assert trace['finish_reason']==finish_reason
    assert trace['output_tokens']==6000 and trace['reasoning_tokens']==5998
    job=app.state.store.one('SELECT * FROM grading_jobs')
    assert job['status']=='failed' and job['attempts']==1


def test_complete_output_still_succeeds_with_reasoning_usage(web):
    client,app,_=web
    class CompleteProvider(FakeProvider):
        async def chat(self,**kwargs):
            return ChatResponse(content=json.dumps(RESULT),finish_reason='stop')
    app.state.services.registry.resolve('essay').provider_factory=lambda:CompleteProvider()
    tid=task(client);submit(client,tid);process(client,app)
    assert detail(client,tid)['submissions'][0]['status']=='ready'


async def test_explicit_reasoning_effort_is_configurable_and_invalid_values_rejected():
    provider=OpenAIProvider(model_name='MiniMax-M3.1-Flash',api_key='test-only',reasoning_effort='medium')
    try:
        assert provider.reasoning_effort=='medium'
    finally:
        await provider.aclose()
    with pytest.raises(ValueError,match='SHU_REASONING_EFFORT'):
        OpenAIProvider(model_name='MiniMax-M3.1-Flash',api_key='test-only',reasoning_effort='none')
