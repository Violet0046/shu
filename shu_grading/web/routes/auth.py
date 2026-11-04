import os

from fastapi import APIRouter, Request, Response

from .contracts import Credentials
from .dependencies import COOKIE, user


def router(services):
    routes = APIRouter(prefix='/api')

    @routes.post('/login')
    def login(body: Credentials, request: Request, response: Response):
        result = services.auth.login(body.username, body.password, request.client.host if request.client else 'unknown')
        response.set_cookie(COOKIE, result['token'], httponly=True, samesite='strict',
                            secure=os.getenv('SHU_COOKIE_SECURE')=='1', max_age=7*86400)
        return {'username': result['username']}

    @routes.post('/logout')
    def logout(request: Request, response: Response):
        services.auth.logout(request.cookies.get(COOKIE,''))
        response.delete_cookie(COOKIE)
        return {'ok': True}

    @routes.get('/me')
    def me(request: Request):
        return services.store.one('SELECT username FROM users WHERE id=?', (user(request),))

    return routes
