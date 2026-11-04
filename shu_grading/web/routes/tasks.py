from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from .contracts import TaskInput
from .dependencies import user


def router(services):
    routes = APIRouter(prefix='/api')

    @routes.get('/task-types')
    def task_types(request: Request):
        user(request)
        return services.registry.available()

    @routes.get('/tasks')
    def tasks(request: Request):
        return services.tasks.list(user(request))

    @routes.post('/tasks')
    def create_task(body: TaskInput, request: Request):
        return services.tasks.create(user(request), body)

    @routes.get('/tasks/{tid}')
    def detail(tid: str, request: Request):
        return services.tasks.detail(user(request), tid)

    @routes.get('/tasks/{tid}/export')
    def report(tid: str, request: Request, confirmed_only: bool=True):
        buffer = services.reports.export(user(request), tid, confirmed_only)
        return StreamingResponse(buffer, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            headers={'Content-Disposition':'attachment; filename="grading-report.xlsx"'})

    return routes
