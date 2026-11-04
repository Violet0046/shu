"""本地一键启动必须能领取任务；测试只使用临时目录和模拟模型。"""
import time

import pytest
from fastapi.testclient import TestClient

import web_app
from shu_grading.web.server import create_app
from test_web_workflow import FakeProvider, detail, submit, task


@pytest.mark.parametrize('arguments,setting,expected', [
    ([], None, '1'),
    (['--api-only'], '1', '0'),
    ([], '0', '0'),
])
def test_local_launcher_and_explicit_server_mode(monkeypatch, arguments, setting, expected):
    if setting is None:
        monkeypatch.setenv('SHU_EMBEDDED_WORKER','test-placeholder')
        monkeypatch.delenv('SHU_EMBEDDED_WORKER', raising=False)
    else:
        monkeypatch.setenv('SHU_EMBEDDED_WORKER',setting)
    calls=[]
    monkeypatch.setattr(web_app.uvicorn,'run',lambda *a,**k:calls.append((a,k)))
    web_app.main(arguments)
    import os
    assert os.environ['SHU_EMBEDDED_WORKER']==expected
    assert calls[0][0]==('shu_grading.web.server:create_app',)
    assert calls[0][1]['factory']


def test_one_local_command_processes_submission_without_external_worker(tmp_path,monkeypatch):
    monkeypatch.setenv('SHU_EMBEDDED_WORKER','test-placeholder')
    monkeypatch.delenv('SHU_EMBEDDED_WORKER',raising=False)
    provider=FakeProvider()
    def fake_server(*args,**kwargs):
        # 通过启动命令设置的默认模式创建应用，不显式传 start_worker=True。
        app=create_app(tmp_path,provider_factory=lambda:provider)
        app.state.services.auth.create_account('本地老师','test-password')
        with TestClient(app) as client:
            assert client.post('/api/login',json={'username':'本地老师','password':'test-password'}).status_code==200
            tid=task(client)
            submit(client,tid)
            deadline=time.monotonic()+5
            while time.monotonic()<deadline:
                row=detail(client,tid)['submissions'][0]
                if row['status']=='ready':
                    break
                time.sleep(.05)
            assert row['status']=='ready'
            assert len(provider.calls)==1
            health=client.get('/api/health').json()
            assert health['embedded_worker'] and health['worker_available']
    monkeypatch.setattr(web_app.uvicorn,'run',fake_server)
    web_app.main([])
