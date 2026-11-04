from fastapi import Request

COOKIE = 'shu_session'


def user(request: Request):
    return request.app.state.services.auth.authenticate(request.cookies.get(COOKIE,''))
