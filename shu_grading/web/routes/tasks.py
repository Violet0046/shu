from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
import re
from urllib.parse import quote

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

    @routes.delete('/tasks/{tid}')
    def delete_task(tid: str, request: Request):
        return services.tasks.delete(user(request), tid)

    @routes.get('/tasks/{tid}/export')
    def report(tid: str, request: Request, confirmed_only: bool=True):
        buffer = services.reports.export(user(request), tid, confirmed_only)
        task = services.tasks.get(user(request), tid)
        title = re.sub(r'[\x00-\x1f<>:"/\\|?*]', '_', task['title']).strip(' .')[:120] or '阅卷成绩'
        filename = quote(title + '.xlsx', safe='')
        return StreamingResponse(buffer, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            headers={'Content-Disposition':f"attachment; filename=grading-report.xlsx; filename*=UTF-8''{filename}"})

    return routes
