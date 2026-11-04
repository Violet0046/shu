"""使用模拟模型验证完整业务流程，不消耗真实模型额度。"""
import base64
import io
import json

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from PIL import Image

from shu_grading.models.base import ChatResponse
from shu_grading.web.server import create_app


RUBRIC = {"max_score": 50, "grade_level": "高一", "criteria": [
    {"name": "内容", "weight": .6, "description": "具体充实"},
    {"name": "语言", "weight": .4, "description": "通顺准确"}]}
RESULT = {"total_score": 40, "max_score": 50, "criterion_scores": [
    {"name": "内容", "score": 24, "max_score": 30, "comment": "内容充实"},
    {"name": "语言", "score": 16, "max_score": 20, "comment": "语言通顺"}],
    "overall_comment": "有真情实感", "highlights": ["选材具体"], "suggestions": ["补充细节"],
    "essay_title": "那一刻，我长大了", "transcription": "我接过她手里的［疑字］，轻轻说了声谢谢。"}


class FakeProvider:
    name = "openai"
    def __init__(self):
        self.calls = []
        self.fail = False
        self.result = RESULT
    def validate_config(self):
        return []
    async def chat(self, messages, **kwargs):
        self.calls.append(messages)
        if self.fail:
            raise RuntimeError("sensitive-provider-key-do-not-display")
        return ChatResponse(content=json.dumps({**self.result, "identity": {"raw_name": "模型乱猜的姓名"}}, ensure_ascii=False))


def image_data():
    buffer = io.BytesIO()
    Image.new("RGB", (120, 180), "white").save(buffer, "PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


@pytest.fixture
def web(tmp_path):
    model = FakeProvider()
    app = create_app(tmp_path, provider_factory=lambda: model, start_worker=False)
    with TestClient(app) as client:
        app.state.services.auth.create_account('老师甲','test-password')
        assert client.post('/api/login', json={"username": "老师甲", "password": "test-password"}).status_code == 200
        yield client, app, model


def task(client, **kwargs):
    response = client.post('/api/tasks', json={"title": "测验", "assignment": "写成长经历", "mode": "anonymous", "rubric": RUBRIC, **kwargs})
    assert response.status_code == 200, response.text
    return response.json()['id']


def submit(client, tid, **kwargs):
    response = client.post(f'/api/tasks/{tid}/submissions', json={"image": image_data(), **kwargs})
    assert response.status_code == 200, response.text
    return response.json()['id']


def detail(client, tid):
    return client.get(f'/api/tasks/{tid}').json()


def process(client, app):
    assert client.portal.call(app.state.process_one)


def test_anonymous_single_call_and_no_name_prompt(web):
    client, app, model = web
    tid = task(client)
    sid = submit(client, tid)
    process(client, app)
    record = detail(client, tid)['submissions'][0]
    assert record['id'] == sid
    assert record['label'] == '答卷 001'
    assert record['status'] == 'ready' and not record['confirmed']
    assert len(model.calls) == 1
    assert '白名单' not in model.calls[0][1].content
    assert '"identity"' not in model.calls[0][0].content
    assert record['ai_result']['total_score'] == 40
    assert client.get(f'/api/submissions/{sid}/image').status_code == 200


def test_named_binding_duplicate_and_correction(web):
    client, app, model = web
    cid = client.post('/api/classes', json={"name": "一班", "students": [
        {"name": "张同学", "student_id": "01"}, {"name": "张同学", "student_id": "02"}]}).json()['id']
    tid = task(client, mode='named', class_id=cid)
    invalid = client.post(f'/api/tasks/{tid}/submissions', json={"image": image_data(), "student_id": "missing"})
    assert invalid.status_code == 422
    sid = submit(client, tid, student_id='01')
    assert client.post(f'/api/tasks/{tid}/submissions', json={"image": image_data(), "student_id": "01"}).status_code == 409
    process(client, app)
    assert '张同学' not in model.calls[0][1].content
    assert client.put(f'/api/submissions/{sid}/identity', json={"student_id": "02"}).status_code == 200
    assert detail(client, tid)['submissions'][0]['student_id'] == '02'
    assert client.get(f'/api/submissions/{sid}/versions').json()[0]['kind'] == 'identity'


def test_templates_are_snapshotted_and_private(web):
    client, app, _ = web
    template_id = client.post('/api/templates', json={"name": "我的标准", "rubric": RUBRIC}).json()['id']
    tid = task(client)
    changed = {**RUBRIC, "grade_level": "高二"}
    assert client.put('/api/templates/'+template_id, json={"name": "新标准", "rubric": changed}).status_code == 200
    assert detail(client, tid)['rubric']['grade_level'] == '高一'
    sid = submit(client, tid)
    with TestClient(app) as other:
        app.state.services.auth.create_account('老师乙','test-password')
        other.post('/api/login', json={"username": "老师乙", "password": "test-password"})
        personal = other.get('/api/templates').json()
        assert len(personal) == 1 and personal[0]['is_default']
        assert all(t['id'] != template_id for t in personal)
        assert other.get(f'/api/tasks/{tid}').status_code == 404
        assert other.get(f'/api/submissions/{sid}/image').status_code == 404
        assert other.get(f'/api/tasks/{tid}/export').status_code == 404
        assert other.put('/api/templates/'+template_id, json={"name": "篡改", "rubric": changed}).status_code == 404


def test_confirmed_review_blocks_regrade_and_rejects_stale_update(web):
    client, app, model = web
    tid = task(client)
    sid = submit(client, tid)
    process(client, app)
    row = detail(client, tid)['submissions'][0]
    result = {**RESULT, "overall_comment": "教师确认的评语"}
    body = {"result": result, "confirm": True, "revision": row['revision']}
    assert client.put(f'/api/submissions/{sid}/review', json=body).status_code == 200
    assert client.put(f'/api/submissions/{sid}/review', json=body).status_code == 409
    assert client.post(f'/api/submissions/{sid}/retry', json={}).status_code == 409
    assert app.state.store.one('SELECT COUNT(*) AS n FROM grading_jobs')['n'] == 1
    assert not client.portal.call(app.state.process_one)
    row = detail(client, tid)['submissions'][0]
    assert row['confirmed'] == 1
    assert row['final_result']['overall_comment'] == '教师确认的评语'
    assert row['ai_result']['overall_comment'] == RESULT['overall_comment']
    assert len(client.get(f'/api/submissions/{sid}/versions').json()) == 2


def test_export_uses_final_and_zero_score(web):
    client, app, _ = web
    tid = task(client)
    sid = submit(client, tid)
    assert client.get(f'/api/tasks/{tid}/export').status_code == 422
    process(client, app)
    row = detail(client, tid)['submissions'][0]
    zero = json.loads(json.dumps(RESULT))
    for c in zero['criterion_scores']:
        c['score'] = 0
    assert client.put(f'/api/submissions/{sid}/review', json={"result": zero, "confirm": True, "revision": row['revision']}).status_code == 200
    response = client.get(f'/api/tasks/{tid}/export')
    assert response.status_code == 200
    book = load_workbook(io.BytesIO(response.content))
    assert book['成绩总表']['C2'].value == 0
    assert book['班级统计']['B9'].value == '0.0'


def test_bad_scores_and_bad_image_rejected(web):
    client, app, _ = web
    tid = task(client)
    assert client.post(f'/api/tasks/{tid}/submissions', json={"image": "not an image"}).status_code == 422
    sid = submit(client, tid)
    process(client, app)
    row = detail(client, tid)['submissions'][0]
    result = json.loads(json.dumps(RESULT))
    result['criterion_scores'][0]['score'] = 100
    assert client.put(f'/api/submissions/{sid}/review', json={"result": result, "revision": row['revision']}).status_code == 422
    result['criterion_scores'] = []
    assert client.put(f'/api/submissions/{sid}/review', json={"result": result, "revision": row['revision']}).status_code == 422


def test_failed_call_can_retry_without_exposing_provider_error(web):
    client, app, model = web
    tid = task(client)
    sid = submit(client, tid)
    model.fail = True
    app.state.store.execute('UPDATE grading_jobs SET max_attempts=1 WHERE submission_id=?',(sid,))
    process(client, app)
    row = detail(client, tid)['submissions'][0]
    assert row['status'] == 'failed'
    assert 'sensitive' not in row['error']
    model.fail = False
    client.post(f'/api/submissions/{sid}/retry', json={})
    process(client, app)
    assert detail(client, tid)['submissions'][0]['status'] == 'ready'


def test_pairing_is_upload_only_and_revocable(web, monkeypatch):
    client, app, _ = web
    monkeypatch.setenv('SHU_PUBLIC_URL', 'http://192.168.1.2:8000')
    tid = task(client)
    token = client.post(f'/api/tasks/{tid}/pair', json={}).json()['token']
    with TestClient(app) as phone:
        assert phone.get('/api/capture/'+token).status_code == 200
        assert phone.get('/api/capture/'+token+'/qr').headers['content-type'] == 'image/png'
        sid = phone.post('/api/capture/'+token, json={"image": image_data()}).json()['id']
        assert phone.get('/api/submissions/'+sid+'/image').status_code == 401
        assert phone.get('/api/tasks/'+tid).status_code == 401
        app.state.store.execute('UPDATE pairs SET expires=0 WHERE token=?', (token,))
        assert phone.post('/api/capture/'+token, json={"image": image_data()}).status_code == 403
    token = client.post(f'/api/tasks/{tid}/pair', json={}).json()['token']
    assert client.request('DELETE', f'/api/tasks/{tid}/pair', json={}).status_code == 200
    assert client.get('/api/capture/'+token).status_code == 403


def test_restart_recovers_processing_and_preserves_login(web):
    client, app, model = web
    tid = task(client)
    sid = submit(client, tid)
    claimed=app.state.services.queue.claim('interrupted-worker')
    assert claimed['submission_id']==sid
    app.state.store.execute('UPDATE grading_jobs SET lease_expires=0 WHERE id=?',(claimed['id'],))
    fresh = create_app(app.state.store.root, provider_factory=lambda: model, start_worker=False)
    with TestClient(fresh) as restarted:
        restarted.cookies.update(client.cookies)
        assert detail(restarted, tid)['submissions'][0]['status'] == 'processing'
        process(restarted, fresh)
        assert detail(restarted, tid)['submissions'][0]['status'] == 'ready'


def test_session_and_origin_protection(web):
    client, app, _ = web
    assert client.post('/api/tasks', json={}, headers={'Origin': 'https://untrusted.example'}).status_code == 403
    assert client.post('/api/logout', content='{}', headers={'Content-Type':'text/plain'}).status_code == 415
    assert client.post('/api/logout', json={}).status_code == 200
    assert client.get('/api/me').status_code == 401
    assert client.post('/api/login', json={"username":"老师甲", "password":"wrong-password"}).status_code == 401
    assert client.post('/api/login', json={"username":"老师甲", "password":"test-password"}).status_code == 200


def test_invalid_model_dimension_does_not_become_zero_score(web):
    client, app, model = web
    model.result = {**RESULT, "criterion_scores": []}
    tid = task(client)
    submit(client, tid)
    process(client, app)
    row = detail(client, tid)['submissions'][0]
    assert row['status'] == 'failed'
    assert row['ai_result'] is None


def test_export_treats_comments_as_text(web):
    client, app, _ = web
    tid = task(client)
    sid = submit(client, tid)
    process(client, app)
    row = detail(client, tid)['submissions'][0]
    result = {**RESULT, "overall_comment": '=HYPERLINK("https://example.invalid", "评语")'}
    assert client.put(f'/api/submissions/{sid}/review', json={"result": result, "confirm": True, "revision": row['revision']}).status_code == 200
    book = load_workbook(io.BytesIO(client.get(f'/api/tasks/{tid}/export').content))
    assert book['成绩总表']['F2'].data_type == 's'


def test_named_default_template_is_editable_deletable_without_reseeding(web):
    client, _, _ = web
    initial = client.get('/api/templates').json()
    assert len(initial) == 1 and initial[0]['is_default']
    assert initial[0]['name'] == '高中记叙文·50分制'
    first = initial[0]['id']
    assert client.get('/api/templates').json()[0]['id'] == first
    assert client.put('/api/templates/'+first, json={'name':'我修改的标准','rubric':RUBRIC}).status_code == 200
    tid = task(client, rubric=client.get('/api/templates').json()[0]['rubric'])
    snapshot = detail(client,tid)['rubric']
    second = client.post('/api/templates', json={'name':'第二套标准','rubric':RUBRIC}).json()['id']
    assert client.put('/api/templates/'+second+'/default', json={}).status_code == 200
    assert next(t for t in client.get('/api/templates').json() if t['is_default'])['id'] == second
    assert client.request('DELETE','/api/templates/'+second,json={}).status_code == 200
    assert client.get('/api/templates').json()[0]['is_default']
    assert client.request('DELETE','/api/templates/'+first,json={}).status_code == 200
    assert client.get('/api/templates').json() == []
    assert detail(client,tid)['rubric'] == snapshot
    replacement = client.post('/api/templates',json={'name':'重新创建','rubric':RUBRIC}).json()['id']
    assert client.get('/api/templates').json()[0]['id'] == replacement
    assert client.get('/api/templates').json()[0]['is_default']


def test_class_edit_automatically_preserves_submitted_students(web):
    client, app, _ = web
    cid = client.post('/api/classes',json={'name':'一班','students':[{'name':'张同学','student_id':'01'},{'name':'李同学','student_id':'02'}]}).json()['id']
    tid = task(client,mode='named',class_id=cid)
    sid = submit(client,tid,student_id='01')
    changed = {'name':'新一班','students':[{'name':'李同学改名','student_id':'02'},{'name':'新同学','student_id':'03'}]}
    assert client.put('/api/classes/'+cid,json=changed).status_code == 200
    row = detail(client,tid)
    assert row['class_name'] == '新一班'
    assert {s['student_id'] for s in row['students']} == {'01','02','03'}
    assert row['submissions'][0]['id'] == sid
    with TestClient(app) as other:
        app.state.services.auth.create_account('老师乙','test-password')
        other.post('/api/login',json={'username':'老师乙','password':'test-password'})
        assert other.put('/api/classes/'+cid,json=changed).status_code == 404
        template_id = client.get('/api/templates').json()[0]['id']
        assert other.request('DELETE','/api/templates/'+template_id,json={}).status_code == 404
        assert other.put('/api/templates/'+template_id+'/default',json={}).status_code == 404
        assert other.post('/api/tasks/'+tid+'/sync-class',json={}).status_code in (404,405)


def test_sync_student_rename_is_audited(web):
    client, app, _ = web
    cid = client.post('/api/classes',json={'name':'一班','students':[{'name':'张同学','student_id':'01'}]}).json()['id']
    tid = task(client,mode='named',class_id=cid)
    sid = submit(client,tid,student_id='01')
    process(client, app)
    client.put('/api/classes/'+cid,json={'name':'一班','students':[{'name':'张同学改名','student_id':'01'}]})
    assert detail(client,tid)['submissions'][0]['label'] == '张同学改名'
    assert client.get('/api/submissions/'+sid+'/versions').json()[0]['kind'] == 'identity'


def test_upload_always_open_including_old_closed_tasks(web,monkeypatch):
    client, app, _ = web
    monkeypatch.setenv('SHU_PUBLIC_URL','http://localhost:8000')
    tid = task(client)
    # 旧版标记结束录入的任务，也不再阻止上传。
    app.state.store.execute('UPDATE tasks SET input_closed=1 WHERE id=?',(tid,))
    sid = submit(client,tid)
    token = client.post('/api/tasks/'+tid+'/pair',json={}).json()['token']
    assert 'input_closed' not in detail(client,tid)
    assert client.put('/api/tasks/'+tid+'/intake',json={'closed':True}).status_code in (404,405)
    assert client.post('/api/tasks/'+tid+'/sync-class',json={}).status_code in (404,405)
    assert client.post('/api/capture/'+token,json={'image':image_data()}).status_code == 200
    process(client,app)
    row = next(r for r in detail(client,tid)['submissions'] if r['id']==sid)
    assert client.put('/api/submissions/'+sid+'/review',json={'result':RESULT,'confirm':True,'revision':row['revision']}).status_code == 200


def test_transcription_and_teacher_deduction_export_and_regrade(web):
    client, app, _ = web
    tid = task(client)
    sid = submit(client,tid)
    process(client,app)
    row = detail(client,tid)['submissions'][0]
    assert row['ai_result']['transcription'] == RESULT['transcription']
    assert row['ai_result']['presentation_deduction'] == 0
    result = {**RESULT,'presentation_deduction':3,'transcription':'不应伪造的转写'}
    assert client.put('/api/submissions/'+sid+'/review',json={'result':result,'confirm':True,'revision':row['revision']}).status_code == 200
    final = detail(client,tid)['submissions'][0]['final_result']
    assert final['total_score'] == 37
    assert final['transcription'] == RESULT['transcription']
    book = load_workbook(io.BytesIO(client.get('/api/tasks/'+tid+'/export').content))
    cells = list(book['成绩总表'].values)
    assert cells[1][cells[0].index('正文分')] == 40
    assert cells[1][cells[0].index('卷面扣分')] == 3
    assert cells[1][2] == 37
    assert client.post('/api/submissions/'+sid+'/retry',json={}).status_code == 409
    assert not client.portal.call(app.state.process_one)
    row = detail(client,tid)['submissions'][0]
    assert row['final_result']['total_score'] == 37 and row['confirmed']
    result['presentation_deduction'] = 51
    assert client.put('/api/submissions/'+sid+'/review',json={'result':result,'revision':row['revision']}).status_code == 422


def test_legacy_database_migrates_without_losing_tasks(tmp_path):
    import sqlite3
    from shu_grading.backend.database import Store
    with sqlite3.connect(tmp_path/'web.sqlite3') as db:
        db.execute('CREATE TABLE tasks(id TEXT PRIMARY KEY, owner TEXT, title TEXT, assignment TEXT, mode TEXT,class_id TEXT,class_name TEXT,students TEXT,rubric TEXT,created REAL)')
        db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?,?)',('legacy','owner','旧任务','作文','anonymous',None,'匿名','[]',json.dumps(RUBRIC),1))
    store = Store(tmp_path)
    row = store.one('SELECT * FROM tasks WHERE id=?',('legacy',))
    assert row['title'] == '旧任务' and row['input_closed'] == 0


def test_original_bytes_are_preserved_separately_from_model_image(web):
    client, app, _ = web
    tid=task(client)
    buffer=io.BytesIO()
    Image.new('RGB',(3800,1200),'#123456').save(buffer,'PNG')
    original=buffer.getvalue()
    response=client.post('/api/tasks/'+tid+'/submissions',json={'image':'data:image/png;base64,'+base64.b64encode(original).decode()})
    assert response.status_code==200
    sid=response.json()['id']
    view=client.get('/api/submissions/'+sid+'/image')
    assert view.content==original and view.headers['content-type']=='image/png'
    row=app.state.store.one('SELECT * FROM submissions WHERE id=?',(sid,))
    with Image.open(app.state.store.root/'uploads'/row['image']) as resized:
        assert resized.width==3072
    # 旧答卷没有原文件时，继续提供已保存的图，不破坏历史记录。
    app.state.store.execute('UPDATE submissions SET original_image=NULL WHERE id=?',(sid,))
    assert client.get('/api/submissions/'+sid+'/image').headers['content-type']=='image/jpeg'
