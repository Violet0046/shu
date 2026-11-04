import time

from fastapi.testclient import TestClient

from test_web_workflow import detail, image_data, process, submit, task, web


def test_pair_reuses_live_token_and_closed_tokens_reject_all_phones(web,monkeypatch):
    client,app,_=web
    monkeypatch.setenv('SHU_PUBLIC_URL','https://school.example')
    tid=task(client)
    one=client.post(f'/api/tasks/{tid}/pair',json={}).json()
    two=client.post(f'/api/tasks/{tid}/pair',json={}).json()
    assert one==two
    assert app.state.store.one('SELECT COUNT(*) AS n FROM pairs')['n']==1
    with TestClient(app) as phone1,TestClient(app) as phone2:
        url='/api/capture/'+one['token']
        assert phone1.get(url).status_code==200
        assert phone2.get(url).status_code==200
        assert client.request('DELETE',f'/api/tasks/{tid}/pair',json={}).status_code==200
        for phone in (phone1,phone2):
            assert phone.get(url).status_code==403
            assert phone.get(url+'/qr').status_code==403
            assert phone.post(url,json={'image':image_data()}).status_code==403
    fresh=client.post(f'/api/tasks/{tid}/pair',json={}).json()
    assert fresh['token']!=one['token']
    assert app.state.store.one('SELECT COUNT(*) AS n FROM submissions')['n']==0


def test_revoke_during_image_preparation_cannot_commit_phone_upload(web,monkeypatch):
    client,app,_=web
    monkeypatch.setenv('SHU_PUBLIC_URL','https://school.example')
    tid=task(client);token=client.post(f'/api/tasks/{tid}/pair',json={}).json()['token']
    original=app.state.services.files.prepare
    def revoke_while_preparing(encoded):
        image=original(encoded)
        app.state.services.capture.revoke(app.state.store.one('SELECT id FROM users')['id'],tid)
        return image
    monkeypatch.setattr(app.state.services.files,'prepare',revoke_while_preparing)
    assert client.post('/api/capture/'+token,json={'image':image_data()}).status_code==403
    assert app.state.store.one('SELECT COUNT(*) AS n FROM submissions')['n']==0
    assert app.state.store.one('SELECT COUNT(*) AS n FROM grading_jobs')['n']==0


def test_deleted_task_disappears_revokes_pair_and_cancels_queued_work(web,monkeypatch):
    client,app,model=web
    monkeypatch.setenv('SHU_PUBLIC_URL','https://school.example')
    tid=task(client);sid=submit(client,tid)
    token=client.post(f'/api/tasks/{tid}/pair',json={}).json()['token']
    with TestClient(app) as stranger:
        app.state.services.auth.create_account('其他老师','password-123')
        stranger.post('/api/login',json={'username':'其他老师','password':'password-123'})
        assert stranger.request('DELETE',f'/api/tasks/{tid}',json={}).status_code==404
    assert client.request('DELETE',f'/api/tasks/{tid}',json={}).status_code==200
    assert all(t['id']!=tid for t in client.get('/api/tasks').json())
    assert client.get(f'/api/tasks/{tid}').status_code==404
    assert client.get(f'/api/tasks/{tid}/export').status_code==404
    assert client.post(f'/api/tasks/{tid}/pair',json={}).status_code==404
    assert client.post(f'/api/tasks/{tid}/submissions',json={'image':image_data()}).status_code==404
    assert client.post(f'/api/submissions/{sid}/retry',json={}).status_code==404
    assert client.get(f'/api/submissions/{sid}/image').status_code==404
    assert client.get('/api/capture/'+token).status_code==403
    assert not client.portal.call(app.state.process_one)
    assert not model.calls
    assert app.state.store.one('SELECT status FROM grading_jobs')['status']=='cancelled'


def test_delete_waits_for_live_lease_and_keeps_historical_usage(web):
    client,app,_=web
    tid=task(client);submit(client,tid)
    job=app.state.services.queue.claim('active-worker')
    assert client.request('DELETE',f'/api/tasks/{tid}',json={}).status_code==409
    assert client.get(f'/api/tasks/{tid}').status_code==200
    app.state.services.queue.abandon(job)
    process(client,app)
    assert client.request('DELETE',f'/api/tasks/{tid}',json={}).status_code==200
    assert app.state.store.one('SELECT COUNT(*) AS n FROM model_calls')['n']==1
    assert app.state.store.one('SELECT status FROM grading_jobs')['status']=='succeeded'


def test_expired_pairs_are_cleaned_when_reopening(web,monkeypatch):
    client,app,_=web
    monkeypatch.setenv('SHU_PUBLIC_URL','https://school.example')
    tid=task(client);old=client.post(f'/api/tasks/{tid}/pair',json={}).json()
    app.state.store.execute('UPDATE pairs SET expires=?',(time.time()-1,))
    fresh=client.post(f'/api/tasks/{tid}/pair',json={}).json()
    assert fresh['token']!=old['token']
    assert app.state.store.one('SELECT COUNT(*) AS n FROM pairs')['n']==1
