import json
import secrets
import time

from .database import dumps
from .errors import ServiceError
from ..utils.word_count import count_essay_words


def public_submission(row):
    result = {k: row[k] for k in ('id','task_id','number','student_id','label','status','error','confirmed','revision','view_rotation')}
    for key in ('ai_result','final_result'):
        result[key] = json.loads(row[key]) if row[key] else None
        if result[key] is not None:
            body = result[key].get('transcription', '')
            result[key]['word_count'] = count_essay_words(body) if body.strip() else None
    return result


def snapshot(task, image):
    return {'task_type': task['task_type'], 'grader_version': task['grader_version'],
            'assignment': task['assignment'], 'configuration': json.loads(task['grading_config']), 'image': image}


class TaskService:
    def __init__(self, store, registry):
        self.store, self.registry = store, registry

    def get(self, uid, tid):
        row = self.store.one('SELECT * FROM tasks WHERE id=? AND owner=? AND deleted_at IS NULL', (tid, uid))
        if not row:
            raise ServiceError(404, '任务不存在')
        return row

    def list(self, uid):
        return self.store.all('''SELECT t.id,t.title,t.mode,t.class_name,t.created,t.task_type,
            (SELECT COUNT(*) FROM submissions s WHERE s.task_id=t.id) AS total,
            (SELECT COUNT(*) FROM submissions s WHERE s.task_id=t.id AND s.confirmed=1) AS confirmed
            FROM tasks t WHERE owner=? AND deleted_at IS NULL ORDER BY created DESC''', (uid,))

    def delete(self, uid, tid):
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            task = db.execute('SELECT id FROM tasks WHERE id=? AND owner=? AND deleted_at IS NULL', (tid,uid)).fetchone()
            if not task:
                raise ServiceError(404, '任务不存在')
            now = time.time()
            if db.execute('''SELECT j.id FROM grading_jobs j JOIN submissions s ON s.id=j.submission_id
                    WHERE s.task_id=? AND j.status='processing' AND j.lease_expires>?''',(tid,now)).fetchone():
                raise ServiceError(409, '任务中仍有答卷正在阅卷，请等待完成后再删除')
            db.execute('UPDATE tasks SET deleted_at=? WHERE id=?',(now,tid))
            db.execute('DELETE FROM pairs WHERE task_id=?',(tid,))
            db.execute('''UPDATE job_attempts SET finished=?,outcome='cancelled' WHERE finished IS NULL
                AND job_id IN (SELECT j.id FROM grading_jobs j JOIN submissions s ON s.id=j.submission_id WHERE s.task_id=?)''',(now,tid))
            db.execute('''UPDATE grading_jobs SET status='cancelled',finished=?,updated=?,lease_token=NULL,lease_expires=NULL
                WHERE submission_id IN (SELECT id FROM submissions WHERE task_id=?) AND status IN ('pending','processing')''',(now,now,tid))
            db.execute("UPDATE submissions SET status='cancelled' WHERE task_id=? AND status IN ('pending','processing')",(tid,))
        return {'ok':True}

    def create(self, uid, body):
        if body.mode not in ('named','anonymous'):
            raise ServiceError(422, '请选择班级实名或匿名阅卷')
        grader = self.registry.resolve(body.task_type)
        config = body.configuration or ({'rubric': body.rubric.model_dump()} if body.rubric else {})
        config = grader.validate_configuration(config)
        tid = secrets.token_hex(16)
        with self.store.connect() as db:
            classroom = db.execute('SELECT * FROM classes WHERE id=? AND owner=?', (body.class_id, uid)).fetchone() if body.mode=='named' else None
            if body.mode=='named' and not classroom:
                raise ServiceError(422, '实名阅卷需要选择自己的班级')
            db.execute('''INSERT INTO tasks(id,owner,title,assignment,mode,class_id,class_name,students,rubric,created,task_type,grader_version,grading_config)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''', (tid, uid, body.title, body.assignment, body.mode,
                classroom['id'] if classroom else None, classroom['name'] if classroom else '匿名阅卷',
                classroom['students'] if classroom else '[]', dumps(config.get('rubric',{})), time.time(),
                grader.task_type, grader.version, dumps(config)))
        return {'id': tid}

    def detail(self, uid, tid):
        task = self.get(uid, tid)
        return {**{k: task[k] for k in ('id','title','assignment','mode','class_id','class_name','task_type','grader_version')},
                'configuration': json.loads(task['grading_config']), 'rubric': json.loads(task['rubric']),
                'students': json.loads(task['students']), 'submissions': [public_submission(s) for s in self.store.all('SELECT * FROM submissions WHERE task_id=? ORDER BY number', (tid,))]}
