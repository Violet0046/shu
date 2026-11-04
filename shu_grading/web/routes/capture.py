import io
import os

from fastapi import APIRouter, Request, Response

from ...backend.errors import ServiceError
from .contracts import SubmissionInput
from .dependencies import user


def router(services):
    routes = APIRouter(prefix='/api')

    @routes.post('/tasks/{tid}/pair')
    def pair(tid: str, request: Request):
        return services.capture.pair(user(request), tid)

    @routes.delete('/tasks/{tid}/pair')
    def revoke(tid: str, request: Request):
        return services.capture.revoke(user(request), tid)

    @routes.get('/capture/{token}')
    def info(token: str):
        return services.capture.info(token)

    @routes.post('/capture/{token}')
    def submit(token: str, body: SubmissionInput):
        return services.capture.submit(token, body)

    @routes.get('/capture/{token}/qr')
    def qr(token: str):
        import qrcode
        services.capture.get(token)
        url = os.getenv('SHU_PUBLIC_URL','').rstrip('/')
        if not url:
            raise ServiceError(422, '服务器未设置手机访问地址')
        buffer = io.BytesIO()
        qrcode.make(f'{url}/#capture={token}').save(buffer, format='PNG')
        return Response(buffer.getvalue(), media_type='image/png')

    return routes
