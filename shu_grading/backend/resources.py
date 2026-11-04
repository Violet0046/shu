import json
import secrets
import time
from pathlib import Path

from ..schemas import Rubric
from .database import dumps
from .errors import ServiceError


class ResourceService:
    def __init__(self, store):
        self.store = store

    def ensure_templates(self, uid):
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT owner FROM preferences WHERE owner=?', (uid,)).fetchone():
                return
            existing = db.execute('SELECT id FROM templates WHERE owner=? ORDER BY rowid LIMIT 1', (uid,)).fetchone()
            tid = existing['id'] if existing else secrets.token_hex(16)
            if not existing:
                rubric = Rubric.model_validate_json((Path(__file__).parent/'default_criteria.json').read_text(encoding='utf8'))
                db.execute('INSERT INTO templates VALUES(?,?,?,?)', (tid, uid, '高中记叙文·50分制', dumps(rubric.model_dump())))
            db.execute('INSERT INTO preferences VALUES(?,?)', (uid, tid))

    def classes(self, uid):
        return [{**r, 'students': json.loads(r['students'])} for r in self.store.all('SELECT id,name,students FROM classes WHERE owner=? ORDER BY name', (uid,))]

    def save_class(self, uid, body, cid=None):
        students = [s.model_dump() for s in body.students]
        if cid is None:
            cid = secrets.token_hex(16)
            self.store.execute('INSERT INTO classes VALUES(?,?,?,?)', (cid, uid, body.name, dumps(students)))
            return {'id': cid}
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('SELECT id FROM classes WHERE id=? AND owner=?', (cid, uid)).fetchone():
                raise ServiceError(404, '班级不存在')
            db.execute('UPDATE classes SET name=?,students=? WHERE id=? AND owner=?', (body.name, dumps(students), cid, uid))
            for task in db.execute('SELECT * FROM tasks WHERE class_id=? AND owner=?', (cid, uid)).fetchall():
                ids = {s['student_id'] for s in students}
                submitted = {r['student_id'] for r in db.execute('SELECT student_id FROM submissions WHERE task_id=?', (task['id'],))}
                retained = [s for s in json.loads(task['students']) if s['student_id'] in submitted and s['student_id'] not in ids]
                db.execute('UPDATE tasks SET students=?,class_name=? WHERE id=?', (dumps(students+retained), body.name, task['id']))
                for student in students:
                    for row in db.execute('SELECT id,label FROM submissions WHERE task_id=? AND student_id=? AND label!=?', (task['id'], student['student_id'], student['name'])).fetchall():
                        db.execute('UPDATE submissions SET label=?,revision=revision+1 WHERE id=?', (student['name'], row['id']))
                        db.execute('INSERT INTO versions VALUES(?,?,?,?,?)', (secrets.token_hex(16), row['id'], 'identity', dumps({'before': row['label'], 'after': student['name']}), time.time()))
        return {'id': cid}

    def templates(self, uid):
        self.ensure_templates(uid)
        default = self.store.one('SELECT default_template_id FROM preferences WHERE owner=?', (uid,))['default_template_id']
        return [{**r, 'rubric': json.loads(r['rubric']), 'is_default': r['id']==default}
                for r in self.store.all('SELECT id,name,rubric FROM templates WHERE owner=? ORDER BY name', (uid,))]

    def save_template(self, uid, body, tid=None):
        self.ensure_templates(uid)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if tid:
                if not db.execute('SELECT id FROM templates WHERE id=? AND owner=?', (tid, uid)).fetchone():
                    raise ServiceError(404, '模板不存在')
                db.execute('UPDATE templates SET name=?,rubric=? WHERE id=? AND owner=?', (body.name, dumps(body.rubric.model_dump()), tid, uid))
            else:
                tid = secrets.token_hex(16)
                db.execute('INSERT INTO templates VALUES(?,?,?,?)', (tid, uid, body.name, dumps(body.rubric.model_dump())))
                db.execute('UPDATE preferences SET default_template_id=? WHERE owner=? AND default_template_id IS NULL', (tid, uid))
        return {'id': tid}

    def set_default(self, uid, tid):
        self.ensure_templates(uid)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('SELECT id FROM templates WHERE id=? AND owner=?', (tid, uid)).fetchone():
                raise ServiceError(404, '模板不存在')
            db.execute('UPDATE preferences SET default_template_id=? WHERE owner=?', (tid, uid))
        return {'ok': True}

    def delete_template(self, uid, tid):
        self.ensure_templates(uid)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('SELECT id FROM templates WHERE id=? AND owner=?', (tid, uid)).fetchone():
                raise ServiceError(404, '模板不存在')
            db.execute('DELETE FROM templates WHERE id=? AND owner=?', (tid, uid))
            fallback = db.execute('SELECT id FROM templates WHERE owner=? ORDER BY rowid LIMIT 1', (uid,)).fetchone()
            db.execute('UPDATE preferences SET default_template_id=? WHERE owner=? AND default_template_id=?', (fallback['id'] if fallback else None, uid, tid))
        return {'ok': True}
