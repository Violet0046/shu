"""持久化账户、任务快照、单份答卷及独立评分版本。"""
import hashlib
import hmac
import json
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path


def password_hash(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
    return f"{salt}:{digest}"


def password_matches(password: str, encoded: str) -> bool:
    salt, _ = encoded.split(":", 1)
    return hmac.compare_digest(password_hash(password, salt), encoded)


class Store:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        (root / "uploads").mkdir(exist_ok=True)
        self.path = root / "web.sqlite3"
        with self.connect() as db:
            db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY, username TEXT UNIQUE, password TEXT);
            CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, user_id TEXT, expires REAL);
            CREATE TABLE IF NOT EXISTS classes(id TEXT PRIMARY KEY, owner TEXT, name TEXT, students TEXT);
            CREATE TABLE IF NOT EXISTS templates(id TEXT PRIMARY KEY, owner TEXT, name TEXT, rubric TEXT);
            CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY, owner TEXT, title TEXT, assignment TEXT,
                mode TEXT, class_id TEXT, class_name TEXT, students TEXT, rubric TEXT, created REAL);
            CREATE TABLE IF NOT EXISTS submissions(id TEXT PRIMARY KEY, task_id TEXT, number INTEGER,
                student_id TEXT, label TEXT, image TEXT, status TEXT, error TEXT,
                ai_result TEXT, final_result TEXT, confirmed INTEGER DEFAULT 0,
                revision INTEGER DEFAULT 0, created REAL, UNIQUE(task_id, number));
            CREATE TABLE IF NOT EXISTS versions(id TEXT PRIMARY KEY, submission_id TEXT, kind TEXT,
                result TEXT, created REAL);
            CREATE TABLE IF NOT EXISTS pairs(token TEXT PRIMARY KEY, task_id TEXT, expires REAL);
            CREATE INDEX IF NOT EXISTS queue_status ON submissions(status, created);
            CREATE INDEX IF NOT EXISTS tasks_owner ON tasks(owner);
            CREATE INDEX IF NOT EXISTS submissions_task ON submissions(task_id);
            CREATE TABLE IF NOT EXISTS preferences(owner TEXT PRIMARY KEY, default_template_id TEXT);
            """)
            # 增量迁移：保留现有账号、任务、评分和原图。
            columns = {r['name'] for r in db.execute('PRAGMA table_info(tasks)')}
            if 'input_closed' not in columns:
                db.execute('ALTER TABLE tasks ADD COLUMN input_closed INTEGER NOT NULL DEFAULT 0')
            submission_columns = {r['name'] for r in db.execute('PRAGMA table_info(submissions)')}
            if 'original_image' not in submission_columns:
                db.execute('ALTER TABLE submissions ADD COLUMN original_image TEXT')
            db.executescript("""
            CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY, applied REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS grading_jobs(
                id TEXT PRIMARY KEY, submission_id TEXT NOT NULL REFERENCES submissions(id),
                snapshot TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
                max_attempts INTEGER NOT NULL DEFAULT 3, available_at REAL NOT NULL,
                lease_token TEXT, lease_expires REAL, worker_id TEXT,
                error TEXT, created REAL NOT NULL, updated REAL NOT NULL, finished REAL,
                result_version_id TEXT REFERENCES versions(id));
            CREATE UNIQUE INDEX IF NOT EXISTS one_active_grading_job ON grading_jobs(submission_id)
                WHERE status IN ('pending','processing');
            CREATE INDEX IF NOT EXISTS grading_jobs_schedule ON grading_jobs(status,available_at,lease_expires);
            CREATE TABLE IF NOT EXISTS job_attempts(
                id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES grading_jobs(id),
                attempt INTEGER NOT NULL, worker_id TEXT, started REAL, finished REAL, outcome TEXT, error TEXT);
            CREATE TABLE IF NOT EXISTS workers(id TEXT PRIMARY KEY, heartbeat REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS login_limits(key TEXT PRIMARY KEY, failures INTEGER, window_end REAL, blocked_until REAL);
            CREATE TABLE IF NOT EXISTS account_audit(id TEXT PRIMARY KEY,user_id TEXT,action TEXT,created REAL);
            """)
            db.execute('BEGIN IMMEDIATE')
            if 'is_admin' not in {r['name'] for r in db.execute('PRAGMA table_info(users)')}:
                db.execute('ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0')
            db.execute('''CREATE TABLE IF NOT EXISTS admin_events(
                id TEXT PRIMARY KEY,actor TEXT,target TEXT,action TEXT,created REAL NOT NULL)''')
            db.execute('''CREATE TABLE IF NOT EXISTS model_calls(
                id TEXT PRIMARY KEY,job_id TEXT NOT NULL,attempt_id TEXT NOT NULL,purpose TEXT,
                provider TEXT,model TEXT,actual_model TEXT,started REAL NOT NULL,finished REAL,
                status TEXT NOT NULL,request_id TEXT,error_type TEXT,error TEXT,
                input_tokens INTEGER,output_tokens INTEGER,cached_tokens INTEGER,cache_write_tokens INTEGER,
                reasoning_tokens INTEGER,usage TEXT,parameters TEXT,messages TEXT,response TEXT)''')
            db.execute('CREATE INDEX IF NOT EXISTS model_calls_job ON model_calls(job_id,started)')
            db.execute('CREATE INDEX IF NOT EXISTS model_calls_started ON model_calls(started)')
            if 'result_version_id' not in {r['name'] for r in db.execute('PRAGMA table_info(grading_jobs)')}:
                db.execute('ALTER TABLE grading_jobs ADD COLUMN result_version_id TEXT REFERENCES versions(id)')
            user_columns = {r['name'] for r in db.execute('PRAGMA table_info(users)')}
            if 'active' not in user_columns:
                db.execute('ALTER TABLE users ADD COLUMN active INTEGER NOT NULL DEFAULT 1')
                # 新版本只保存会话令牌摘要，旧会话需重新登录一次。
                db.execute('DELETE FROM sessions')
            if 'recipient' not in user_columns:
                db.execute("ALTER TABLE users ADD COLUMN recipient TEXT NOT NULL DEFAULT ''")
            for name, declaration in [('task_type', "TEXT NOT NULL DEFAULT 'essay'"),
                                      ('grader_version', "TEXT NOT NULL DEFAULT '1'"),
                                      ('grading_config', 'TEXT')]:
                if name not in {r['name'] for r in db.execute('PRAGMA table_info(tasks)')}:
                    db.execute(f'ALTER TABLE tasks ADD COLUMN {name} {declaration}')
            for name in ('client_request_id', 'request_hash'):
                if name not in {r['name'] for r in db.execute('PRAGMA table_info(submissions)')}:
                    db.execute(f'ALTER TABLE submissions ADD COLUMN {name} TEXT')
            db.execute('CREATE UNIQUE INDEX IF NOT EXISTS submission_request ON submissions(task_id,client_request_id) WHERE client_request_id IS NOT NULL')
            for row in db.execute('SELECT id,rubric FROM tasks WHERE grading_config IS NULL').fetchall():
                db.execute('UPDATE tasks SET grading_config=? WHERE id=?', (dumps({'rubric': json.loads(row['rubric'])}), row['id']))
            # 只迁移没有队列记录的旧答卷；不重置其他进程持有的有效租约。
            for row in db.execute("""SELECT s.id,s.image,t.assignment,t.task_type,t.grader_version,t.grading_config
                FROM submissions s JOIN tasks t ON t.id=s.task_id WHERE s.status IN ('pending','processing')
                AND NOT EXISTS(SELECT 1 FROM grading_jobs j WHERE j.submission_id=s.id)""").fetchall():
                now = time.time()
                snapshot = {'assignment': row['assignment'], 'task_type': row['task_type'],
                            'grader_version': row['grader_version'], 'configuration': json.loads(row['grading_config']), 'image': row['image']}
                db.execute('INSERT INTO grading_jobs(id,submission_id,snapshot,available_at,created,updated) VALUES(?,?,?,?,?,?)',
                           (secrets.token_hex(16), row['id'], dumps(snapshot), now, now, now))
                db.execute("UPDATE submissions SET status='pending' WHERE id=?", (row['id'],))
            db.execute('INSERT OR IGNORE INTO schema_migrations VALUES(2,?)', (time.time(),))
            db.execute('INSERT OR IGNORE INTO schema_migrations VALUES(3,?)', (time.time(),))

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA busy_timeout=30000')
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def one(self, sql, args=()):
        with self.connect() as db:
            row = db.execute(sql, args).fetchone()
            return dict(row) if row else None

    def all(self, sql, args=()):
        with self.connect() as db:
            return [dict(row) for row in db.execute(sql, args).fetchall()]

    def execute(self, sql, args=()):
        with self.connect() as db:
            db.execute(sql, args)


def dumps(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False)
