"""Volcengine Ark Responses: high-detail images, thinking disabled."""
from typing import Any

import httpx

from .base import ChatMessage, ChatResponse, ModelProvider


class ArkResponseError(RuntimeError):
    def __init__(self, status, body, request_id=None):
        self.body, self.request_id = body, request_id
        error = body.get('error') or {}
        message = error.get('message', '模型响应失败') if isinstance(error, dict) else str(error)
        super().__init__(f'方舟接口 HTTP {status}: {message}')


class DoubaoProvider(ModelProvider):
    name = 'doubao'

    def __init__(self, model_name='doubao-seed-2-1-lite-260915', api_key=None,
                 base_url=None, max_tokens=6000, temperature=.1, **kwargs: Any):
        super().__init__(model_name)
        if not api_key:
            raise ValueError('豆包需要 ARK_API_KEY，请配置火山方舟 API 密钥')
        self.max_tokens, self.temperature = max_tokens, temperature
        self.client = httpx.AsyncClient(
            base_url=(base_url or 'https://ark.cn-beijing.volces.com/api/v3').rstrip('/')+'/',
            headers={'Authorization':f'Bearer {api_key}','Accept-Encoding':'gzip, deflate'},
            timeout=httpx.Timeout(300, connect=30),
        )

    async def chat(self, messages: list[ChatMessage], json_mode=False, temperature=None, max_tokens=None):
        inputs = []
        for message in messages:
            content = [{'type':'input_text','text':message.content}] if message.content else []
            for image in message.images:
                content.append({'type':'input_image','image_url':f'data:{image.media_type};base64,{image.base64_data}',
                                'detail':'high'})
            inputs.append({'role':message.role,'content':content})
        # JSON is constrained by the grading/repair prompts, not Chat Completions parameters.
        payload = {'model':self.model_name,'input':inputs,'stream':False,
                   'thinking':{'type':'disabled'},'max_output_tokens':max_tokens or self.max_tokens,
                   'temperature':self.temperature if temperature is None else temperature,
                   'store':False}
        response = await self.client.post('responses',json=payload)
        request_id = response.headers.get('x-request-id') or response.headers.get('x-tt-logid')
        try:
            data = response.json()
        except ValueError:
            raise ArkResponseError(response.status_code,{'error':{'message':'接口没有返回有效 JSON'}},request_id)
        if not response.is_success:
            raise ArkResponseError(response.status_code,data,request_id)
        if data.get('error') or data.get('status') in ('failed','cancelled'):
            # HTTP 200 can still contain a provider-side failure. Do not accept it as grading.
            raise ArkResponseError(400,data,request_id)
        text = []
        refused = False
        for output in data.get('output',[]):
            if output.get('type') != 'message':
                continue
            for part in output.get('content',[]):
                if part.get('type') == 'output_text':
                    text.append(part.get('text',''))
                elif part.get('type') == 'refusal':
                    refused = True
        incomplete = data.get('incomplete_details') or {}
        reason = 'length' if incomplete.get('reason') == 'max_output_tokens' else (
            'refusal' if refused else 'stop' if data.get('status') == 'completed' else data.get('status'))
        if data.get('status') == 'incomplete' and reason != 'length':
            reason = 'incomplete'
        usage = data.get('usage') or {}
        # Retain numeric usage and provider identifiers without retaining reasoning output.
        raw = {'usage':usage,'model':data.get('model',self.model_name),
               '_request_id':request_id or data.get('id')}
        return ChatResponse(content=''.join(text),usage={'input_tokens':usage.get('input_tokens'),
            'output_tokens':usage.get('output_tokens')},model=raw['model'],raw=raw,finish_reason=reason)
