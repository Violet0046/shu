"""Each actual model request is recorded independently of grading/lease success."""
import asyncio
import os
import re
import secrets
import time
from contextvars import ContextVar

from ..backend.database import dumps

trace_context = ContextVar('grading_trace', default=None)


def redact(text):
    text = str(text)
    for key, value in os.environ.items():
        if any(part in key.upper() for part in ('API_KEY', 'SECRET', 'PASSWORD', 'ACCESS_TOKEN')) and len(value) >= 8:
            text = text.replace(value, '[已隐藏]')
    return re.sub(r'(?i)(bearer\s+|sk-)[A-Za-z0-9_\-.]+', '[已隐藏]', text)


def numeric_usage(value):
    if hasattr(value, 'model_dump'):
        value = value.model_dump()
    if not isinstance(value, dict):
        return {}
    return {k: numeric_usage(v) if isinstance(v, dict) else v for k, v in value.items()
            if isinstance(v, (dict, int)) and not isinstance(v, bool)}


def normalize_usage(provider, raw, supplied):
    def number(*values):
        return next((v for v in values if isinstance(v, int) and not isinstance(v, bool) and v >= 0), None)
    usage = numeric_usage(raw)
    prompt_details = usage.get('prompt_tokens_details')
    completion_details = usage.get('completion_tokens_details')
    prompt_details = prompt_details if isinstance(prompt_details, dict) else {}
    completion_details = completion_details if isinstance(completion_details, dict) else {}
    inp = number(usage.get('prompt_tokens'), usage.get('input_tokens'), supplied.get('input_tokens'))
    out = number(usage.get('completion_tokens'), usage.get('output_tokens'), supplied.get('output_tokens'))
    cached = number(prompt_details.get('cached_tokens'), usage.get('cache_read_input_tokens'),
                    usage.get('prompt_cache_hit_tokens'))
    write = number(usage.get('cache_creation_input_tokens'))
    reasoning = number(completion_details.get('reasoning_tokens'))
    # Anthropic reports uncached input separately; OpenAI includes cache hits in prompt_tokens.
    if provider == 'claude' and inp is not None:
        inp += (cached or 0) + (write or 0)
    return {'input_tokens': inp, 'output_tokens': out, 'cached_tokens': cached,
            'cache_write_tokens': write, 'reasoning_tokens': reasoning, 'usage': dumps(usage)}


async def call_model(provider, *, purpose='grading', **kwargs):
    context = trace_context.get()
    if context is None:
        return await provider.chat(**kwargs)
    store, job = context
    cid = secrets.token_hex(16)
    messages = [{'role': m.role, 'content': redact(m.content),
                 'images': [{'file': image.path.name if image.path else None, 'media_type': image.media_type}
                            for image in m.images]} for m in kwargs.get('messages', [])]
    parameters = {k: kwargs[k] for k in ('json_mode','temperature','max_tokens') if k in kwargs}
    for key in ('temperature', 'max_tokens'):
        parameters.setdefault(key, getattr(provider, key, None))
    if getattr(provider, 'model_name', '').lower() == 'minimax-m3.1-flash':
        parameters.update(thinking='adaptive', reasoning_effort=getattr(provider,'reasoning_effort','low'))
    elif getattr(provider, 'model_name', '').lower() == 'minimax-m3':
        parameters.update(thinking='disabled')
    store.execute('''INSERT INTO model_calls(id,job_id,attempt_id,purpose,provider,model,started,status,parameters,messages)
        VALUES(?,?,?,?,?,?,?,'processing',?,?)''', (cid,job['id'],job['lease_token'],purpose,provider.name,
        getattr(provider,'model_name',''),time.time(),dumps(parameters),dumps(messages)))
    try:
        response = await provider.chat(**kwargs)
    except BaseException as exc:
        raw = getattr(exc, 'body', None)
        usage = normalize_usage(provider.name, raw.get('usage', {}) if isinstance(raw, dict) else {}, {})
        _finish(store, cid, usage, status='interrupted' if isinstance(exc, asyncio.CancelledError) else 'failed',
                request_id=getattr(exc, 'request_id', None),error_type=type(exc).__name__,error=redact(exc)[:4000])
        raise
    else:
        raw = response.raw
        raw_usage = raw.get('usage', {}) if isinstance(raw, dict) else getattr(raw, 'usage', None)
        # Ollama uses counters at the response root.
        supplied = response.usage or {}
        usage = normalize_usage(provider.name, raw_usage, supplied)
        _finish(store, cid, usage, status='succeeded', finish_reason=response.finish_reason, actual_model=(raw.get('model') if isinstance(raw, dict) else getattr(raw, 'model', None)) or response.model,
                request_id=raw.get('_request_id') if isinstance(raw, dict) else getattr(raw, '_request_id', None), response=redact(response.content))
        return response


def _finish(store, cid, usage, **fields):
    fields.update(usage, finished=time.time())
    store.execute('UPDATE model_calls SET '+','.join(f'{key}=?' for key in fields)+' WHERE id=?',
                  (*fields.values(),cid))
