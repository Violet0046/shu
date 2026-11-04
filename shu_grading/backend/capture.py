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
        token = secrets.token_urlsafe(32)
        expires = time.time()+3600
        self.store.execute('INSERT INTO pairs VALUES(?,?,?)', (token, tid, expires))
        return {'url': f'{public_url}/#capture={token}', 'token': token, 'expires': expires}

    def get(self, token):
        task = self.store.one('''SELECT t.* FROM pairs p JOIN tasks t ON t.id=p.task_id
            JOIN users u ON u.id=t.owner WHERE p.token=? AND p.expires>? AND u.active=1''', (token, time.time()))
        if not task:
            raise ServiceError(403, '拍照入口已过期或账号已停用，请在电脑端重新生成')
        return task

    def info(self, token):
        task = self.get(token)
        submitted = {r['student_id'] for r in self.store.all('SELECT student_id FROM submissions WHERE task_id=?', (task['id'],))}
        return {'title': task['title'], 'mode': task['mode'], 'class_name': task['class_name'],
                'students': [{**s, 'submitted': s['student_id'] in submitted} for s in json.loads(task['students'])]}

    def submit(self, token, body):
        task = self.get(token)
        return self.submissions.submit(task['owner'], task['id'], body)

    def revoke(self, uid, tid):
        self.tasks.get(uid, tid)
        self.store.execute('DELETE FROM pairs WHERE task_id=?', (tid,))
        return {'ok': True}
