"""Browser-proven, seven-day login reuse; Plaza remains the identity authority."""
import hashlib
import secrets
import time

from fastapi import Request
from .window_sessions import window_context, window_scope

LIFETIME = 7 * 24 * 60 * 60


class RememberedLogin:
    def __init__(self, store, terminal_scope, cookie):
        self.store, self.scope, self.cookie = store, terminal_scope, cookie + '_remember'
        with store.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS remembered_logins(proof TEXT PRIMARY KEY,terminal TEXT,owner TEXT,session_id TEXT,expires REAL)')

    @staticmethod
    def digest(proof):
        return hashlib.sha256(proof.encode()).hexdigest()

    def forget(self, request, response, *, signed_out=False):
        proof=request.cookies.get(self.cookie, '')
        if proof:
            with self.store.connect() as db:
                db.execute('DELETE FROM remembered_logins WHERE proof=? AND terminal=?',(self.digest(proof),self.scope))
        if signed_out:
            # Other windows may remain signed in. Their next bootstrap must not
            # silently recreate a cache deliberately cleared by this browser.
            response.set_cookie(self.cookie,'signed-out',httponly=True,samesite='strict',
                                max_age=LIFETIME,secure=request.url.scheme=='https')
        else:
            response.delete_cookie(self.cookie, httponly=True, samesite='strict', secure=request.url.scheme=='https')

    def issue(self, request, response, owner, sid):
        now=time.time()
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            session=db.execute('SELECT expires FROM sessions WHERE id=? AND expires>?',(sid,now)).fetchone()
            if not session:return response
            # Reuse never extends the original session's absolute lifetime.
            expires=min(session['expires'],now+LIFETIME)
            old=request.cookies.get(self.cookie,'')
            if old:db.execute('DELETE FROM remembered_logins WHERE proof=? AND terminal=?',(self.digest(old),self.scope))
            db.execute('DELETE FROM remembered_logins WHERE expires<=?',(now,))
            proof=secrets.token_urlsafe(32)
            db.execute('INSERT INTO remembered_logins VALUES(?,?,?,?,?)',(self.digest(proof),self.scope,owner,sid,expires))
        response.set_cookie(self.cookie,proof,httponly=True,samesite='strict',
                            max_age=max(1,int(expires-now)),secure=request.url.scheme=='https')
        response.headers['Cache-Control']='no-store'
        return response

    def owner(self, request):
        """The account of this browser's live remembered login, if any; Plaza is not asked."""
        proof=request.cookies.get(self.cookie,'')
        if not proof or len(proof)>128:return None
        with self.store.connect() as db:
            row=db.execute('SELECT owner,session_id FROM remembered_logins WHERE proof=? AND terminal=? AND expires>?',
                           (self.digest(proof),self.scope,time.time())).fetchone()
        return row['owner'] if row and self.store.read_session(row['session_id']) else None

    async def restore(self, request, principal):
        proof=request.cookies.get(self.cookie,'')
        if not proof or len(proof)>128:return None
        with self.store.connect() as db:
            row=db.execute('SELECT * FROM remembered_logins WHERE proof=? AND terminal=? AND expires>?',
                           (self.digest(proof),self.scope,time.time())).fetchone()
        if not row or not self.store.read_session(row['session_id']):return None
        # The opaque browser proof authorizes only this recorded session. The
        # ordinary verifier refreshes tokens under its existing lock and checks
        # revocation; neither a window selector nor an arbitrary owner is proof.
        context=dict(request.scope)
        context['state']={'selected_session_id':row['session_id'],'expected_session_owner':row['owner'],
                          'remembered_login_probe':True}
        user=await principal(Request(context),False)
        if not user or user['id']!=row['owner']:return None
        # Sign-out/revocation may race the upstream check. Bind only while the
        # same browser proof and session remain valid in one transaction.
        target=window_scope(self.scope,window_context(request));now=time.time()
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            live=db.execute('SELECT * FROM remembered_logins WHERE proof=? AND terminal=? AND expires>?',
                            (self.digest(proof),self.scope,now)).fetchone()
            session=db.execute('SELECT expires FROM sessions WHERE id=? AND expires>?',(row['session_id'],now)).fetchone()
            current=db.execute('SELECT * FROM terminal_logins WHERE terminal=? AND expires>?',(target,now)).fetchone()
            if not live or not session or live['session_id']!=row['session_id'] or live['owner']!=user['id']:
                return None
            if current and current['owner']!=user['id']:return None
            expires=min(live['expires'],session['expires'])
            db.execute('INSERT OR REPLACE INTO terminal_logins VALUES(?,?,?,?)',(target,user['id'],row['session_id'],expires))
        return user,row['session_id'],expires
