"""Single-use, audience/body-bound call credentials; never UI sessions."""
from contextvars import ContextVar
import hashlib
import logging
import json
import os
import math
from pathlib import Path
from urllib.parse import parse_qsl, urlencode
import secrets
import sqlite3
import time
import httpx
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials
from starlette.concurrency import run_in_threadpool
from anyio import CapacityLimiter
from anyio.to_thread import run_sync

from .approved_access import BASIS as APPROVED, PUBLISHED, call_basis, card_owner, is_approved_operation, published_practice

LOG = logging.getLogger(__name__)
PREFIX = 'pc-call-'
HISTORY_DAYS = 90
CURRENT = ContextVar('scoped_call', default=None)
AUTH_FIELDS = {'caller_plaza_token','caller_user_token','caller_direct_token'}


def binding(method, path, body=None, query=''):
    if isinstance(body, dict): body = {k:v for k,v in body.items() if k not in AUTH_FIELDS}
    return hashlib.sha256(json.dumps({'method':method.upper(), 'path':path,
        'query':urlencode(sorted(parse_qsl(query, keep_blank_values=True))), 'body':body},
        sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def operation_binding(method, path, body=None, query=''):
    """The verified-operation protocol binds exact query bytes and ordering."""
    if isinstance(body, dict):body={k:v for k,v in body.items() if k not in AUTH_FIELDS}
    return hashlib.sha256(json.dumps({'method':method.upper(),'path':path,'query':query,'body':body},
        sort_keys=True,separators=(',', ':'),allow_nan=False).encode()).hexdigest()


def request_operation(path,body):
    if path.startswith('/use_practice/') and isinstance(body,dict):
        content=body.get('content')
        return (content.get('pulse_name') if isinstance(content,dict) else None) or path.rsplit('/',1)[-1]
    return path


class CallGrants:
    def __init__(self, path):
        self.path = Path(path); self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('CREATE TABLE IF NOT EXISTS call_grants(id TEXT PRIMARY KEY, token_hash TEXT UNIQUE, payload TEXT, expires REAL, consumed INTEGER DEFAULT 0, revoked INTEGER DEFAULT 0)')
            if 'root' not in {r[1] for r in db.execute('PRAGMA table_info(call_grants)')}:
                db.execute('ALTER TABLE call_grants ADD COLUMN root TEXT')
            db.execute('CREATE INDEX IF NOT EXISTS call_grant_roots ON call_grants(root)')
            db.execute('CREATE INDEX IF NOT EXISTS call_grant_expiry ON call_grants(expires)')
            db.execute('CREATE TABLE IF NOT EXISTS call_grant_usage(id TEXT PRIMARY KEY, calls INTEGER NOT NULL)')
            # Each person's own connection history: which agent was called, for what, why it was allowed.
            # Grants themselves are deleted a day after expiry; these short rows stay for HISTORY_DAYS.
            db.execute('CREATE TABLE IF NOT EXISTS call_grant_history(id TEXT PRIMARY KEY, account TEXT NOT NULL, issued REAL NOT NULL, '
                       'actor TEXT, actor_name TEXT, audience TEXT, operation TEXT, basis TEXT, depth INTEGER, root TEXT, '
                       'used INTEGER DEFAULT 0, revoked INTEGER DEFAULT 0)')
            db.execute('CREATE INDEX IF NOT EXISTS call_grant_history_account ON call_grant_history(account, issued)')
        self.path.chmod(0o600)

    def connect(self): return sqlite3.connect(self.path, timeout=10)

    def issue(self, *, actor, audience, user=None, request_hash, delegates=(), ttl=300, parent=None, operation='', actor_name='', authority=None, basis=None):
        if not actor or not audience or len(request_hash) != 64 or not 1 <= ttl <= 300:
            raise ValueError('Invalid call scope.')
        if not isinstance(operation,str) or len(operation)>200 or not isinstance(delegates,(list,tuple)) or len(delegates)>128:
            raise ValueError('Invalid delegated operations.')
        if any(not isinstance(d,dict) or set(d)!={'agentId','operation'} or any(not isinstance(v,str) or not v or len(v)>200 for v in d.values()) for d in delegates):
            raise ValueError('Invalid delegated operations.')
        parent_row = self.read(parent, include_consumed=True) if parent else None
        if parent_row:
            if parent_row['audience'] != actor or parent_row['depth'] >= 4 or {'agentId':audience,'operation':operation} not in parent_row['delegates']:
                raise PermissionError('Call delegation exceeds its authority.')
            user = parent_row.get('user')
            ttl = min(ttl, max(0, int(parent_row['expires']-time.time())))
            if ttl<1: raise PermissionError('Parent call grant expired.')
            delegates = parent_row['delegates']
        token = PREFIX + secrets.token_urlsafe(32); id = secrets.token_hex(16)
        value = {'id':id,'actor':actor,'audience':audience,'user':user,'binding':request_hash,
            'delegates':list(delegates),'expires':time.time()+ttl,'depth':parent_row['depth']+1 if parent_row else 0,
            'operation':operation,'actorName':actor_name,
            'root':parent_row['root'] if parent_row else id,
            'rootActor':parent_row.get('rootActor',parent_row['actor']) if parent_row else actor,
            'parentId':parent_row['id'] if parent_row else None}
        if authority is not None:
            value['authority']=authority
        # Why Plaza allowed the root call; onward hops keep it.
        basis=parent_row.get('basis') if parent_row else basis
        if basis:
            value['basis']=basis
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if parent_row and db.execute('SELECT count(*) FROM call_grants WHERE root=?',(value['root'],)).fetchone()[0]>=33:
                raise PermissionError('Call delegation exceeds its invocation limit.')
            # Keep issuance bounded even when a long-running deployment has
            # accumulated large scope payloads. Expired credentials are already
            # rejected by read(); cleanup is storage maintenance only.
            db.execute('DELETE FROM call_grants WHERE id IN (SELECT id FROM call_grants WHERE expires<? ORDER BY expires LIMIT 128)', (time.time()-86400,))
            db.execute('INSERT INTO call_grants(id,token_hash,payload,expires,root) VALUES(?,?,?,?,?)',
                (id,hashlib.sha256(token.encode()).hexdigest(),json.dumps(value),value['expires'],value['root']))
            account=(user or {}).get('id') if isinstance(user,dict) else None
            if account:
                db.execute('DELETE FROM call_grant_history WHERE id IN (SELECT id FROM call_grant_history WHERE issued<? ORDER BY issued LIMIT 128)',
                    (time.time()-HISTORY_DAYS*86400,))
                db.execute('INSERT INTO call_grant_history(id,account,issued,actor,actor_name,audience,operation,basis,depth,root) VALUES(?,?,?,?,?,?,?,?,?,?)',
                    (id,str(account),time.time(),actor,actor_name,audience,operation,value.get('basis'),value['depth'],value['root']))
        return {'token':token,'id':id,'expiresAt':value['expires']}

    def by_id(self, id):
        """Read an expired receipt for renewal; expiry never restores revocation."""
        with self.connect() as db:
            row=db.execute('SELECT payload,revoked FROM call_grants WHERE id=?',(id,)).fetchone()
            if not row or row[1]:raise PermissionError('Grant receipt unavailable or revoked.')
            value=json.loads(row[0])
            root=db.execute('SELECT revoked FROM call_grants WHERE id=?',(value['root'],)).fetchone()
            if not root or root[0]:raise PermissionError('Grant root revoked.')
        return value

    def renew(self, id, *, actor, user, authority, ttl=300):
        """Renew exact transport authority after fresh user/owner ACL verification."""
        old=self.by_id(id)
        if old['actor']!=actor or not user or (old.get('user') or {}).get('id')!=user.get('id') or old.get('authority')!=authority:
            raise PermissionError('Continuation cannot change actor, user, intent or authority.')
        if not 1<=ttl<=300:raise ValueError('Invalid continuation lifetime.')
        token=PREFIX+secrets.token_urlsafe(32);new_id=secrets.token_hex(16)
        value={**old,'id':new_id,'expires':time.time()+ttl,'continuationOf':id}
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            root=db.execute('SELECT revoked FROM call_grants WHERE id=?',(old['root'],)).fetchone()
            if not root or root[0]:raise PermissionError('Grant root revoked.')
            if db.execute('SELECT count(*) FROM call_grants WHERE root=?',(old['root'],)).fetchone()[0]>=33:
                raise PermissionError('Continuation exceeds the invocation limit.')
            db.execute('INSERT INTO call_grants(id,token_hash,payload,expires,root) VALUES(?,?,?,?,?)',
                (new_id,hashlib.sha256(token.encode()).hexdigest(),json.dumps(value),value['expires'],value['root']))
        return {'token':token,'id':new_id,'expiresAt':value['expires'],'continuationOf':id}

    def read(self, token, *, include_consumed=False):
        with self.connect() as db:
            row=db.execute('SELECT payload,expires,consumed,revoked FROM call_grants WHERE token_hash=?', (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
            if not row or row[1] <= time.time() or row[3] or row[2] and not include_consumed:
                raise PermissionError('Call grant expired, revoked or already used.')
            value=json.loads(row[0])
            root=db.execute('SELECT revoked FROM call_grants WHERE id=?',(value['root'],)).fetchone()
            if not root or root[0]: raise PermissionError('Call grant revoked.')
        return value

    def consume(self, token, audience, request_hash, operation=None):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            value=self.read(token)
            matches_operation = operation is None or value.get('operation') == operation
            if value.get('authority') and operation == value['authority'].get('path') and not operation.startswith('/use_practice/'):
                matches_operation = True  # Semantic name is pinned by the verified contract; transport is separately exact.
            if value['audience']!=audience:raise PermissionError('Call grant is for another audience.')
            if value['binding']!=request_hash:raise PermissionError('Call grant binds another request.')
            if not matches_operation:raise PermissionError('Call grant is for another operation.')
            policy=(value.get('authority') or {}).get('onwardPolicy')
            if policy:
                row=db.execute('SELECT calls FROM call_grant_usage WHERE id=?',(policy['key'],)).fetchone()
                count=row[0] if row else 0
                if count>=policy['maxCalls']:raise PermissionError('Downstream call ceiling reached.')
                db.execute('INSERT INTO call_grant_usage VALUES(?,1) ON CONFLICT(id) DO UPDATE SET calls=calls+1',(policy['key'],))
            db.execute('UPDATE call_grants SET consumed=1 WHERE id=?',(value['id'],))
            db.execute('UPDATE call_grant_history SET used=1 WHERE id=?',(value['id'],))
        return value

    def revoke(self, id, owner):
        with self.connect() as db:
            row=db.execute('SELECT payload FROM call_grants WHERE id=?',(id,)).fetchone()
            if not row or (json.loads(row[0]).get('user') or {}).get('id')!=owner: raise PermissionError('Grant unavailable.')
            db.execute('UPDATE call_grants SET revoked=1 WHERE id=?',(id,))
            db.execute('UPDATE call_grant_history SET revoked=1 WHERE id=? OR root=?',(id,id))

    def history(self, account, *, limit=50, before=None):
        """This account's own grants, newest first."""
        with self.connect() as db:
            rows=db.execute('SELECT id,issued,actor,actor_name,audience,operation,basis,depth,root,used,revoked FROM call_grant_history '
                            'WHERE account=? AND issued<? ORDER BY issued DESC, id LIMIT ?',(str(account),before or 1e18,limit)).fetchall()
        keys=('id','issued','actor','actorName','audience','operation','basis','depth','root','used','revoked')
        return [dict(zip(keys,row)) for row in rows]


def install_plaza_call_grants(plaza):
    grants=CallGrants(plaza._client_sessions().path.with_name('call-grants.sqlite'))
    plaza.call_grants=grants
    # Dedicated bounded validation workers keep shared callback workers free.
    # anyio propagates the request ContextVars into these synchronous callbacks.
    authority_workers=CapacityLimiter(8)
    def agent_auth(token):
        state=plaza._get_plaza_practice().state
        return state.verify_token(HTTPAuthorizationCredentials(scheme='Bearer', credentials=token))

    from .peer_connections import mount_owner_connections
    mount_owner_connections(plaza,agent_auth)

    @plaza.app.post('/api/call-grants')
    async def issue(request: Request):
        if len(await request.body())>65536:raise HTTPException(413,'Call scope is too large.')
        data=await request.json()
        if not isinstance(data,dict):raise HTTPException(422,'Call scope must be an object.')
        actor=await run_in_threadpool(agent_auth,request.headers.get('x-agent-token',''))
        state=plaza._get_plaza_practice().state
        if data.get('audience') not in state.agent_cards:raise HTTPException(403,'Call audience is not registered.')
        parent=data.get('parent')
        user=None
        if not parent:
            bearer=request.headers.get('authorization','').removeprefix('Bearer ')
            if bearer and bearer != request.headers.get('x-agent-token'):
                # External identity verification is synchronous. Performing it
                # on the event loop stalls health, UI sessions and unrelated
                # grant consumption during concurrent requests. Revalidate on
                # every issuance; do not cache identity or authority here.
                try: user=(await run_in_threadpool(plaza._get_authenticated_ui_context,request))[2]
                except HTTPException as exc:
                    if exc.status_code not in (401,403):raise
                    user=(await run_in_threadpool(plaza._resolve_agent_owner_from_key_cached,bearer)).get('_user_profile')
                user={k:user.get(k) for k in ('id','email','username','display_name')}
        delegates=data.get('delegates',[])
        if not isinstance(delegates,list) or len(delegates)>128:raise HTTPException(422,'Invalid downstream authority.')
        basis=None
        if not parent:
            actor_card=state.agent_cards.get(actor['agent_id'],{})
            audience_card=state.agent_cards.get(data.get('audience'),{})
            owner=actor_card.get('owner_user_id') or actor_card.get('meta',{}).get('owner_user_id')
            # Plaza decides who may call whom: the same owner, an agent the Plaza owner approved,
            # or any registered agent calling a practice its provider published (paid or public).
            basis=await run_in_threadpool(call_basis,plaza,actor_card,audience_card,user)
            # The paying user may differ from the dispatching agent's owner for a published practice.
            if not basis and published_practice(audience_card,data.get('operation','')):
                basis=PUBLISHED
            if not basis:raise HTTPException(403,'Plaza has not approved this agent to call that agent.')
            if basis in (APPROVED,PUBLISHED):
                # Approval covers practices and API calls only; UI and configuration stay with the owner.
                if not is_approved_operation(data.get('operation','')):
                    raise HTTPException(403,'An owner-approved agent may call practices and APIs only.')
                audience_owner=card_owner(audience_card)
                for delegate in delegates:
                    recipient=state.agent_cards.get(delegate.get('agentId'),{}) if isinstance(delegate,dict) else {}
                    if card_owner(recipient)!=audience_owner or not is_approved_operation(delegate.get('operation')):
                        raise HTTPException(403,"Onward calls for an approved or published call stay within the provider owner's practices and APIs.")
            else:
                if delegates and user and user.get('id')!=owner:
                    raise HTTPException(403,'Downstream delegation requires the original user to own the issuing service.')
                for delegate in delegates:
                    recipient=state.agent_cards.get(delegate.get('agentId'),{}) if isinstance(delegate,dict) else {}
                    recipient_owner=recipient.get('owner_user_id') or recipient.get('meta',{}).get('owner_user_id')
                    if not owner or recipient_owner!=owner:raise HTTPException(403,'Downstream delegation requires the same verified owner.')
        else:
            # Registration ownership can change after issue. Recheck every hop.
            try:parent_row=await run_in_threadpool(grants.read,parent,include_consumed=True)
            except PermissionError as exc:raise HTTPException(403,str(exc)) from exc
            actor_card=state.agent_cards.get(actor['agent_id'],{})
            recipient=state.agent_cards.get(data.get('audience'),{})
            actor_owner=actor_card.get('owner_user_id') or actor_card.get('meta',{}).get('owner_user_id')
            recipient_owner=recipient.get('owner_user_id') or recipient.get('meta',{}).get('owner_user_id')
            # An owner-approved caller's request may continue to the same owner's agents, as practices only.
            approved=parent_row.get('basis') in (APPROVED,PUBLISHED)
            original=(parent_row.get('user') or {}).get('id',actor_owner)
            if not actor_owner or recipient_owner!=actor_owner or (original!=actor_owner and not approved):
                raise HTTPException(403,'Downstream delegation requires the same current verified owner and original user.')
            if approved and not is_approved_operation(data.get('operation','')):
                raise HTTPException(403,'Onward calls for an owner-approved agent are practices and APIs only.')
            if parent_row.get('authority'):
                raise HTTPException(403,'Verified operation grants require the operation-grant delegation endpoint.')
        try:
            return await run_in_threadpool(grants.issue,actor=actor['agent_id'],audience=data.get('audience'),user=user,
                request_hash=data.get('binding',''),delegates=delegates,parent=parent,operation=data.get('operation',''),actor_name=actor.get('agent_name',''),
                basis=basis)
        except (ValueError,PermissionError) as exc: raise HTTPException(403,str(exc)) from exc

    @plaza.app.post('/api/call-grants/consume')
    async def consume(request: Request):
        data=await request.json()
        def check_and_consume():
            actor=agent_auth(request.headers.get('authorization','').removeprefix('Bearer '))
            value=grants.read(data['token'])
            if value.get('authority'):
                validator=getattr(plaza,'validate_operation_grant',None)
                if not callable(validator):raise PermissionError('Operation authority verifier is unavailable.')
                validator(value)
            request_hash=data.get('exact_binding',data['binding']) if value.get('authority') else data['binding']
            return grants.consume(data['token'],actor['agent_id'],request_hash,data.get('operation'))
        # Reserve the entire authority check, including its storage and service
        # authentication, away from unrelated blocking UI callbacks. Splitting
        # it across the default worker pool still starves consumption/status.
        try:return await run_sync(check_and_consume,limiter=authority_workers)
        except (KeyError,PermissionError) as exc:
            # The caller learns only that the grant was rejected; Plaza's own log keeps which check failed.
            LOG.warning('Call grant rejected for operation %s: %s', str(data.get('operation'))[:120], str(exc)[:200])
            raise HTTPException(403,'Call grant rejected.') from exc

    @plaza.app.post('/api/call-grants/status')
    async def status(request: Request):
        data=await request.json()
        def check_status():
            actor=agent_auth(request.headers.get('authorization','').removeprefix('Bearer '))
            value=grants.read(data['token'],include_consumed=True)
            if value['audience']!=actor['agent_id']:raise PermissionError('Wrong audience.')
            if value.get('authority'):
                plaza.validate_operation_grant(value)
            return {'active':True,'expiresAt':value['expires'],'user':value.get('user') or {}}
        try:return await run_sync(check_status,limiter=authority_workers)
        except (KeyError,PermissionError):raise HTTPException(403,'Call grant expired or revoked.') from None
        except ValueError as exc:raise HTTPException(403,str(exc)) from exc

    @plaza.app.get('/api/call-grants/history')
    async def history(request: Request):
        """The signed-in person's own connection history, from their sign-in or their Terminal."""
        try:
            limit=int(request.query_params.get('limit','50'));before=request.query_params.get('before')
            before=float(before) if before else None
        except ValueError:raise HTTPException(422,'Use a number for limit and before.') from None
        if not 1<=limit<=200 or set(request.query_params)-{'limit','before'}:raise HTTPException(422,'Use limit 1–200 and before only.')
        def run():
            account=str(plaza._get_authenticated_ui_context(request)[2]['id'])
            rows=grants.history(account,limit=limit,before=before)
            cards=plaza._get_plaza_practice().state.agent_cards
            name=lambda identity:(cards.get(identity) or {}).get('name') or ''
            entries=[{'issuedAt':r['issued'],'agent':name(r['audience']) or 'Agent no longer registered','agentOnline':r['audience'] in cards,
                      'caller':r['actorName'] or name(r['actor']) or 'Agent no longer registered','operation':r['operation'] or '',
                      'basis':r['basis'] or '','onward':bool(r['depth']),
                      'outcome':'revoked' if r['revoked'] else 'used' if r['used'] else 'issued'} for r in rows]
            return {'entries':entries,'next':rows[-1]['issued'] if len(rows)==limit else None,'keptDays':HISTORY_DAYS}
        return await run_in_threadpool(run)

    @plaza.app.post('/api/call-grants/{id}/revoke')
    async def revoke(id: str, request: Request):
        user=(await run_in_threadpool(plaza._get_authenticated_ui_context,request))[2]
        try: await run_in_threadpool(grants.revoke,id,user['id'])
        except PermissionError as exc: raise HTTPException(403,str(exc)) from exc
        return {'revoked':True}


def install_agent_call_grants(agent):
    timeout = float(os.environ.get('PROMPITS_CALL_GRANT_TIMEOUT_SECONDS', '10'))
    if not math.isfinite(timeout) or not 1 <= timeout <= 90:
        raise ValueError('Call grant timeout must be bounded between one and ninety seconds.')
    @agent.app.middleware('http')
    async def scoped(request, call_next):
        token=request.headers.get('authorization','').removeprefix('Bearer ')
        body=None
        if request.url.path.startswith('/use_practice/'):
            try: body=await request.json()
            except ValueError: pass
            if isinstance(body,dict): token=body.get('caller_plaza_token') or token
        if not str(token).startswith(PREFIX): return await call_next(request)
        if body is None and request.method not in ('GET','HEAD'):
            try: body=await request.json()
            except ValueError: return JSONResponse({'detail':'Call grants require JSON.'},status_code=403)
        try:
            response=await agent._plaza_request_async('post','/api/call-grants/consume',
                headers=agent._ensure_token_valid(),json={'token':token,'binding':binding(request.method,request.url.path,body,request.url.query),
                    'exact_binding':operation_binding(request.method,request.url.path,body,request.url.query),'operation':request_operation(request.url.path,body)},timeout=timeout,retries=0)
            if response.status_code!=200: raise HTTPException(403,'Call grant rejected.')
            verified=response.json()
            agent_id=agent.agent_id() if callable(agent.agent_id) else agent.agent_id
            if verified['audience']!=str(agent_id): raise HTTPException(403,'Call audience mismatch.')
            request.state.scoped_call=verified
            context=CURRENT.set({**verified,'token':token,'plaza':agent.plaza_url,
                'transport':{'method':request.method,'path':request.url.path,'query':request.url.query,
                             'binding':operation_binding(request.method,request.url.path,body,request.url.query) if verified.get('authority')
                                else binding(request.method,request.url.path,body,request.url.query)}})
            try: return await call_next(request)
            finally: CURRENT.reset(context)
        except HTTPException as exc: return JSONResponse({'detail':exc.detail},status_code=exc.status_code)
        except httpx.RequestError:
            # A consume acknowledgement may be lost after Plaza has accepted
            # it. Never retry it or enter an unverified business handler.
            return JSONResponse({'detail':'Scoped authority unavailable. Check the original operation receipt before trying again.',
                                 'recoveryRequired':True,'executionReplayAllowed':False},status_code=503)


def verified_context():
    grant=CURRENT.get()
    if not grant: raise HTTPException(403,'Scoped token requires a verified request.')
    user=grant.get('user') or {}
    return {'agent_id':grant['actor'],'agent_name':grant.get('actorName'),'auth_mode':'scoped_call','plaza_url':grant.get('plaza'),
        'user_id':user.get('id'),'user_auth_mode':'scoped_call','user_role':'user','user':user,
        'email':user.get('email'),'username':user.get('username'),'delegation':grant}


async def stream_user(agent,token):
    """Revalidate an already consumed stream call; cannot execute another call."""
    response=await agent._plaza_request_async('post','/api/call-grants/status',
        headers=agent._ensure_token_valid(),json={'token':token},timeout=10,retries=0)
    if response.status_code!=200:raise HTTPException(401,'Stream call grant expired or revoked.')
    user=response.json().get('user') or {}
    if not user.get('id'):raise HTTPException(401,'Stream requires a user grant.')
    return user
