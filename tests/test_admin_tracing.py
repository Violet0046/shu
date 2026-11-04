import asyncio
import json
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from shu_grading.models.base import ChatResponse
from shu_grading.models.tracing import normalize_usage
from shu_grading.backend.errors import ServiceError
from shu_grading.web.server import create_app
from test_web_workflow import FakeProvider, RESULT, detail, process, submit, task, web


def become_admin(client, app):
    app.state.services.auth.set_admin('老师甲', True)
    assert client.post('/api/login', json={'username':'老师甲','password':'test-password'}).status_code == 200


def test_admin_is_explicit_and_every_endpoint_rejects_teacher(web):
    client, app, _ = web
    assert not client.get('/api/me').json()['is_admin']
    for path in ('overview','accounts','traces','traces/unknown','events'):
        assert client.get('/api/admin/'+path).status_code == 403
    assert client.post('/api/admin/accounts',json={'username':'其他老师','password':'password-123'}).status_code == 403
    uid=app.state.store.one('SELECT id FROM users')['id']
    assert client.patch('/api/admin/accounts/'+uid,json={'active':False}).status_code == 403
    become_admin(client, app)
    assert client.get('/api/me').json()['is_admin']
    assert client.get('/api/admin/overview').status_code == 200
    # Privilege revocation takes effect for an already authenticated client.
    app.state.services.auth.create_account('第二管理员','password-123')
    app.state.services.auth.set_admin('第二管理员',True)
    app.state.services.auth.set_admin('老师甲',False)
    assert client.get('/api/admin/accounts').status_code == 401


def test_web_account_management_revokes_sessions_and_audits_without_passwords(web):
    client, app, _ = web
    become_admin(client,app)
    body={'username':'教师乙','password':'initial-password','recipient':'一年级'}
    uid=client.post('/api/admin/accounts',json=body).json()['id']
    assert client.post('/api/admin/accounts',json={**body,'is_admin':True}).status_code == 422
    with TestClient(app) as teacher:
        assert teacher.post('/api/login',json={'username':'教师乙','password':'initial-password'}).status_code == 200
        assert client.patch('/api/admin/accounts/'+uid,json={'password':'replacement-password'}).status_code == 200
        assert teacher.get('/api/me').status_code == 401
        assert teacher.post('/api/login',json={'username':'教师乙','password':'replacement-password'}).status_code == 200
        assert client.patch('/api/admin/accounts/'+uid,json={'active':False}).status_code == 200
        assert teacher.get('/api/me').status_code == 401
        assert client.patch('/api/admin/accounts/'+uid,json={'active':True}).status_code == 200
    own=client.get('/api/admin/accounts').json()['current_user']
    assert client.patch('/api/admin/accounts/'+own,json={'active':False}).status_code == 409
    payload=client.get('/api/admin/accounts').text+client.get('/api/admin/events').text
    assert 'initial-password' not in payload and 'replacement-password' not in payload
    events=client.get('/api/admin/events').json()
    assert {'created','password_reset','disabled','enabled'} <= {e['action'] for e in events}
    assert client.patch('/api/admin/accounts/'+uid,json={'is_admin':True}).status_code == 422


@pytest.mark.parametrize('provider,raw,expected',[
    ('openai',{'prompt_tokens':100,'completion_tokens':30,'prompt_tokens_details':{'cached_tokens':80},
               'completion_tokens_details':{'reasoning_tokens':20}},(100,30,80,None,20)),
    ('claude',{'input_tokens':20,'output_tokens':30,'cache_read_input_tokens':80,'cache_creation_input_tokens':10},(110,30,80,10,None)),
    ('openai',{},(None,None,None,None,None)),
    ('openai',{'prompt_tokens':0,'completion_tokens':0,'prompt_tokens_details':{'cached_tokens':0}},(0,0,0,None,None)),
])
def test_usage_distinguishes_missing_and_zero_and_avoids_double_count(provider,raw,expected):
    normalized=normalize_usage(provider,raw,{})
    assert tuple(normalized[k] for k in ('input_tokens','output_tokens','cached_tokens','cache_write_tokens','reasoning_tokens')) == expected


def test_model_calls_persist_even_when_scoring_json_is_invalid(web,monkeypatch):
    client,app,_=web
    secret='test-real-secret-123456'
    monkeypatch.setenv('OPENAI_API_KEY',secret)
    class RepairProvider(FakeProvider):
        model_name='MiniMax-M3.1-Flash'
        async def chat(self,messages,**kwargs):
            self.calls.append(messages)
            return ChatResponse(content='not JSON '+secret,usage={},raw={'model':'actual-model','_request_id':'request-123',
                'usage':{'prompt_tokens':100,'completion_tokens':30,'prompt_tokens_details':{'cached_tokens':80},
                         'completion_tokens_details':{'reasoning_tokens':20}},'reasoning_content':'DO NOT SAVE THOUGHTS'})
    provider=RepairProvider()
    app.state.services.registry.resolve('essay').provider_factory=lambda:provider
    tid=task(client);submit(client,tid);process(client,app)
    assert len(provider.calls)==2
    records=app.state.store.all('SELECT * FROM model_calls ORDER BY started')
    assert [r['purpose'] for r in records]==['grading','json_repair']
    assert all(r['status']=='succeeded' for r in records)
    assert all(r['input_tokens']==100 and r['cached_tokens']==80 for r in records)
    assert all(r['request_id']=='request-123' and r['actual_model']=='actual-model' for r in records)
    assert secret not in json.dumps(records) and 'DO NOT SAVE THOUGHTS' not in json.dumps(records)
    assert 'base64' not in records[0]['messages']
    assert detail(client,tid)['submissions'][0]['status']=='failed'
    become_admin(client,app)
    totals=client.get('/api/admin/overview').json()['totals']
    assert totals['calls']==2 and totals['input_tokens']==200 and totals['cached_tokens']==160
    listed=client.get('/api/admin/traces?q='+tid).json()
    assert listed['total']==0
    listed=client.get('/api/admin/traces?status=failed&model=MiniMax').json()
    assert listed['total']==1
    trace=client.get('/api/admin/traces/'+listed['items'][0]['id']).json()
    assert len(trace['calls'])==2 and trace['job']['status']=='failed'


def test_failures_and_unknown_usage_are_recorded(web):
    client,app,model=web
    model.fail=True
    tid=task(client);submit(client,tid);process(client,app)
    call=app.state.store.one('SELECT * FROM model_calls')
    assert call['status']=='failed' and call['input_tokens'] is None
    assert call['error_type']=='RuntimeError'
    model.fail=False
    # Unreported usage on successful responses must remain unknown too.
    tid=task(client);submit(client,tid);process(client,app)
    calls=app.state.store.all('SELECT * FROM model_calls')
    assert len(calls)==2 and all(c['input_tokens'] is None for c in calls)


def test_cli_bootstrap_admin_and_migration_are_repeatable(tmp_path):
    result=subprocess.run([sys.executable,'-m','shu_grading.backend.admin','--data-dir',str(tmp_path),
        'create-user','administrator','--admin','--password-stdin'],input='known-admin-password\n',text=True,capture_output=True,timeout=30)
    assert result.returncode==0,result.stderr
    assert 'known-admin-password' not in result.stdout
    app=create_app(tmp_path,start_worker=False)
    with TestClient(app) as client:
        assert client.post('/api/login',json={'username':'administrator','password':'known-admin-password'}).json()['is_admin']
        assert client.get('/api/admin/overview').status_code==200
        with pytest.raises(ServiceError,match='至少保留'):
            app.state.services.auth.set_admin('administrator',False)


def test_concurrent_jobs_keep_separate_traces_and_cancelled_calls_are_retained(web):
    client,app,_=web
    class SlowProvider(FakeProvider):
        model_name='fake-vision'
        async def chat(self,messages,**kwargs):
            await asyncio.sleep(.05)
            return ChatResponse(content=json.dumps(RESULT),usage={'input_tokens':11,'output_tokens':7})
    app.state.services.registry.resolve('essay').provider_factory=lambda:SlowProvider()
    for assignment in ('第一份题目','第二份题目'):
        tid=task(client,assignment=assignment);submit(client,tid)
    from shu_grading.backend.worker import GradingWorker
    async def parallel():
        await asyncio.gather(GradingWorker(app.state.services,'one').run_once(),GradingWorker(app.state.services,'two').run_once())
    client.portal.call(parallel)
    calls=app.state.store.all('SELECT * FROM model_calls')
    assert len({c['job_id'] for c in calls})==2
    for call in calls:
        snapshot=json.loads(app.state.store.one('SELECT snapshot FROM grading_jobs WHERE id=?',(call['job_id'],))['snapshot'])
        assert snapshot['assignment'] in call['messages']
    class BlockedProvider(FakeProvider):
        async def chat(self,**kwargs):
            await asyncio.Event().wait()
    app.state.services.registry.resolve('essay').provider_factory=lambda:BlockedProvider()
    tid=task(client);submit(client,tid)
    async def interrupted():
        runner=asyncio.create_task(GradingWorker(app.state.services,'interrupted').run_once())
        for _ in range(100):
            if app.state.store.one("SELECT id FROM model_calls WHERE status='processing'"):
                break
            await asyncio.sleep(.01)
        runner.cancel()
        with pytest.raises(asyncio.CancelledError):
            await runner
    client.portal.call(interrupted)
    row=app.state.store.one("SELECT * FROM model_calls WHERE status='interrupted'")
    assert row and row['finished'] and row['input_tokens'] is None
