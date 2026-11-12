"""Revocable delegated client sessions, approved by an authenticated Plaza user.

Only hashes of device, access and refresh secrets are persisted. A device grant
is short lived, proof-key bound, and consumed atomically. No upstream login
session or provider refresh token is copied to the client.

A grant may name where its approval returns (RFC 8252: a loopback address on the
client's computer, or a configured client origin). Approving it then sends a
one-time ticket there, and only device code, proof and ticket together become a
session. Whoever starts a grant and sends its link to someone else never receives
the ticket, so a phishing link signs nobody in.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import HTTPException, Request
from starlette.concurrency import run_in_threadpool

ACCESS_PREFIX = 'pc-access-'
REFRESH_PREFIX = 'pc-refresh-'
DEFAULT_SESSION_LIFETIME = 86400
MAX_SESSION_LIFETIME = 7 * 86400
LASTING = 253402300799.0  # 9999-12-31: a lasting session ends only when it is revoked


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def challenge(verifier):
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')


class ClientSessions:
    def __init__(self, path, *, clock=time.time, access_prefix=ACCESS_PREFIX, refresh_prefix=REFRESH_PREFIX, lasting=False):
        """`lasting` sessions never expire: they end only when revoked. Their access tokens still rotate hourly."""
        self.path = Path(path)
        self.clock = clock
        self.access_prefix, self.refresh_prefix = access_prefix, refresh_prefix
        self.lasting = lasting
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS device_grants (
                    device TEXT PRIMARY KEY, code TEXT UNIQUE, label TEXT, challenge TEXT,
                    expires REAL, polled REAL DEFAULT 0, owner TEXT, decision TEXT);
                CREATE TABLE IF NOT EXISTS client_sessions (
                    id TEXT PRIMARY KEY, owner TEXT, label TEXT, access TEXT UNIQUE,
                    refresh TEXT UNIQUE, access_expires REAL, expires REAL, created REAL);
            ''')
            db.execute('BEGIN IMMEDIATE')
            columns = {row['name'] for row in db.execute('PRAGMA table_info(device_grants)')}
            if 'lifetime' not in columns:
                db.execute('ALTER TABLE device_grants ADD COLUMN lifetime INTEGER NOT NULL DEFAULT 86400')
            if 'return_to' not in columns:
                db.execute('ALTER TABLE device_grants ADD COLUMN return_to TEXT')
                db.execute('ALTER TABLE device_grants ADD COLUMN ticket TEXT')
            if lasting:
                # Sessions issued before this store became lasting keep going too: only a revoke
                # (which deletes the row) ends one, however long ago its old end time passed.
                db.execute('UPDATE client_sessions SET expires=? WHERE expires<?', (LASTING, LASTING))
        os.chmod(self.path, 0o600)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def begin(self, label, proof, session_lifetime_seconds=DEFAULT_SESSION_LIFETIME, return_to=None):
        if not isinstance(label, str) or not 1 <= len(label.strip()) <= 80 or any(ord(c) < 32 for c in label):
            raise HTTPException(422, 'Provide a short client name.')
        if not isinstance(proof, str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}', proof):
            raise HTTPException(422, 'An S256 proof challenge is required.')
        if type(session_lifetime_seconds) is not int or not 1 <= session_lifetime_seconds <= MAX_SESSION_LIFETIME:
            raise HTTPException(422, 'Client session lifetime must be between one second and seven days.')
        device, code = secrets.token_urlsafe(32), secrets.token_hex(5).upper()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM device_grants WHERE expires < ?', (self.clock(),))
            if db.execute('SELECT count(*) FROM device_grants').fetchone()[0] >= 1000:
                raise HTTPException(429, 'Too many pending client connections.')
            db.execute('INSERT INTO device_grants(device,code,label,challenge,expires,lifetime,return_to) VALUES(?,?,?,?,?,?,?)',
                       (digest(device), code, label.strip(), proof, self.clock() + 300,session_lifetime_seconds,return_to))
        return {'device_code': device, 'user_code': code, 'expires_in': 300, 'interval': 5}

    def pending(self, code):
        if not isinstance(code, str) or not re.fullmatch(r'[A-F0-9]{10}', code):
            raise HTTPException(404, 'Connection request expired or unavailable.')
        with self.connect() as db:
            row = db.execute('SELECT label,expires,decision,lifetime FROM device_grants WHERE code=?', (code,)).fetchone()
        if not row or row['expires'] <= self.clock() or row['decision']:
            raise HTTPException(404, 'Connection request expired or unavailable.')
        return {'client_name': row['label'], 'expires_at': row['expires'], 'user_code': code,
                'scope': 'User agent access; no administrator access', 'session_lifetime_seconds': row['lifetime']}

    def bound(self, code):
        """Whether this pending grant returns its approval to the client that started it."""
        with self.connect() as db:
            row = db.execute('SELECT return_to FROM device_grants WHERE code=?', (code,)).fetchone()
        return bool(row and row['return_to'])

    def approve(self, code, owner, approved):
        self.pending(code)
        if type(approved) is not bool:
            raise HTTPException(422, 'Choose allow or deny.')
        ticket = secrets.token_urlsafe(32) if approved else None
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT return_to FROM device_grants WHERE code=?', (code,)).fetchone()
            target = row['return_to'] if row else None
            changed = db.execute('UPDATE device_grants SET owner=?,decision=?,ticket=? WHERE code=? AND decision IS NULL AND expires>?',
                                 (owner, 'approved' if approved else 'denied', digest(ticket) if ticket and target else None,
                                  code, self.clock())).rowcount
            if changed != 1:
                raise HTTPException(409, 'This connection request was already handled.')
        result = {'status': 'approved' if approved else 'denied'}
        if ticket and target:
            # The approving browser carries the ticket to the client's own address; nobody else sees it.
            parts = urlsplit(target)
            result['redirect'] = urlunsplit(parts._replace(query=urlencode([*parse_qsl(parts.query), ('ticket', ticket)])))
        return result

    def pending_proof(self, device, verifier):
        """Bind provider login to a pending grant, without publishing its proof."""
        if not isinstance(device, str) or len(device) > 128 or not isinstance(verifier, str) or not re.fullmatch(r'[A-Za-z0-9._~-]{43,128}', verifier):
            raise HTTPException(400, 'Invalid device proof.')
        with self.connect() as db:
            row = db.execute('SELECT * FROM device_grants WHERE device=?', (digest(device),)).fetchone()
        if not row or row['expires'] <= self.clock() or row['decision'] or not hmac.compare_digest(row['challenge'], challenge(verifier)):
            raise HTTPException(400, 'Connection request expired or proof invalid.')
        return row['code']

    def _tokens(self, db, session_id, owner, label, expires, created):
        if self.lasting:
            expires = LASTING
        access, refresh = self.access_prefix + secrets.token_urlsafe(32), self.refresh_prefix + secrets.token_urlsafe(32)
        lifetime = max(1, min(3600, int(expires - self.clock())))
        db.execute('INSERT OR REPLACE INTO client_sessions VALUES(?,?,?,?,?,?,?,?)',
                   (session_id, owner, label, digest(access), digest(refresh), self.clock() + lifetime, expires, created))
        return {'access_token': access, 'refresh_token': refresh, 'token_type': 'Bearer',
                'expires_in': lifetime, 'expires_at': int(self.clock() + lifetime),
                'plaza_expires_at': int(expires), 'plaza_expires_in': int(expires - self.clock()),
                'client_session_id': session_id}

    def exchange(self, device, verifier, ticket=None):
        if not isinstance(device, str) or len(device) > 128 or not isinstance(verifier, str) or not re.fullmatch(r'[A-Za-z0-9._~-]{43,128}', verifier):
            raise HTTPException(400, 'Invalid device proof.')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM device_grants WHERE device=?', (digest(device),)).fetchone()
            if not row or row['expires'] <= self.clock() or not hmac.compare_digest(row['challenge'], challenge(verifier)):
                raise HTTPException(400, 'Connection request expired or proof invalid.')
            if row['decision'] == 'denied':
                db.execute('DELETE FROM device_grants WHERE device=?', (row['device'],))
                return {'error': 'access_denied'}
            if row['polled'] and self.clock() - row['polled'] < 4:
                return {'error': 'slow_down', 'interval': 5}
            db.execute('UPDATE device_grants SET polled=? WHERE device=?', (self.clock(), row['device']))
            if row['decision'] != 'approved':
                return {'error': 'authorization_pending'}
            # A returning grant waits for the ticket that went back to its own client.
            if row['return_to'] and not (isinstance(ticket, str) and len(ticket) <= 128 and row['ticket']
                                         and hmac.compare_digest(row['ticket'], digest(ticket))):
                return {'error': 'authorization_pending'}
            db.execute('DELETE FROM device_grants WHERE device=?', (row['device'],))
            session = self._tokens(db, secrets.token_hex(16), row['owner'], row['label'], self.clock() + row['lifetime'], self.clock())
            return {'owner': row['owner'], 'session': session}

    def resolve(self, token, *, refresh=False):
        if not isinstance(token, str) or len(token) > 200:
            raise HTTPException(401, 'Client session is invalid.')
        column = 'refresh' if refresh else 'access'
        with self.connect() as db:
            row = db.execute(f'SELECT * FROM client_sessions WHERE {column}=?', (digest(token),)).fetchone()
        if not row or row['expires'] <= self.clock() or (not refresh and row['access_expires'] <= self.clock()):
            raise HTTPException(401, 'Client session expired or revoked.')
        return dict(row)

    def refresh(self, token):
        row = self.resolve(token, refresh=True)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('SELECT 1 FROM client_sessions WHERE id=? AND refresh=? AND expires>?',
                              (row['id'], digest(token), self.clock())).fetchone():
                raise HTTPException(401, 'Client refresh token was already used.')
            return self._tokens(db, row['id'], row['owner'], row['label'], row['expires'], row['created'])

    def sessions(self, owner):
        with self.connect() as db:
            return [dict(row) for row in db.execute('SELECT id,label,created,expires FROM client_sessions WHERE owner=? AND expires>?', (owner, self.clock()))]

    def revoke(self, owner, session_id):
        with self.connect() as db:
            db.execute('DELETE FROM client_sessions WHERE owner=? AND id=?', (owner, session_id))
        return {'status': 'revoked'}


def return_target(value, origins=()):
    """Where an approval may return: a loopback address with a port (RFC 8252), or a configured client origin."""
    if value is None:
        return None
    try:
        parts = urlsplit(value) if isinstance(value, str) and len(value) <= 512 else None
        loopback = bool(parts and parts.scheme == 'http' and parts.hostname in ('127.0.0.1', 'localhost', '::1') and parts.port)
    except ValueError:
        parts = None
    if (not parts or parts.username or parts.password or parts.fragment or not parts.path.startswith('/')
            or any(ord(c) < 33 for c in value) or not (loopback or parts.scheme + '://' + parts.netloc in origins)):
        raise HTTPException(422, 'A sign-in returns only to the computer that started it or to a configured client origin.')
    return value


def install_client_authorization(plaza, store):
    """Mount a device approval flow. The host supplies verified user context."""
    from .client_approval import client_origins, install_client_approval
    install_client_approval(plaza)

    def require_return():
        # Turn on once every client returns its approval; until then, unbound grants still work by one click.
        return os.environ.get('PROMPITS_CLIENT_AUTH_REQUIRE_RETURN') == '1' or bool(getattr(plaza, 'raw_config', {}).get('client_auth_require_return'))
    async def body(request):
        if len(await request.body()) > 4096:
            raise HTTPException(413, 'Connection request is too large.')
        try:
            value = await request.json()
            if not isinstance(value, dict):
                raise ValueError()
            return value
        except ValueError as exc:
            raise HTTPException(422, 'Expected a JSON object.') from exc

    @plaza.app.post('/api/client-auth/device')
    async def begin(request: Request):
        data = await body(request)
        target = return_target(data.get('return_to'), client_origins(plaza))
        if target is None and require_return():
            raise HTTPException(422, 'This Plaza signs in only clients that receive their approval themselves. Update your client.')
        return await run_in_threadpool(store.begin, data.get('client_name'), data.get('code_challenge'),
                                       data.get('session_lifetime_seconds',DEFAULT_SESSION_LIFETIME), target)

    @plaza.app.get('/api/client-auth/request/{code}')
    async def pending(code: str):
        return await run_in_threadpool(store.pending, code)

    @plaza.app.post('/api/client-auth/oauth')
    async def oauth(request: Request):
        data = await body(request)
        def prepare():
            from urllib.parse import parse_qs, urlsplit
            code = store.pending_proof(data.get('device_code'), data.get('code_verifier'))
            # A provider sign-in approves without another click, so it must return to the client that started it.
            if not store.bound(code):
                raise HTTPException(403, 'Provider sign-in needs a client that receives its approval itself. Update your client.')
            url = plaza._build_oauth_redirect(provider=data.get('provider'), next_path='/client-signin-complete')
            state = parse_qs(urlsplit(url).query)['state'][0]
            with plaza._oauth_state_lock:
                plaza._oauth_states[state]['client_grant'] = code
            return {'authorization_url': url+'&prompt=select_account'}
        return await run_in_threadpool(prepare)

    @plaza.app.post('/api/client-auth/approve')
    async def approve(request: Request):
        data = await body(request)
        def perform():
            token, _, user = plaza._get_authenticated_ui_context(request)
            if token.startswith(ACCESS_PREFIX):
                raise HTTPException(403, 'Approve from a normal Plaza browser session.')
            return store.approve(data.get('user_code'), user['id'], data.get('approved'))
        return await run_in_threadpool(perform)

    @plaza.app.post('/api/client-auth/token')
    async def exchange(request: Request):
        data = await body(request)
        def perform():
            result = store.exchange(data.get('device_code'), data.get('code_verifier'), data.get('ticket'))
            if result.get('owner'):
                user = plaza._get_ui_user(result.pop('owner'))
                if not user or not plaza._ui_user_status_is_active(user):
                    raise HTTPException(403, 'This account is disabled.')
                result['user'] = {**user, 'role': 'user'}
            return result
        return await run_in_threadpool(perform)

    @plaza.app.get('/api/client-auth/sessions')
    async def sessions(request: Request):
        return await run_in_threadpool(lambda: {'sessions': store.sessions(plaza._require_ui_user(request)['id'])})

    @plaza.app.post('/api/client-auth/revoke')
    async def revoke(request: Request):
        data = await body(request)
        return await run_in_threadpool(lambda: store.revoke(plaza._require_ui_user(request)['id'], data.get('id')))
