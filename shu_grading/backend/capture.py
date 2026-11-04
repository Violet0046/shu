import json
import os
import secrets
import time

from .errors import ServiceError


class CaptureService:
    def __init__(self, store, tasks, submissions):
        self.store, self.tasks, self.submissions = store, tasks, submissions

    def pair(self, uid, tid):
        self.tasks.get(uid, tid)
        public_url = os.getenv('SHU_PUBLIC_URL','').rstrip('/')
        if not public_url:
            raise ServiceError(422, '请先设置手机可访问的 SHU_PUBLIC_URL，再生成拍照二维码')
        now = time.time()
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('SELECT id FROM tasks WHERE id=? AND owner=? AND deleted_at IS NULL',(tid,uid)).fetchone():
                raise ServiceError(404, '任务不存在')
            db.execute('DELETE FROM pairs WHERE expires<=?',(now,))
            existing = db.execute('SELECT token,expires FROM pairs WHERE task_id=? ORDER BY expires DESC LIMIT 1',(tid,)).fetchone()
            token = existing['token'] if existing else secrets.token_urlsafe(32)
            expires = existing['expires'] if existing else now+3600
            if not existing:
                db.execute('INSERT INTO pairs VALUES(?,?,?)', (token, tid, expires))
        return {'url': f'{public_url}/#capture={token}', 'token': token, 'expires': expires}

    def get(self, token):
        task = self.store.one('''SELECT t.* FROM pairs p JOIN tasks t ON t.id=p.task_id
            JOIN users u ON u.id=t.owner WHERE p.token=? AND p.expires>? AND u.active=1 AND t.deleted_at IS NULL''', (token, time.time()))
        if not task:
            raise ServiceError(403, '拍照入口已关闭、过期或账号已停用，请在电脑端重新开启')
        return task

    def info(self, token):
        task = self.get(token)
        submitted = {r['student_id'] for r in self.store.all('SELECT student_id FROM submissions WHERE task_id=?', (task['id'],))}
        return {'title': task['title'], 'mode': task['mode'], 'class_name': task['class_name'],
                'students': [{**s, 'submitted': s['student_id'] in submitted} for s in json.loads(task['students'])]}

    def submit(self, token, body):
        task = self.get(token)
        return self.submissions.submit(task['owner'], task['id'], body, capture_token=token)

    def revoke(self, uid, tid):
        self.tasks.get(uid, tid)
        self.store.execute('DELETE FROM pairs WHERE task_id=?', (tid,))
        return {'ok': True}
