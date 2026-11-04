"""Administrator-only monitoring and account operations."""
import json
import time

from fastapi import APIRouter, Request, Query
from pydantic import BaseModel, Field, ConfigDict

from ...backend.errors import ServiceError
from .dependencies import user


class AccountInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    username: str = Field(min_length=2, max_length=80)
    password: str = Field(min_length=8, max_length=128)
    recipient: str = Field(default='', max_length=200)


class AccountChange(BaseModel):
    model_config = ConfigDict(extra='forbid')
    active: bool | None = None
    password: str | None = Field(default=None, min_length=8, max_length=128)


def router(services):
    routes = APIRouter(prefix='/api/admin')
    store = services.store

    def administrator(request):
        uid = user(request)
        if not store.one('SELECT is_admin FROM users WHERE id=?', (uid,))['is_admin']:
            raise ServiceError(403, '仅管理员可访问此页面')
        return uid

    @routes.get('/overview')
    def overview(request: Request, since: float = 0):
        administrator(request)
        since = since or time.time()-86400
        totals = store.one('''SELECT COUNT(*) AS calls,SUM(status='failed') AS failed,
            SUM(input_tokens) AS input_tokens,SUM(output_tokens) AS output_tokens,
            SUM(cached_tokens) AS cached_tokens,SUM(cache_write_tokens) AS cache_write_tokens,
            SUM(reasoning_tokens) AS reasoning_tokens,
            SUM(input_tokens IS NULL OR output_tokens IS NULL) AS unknown_calls,
            AVG(finished-started) AS average_seconds FROM model_calls WHERE started>=?''', (since,))
        models = store.all('''SELECT provider,model,COUNT(*) AS calls,SUM(input_tokens) AS input_tokens,
            SUM(output_tokens) AS output_tokens,SUM(cached_tokens) AS cached_tokens,
            SUM(input_tokens IS NULL OR output_tokens IS NULL) AS unknown_calls
            FROM model_calls WHERE started>=? GROUP BY provider,model''', (since,))
        workers = store.all('SELECT id,heartbeat FROM workers ORDER BY heartbeat DESC')
        for worker in workers:
            worker['online'] = worker['heartbeat'] > time.time()-services.queue.lease_seconds
        return {'totals':totals,'models':models,'workers':workers,
                'queue':store.all('SELECT status,COUNT(*) AS count FROM grading_jobs GROUP BY status'),
                'accounts':store.one('SELECT COUNT(*) AS total,SUM(active=1) AS active FROM users'),
                'generated_at':time.time()}

    @routes.get('/accounts')
    def accounts(request: Request, q: str='', offset: int=Query(0, ge=0), limit: int=Query(50, ge=1, le=100)):
        uid = administrator(request)
        pattern = '%'+q[:150]+'%'
        rows = store.all('''SELECT u.id,u.username,u.active,u.is_admin,u.recipient,
            (SELECT COUNT(*) FROM tasks t WHERE t.owner=u.id) AS tasks,
            (SELECT COUNT(*) FROM submissions s JOIN tasks t ON t.id=s.task_id WHERE t.owner=u.id) AS submissions,
            (SELECT SUM(c.input_tokens) FROM model_calls c JOIN grading_jobs j ON j.id=c.job_id
             JOIN submissions s ON s.id=j.submission_id JOIN tasks t ON t.id=s.task_id WHERE t.owner=u.id) AS input_tokens,
            (SELECT SUM(c.output_tokens) FROM model_calls c JOIN grading_jobs j ON j.id=c.job_id
             JOIN submissions s ON s.id=j.submission_id JOIN tasks t ON t.id=s.task_id WHERE t.owner=u.id) AS output_tokens,
            (SELECT SUM(c.cached_tokens) FROM model_calls c JOIN grading_jobs j ON j.id=c.job_id
             JOIN submissions s ON s.id=j.submission_id JOIN tasks t ON t.id=s.task_id WHERE t.owner=u.id) AS cached_tokens
            FROM users u WHERE u.username LIKE ? OR u.recipient LIKE ? ORDER BY u.username LIMIT ? OFFSET ?''',
            (pattern,pattern,limit,offset))
        return {'items':rows,'current_user':uid,'total':store.one(
            'SELECT COUNT(*) AS n FROM users WHERE username LIKE ? OR recipient LIKE ?', (pattern,pattern))['n']}

    @routes.post('/accounts')
    def create_account(body: AccountInput, request: Request):
        uid = administrator(request)
        return {'id':services.auth.create_account(body.username,body.password,body.recipient,actor=uid)}

    @routes.patch('/accounts/{account_id}')
    def change_account(account_id: str, body: AccountChange, request: Request):
        uid = administrator(request)
        account = store.one('SELECT username FROM users WHERE id=?', (account_id,))
        if not account:
            raise ServiceError(404, '账户不存在')
        if uid == account_id and body.active is False:
            raise ServiceError(409, '不能停用当前管理员账户')
        if body.active is None and body.password is None:
            raise ServiceError(422, '请指定账户操作')
        services.auth.manage_account(account['username'],active=body.active,password=body.password,actor=uid)
        return {'ok':True}

    @routes.get('/traces')
    def traces(request: Request, q: str='', status: str='', model: str='', start: float=0,
               end: float=4102444800, offset: int=Query(0, ge=0), limit: int=Query(50, ge=1, le=100)):
        administrator(request)
        where = '''WHERE j.created>=? AND j.created<=? AND (u.username LIKE ? OR t.title LIKE ? OR s.label LIKE ?)
                   AND (?='' OR j.status=?) AND (?='' OR EXISTS(SELECT 1 FROM model_calls f WHERE f.job_id=j.id AND f.model LIKE ?))'''
        args = (start,end,*(['%'+q[:150]+'%']*3),status,status,model,'%'+model[:150]+'%')
        source = ''' FROM grading_jobs j JOIN submissions s ON s.id=j.submission_id
            JOIN tasks t ON t.id=s.task_id JOIN users u ON u.id=t.owner '''
        total = store.one('SELECT COUNT(*) AS n'+source+where,args)['n']
        rows = store.all('''SELECT j.id,j.created,j.finished,j.status,j.attempts,j.error,t.title,u.username,s.label,
            (SELECT COUNT(*) FROM model_calls c WHERE c.job_id=j.id) AS calls,
            (SELECT SUM(input_tokens) FROM model_calls c WHERE c.job_id=j.id) AS input_tokens,
            (SELECT SUM(output_tokens) FROM model_calls c WHERE c.job_id=j.id) AS output_tokens,
            (SELECT SUM(cached_tokens) FROM model_calls c WHERE c.job_id=j.id) AS cached_tokens,
            (SELECT COUNT(*) FROM model_calls c WHERE c.job_id=j.id AND (input_tokens IS NULL OR output_tokens IS NULL)) AS unknown_calls,
            (SELECT GROUP_CONCAT(DISTINCT model) FROM model_calls c WHERE c.job_id=j.id) AS models'''+source+where+
            ' ORDER BY j.created DESC,j.id LIMIT ? OFFSET ?', (*args,limit,offset))
        return {'items':rows,'total':total}

    @routes.get('/traces/{job_id}')
    def trace_detail(job_id: str, request: Request):
        administrator(request)
        job = store.one('''SELECT j.id,j.status,j.created,j.finished,j.error,j.attempts,s.label,t.title,u.username
            FROM grading_jobs j JOIN submissions s ON s.id=j.submission_id JOIN tasks t ON t.id=s.task_id
            JOIN users u ON u.id=t.owner WHERE j.id=?''', (job_id,))
        if not job:
            raise ServiceError(404, '阅卷作业不存在')
        calls = store.all('SELECT * FROM model_calls WHERE job_id=? ORDER BY started,id', (job_id,))
        for call in calls:
            for key in ('usage','messages','parameters'):
                call[key] = json.loads(call[key]) if call[key] else None
            if call['status']=='processing':
                attempt = store.one('''SELECT a.finished,a.outcome,j.lease_expires FROM job_attempts a
                    JOIN grading_jobs j ON j.id=a.job_id WHERE a.id=?''', (call['attempt_id'],))
                if attempt and (attempt['finished'] or (attempt['lease_expires'] or 0)<time.time()):
                    call['status'] = 'unknown'
        return {'job':job,'attempts':store.all('''SELECT attempt,worker_id,started,finished,outcome,error,id
            FROM job_attempts WHERE job_id=? ORDER BY attempt''', (job_id,)), 'calls':calls}

    @routes.get('/events')
    def events(request: Request):
        administrator(request)
        return store.all('''SELECT e.action,e.created,COALESCE(a.username,'服务器命令') AS actor,u.username AS target
            FROM admin_events e LEFT JOIN users a ON a.id=e.actor LEFT JOIN users u ON u.id=e.target
            ORDER BY e.created DESC LIMIT 100''')

    return routes
