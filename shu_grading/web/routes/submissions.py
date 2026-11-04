from fastapi import APIRouter, Request
from fastapi.responses import FileResponse

from .contracts import ReviewInput, SubmissionInput
from .dependencies import user


def router(services):
    routes = APIRouter(prefix='/api')

    @routes.post('/tasks/{tid}/submissions')
    def submit(tid: str, body: SubmissionInput, request: Request):
        return services.submissions.submit(user(request), tid, body)

    @routes.get('/submissions/{sid}/image')
    def image(sid: str, request: Request):
        row = services.submissions.get(user(request), sid)
        return FileResponse(services.files.path(row['original_image'] or row['image']))

    @routes.put('/submissions/{sid}/review')
    def review(sid: str, body: ReviewInput, request: Request):
        return services.submissions.review(user(request), sid, body)

    @routes.post('/submissions/{sid}/retry')
    def retry(sid: str, request: Request):
        return services.submissions.retry(user(request), sid)

    @routes.put('/submissions/{sid}/identity')
    def identity(sid: str, body: dict, request: Request):
        return services.submissions.correct_identity(user(request), sid, body.get('student_id'))

    @routes.get('/submissions/{sid}/versions')
    def versions(sid: str, request: Request):
        return services.submissions.versions(user(request), sid)

    @routes.get('/submissions/{sid}/jobs')
    def jobs(sid: str, request: Request):
        return services.submissions.jobs(user(request), sid)

    return routes
