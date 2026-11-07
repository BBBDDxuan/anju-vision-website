"""Single-household accounts. Legacy gateway tokens never authorize private data here."""
import hashlib
import hmac
import ipaddress
import os
import re
import secrets
import ssl
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field, SecretStr
from typing import Literal

from .errors import AppError
from .lan_setup import local_peer

COOKIE = 'anju_session'
SESSION_TTL = 7 * 24 * 3600
INVITE_TTL = 10 * 60
LOGIN_CODE_TTL = 2 * 60
PEM_CERT = rb"-----BEGIN CERTIFICATE-----[A-Za-z0-9+/=\s]+-----END CERTIFICATE-----"
PUBLIC = {
    ('GET', '/api/auth/status'), ('POST', '/api/auth/setup'),
    ('POST', '/api/auth/login'), ('POST', '/api/auth/invite-info'),
    ('POST', '/api/auth/register'), ('POST', '/api/auth/login-code/exchange'),
    ('GET', '/api/auth/pair-ca'),
}
AUTH_LOCK = threading.BoundedSemaphore(2)


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def password_hash(password, salt):
    if not AUTH_LOCK.acquire(timeout=2):
        raise AppError('AUTH_BUSY', '登录服务繁忙，请稍后重试。', 429)
    try:
        return hashlib.scrypt(password.encode(), salt=salt, n=2**17, r=8, p=1, maxmem=160 * 1024**2)
    finally:
        AUTH_LOCK.release()


class Credentials(BaseModel):
    username: str = Field(pattern=r'^[A-Za-z0-9_.-]{3,32}$')
    password: SecretStr = Field(min_length=1, max_length=128)
    client: Literal['web', 'app'] = 'web'


class Registration(Credentials):
    display_name: str = Field(min_length=1, max_length=40)


class Setup(Registration):
    household_name: str = Field(min_length=1, max_length=60)


class InviteCode(BaseModel):
    code: SecretStr = Field(min_length=43, max_length=43)


class Register(Registration, InviteCode):
    pass


class Accounts:
    def __init__(self, path: Path):
        self.path = path
        self.attempts = {}
        self.lock = threading.Lock()
        self.demo_http = os.getenv('ANJU_ACCOUNT_DEMO_HTTP') == '1'
        self.origin = os.getenv('ANJU_ACCOUNT_ORIGIN', '').strip().rstrip('/')
        self.pair_ca = None
        self.pair_fingerprints = {}
        if ca_path := os.getenv('ANJU_ACCOUNT_CA_PEM'):
            ca = Path(ca_path).read_bytes()
            if len(ca) > 16384 or not re.fullmatch(rb"(?:\s*" + PEM_CERT + rb")+\s*", ca):
                raise ValueError('Pairing CA must contain only public PEM certificates, up to 16 KiB.')
            certs = Path(os.environ['ANJU_ACCOUNT_TLS_CERT']).read_bytes()
            leaf = re.search(PEM_CERT, certs)
            if not leaf:
                raise ValueError('Pairing requires a valid HTTPS server certificate.')
            self.pair_ca = ca
            self.pair_fingerprints = {
                'ca_sha256': hashlib.sha256(ca).hexdigest(),
                'tls_sha256': hashlib.sha256(ssl.PEM_cert_to_DER_cert(leaf[0].decode('ascii'))).hexdigest(),
            }
        with self.db() as c:
            c.executescript("""
                CREATE TABLE IF NOT EXISTS household(
                    id INTEGER PRIMARY KEY CHECK(id=1), name TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS users(
                    id TEXT PRIMARY KEY, username TEXT COLLATE NOCASE UNIQUE NOT NULL,
                    display_name TEXT NOT NULL, salt BLOB NOT NULL, password_hash BLOB NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('admin','member')),
                    contact_role TEXT NOT NULL CHECK(contact_role IN ('none','primary','backup')),
                    disabled INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS sessions(
                    digest TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
                    expires INTEGER NOT NULL, created INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS qr_logins(
                    digest TEXT PRIMARY KEY,
                    session_digest TEXT UNIQUE NOT NULL REFERENCES sessions(digest) ON DELETE CASCADE,
                    expires INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS invitations(
                    digest TEXT PRIMARY KEY, contact_role TEXT NOT NULL, expires INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS account_audit(
                    id INTEGER PRIMARY KEY, actor TEXT NOT NULL, action TEXT NOT NULL,
                    target TEXT NOT NULL, created INTEGER NOT NULL);
                PRAGMA user_version=1;
            """)

    @contextmanager
    def db(self):
        c = sqlite3.connect(self.path, timeout=10)
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA foreign_keys=ON')
        try:
            with c:
                yield c
        finally:
            c.close()

    def transport(self, request):
        if request.url.scheme == 'https' or local_peer(request):
            return
        if self.demo_http and request.client:
            try:
                peer = ipaddress.IPv4Address(request.client.host)
                if any(peer in ipaddress.IPv4Network(net) for net in ('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16')):
                    return
            except ValueError:
                pass
        raise AppError('HTTPS_REQUIRED', '局域网账号登录需要 HTTPS；请联系电脑管理员。', 403)

    def throttle(self, request):
        peer = request.client.host if request.client else ''
        now = time.monotonic()
        with self.lock:
            self.attempts = {k: v for k, v in self.attempts.items() if now - v[0] < 60}
            start, count = self.attempts.get(peer, (now, 0))
            if count >= 12 or (peer not in self.attempts and len(self.attempts) >= 256):
                raise AppError('AUTH_RATE_LIMIT', '请求过于频繁，请一分钟后重试。', 429)
            self.attempts[peer] = (start, count + 1)

    @staticmethod
    def profile(row):
        return {k: row[k] for k in ('id', 'username', 'display_name', 'role', 'household_name')}

    def user(self, request):
        self.transport(request)
        auth = request.headers.get('authorization', '')
        token = (auth[7:] if auth[:7].lower() == 'bearer ' else '') if auth else request.cookies.get(COOKIE, '')
        if len(token) != 43:
            raise AppError('LOGIN_REQUIRED', '请先登录家庭账号。', 401)
        if not auth and request.method not in {'GET', 'HEAD', 'OPTIONS'}:
            if request.headers.get('origin') != str(request.base_url).rstrip('/'):
                raise AppError('ORIGIN_REJECTED', '请从本机同源网页操作。', 403)
        with self.db() as c:
            row = c.execute("""SELECT u.*, h.name AS household_name FROM sessions s
                JOIN users u ON u.id=s.user_id CROSS JOIN household h
                WHERE s.digest=? AND s.expires>? AND u.disabled=0""", (digest(token), time.time())).fetchone()
        if row is None:
            raise AppError('LOGIN_REQUIRED', '登录已失效，请重新登录。', 401)
        request.state.account = self.profile(row)
        request.state.session_digest = digest(token)
        return request.state.account

    def add_user(self, c, body, role, salt, hashed):
        uid = uuid4().hex
        try:
            c.execute('INSERT INTO users VALUES(?,?,?,?,?,?,?,0)',
                      (uid, body.username.lower(), body.display_name.strip(), salt, hashed, role, 'none'))
        except sqlite3.IntegrityError:
            raise AppError('USERNAME_TAKEN', '用户名已存在。', 409) from None
        return uid

    def issue(self, c, uid, client, request):
        token = secrets.token_urlsafe(32)
        now = int(time.time())
        c.execute('DELETE FROM sessions WHERE expires<=?', (now,))
        c.execute('INSERT INTO sessions VALUES(?,?,?,?)', (digest(token), uid, now+SESSION_TTL, now))
        row = c.execute('SELECT u.*, h.name AS household_name FROM users u CROSS JOIN household h WHERE u.id=?',
                        (uid,)).fetchone()
        data = {'user': self.profile(row)}
        if client == 'app':
            data['token'] = token
        response = JSONResponse(data)
        if client == 'web':
            response.set_cookie(COOKIE, token, max_age=SESSION_TTL, httponly=True,
                                secure=request.url.scheme == 'https', samesite='strict', path='/')
        return response

    @staticmethod
    def require_admin(request):
        if request.state.account['role'] != 'admin':
            raise AppError('ADMIN_REQUIRED', '仅家庭管理员可执行此操作。', 403)

    @staticmethod
    def audit(c, actor, action, target):
        c.execute('INSERT INTO account_audit(actor,action,target,created) VALUES(?,?,?,?)',
                  (actor, action, target, int(time.time())))

    def gate(self, request):
        user = self.user(request)
        method, path = request.method, request.url.path
        # The existing notification relay has shared role tokens. Do not reuse
        # those tokens in account mode until per-user delivery is implemented.
        if path.startswith('/api/v1/push-tokens'):
            raise AppError('PUSH_ACCOUNT_PENDING', '账号模式暂不支持后台推送。', 403)
        if user['role'] == 'admin' or method in {'GET', 'HEAD', 'OPTIONS'}:
            return
        import re
        if method == 'POST' and re.fullmatch(
            r'/api/(?:alerts/[^/]+/(?:ack|resolve)|videos/[^/]+/(?:query|alerts/[^/]+/acknowledge)|v1/events/[^/]+/(?:ack|resolve)|auth/(?:logout|login-code))',
            path
        ):
            return
        raise AppError('ADMIN_REQUIRED', '此操作仅向家庭管理员开放。', 403)

    def qr_origin(self):
        if not self.origin:
            raise AppError('QR_ADDRESS_MISSING',
                           '电脑尚未设置手机可用的地址。请在电脑用 start-phone.ps1 启动 HTTPS 手机网关。', 503)
        try:
            origin = urlsplit(self.origin)
            host = origin.hostname
            valid_port = origin.port != 0
        except ValueError:
            host, valid_port = None, False
        if (not host or not valid_port or origin.scheme not in {'http', 'https'} or
                origin.username is not None or origin.path or origin.query or origin.fragment):
            raise AppError('QR_ADDRESS_INVALID',
                           '手机网关地址格式有误。请在电脑用 start-phone.ps1 指定当前 Wi-Fi 的 IP 地址。', 503)
        try:
            address = ipaddress.ip_address(host)
            local = address.is_loopback or address.is_unspecified
        except ValueError:
            local = host.rstrip('.') == 'localhost'
        if local:
            raise AppError('QR_ADDRESS_LOCAL_ONLY',
                           '网关配置的是电脑本机地址，手机无法访问。请用 start-phone.ps1 指定当前 Wi-Fi 的 IP 地址。', 503)
        if origin.scheme == 'http' and not self.demo_http:
            raise AppError('HTTPS_REQUIRED',
                           '电脑的手机网关当前使用 HTTP。请用 start-phone.ps1 重新启动 HTTPS 手机网关。', 403)
        return self.origin

    def router(self):
        router = APIRouter(prefix='/api/auth')

        @router.get('/status')
        def status(request: Request):
            with self.db() as c:
                initialized = c.execute('SELECT 1 FROM household').fetchone() is not None
            return {'enabled': True, 'setup_allowed': not initialized and local_peer(request),
                    'insecure_lan_demo': self.demo_http}

        @router.post('/setup')
        def setup(body: Setup, request: Request):
            if not local_peer(request):
                raise AppError('SETUP_LOCAL_ONLY', '请在后端电脑本机网页进行首次设置。', 403)
            self.throttle(request)
            password = body.password.get_secret_value()
            if len(password) < 8 or not body.household_name.strip() or not body.display_name.strip():
                raise AppError('ACCOUNT_INPUT', '家庭名称和昵称不能为空，密码至少需要 8 个字符。', 422)
            salt = secrets.token_bytes(16)
            hashed = password_hash(password, salt)
            with self.db() as c:
                c.execute('BEGIN IMMEDIATE')
                if c.execute('SELECT 1 FROM household').fetchone():
                    raise AppError('SETUP_DONE', '家庭已建立，请直接登录。', 409)
                c.execute('INSERT INTO household VALUES(1,?)', (body.household_name.strip(),))
                uid = self.add_user(c, body, 'admin', salt, hashed)
                self.audit(c, uid, 'setup', uid)
                return self.issue(c, uid, body.client, request)

        @router.post('/login')
        def login(body: Credentials, request: Request):
            self.transport(request)
            self.throttle(request)
            with self.db() as c:
                row = c.execute('SELECT * FROM users WHERE username=?', (body.username,)).fetchone()
                hashed = password_hash(body.password.get_secret_value(), row['salt'] if row else bytes(16))
                if not row or row['disabled'] or not hmac.compare_digest(row['password_hash'], hashed):
                    raise AppError('BAD_LOGIN', '用户名或密码不正确。', 401)
                return self.issue(c, row['id'], body.client, request)

        @router.get('/me')
        def me(request: Request):
            return {'user': request.state.account}

        @router.post('/logout')
        def logout(request: Request):
            with self.db() as c:
                c.execute('DELETE FROM sessions WHERE digest=?', (request.state.session_digest,))
            response = JSONResponse({'ok': True})
            response.delete_cookie(COOKIE, path='/')
            return response

        @router.get('/family-members')
        def family_members(request: Request):
            with self.db() as c:
                rows = c.execute('SELECT id,display_name,role FROM users WHERE disabled=0 ORDER BY role,display_name,id')
                return {'members': [dict(r) for r in rows]}

        @router.get('/users')
        def users(request: Request):
            self.require_admin(request)
            with self.db() as c:
                rows = c.execute('SELECT id,username,display_name,role,disabled FROM users ORDER BY role,username')
                return {'users': [dict(r) for r in rows]}

        @router.post('/users/{uid}/revoke')
        def revoke(uid: str, request: Request):
            self.require_admin(request)
            with self.db() as c:
                changed = c.execute("UPDATE users SET disabled=1 WHERE id=? AND role='member' AND disabled=0", (uid,))
                if not changed.rowcount:
                    raise AppError('MEMBER_NOT_FOUND', '没有可停用的家庭账号。', 404)
                c.execute('DELETE FROM sessions WHERE user_id=?', (uid,))
                self.audit(c, request.state.account['id'], 'revoke', uid)
            return {'ok': True}

        @router.get('/pair-ca')
        def pairing_ca(request: Request):
            if request.url.scheme != 'https' or self.pair_ca is None:
                raise AppError('PAIR_CA_UNAVAILABLE', '电脑网关没有提供配对证书，请检查 HTTPS 启动配置。', 404)
            return Response(self.pair_ca, media_type='application/x-pem-file',
                            headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})

        @router.get('/connection-code')
        def connection_code(request: Request):
            self.require_admin(request)
            return {'kind': 'anju-server-v1', 'origin': self.qr_origin(), **self.pair_fingerprints}

        @router.post('/login-code')
        def create_login_code(request: Request):
            origin = self.qr_origin()
            if request.url.scheme != 'https' or not origin.startswith('https:'):
                raise AppError('HTTPS_REQUIRED', '请在电脑 HTTPS 网页生成扫码登录二维码。', 403)
            if request.headers.get('authorization'):
                raise AppError('QR_WEB_REQUIRED', '请在已登录的电脑网页生成扫码登录二维码。', 403)
            code = secrets.token_urlsafe(32)
            now = int(time.time())
            source = request.state.session_digest
            with self.db() as c:
                c.execute('BEGIN IMMEDIATE')
                if not c.execute('SELECT 1 FROM sessions WHERE digest=? AND expires>?', (source, now)).fetchone():
                    raise AppError('LOGIN_REQUIRED', '电脑登录已失效，请重新登录。', 401)
                c.execute('DELETE FROM qr_logins WHERE expires<=? OR session_digest=?', (now, source))
                c.execute('INSERT INTO qr_logins VALUES(?,?,?)', (digest(code), source, now + LOGIN_CODE_TTL))
                self.audit(c, request.state.account['id'], 'qr_login_create', '')
            return {'kind': 'anju-login-v1', 'origin': origin, 'code': code, **self.pair_fingerprints}

        @router.post('/login-code/exchange')
        def exchange_login_code(body: InviteCode, request: Request):
            if request.url.scheme != 'https':
                raise AppError('HTTPS_REQUIRED', '扫码登录需要 HTTPS。', 403)
            self.throttle(request)
            now = int(time.time())
            key = digest(body.code.get_secret_value())
            with self.db() as c:
                c.execute('BEGIN IMMEDIATE')
                row = c.execute('''SELECT s.user_id FROM qr_logins q
                    JOIN sessions s ON s.digest=q.session_digest JOIN users u ON u.id=s.user_id
                    WHERE q.digest=? AND q.expires>? AND s.expires>? AND u.disabled=0
                ''', (key, now, now)).fetchone()
                if not row:
                    raise AppError('QR_LOGIN_INVALID', '登录二维码已过期、已使用或已失效，请在电脑重新生成。', 403)
                c.execute('DELETE FROM qr_logins WHERE digest=?', (key,))
                self.audit(c, row['user_id'], 'qr_login_exchange', '')
                return self.issue(c, row['user_id'], 'app', request)

        @router.post('/invite')
        def invite(request: Request):
            self.require_admin(request)
            origin = self.qr_origin()
            code = secrets.token_urlsafe(32)
            now = int(time.time())
            with self.db() as c:
                c.execute('DELETE FROM invitations WHERE expires<=?', (now,))
                if c.execute('SELECT COUNT(*) FROM invitations').fetchone()[0] >= 50:
                    raise AppError('INVITE_LIMIT', '有效邀请过多，请稍后重试。', 429)
                c.execute('INSERT INTO invitations VALUES(?,?,?)', (digest(code), 'none', now+INVITE_TTL))
                self.audit(c, request.state.account['id'], 'invite', 'member')
            return {'kind': 'anju-invite-v1', 'origin': origin, 'code': code, **self.pair_fingerprints}

        def valid_invite(c, body):
            row = c.execute('SELECT * FROM invitations WHERE digest=? AND expires>?',
                            (digest(body.code.get_secret_value()), time.time())).fetchone()
            if not row:
                raise AppError('INVITE_INVALID', '邀请已过期、已使用或无效。', 403)
            return row

        @router.post('/invite-info')
        def info(body: InviteCode, request: Request):
            self.transport(request)
            self.throttle(request)
            with self.db() as c:
                valid_invite(c, body)
                return {'household_name': c.execute('SELECT name FROM household').fetchone()[0]}

        @router.post('/register')
        def register(body: Register, request: Request):
            self.transport(request)
            self.throttle(request)
            password = body.password.get_secret_value()
            if len(password) < 8 or not body.display_name.strip():
                raise AppError('ACCOUNT_INPUT', '昵称不能为空，密码至少需要 8 个字符。', 422)
            with self.db() as c:
                valid_invite(c, body)
            salt = secrets.token_bytes(16)
            hashed = password_hash(password, salt)
            with self.db() as c:
                c.execute('BEGIN IMMEDIATE')
                valid_invite(c, body)
                uid = self.add_user(c, body, 'member', salt, hashed)
                c.execute('DELETE FROM invitations WHERE digest=?', (digest(body.code.get_secret_value()),))
                self.audit(c, uid, 'register', uid)
                return self.issue(c, uid, body.client, request)

        return router
