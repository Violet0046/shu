"""账号由管理员建立；登录不创建账号。"""
import hashlib
import os
import secrets
import sqlite3
import time

from .database import password_hash, password_matches
from .errors import ServiceError


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class AuthService:
    def __init__(self, store):
        self.store = store
        self.single_session = os.getenv('SHU_SINGLE_SESSION') == '1'

    def create_account(self, username, password, recipient="", actor=None):
        username = username.strip()
        if not 2 <= len(username) <= 80 or not 8 <= len(password) <= 128:
            raise ServiceError(422, "账号需为 2～80 个字符，密码需为 8～128 个字符")
        uid = secrets.token_hex(16)
        try:
            with self.store.connect() as db:
                db.execute('INSERT INTO users(id,username,password,active,recipient) VALUES(?,?,?,1,?)',
                           (uid, username, password_hash(password), recipient))
                db.execute('INSERT INTO account_audit VALUES(?,?,?,?)', (secrets.token_hex(16), uid, 'created', time.time()))
                if actor:
                    db.execute('INSERT INTO admin_events VALUES(?,?,?,?,?)', (secrets.token_hex(16),actor,uid,'created',time.time()))
        except sqlite3.IntegrityError:
            raise ServiceError(409, "账号已存在")
        return uid

    def _account(self, username):
        row = self.store.one('SELECT * FROM users WHERE username=?', (username,))
        if not row:
            raise ServiceError(404, '账号不存在')
        return row

    def manage_account(self, username, active=None, password=None, actor=None):
        account = self._account(username)
        if password is not None and not 8 <= len(password) <= 128:
            raise ServiceError(422, '密码需为 8～128 个字符')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            latest = db.execute('SELECT * FROM users WHERE id=?', (account['id'],)).fetchone()
            if active is False and latest['is_admin']:
                if actor == account['id']:
                    raise ServiceError(409, '不能停用当前管理员账户')
                if db.execute('SELECT COUNT(*) FROM users WHERE active=1 AND is_admin=1').fetchone()[0] <= 1:
                    raise ServiceError(409, '至少保留一个启用的管理员账户')
            if active is not None:
                db.execute('UPDATE users SET active=? WHERE id=?', (int(active), account['id']))
            if password is not None:
                db.execute('UPDATE users SET password=? WHERE id=?', (password_hash(password), account['id']))
            db.execute('DELETE FROM sessions WHERE user_id=?', (account['id'],))
            db.execute('DELETE FROM pairs WHERE task_id IN (SELECT id FROM tasks WHERE owner=?)', (account['id'],))
            db.execute('INSERT INTO account_audit VALUES(?,?,?,?)', (secrets.token_hex(16), account['id'], 'password_reset' if password is not None else 'enabled' if active else 'disabled', time.time()))
            if actor:
                db.execute('INSERT INTO admin_events VALUES(?,?,?,?,?)', (secrets.token_hex(16),actor,account['id'],
                    'password_reset' if password is not None else 'enabled' if active else 'disabled',time.time()))

    def set_admin(self, username, enabled):
        account = self._account(username)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if not enabled and account['active'] and account['is_admin'] and db.execute(
                    'SELECT COUNT(*) FROM users WHERE active=1 AND is_admin=1').fetchone()[0] <= 1:
                raise ServiceError(409, '至少保留一个启用的管理员账户')
            db.execute('UPDATE users SET is_admin=? WHERE id=?', (int(enabled),account['id']))
            db.execute('DELETE FROM sessions WHERE user_id=?', (account['id'],))
            db.execute('INSERT INTO admin_events VALUES(?,?,?,?,?)', (secrets.token_hex(16),'server',account['id'],
                'admin_granted' if enabled else 'admin_revoked',time.time()))

    def login(self, username, password, address="local"):
        username = username.strip()
        key = digest(username + '|' + address)
        now = time.time()
        limit = self.store.one('SELECT * FROM login_limits WHERE key=?', (key,))
        if limit and limit['blocked_until'] > now:
            raise ServiceError(429, '登录尝试过多，请稍后再试')
        account = self.store.one('SELECT * FROM users WHERE username=?', (username,))
        encoded = account['password'] if account else password_hash('invalid-password', '0' * 32)
        if not password_matches(password, encoded) or not account or not account['active']:
            with self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                previous = db.execute('SELECT * FROM login_limits WHERE key=?', (key,)).fetchone()
                count = previous['failures'] + 1 if previous and previous['window_end'] > now else 1
                db.execute('INSERT OR REPLACE INTO login_limits VALUES(?,?,?,?)', (key, count, now+600, now+900 if count >= 5 else 0))
            raise ServiceError(401, '账号或密码不正确，或账号已停用')
        token = secrets.token_urlsafe(32)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            # 防止管理员重置密码或停用期间，旧登录请求重新建立会话。
            latest = db.execute('SELECT active,password FROM users WHERE id=?', (account['id'],)).fetchone()
            if not latest['active'] or latest['password'] != encoded:
                raise ServiceError(401, '账号状态已更新，请重新登录')
            db.execute('DELETE FROM login_limits WHERE key=?', (key,))
            db.execute('DELETE FROM sessions WHERE expires<?', (now,))
            if self.single_session:
                db.execute('DELETE FROM sessions WHERE user_id=?', (account['id'],))
            db.execute('INSERT INTO sessions VALUES(?,?,?)', (digest(token), account['id'], now+7*86400))
        return {'username': account['username'], 'token': token}

    def authenticate(self, token):
        row = self.store.one('''SELECT u.id FROM sessions s JOIN users u ON u.id=s.user_id
            WHERE s.token=? AND s.expires>? AND u.active=1''', (digest(token), time.time()))
        if not row:
            raise ServiceError(401, '请使用管理员分配的账号登录')
        return row['id']

    def logout(self, token):
        self.store.execute('DELETE FROM sessions WHERE token=?', (digest(token),))
