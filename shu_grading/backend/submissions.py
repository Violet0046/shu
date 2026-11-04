import hashlib
import json
import secrets
import time

from .database import dumps
from .errors import ServiceError
from .tasks import snapshot


class SubmissionService:
    def __init__(self, store, tasks, files, queue, registry, max_attempts=3):
        self.store, self.tasks, self.files = store, tasks, files
        self.queue, self.registry, self.max_attempts = queue, registry, max_attempts

    def get(self, uid, sid):
        row = self.store.one('''SELECT s.* FROM submissions s JOIN tasks t ON t.id=s.task_id
            WHERE s.id=? AND t.owner=? AND t.deleted_at IS NULL''', (sid, uid))
        if not row:
            raise ServiceError(404, '答卷不存在')
        return row

    def submit(self, uid, tid, body, capture_token=None):
        self.tasks.get(uid, tid)
        prepared = self.files.prepare(body.image)
        fingerprint = hashlib.sha256(dumps([prepared.digest, body.student_id, body.paper_number.strip()]).encode()).hexdigest()
        sid = secrets.token_hex(16)
        keys = None
        try:
            with self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                task = db.execute('SELECT * FROM tasks WHERE id=? AND owner=? AND deleted_at IS NULL', (tid, uid)).fetchone()
                if not task:
                    raise ServiceError(404, '任务不存在')
                if capture_token is not None and not db.execute(
                        'SELECT token FROM pairs WHERE token=? AND task_id=? AND expires>?',
                        (capture_token,tid,time.time())).fetchone():
                    raise ServiceError(403, '拍照入口已关闭或过期，请在电脑端重新开启')
                owner = db.execute('SELECT active FROM users WHERE id=?', (uid,)).fetchone()
                if not owner or not owner['active']:
                    raise ServiceError(403, '账号已停用')
                if body.request_id:
                    existing = db.execute('SELECT id,label,request_hash FROM submissions WHERE task_id=? AND client_request_id=?', (tid, body.request_id)).fetchone()
                    if existing:
                        if existing['request_hash'] != fingerprint:
                            raise ServiceError(409, '同一提交标识不能用于不同的答卷')
                        return {'id': existing['id'], 'label': existing['label']}
                student = None
                if task['mode']=='named':
                    student = next((s for s in json.loads(task['students']) if s['student_id']==body.student_id), None)
                    if not student:
                        raise ServiceError(422, '请从本班名单选择学生')
                    if db.execute('SELECT id FROM submissions WHERE task_id=? AND student_id=?', (tid, body.student_id)).fetchone():
                        raise ServiceError(409, '该学生已录入，请在已有答卷中重评或更正身份')
                keys = self.files.save(sid, prepared)
                number = db.execute('SELECT COALESCE(MAX(number),0)+1 FROM submissions WHERE task_id=?', (tid,)).fetchone()[0]
                label = student['name'] if student else body.paper_number.strip() or f'答卷 {number:03d}'
                db.execute('''INSERT INTO submissions(id,task_id,number,student_id,label,image,status,created,original_image,client_request_id,request_hash)
                    VALUES(?,?,?,?,?,?,'pending',?,?,?,?)''', (sid, tid, number, student['student_id'] if student else None,
                    label, keys[0], time.time(), keys[1], body.request_id, fingerprint))
                self.queue.enqueue(db, sid, snapshot(task, keys[0]), self.max_attempts)
        except Exception:
            if keys:
                self.files.discard(*keys)
            raise
        return {'id': sid, 'label': label}

    def review(self, uid, sid, body):
        row = self.get(uid, sid)
        task = self.tasks.get(uid, row['task_id'])
        grader = self.registry.resolve(task['task_type'], task['grader_version'])
        body.result.transcription = json.loads(row['ai_result'] or '{}').get('transcription', '')
        body.result.essay_title = json.loads(row['ai_result'] or '{}').get('essay_title', '')
        body.result = type(body.result).model_validate(body.result.model_dump())
        result = dumps(grader.validate_review(json.loads(task['grading_config']), body.result).model_dump(mode='json'))
        with self.store.connect() as db:
            cursor = db.execute('''UPDATE submissions SET final_result=?,confirmed=?,revision=revision+1
                WHERE id=? AND revision=? AND ai_result IS NOT NULL AND status NOT IN ('pending','processing')''',
                (result, int(body.confirm), sid, body.revision))
            if cursor.rowcount != 1:
                raise ServiceError(409, '答卷已更新或正在评分，请刷新后再保存')
            db.execute('INSERT INTO versions VALUES(?,?,?,?,?)', (secrets.token_hex(16), sid, 'confirmed' if body.confirm else 'draft', result, time.time()))
        return {'ok': True}

    def retry(self, uid, sid):
        row = self.get(uid, sid)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            task = db.execute('SELECT * FROM tasks WHERE id=? AND owner=? AND deleted_at IS NULL', (row['task_id'], uid)).fetchone()
            if not task:
                raise ServiceError(404, '任务不存在')
            latest = db.execute('SELECT status,image,confirmed FROM submissions WHERE id=?', (sid,)).fetchone()
            if latest['confirmed']:
                raise ServiceError(409, '已确认的成绩不能进行 AI 重审')
            if latest['status'] in ('pending','processing'):
                raise ServiceError(409, '答卷已在处理队列中')
            self.queue.enqueue(db, sid, snapshot(task, latest['image']), self.max_attempts)
            db.execute("UPDATE submissions SET status='pending',error=NULL,revision=revision+1 WHERE id=?", (sid,))
        return {'ok': True}

    def correct_identity(self, uid, sid, student_id):
        row = self.get(uid, sid)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            task = db.execute('SELECT * FROM tasks WHERE id=? AND owner=?', (row['task_id'], uid)).fetchone()
            student = next((s for s in json.loads(task['students']) if s['student_id']==student_id), None)
            if not student:
                raise ServiceError(422, '请选择本班学生')
            if db.execute('SELECT id FROM submissions WHERE task_id=? AND student_id=? AND id!=?', (task['id'], student_id, sid)).fetchone():
                raise ServiceError(409, '该学生已有答卷')
            db.execute('UPDATE submissions SET student_id=?,label=?,revision=revision+1 WHERE id=?', (student_id, student['name'], sid))
            db.execute('INSERT INTO versions VALUES(?,?,?,?,?)', (secrets.token_hex(16), sid, 'identity', dumps({'before': row['label'], 'after': student['name'], 'student_id': student_id}), time.time()))
        return {'ok': True}

    def versions(self, uid, sid):
        self.get(uid, sid)
        return [{**r, 'result': json.loads(r['result'])} for r in self.store.all('SELECT kind,result,created FROM versions WHERE submission_id=? ORDER BY created DESC', (sid,))]

    def jobs(self, uid, sid):
        self.get(uid, sid)
        return self.store.all('''SELECT id,status,attempts,max_attempts,error,created,updated,finished,result_version_id
            FROM grading_jobs WHERE submission_id=? ORDER BY created DESC''', (sid,))
