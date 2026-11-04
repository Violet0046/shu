"""验证账户分配、独立队列、租约、幂等提交及题型扩展。"""
import asyncio
import io
import json
import subprocess
import sys
import time
from dataclasses import fields

import pytest
from fastapi.testclient import TestClient

from shu_grading.backend.database import Store, dumps
from shu_grading.backend.errors import ServiceError
from shu_grading.backend.grading import GraderRegistry, GradingInput
from shu_grading.backend.queue import JobQueue
from shu_grading.backend.worker import GradingWorker
from shu_grading.schemas import CriterionScore, GradingResult
from shu_grading.web.server import create_app
from test_web_workflow import FakeProvider, RESULT, RUBRIC, detail, image_data, process, submit, task, web


def test_public_registration_removed_and_optional_single_login(web):
    client, app, _ = web
    app.state.services.auth.single_session=True
    assert client.post('/api/register',json={'username':'公开注册','password':'password-123'}).status_code==404
    assert app.state.store.one('SELECT id FROM users WHERE username=?',('公开注册',)) is None
    with TestClient(app) as second:
        assert second.post('/api/login',json={'username':'老师甲','password':'test-password'}).status_code==200
        assert client.get('/api/me').status_code==401
        assert second.get('/api/me').status_code==200
        token=second.cookies.get('shu_session')
        assert app.state.store.one('SELECT token FROM sessions')['token']!=token


def test_one_assigned_account_can_use_multiple_devices_by_default(web):
    client,app,_=web
    with TestClient(app) as second:
        assert second.post('/api/login',json={'username':'老师甲','password':'test-password'}).status_code==200
        assert client.get('/api/me').status_code==200
        assert second.get('/api/me').status_code==200


def test_admin_disable_reset_revokes_sessions_and_phone(web,monkeypatch):
    client, app, _ = web
    monkeypatch.setenv('SHU_PUBLIC_URL','http://localhost:8000')
    tid=task(client)
    token=client.post('/api/tasks/'+tid+'/pair',json={}).json()['token']
    app.state.services.auth.manage_account('老师甲',active=False)
    assert client.get('/api/me').status_code==401
    assert client.get('/api/capture/'+token).status_code==403
    assert client.post('/api/login',json={'username':'老师甲','password':'test-password'}).status_code==401
    app.state.services.auth.manage_account('老师甲',active=True,password='new-password-123')
    assert client.post('/api/login',json={'username':'老师甲','password':'test-password'}).status_code==401
    assert client.post('/api/login',json={'username':'老师甲','password':'new-password-123'}).status_code==200


def test_server_cli_creates_account_without_password_argument(tmp_path):
    result=subprocess.run([sys.executable,'-m','shu_grading.backend.admin','--data-dir',str(tmp_path),
        'create-user','cli-teacher','--password-stdin'],input='known-test-password\n',text=True,capture_output=True,timeout=30)
    assert result.returncode==0,result.stderr
    assert 'known-test-password' not in result.stdout
    app=create_app(tmp_path,start_worker=False)
    with TestClient(app) as client:
        assert client.post('/api/login',json={'username':'cli-teacher','password':'known-test-password'}).status_code==200


def test_submission_and_queue_are_idempotent(web):
    client, app, _ = web
    tid=task(client)
    body={'image':image_data(),'request_id':'request-000001'}
    first=client.post('/api/tasks/'+tid+'/submissions',json=body)
    second=client.post('/api/tasks/'+tid+'/submissions',json=body)
    assert first.status_code==second.status_code==200 and first.json()==second.json()
    assert len(detail(client,tid)['submissions'])==1
    assert len(app.state.store.all('SELECT id FROM grading_jobs'))==1
    assert client.post('/api/tasks/'+tid+'/submissions',json={**body,'paper_number':'another'}).status_code==409


def test_queue_failure_rolls_back_submission_and_files(web,monkeypatch):
    client, app, _ = web
    tid=task(client)
    def reject(*args,**kwargs):
        raise ServiceError(503,'测试队列故障')
    monkeypatch.setattr(app.state.services.queue,'enqueue',reject)
    response=client.post('/api/tasks/'+tid+'/submissions',json={'image':image_data()})
    assert response.status_code==503
    assert detail(client,tid)['submissions']==[]
    assert app.state.store.all('SELECT id FROM grading_jobs')==[]
    assert list((app.state.store.root/'uploads').iterdir())==[]


def test_two_processes_cannot_claim_same_job(web):
    client, app, _ = web
    tid=task(client)
    submit(client,tid)
    script="""import sys
from shu_grading.backend.database import Store
from shu_grading.backend.queue import JobQueue
from pathlib import Path
job=JobQueue(Store(Path(sys.argv[1]))).claim(sys.argv[2])
print(job['id'] if job else 'none')
"""
    processes=[subprocess.Popen([sys.executable,'-c',script,str(app.state.store.root),'worker-'+str(i)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) for i in range(2)]
    outputs=[]
    for p in processes:
        out,err=p.communicate(timeout=30)
        assert p.returncode==0,err
        outputs.append(out.strip())
    assert outputs.count('none')==1
    assert app.state.store.one('SELECT attempts FROM grading_jobs')['attempts']==1


def test_expired_lease_reclaims_and_fences_old_result(web):
    client, app, _ = web
    tid=task(client)
    sid=submit(client,tid)
    now=[time.time()+1]
    queue=JobQueue(app.state.store,lease_seconds=10,clock=lambda:now[0])
    first=queue.claim('old-worker')
    assert first and queue.renew(first)
    now[0]+=11
    replacement=queue.claim('new-worker')
    assert replacement['id']==first['id'] and replacement['attempts']==2
    result=GradingResult.model_validate(RESULT)
    assert not queue.complete(first,result)
    assert not queue.fail(first,'stale')
    assert not queue.renew(first)
    assert queue.complete(replacement,result)
    assert len(client.get('/api/submissions/'+sid+'/versions').json())==1


def test_transient_failure_retries_with_backoff(web):
    client, app, provider = web
    tid=task(client)
    sid=submit(client,tid)
    provider.fail=True
    process(client,app)
    job=app.state.store.one('SELECT * FROM grading_jobs')
    assert job['status']=='pending' and job['attempts']==1 and job['available_at']>time.time()
    assert not client.portal.call(app.state.process_one)
    assert detail(client,tid)['submissions'][0]['status']=='pending'
    app.state.store.execute('UPDATE grading_jobs SET available_at=0 WHERE id=?',(job['id'],))
    provider.fail=False
    process(client,app)
    assert app.state.store.one('SELECT * FROM grading_jobs')['status']=='succeeded'
    assert len(client.get('/api/submissions/'+sid+'/versions').json())==1
    jobs=client.get('/api/submissions/'+sid+'/jobs').json()
    assert jobs[0]['attempts']==2 and jobs[0]['error'] is None


@pytest.mark.parametrize('message,expected',[('401 AuthenticationError','failed'),('429 rate limit','pending'),
                                           ('process() takes no keyword arguments','failed')])
def test_provider_auth_failure_is_terminal_but_rate_limit_is_retryable(web,message,expected):
    client,app,_=web
    class FailingProvider(FakeProvider):
        async def chat(self,*args,**kwargs):
            if message == 'process() takes no keyword arguments':
                raise TypeError(message)
            raise RuntimeError(message)
    app.state.services.registry.resolve('essay').provider_factory=lambda:FailingProvider()
    tid=task(client)
    submit(client,tid)
    process(client,app)
    assert app.state.store.one('SELECT status FROM grading_jobs')['status']==expected


def test_heartbeat_keeps_slow_job_exclusively_owned(web):
    client, app, _ = web
    class SlowProvider(FakeProvider):
        async def chat(self,*args,**kwargs):
            await asyncio.sleep(.7)
            return await super().chat(*args,**kwargs)
    app.state.services.registry.resolve('essay').provider_factory=lambda:SlowProvider()
    app.state.services.queue.lease_seconds=.3
    tid=task(client)
    submit(client,tid)
    worker=GradingWorker(app.state.services,'slow-worker')
    other=JobQueue(app.state.store,lease_seconds=.3)
    async def scenario():
        running=asyncio.create_task(worker.run_once())
        await asyncio.sleep(.5)
        assert other.claim('should-not-steal') is None
        assert await running
    client.portal.call(scenario)
    assert app.state.store.one('SELECT attempts,status FROM grading_jobs')=={'attempts':1,'status':'succeeded'}


def test_exhausted_crashed_job_fails_without_infinite_reclaim(web):
    client, app, _ = web
    tid=task(client)
    sid=submit(client,tid)
    app.state.store.execute('UPDATE grading_jobs SET max_attempts=1 WHERE submission_id=?',(sid,))
    now=[time.time()+1]
    queue=JobQueue(app.state.store,lease_seconds=10,clock=lambda:now[0])
    first=queue.claim('crashed')
    now[0]+=11
    assert queue.claim('replacement') is None
    assert app.state.store.one('SELECT status FROM grading_jobs')['status']=='failed'
    assert not queue.complete(first,GradingResult.model_validate(RESULT))
    assert detail(client,tid)['submissions'][0]['status']=='failed'


def test_job_snapshot_does_not_follow_later_task_mutation(web):
    client, app, provider = web
    tid=task(client)
    sid=submit(client,tid)
    original=app.state.store.one('SELECT snapshot FROM grading_jobs')['snapshot']
    app.state.store.execute('UPDATE tasks SET assignment=? WHERE id=?',('这不应出现在已入队的评分中',tid))
    process(client,app)
    assert '写成长经历' in provider.calls[0][1].content
    assert '这不应出现在' not in provider.calls[0][1].content
    assert app.state.store.one('SELECT snapshot FROM grading_jobs')['snapshot']==original


def test_disabled_account_does_not_start_pending_model_call(web):
    client, app, provider=web
    tid=task(client)
    submit(client,tid)
    app.state.services.auth.manage_account('老师甲',active=False)
    process(client,app)
    assert provider.calls==[]
    assert app.state.store.one('SELECT status FROM grading_jobs')['status']=='failed'


def test_login_attempts_are_limited(web):
    client, app, _=web
    for _ in range(5):
        assert client.post('/api/login',json={'username':'nonexistent','password':'wrong-password'}).status_code==401
    assert client.post('/api/login',json={'username':'nonexistent','password':'wrong-password'}).status_code==429


def test_web_and_worker_can_be_created_independently(web):
    client, app, provider=web
    from shu_grading.backend.services import build_services
    tid=task(client)
    submit(client,tid)
    assert detail(client,tid)['submissions'][0]['status']=='pending'
    assert not client.get('/api/health').json()['worker_available']
    independent=build_services(app.state.store.root,provider_factory=lambda:provider)
    worker=GradingWorker(independent)
    assert client.portal.call(worker.run_once)
    assert detail(client,tid)['submissions'][0]['status']=='ready'
    assert client.get('/api/health').json()['worker_available']


def test_separate_worker_process_finishes_web_submission(web):
    client,app,_=web
    tid=task(client)
    submit(client,tid)
    script='''import asyncio,sys
from shu_grading.backend.services import build_services
from shu_grading.backend.worker import GradingWorker
from shu_grading.models.base import ChatResponse
class Provider:
    name='openai'
    def validate_config(self): return []
    async def chat(self,*args,**kwargs): return ChatResponse(content=sys.argv[2])
services=build_services(sys.argv[1],provider_factory=lambda:Provider())
assert asyncio.run(GradingWorker(services).run_once())
'''
    result=subprocess.run([sys.executable,'-c',script,str(app.state.store.root),json.dumps(RESULT)],capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
    assert detail(client,tid)['submissions'][0]['status']=='ready'
    job=app.state.store.one('SELECT * FROM grading_jobs')
    assert job['status']=='succeeded' and job['result_version_id']


def test_other_question_type_uses_same_submission_queue_and_review(tmp_path):
    class TranslationGrader:
        task_type='translation'
        version='1'
        title='测试翻译实现'
        received=None
        def validate_configuration(self,configuration):
            if not configuration.get('reference'):
                raise ServiceError(422,'需要参考答案')
            return {'reference':configuration['reference']}
        async def grade(self,request):
            self.received=request
            return GradingResult(total_score=4,max_score=5,criterion_scores=[CriterionScore(name='句意',score=4,max_score=5)],overall_comment='基本准确')
        def validate_review(self,configuration,result):
            if len(result.criterion_scores)!=1 or not 0<=result.criterion_scores[0].score<=5:
                raise ServiceError(422,'无效得分')
            result.total_score=result.criterion_scores[0].score
            return result
    grader=TranslationGrader()
    registry=GraderRegistry()
    registry.register(grader)
    app=create_app(tmp_path,registry=registry,start_worker=False)
    app.state.services.auth.create_account('测试老师','test-password')
    with TestClient(app) as client:
        client.post('/api/login',json={'username':'测试老师','password':'test-password'})
        created=client.post('/api/tasks',json={'title':'翻译任务','assignment':'翻译这句话','task_type':'translation','configuration':{'reference':'参考译文'}})
        assert created.status_code==200,created.text
        tid=created.json()['id']
        sid=submit(client,tid)
        process(client,app)
        row=detail(client,tid)['submissions'][0]
        assert row['ai_result']['total_score']==4
        assert client.put('/api/submissions/'+sid+'/review',json={'result':row['ai_result'],'confirm':True,'revision':row['revision']}).status_code==200
        assert client.get('/api/tasks/'+tid+'/export').status_code==200
        assert {f.name for f in fields(grader.received)}=={'assignment','image_paths','configuration'}
        assert isinstance(grader.received,GradingInput)
        assert client.get('/api/task-types').json()[0]['task_type']=='translation'
        assert client.post('/api/tasks',json={'title':'未开放','assignment':'题目','task_type':'unknown','configuration':{}}).status_code==422
