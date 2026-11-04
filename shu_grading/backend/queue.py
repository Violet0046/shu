"""数据库持久队列：原子领取、租约续期、失效恢复和带令牌的结果提交。"""
import json
import secrets
import sqlite3
import time

from .database import dumps
from .errors import ServiceError


class JobQueue:
    def __init__(self, store, lease_seconds=120, clock=time.time):
        self.store = store
        self.lease_seconds = lease_seconds
        self.clock = clock

    def enqueue(self, db, submission_id, snapshot, max_attempts=3):
        now = self.clock()
        jid = secrets.token_hex(16)
        try:
            db.execute('''INSERT INTO grading_jobs(id,submission_id,snapshot,max_attempts,available_at,created,updated)
                VALUES(?,?,?,?,?,?,?)''', (jid, submission_id, dumps(snapshot), max_attempts, now, now, now))
        except sqlite3.IntegrityError:
            raise ServiceError(409, '答卷已在处理队列中')
        return jid

    def claim(self, worker_id):
        now = self.clock()
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            # 先终结已耗尽尝试次数且租约失效的任务。
            expired = db.execute('''SELECT * FROM grading_jobs WHERE attempts>=max_attempts
                AND (status='pending' OR (status='processing' AND lease_expires<=?))''', (now,)).fetchall()
            for job in expired:
                message = '处理进程中断且重试次数已用尽，请手动重试'
                db.execute("UPDATE grading_jobs SET status='failed',error=?,updated=?,finished=?,lease_token=NULL WHERE id=?", (message, now, now, job['id']))
                db.execute("UPDATE submissions SET status='failed',error=? WHERE id=?", (message, job['submission_id']))
                db.execute("UPDATE job_attempts SET finished=?,outcome='lease_expired' WHERE job_id=? AND finished IS NULL", (now, job['id']))
            job = db.execute('''SELECT * FROM grading_jobs WHERE attempts<max_attempts AND
                ((status='pending' AND available_at<=?) OR (status='processing' AND lease_expires<=?))
                ORDER BY available_at,created,id LIMIT 1''', (now, now)).fetchone()
            if not job:
                return None
            if job['status'] == 'processing':
                db.execute("UPDATE job_attempts SET finished=?,outcome='lease_expired' WHERE job_id=? AND finished IS NULL", (now, job['id']))
            token = secrets.token_hex(24)
            db.execute('''UPDATE grading_jobs SET status='processing',attempts=attempts+1,worker_id=?,
                lease_token=?,lease_expires=?,updated=?,error=NULL WHERE id=?''', (worker_id, token, now+self.lease_seconds, now, job['id']))
            db.execute("UPDATE submissions SET status='processing',error=NULL WHERE id=?", (job['submission_id'],))
            db.execute('INSERT INTO job_attempts(id,job_id,attempt,worker_id,started) VALUES(?,?,?,?,?)',
                       (token, job['id'], job['attempts']+1, worker_id, now))
            claimed = dict(db.execute('SELECT * FROM grading_jobs WHERE id=?', (job['id'],)).fetchone())
            claimed['snapshot'] = json.loads(claimed['snapshot'])
            return claimed

    def renew(self, job):
        now = self.clock()
        with self.store.connect() as db:
            result = db.execute('''UPDATE grading_jobs SET lease_expires=?,updated=?
                WHERE id=? AND status='processing' AND lease_token=? AND lease_expires>?''',
                (now+self.lease_seconds, now, job['id'], job['lease_token'], now))
            return result.rowcount == 1

    def _owns(self, db, job):
        return db.execute("SELECT id FROM grading_jobs WHERE id=? AND status='processing' AND lease_token=? AND lease_expires>?",
                          (job['id'], job['lease_token'], self.clock())).fetchone() is not None

    def complete(self, job, result):
        now = self.clock()
        encoded = dumps(result.model_dump(mode='json'))
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if not self._owns(db, job):
                return False
            version_id = secrets.token_hex(16)
            db.execute('INSERT INTO versions VALUES(?,?,?,?,?)', (version_id, job['submission_id'], 'ai', encoded, now))
            db.execute("UPDATE submissions SET status='ready',ai_result=?,error=NULL,revision=revision+1 WHERE id=?", (encoded, job['submission_id']))
            db.execute("UPDATE grading_jobs SET status='succeeded',updated=?,finished=?,result_version_id=?,lease_token=NULL,lease_expires=NULL WHERE id=?", (now, now, version_id, job['id']))
            db.execute("UPDATE job_attempts SET finished=?,outcome='succeeded' WHERE id=?", (now, job['lease_token']))
            return True

    def fail(self, job, message, retryable=False):
        now = self.clock()
        retry = retryable and job['attempts'] < job['max_attempts']
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if not self._owns(db, job):
                return False
            db.execute('''UPDATE grading_jobs SET status=?,available_at=?,updated=?,finished=?,error=?,
                lease_token=NULL,lease_expires=NULL WHERE id=?''',
                ('pending' if retry else 'failed', now+min(30,2**job['attempts']), now, None if retry else now, message, job['id']))
            db.execute('UPDATE submissions SET status=?,error=? WHERE id=?', ('pending' if retry else 'failed', None if retry else message, job['submission_id']))
            db.execute('UPDATE job_attempts SET finished=?,outcome=?,error=? WHERE id=?',
                       (now, 'retry_scheduled' if retry else 'failed', message, job['lease_token']))
            return True

    def abandon(self, job):
        now = self.clock()
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if not self._owns(db, job):
                return
            db.execute("UPDATE grading_jobs SET status='pending',available_at=?,updated=?,lease_token=NULL,lease_expires=NULL WHERE id=?", (now, now, job['id']))
            db.execute("UPDATE submissions SET status='pending' WHERE id=?", (job['submission_id'],))
            db.execute("UPDATE job_attempts SET finished=?,outcome='interrupted' WHERE id=?", (now, job['lease_token']))

    def heartbeat(self, worker_id):
        self.store.execute('INSERT OR REPLACE INTO workers VALUES(?,?)', (worker_id, self.clock()))
