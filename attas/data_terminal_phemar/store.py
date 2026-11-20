"""Owner-scoped, versioned state and encrypted connection/session storage."""

# OBSOLETE BUSINESS BRANCHES — remove after their owning-agent cutovers.
# Keep only generic container/transport primitives; do not extend domain branches.
# See BUSINESS_LOGIC_OBSOLETE.json. This marker does not retire live routes.
from __future__ import annotations

import json
import os
import secrets
import sqlite3
import time
import math
import hashlib
from pathlib import Path

from cryptography.fernet import Fernet
from fastapi import HTTPException

from prompits.core.file_lock import exclusive

KEY = 'ATTAS_TERMINAL_VAULT_KEY'
MISSING_KEY = ('Retis Terminal did not start: the app has not handed over its vault key. '
               'Open Retis Terminal to start it.')


def missing_key():
    """Why this Terminal must not start, or None. In the Retis Terminal app (Retis MAS Manager runs it) the app
    keeps the vault key and hands it over; the Terminal never makes a key file of its own there, because data
    written with one could not be read with the app's key. Kit and local Terminals keep their `vault.key`."""
    return MISSING_KEY if os.environ.get('RETIS_MAS_MANAGER') == '1' and not os.environ.get(KEY) else None


class Store:
    def __init__(self, directory: Path):
        refused = missing_key()
        if refused:
            raise RuntimeError(refused)
        in_app = os.environ.get('RETIS_MAS_MANAGER') == '1'
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(directory, 0o700)
        key = directory / 'vault.key'
        if not in_app and not key.exists():
            temporary = directory / ('vault-' + secrets.token_hex(8) + '.tmp')
            try:
                fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, 'wb') as out:
                    out.write(Fernet.generate_key())
                    out.flush()
                    os.fsync(out.fileno())
                os.link(temporary, key)
            except FileExistsError:
                pass
            finally:
                temporary.unlink(missing_ok=True)
        self.cipher = Fernet(os.environ.get(KEY, '').encode() or key.read_bytes())
        self.path = directory / 'state.sqlite'
        with exclusive(directory / 'initialize.lock'):
            with self.connect() as db:
                db.execute('PRAGMA journal_mode=WAL')
                db.execute('CREATE TABLE IF NOT EXISTS documents(owner TEXT,kind TEXT,id TEXT,revision INTEGER,payload TEXT,updated REAL,PRIMARY KEY(owner,kind,id))')
                db.execute('CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY,payload TEXT,expires REAL)')
                db.execute('CREATE TABLE IF NOT EXISTS budgets(id TEXT PRIMARY KEY,owner TEXT,scope TEXT,day TEXT,amount REAL,status TEXT,created REAL)')
                db.execute('CREATE TABLE IF NOT EXISTS refresh_locks(id TEXT PRIMARY KEY,holder TEXT,expires REAL)')
                db.execute('CREATE TABLE IF NOT EXISTS terminal_logins(terminal TEXT PRIMARY KEY,owner TEXT,session_id TEXT,expires REAL)')
                # Which browsers may present a login's cookie (window_sessions.BROWSER_HEADER).
                db.execute('CREATE TABLE IF NOT EXISTS terminal_browsers(terminal TEXT,browser TEXT,session_id TEXT,PRIMARY KEY(terminal,browser))')
        os.chmod(self.path, 0o600)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def bind_terminal(self, terminal, owner, session_id=''):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM terminal_logins WHERE expires<?',(time.time(),))
            old=db.execute('SELECT * FROM terminal_logins WHERE terminal=?',(terminal,)).fetchone()
            if old and old['owner']!=owner:
                raise HTTPException(409,'This Terminal is already signed in. Sign out before using another Plaza account, or open a separate Terminal instance.')
            sid=session_id or (old['session_id'] if old else '')
            session=db.execute('SELECT expires FROM sessions WHERE id=?',(sid,)).fetchone()
            expires=session['expires'] if session else time.time()+86400
            db.execute('INSERT OR REPLACE INTO terminal_logins VALUES(?,?,?,?)',(terminal,owner,sid,expires))

    def refresh_terminal(self, terminal, owner, session_id):
        """Extend a binding that still names this owner and session, as bind_terminal would; never create one.
        Returns whether the binding is still there."""
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            session=db.execute('SELECT expires FROM sessions WHERE id=?',(session_id,)).fetchone()
            expires=session['expires'] if session else time.time()+86400
            return db.execute('UPDATE terminal_logins SET expires=? WHERE terminal=? AND owner=? AND session_id=? AND expires>?',
                              (expires,terminal,owner,session_id,time.time())).rowcount>0

    def bind_hosted_terminal(self, terminal, owner, access_token):
        # Persist the verified host session in the same encrypted vault as a
        # direct login. It remains private and expires; every use is revalidated
        # with Plaza. Native logins retain their own refresh-token lifecycle.
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old=db.execute('SELECT * FROM terminal_logins WHERE terminal=? AND expires>?',(terminal,time.time())).fetchone()
            if old and old['owner']!=owner:raise HTTPException(409,'This Terminal is already signed in. Use a separate Terminal for another Plaza account.')
            sid=old['session_id'] if old else ''
            saved=db.execute('SELECT * FROM sessions WHERE id=? AND expires>?',(sid,time.time())).fetchone() if sid else None
            if saved and not self.decrypt(saved['payload']).get('hosted'):
                db.execute('UPDATE terminal_logins SET expires=? WHERE terminal=?',(time.time()+86400,terminal));return sid
            sid=sid if saved else secrets.token_urlsafe(32)
            payload=self.encrypt({'access_token':access_token,'hosted':True})
            db.execute('INSERT OR REPLACE INTO sessions VALUES(?,?,?)',(sid,payload,time.time()+86400))
            db.execute('INSERT OR REPLACE INTO terminal_logins VALUES(?,?,?,?)',(terminal,owner,sid,time.time()+86400))
            return sid

    def terminal_login(self, terminal):
        with self.connect() as db:
            row=db.execute('SELECT * FROM terminal_logins WHERE terminal=? AND expires>?',(terminal,time.time())).fetchone()
        return dict(row) if row else None

    def installation_owner(self, terminal):
        key = hashlib.sha256(terminal.encode()).hexdigest()
        try:
            state = self.decrypt(self.get('_installation', 'enrollment', key)['secret']).get('state') or {}
        except HTTPException as exc:
            if exc.status_code != 404:
                raise
            return None
        now=time.time()
        active=state.get('accessState','active')=='active' and (state.get('validFrom') is None or state['validFrom']<=now) and (state.get('validUntil') is None or state['validUntil']>now)
        return state.get('requester') if state.get('status') == 'approved' and active else None

    def bind_browser(self, terminal, session_id, browser):
        """Let the browser holding this key (its digest) present this login's cookie for this Terminal or window."""
        if not terminal or not session_id or not browser:
            raise ValueError('A browser login needs its Terminal, session and browser key.')
        with self.connect() as db:
            db.execute('DELETE FROM terminal_browsers WHERE session_id NOT IN (SELECT id FROM sessions)')
            db.execute('INSERT OR REPLACE INTO terminal_browsers VALUES(?,?,?)', (terminal, browser, session_id))

    def browser_login(self, terminal, session_id, browser):
        """The live binding this cookie's session holds for this Terminal or window, presented by the browser it was
        issued to; None otherwise. A window that signed out has no binding, so its old cookie is no login."""
        if not session_id or not browser or len(session_id) > 200:
            return None
        now = time.time()
        with self.connect() as db:
            row = db.execute('SELECT l.* FROM terminal_logins l JOIN sessions s ON s.id=l.session_id '
                             'JOIN terminal_browsers b ON b.terminal=l.terminal AND b.session_id=l.session_id '
                             'WHERE l.terminal=? AND l.session_id=? AND b.browser=? AND l.expires>? AND s.expires>?',
                             (terminal, session_id, browser, now, now)).fetchone()
        return dict(row) if row else None

    def release_terminal(self, terminal, owner):
        with self.connect() as db:
            if db.execute('DELETE FROM terminal_logins WHERE terminal=? AND owner=?',(terminal,owner)).rowcount:
                db.execute('DELETE FROM terminal_browsers WHERE terminal=?',(terminal,))

    def list(self, owner, kind):
        with self.connect() as db:
            rows = db.execute('SELECT * FROM documents WHERE owner=? AND kind=? ORDER BY updated DESC', (owner, kind)).fetchall()
        return [self.unpack(row) for row in rows]



    @staticmethod
    def unpack(row):
        from .phema_transfer import hydrate
        return hydrate({'id': row['id'], 'revision': row['revision'], 'updatedAt': row['updated'], **json.loads(row['payload'])})

    def get(self, owner, kind, id):
        with self.connect() as db:
            row = db.execute('SELECT * FROM documents WHERE owner=? AND kind=? AND id=?', (owner, kind, id)).fetchone()
        if not row:
            raise HTTPException(404, 'Record unavailable.')
        return self.unpack(row)

    def put(self, owner, kind, id, payload, revision=None):
        data = json.dumps(payload, allow_nan=False, separators=(',', ':'))
        if len(data) > (5_000_000 if kind in ('workspace-state','workspace-import','workspace-conflict','workspace-revision') else 500_000):
            raise HTTPException(413, 'Document exceeds 500 KB.')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT * FROM documents WHERE owner=? AND kind=? AND id=?', (owner, kind, id)).fetchone()
            current = old['revision'] if old else 0
            if revision is not None and revision != current:
                raise HTTPException(409, 'Revision changed. Reload before saving; your draft is preserved.')
            if old and kind == 'workspace-state':
                db.execute('INSERT OR IGNORE INTO documents VALUES(?,?,?,?,?,?)',
                           (owner,'workspace-revision',id+':'+str(current),current,old['payload'],old['updated']))
            db.execute('INSERT OR REPLACE INTO documents VALUES(?,?,?,?,?,?)', (owner, kind, id, current + 1, data, time.time()))
        return self.get(owner, kind, id)

    def delete(self, owner, kind, id):
        with self.connect() as db:
            db.execute('DELETE FROM documents WHERE owner=? AND kind=? AND id=?', (owner, kind, id))

    def encrypt(self, data):
        return self.cipher.encrypt(json.dumps(data).encode()).decode()

    def decrypt(self, data):
        return json.loads(self.cipher.decrypt(data.encode()))

    def session(self, payload, *, lifetime=86400):
        if type(lifetime) is not int or not 1 <= lifetime <= 604800:
            raise ValueError('Session lifetime must be between one second and seven days.')
        id = secrets.token_urlsafe(32)
        expires=time.time()+lifetime
        if payload.get('plaza_expires_at') is not None:
            expires=min(expires,float(payload['plaza_expires_at']))
        with self.connect() as db:
            db.execute('DELETE FROM sessions WHERE expires<?', (time.time(),))
            db.execute('INSERT INTO sessions VALUES(?,?,?)', (id, self.encrypt(payload), expires))
        return id

    def read_session(self, id):
        with self.connect() as db:
            row = db.execute('SELECT * FROM sessions WHERE id=? AND expires>?', (id, time.time())).fetchone()
        return self.decrypt(row['payload']) if row else None

    def end_session(self, id):
        with self.connect() as db:
            db.execute('DELETE FROM sessions WHERE id=?', (id,))

    def claim_refresh(self, id, holder):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM refresh_locks WHERE expires<?', (time.time(),))
            db.execute('INSERT OR IGNORE INTO refresh_locks VALUES(?,?,?)',(id,holder,time.time()+30))
            return db.execute('SELECT holder FROM refresh_locks WHERE id=?',(id,)).fetchone()['holder']==holder

    def release_refresh(self, id, holder):
        with self.connect() as db:
            db.execute('DELETE FROM refresh_locks WHERE id=? AND holder=?',(id,holder))



    def reserve(self, owner, scope, amount, ceiling):
        if type(amount) not in (float, int) or not math.isfinite(amount) or amount < 0:
            raise HTTPException(409, 'Cost is unknown or invalid.')
        if amount == 0:
            return 'free'
        id = secrets.token_hex(16)
        day = time.strftime('%Y-%m-%d', time.gmtime())
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            used = db.execute("SELECT COALESCE(SUM(amount),0) FROM budgets WHERE owner=? AND scope=? AND day=? AND status!='released'", (owner, scope, day)).fetchone()[0]
            if ceiling is not None and (amount > ceiling or used + amount > ceiling):
                raise HTTPException(409, 'Daily Terminal budget reached; increase the limit or turn off Enable budget limit in settings.')
            db.execute('INSERT INTO budgets VALUES(?,?,?,?,?,?,?)', (id, owner, scope, day, amount, 'reserved', time.time()))
        return id

    def settle(self, owner, id, status='settled'):
        if status not in ('settled', 'uncertain', 'released'):
            raise ValueError('Invalid settlement state')
        with self.connect() as db:
            db.execute('UPDATE budgets SET status=? WHERE owner=? AND id=? AND status=?', (status, owner, id, 'reserved'))

    def settle_part(self, owner, id, settled):
        """Record what Plaza actually paid out of a reservation: a refund still pays work it recorded as delivered.

        `settled` points stay counted as spent and the rest is released. A reservation already closed is left as it is,
        except one marked uncertain, which this confirmed outcome resolves.
        """
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM budgets WHERE owner=? AND id=?', (owner, id)).fetchone()
            if row is None or row['status'] not in ('reserved', 'uncertain'):
                return
            settled = min(max(settled, 0), row['amount'])
            if settled <= 0 or settled >= row['amount']:
                db.execute('UPDATE budgets SET status=? WHERE owner=? AND id=?', ('settled' if settled > 0 else 'released', owner, id))
                return
            db.execute('UPDATE budgets SET amount=?,status=? WHERE owner=? AND id=?', (settled, 'settled', owner, id))
            db.execute('INSERT INTO budgets VALUES(?,?,?,?,?,?,?)',
                       (id + ':released', owner, row['scope'], row['day'], row['amount'] - settled, 'released', row['created']))

    def usage(self, owner):
        with self.connect() as db:
            rows = db.execute("SELECT scope,status,SUM(amount) amount FROM budgets WHERE owner=? AND day=? GROUP BY scope,status", (owner, time.strftime('%Y-%m-%d', time.gmtime()))).fetchall()
        return [dict(row) for row in rows]
