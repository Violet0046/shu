"""Exercise the installed SDK and HTTP decoder without paid API requests."""
import gzip
import json

import pytest
from openai import _base_client

from shu_grading.models.base import ChatMessage, ImageInput
from shu_grading.models.openai import OpenAIProvider


@pytest.mark.parametrize('model', ['MiniMax-M3', 'MiniMax-M3.1-Flash', 'minimax-m3.1-flash', 'gpt-4o', 'MiniMax-future'])
async def test_sdk_avoids_brotli_and_decodes_compressed_response(model):
    http = getattr(_base_client, 'httpx2', None) or _base_client.httpx
    provider = OpenAIProvider(model_name=model, api_key='test-only', base_url='https://example.invalid/v1')
    requests = []

    def respond(request):
        requests.append(request)
        assert request.headers['accept-encoding'] == 'gzip, deflate'
        body = json.loads(request.content)
        assert body['model'] == model
        if model.lower() == 'minimax-m3.1-flash':
            assert body['thinking'] == {'type': 'adaptive'}
            assert body['reasoning_effort'] == 'medium'
        elif model == 'MiniMax-M3':
            assert body['thinking'] == {'type': 'disabled'}
        else:
            assert 'thinking' not in body
        assert body['messages'][0]['content'][1]['image_url']['url'].startswith('data:image/png;base64,')
        payload = {'id': 'test', 'object': 'chat.completion', 'created': 0, 'model': model,
                   'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': '{"ok":true}',
                                                       'reasoning_content': 'Internal reasoning, not grading JSON'},
                                'finish_reason': 'stop'}],
                   'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15}}
        return http.Response(200, headers={'content-encoding': 'gzip', 'content-type': 'application/json'},
                             content=gzip.compress(json.dumps(payload).encode()))

    # Replace only the transport; preserve SDK headers and response processing.
    await provider.client.close()
    provider.client = provider.client.with_options(http_client=http.AsyncClient(transport=http.MockTransport(respond)))
    try:
        result = await provider.chat([ChatMessage('user', 'grade', [ImageInput(base64_data='AA==', media_type='image/png')])], json_mode=True)
        assert result.content == '{"ok":true}'
        assert result.usage == {'input_tokens': 10, 'output_tokens': 5}
        assert len(requests) == 1
    finally:
        await provider.aclose()
