"""Short-lived, single-use transfers of an already verified Terminal login."""
import hashlib
import secrets
import time
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse
from .window_sessions import window_context,window_scope,window_cookie


def install_session_handoff(app, principal, store, terminal_scope, cookie, generic_cookie):
    with store.connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS browser_handoffs(key TEXT PRIMARY KEY,terminal TEXT,owner TEXT,session_id TEXT,target TEXT,expires REAL)')

    @app.post('/api/auth/browser-handoff')
    async def issue(request: Request):
        user = await principal(request)
        body = await request.json()
        target = body.get('target', '/')
        if not isinstance(target, str) or not target.startswith('/') or target.startswith('//') or '\\' in target or any(ord(c) < 32 for c in target):
            raise HTTPException(422, 'Choose a page in this Terminal.')
        parsed = urlsplit(target)
        if parsed.scheme or parsed.netloc or parsed.path.startswith('/api/auth/'):
            raise HTTPException(422, 'Choose a page in this Terminal.')
        scope=window_scope(terminal_scope,window_context(request))
        binding = store.terminal_login(scope)
        if not binding or binding['owner'] != user['id'] or not store.read_session(binding['session_id']):
            raise HTTPException(401, 'A current Terminal login is required.')
        ticket = secrets.token_urlsafe(32)
        key = hashlib.sha256(ticket.encode()).hexdigest()
        with store.connect() as db:
            db.execute('DELETE FROM browser_handoffs WHERE expires<?', (time.time(),))
            db.execute('INSERT INTO browser_handoffs VALUES(?,?,?,?,?,?)',
                       (key, scope, user['id'], binding['session_id'], target, time.time()+180))
        return {'path': '/api/auth/browser-handoff/'+ticket}

    @app.get('/api/auth/browser-handoff/{ticket}')
    async def accept(ticket: str, request: Request):
        key = hashlib.sha256(ticket.encode()).hexdigest()
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM browser_handoffs WHERE key=?', (key,)).fetchone()
            if row and row['terminal']!=terminal_scope and not row['terminal'].startswith(terminal_scope+'|window|'):row=None
            if row:
                db.execute('DELETE FROM browser_handoffs WHERE key=?', (key,))
            binding = db.execute('SELECT * FROM terminal_logins WHERE terminal=?', (row['terminal'],)).fetchone() if row else None
            session = db.execute('SELECT expires FROM sessions WHERE id=?', (row['session_id'],)).fetchone() if row else None
        if not row or row['expires'] <= time.time() or not binding or not session or binding['owner'] != row['owner'] or binding['session_id'] != row['session_id'] or min(binding['expires'], session['expires']) <= time.time():
            raise HTTPException(401, 'This Terminal login handoff is unavailable or expired.')
        target=row['target'];sid=row['session_id'];names={cookie,generic_cookie}
        if '|window|' in row['terminal']:
            window=secrets.token_hex(16)
            store.bind_terminal(window_scope(terminal_scope,window),row['owner'],sid)
            parsed=urlsplit(target);query=dict(parse_qsl(parsed.query));query['terminalWindow']=window
            target=urlunsplit((parsed.scheme,parsed.netloc,parsed.path,urlencode(query),parsed.fragment))
            names={window_cookie(cookie,window)}
        response = RedirectResponse(target, status_code=303)
        # Reuse the existing login and expiry; no new credentials or session.
        age = max(1, int(min(binding['expires'], session['expires'])-time.time()))
        for name in names:
            response.set_cookie(name, sid, httponly=True, samesite='strict', max_age=age,secure=request.url.scheme=='https')
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Referrer-Policy'] = 'no-referrer'
        return response
