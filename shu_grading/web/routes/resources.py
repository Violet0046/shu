from fastapi import APIRouter, Request

from .contracts import ClassInput, TemplateInput
from .dependencies import user


def router(services):
    routes = APIRouter(prefix='/api')

    @routes.get('/classes')
    def classes(request: Request):
        return services.resources.classes(user(request))

    @routes.post('/classes')
    def create_class(body: ClassInput, request: Request):
        return services.resources.save_class(user(request), body)

    @routes.put('/classes/{cid}')
    def edit_class(cid: str, body: ClassInput, request: Request):
        return services.resources.save_class(user(request), body, cid)

    @routes.get('/templates')
    def templates(request: Request):
        return services.resources.templates(user(request))

    @routes.post('/templates')
    def create_template(body: TemplateInput, request: Request):
        return services.resources.save_template(user(request), body)

    @routes.put('/templates/{tid}')
    def update_template(tid: str, body: TemplateInput, request: Request):
        return services.resources.save_template(user(request), body, tid)

    @routes.put('/templates/{tid}/default')
    def default_template(tid: str, request: Request):
        return services.resources.set_default(user(request), tid)

    @routes.delete('/templates/{tid}')
    def delete_template(tid: str, request: Request):
        return services.resources.delete_template(user(request), tid)

    return routes
