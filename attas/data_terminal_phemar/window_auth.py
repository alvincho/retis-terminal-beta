"""Independent browser-window logins: Plaza device sign-in, through a browser or with the account and password typed
into the Terminal."""
import asyncio
import hashlib
import secrets
import sys
import time
import os
from urllib.parse import parse_qs, quote, urlsplit
import httpx
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from prompits.core.client_sessions import challenge
from .window_sessions import WINDOW, saved_tabs, window_context, window_cookie, window_scope
from .remembered_login import LIFETIME


def install_window_auth(app, principal, store, scope, cookie, client, plaza_url, connection, *, auth_origin=None, remembered_login=None,
                        account_access=None):
    async def first_approval(enrollment, token, here):
        """Nobody has approved this Terminal yet: the first account to sign in sends the installation's own request, as
        itself, and Plaza decides (approving it on its own where it does). No Plaza page is needed (Alvin, 2026-11-13)."""
        state = await enrollment.begin(here)
        if state.get('status') == 'draft':
            await enrollment.submit_with_session(token)
        await enrollment.status(connect=True)

    async def approve_at_sign_in(request, owner, sid):
        """The Terminal checks the signed-in account's approval now: one without it asks for it at once, as itself
        (account_access.ensure), so the window is ready where Plaza approves automatically."""
        if not account_access or not owner:
            return
        first = not account_access.owner()
        if not first and (not account_access.other(owner) or account_access.approved(owner)):
            return
        token = (store.read_session(sid) or {}).get('access_token')
        if not token:
            return
        from .local_access import own_origin
        if first:
            enrollment = getattr(request.app.state, 'enrollment', None)
            if enrollment is None or store.terminal_login(scope):
                return
            try:await asyncio.wait_for(first_approval(enrollment, token, own_origin(request)), 12)
            except (asyncio.TimeoutError, HTTPException, httpx.HTTPError) as exc:
                # The panel shows the request's stage; Plaza's answer is logged for the operator.
                print(time.strftime('%Y-%m-%d %H:%M:%S%z') + ' WARNING:  Plaza did not take this Terminal\'s first request at sign-in: '
                      + (str(exc.status_code) + ' ' + str(exc.detail)[:200] if isinstance(exc, HTTPException) else type(exc).__name__),
                      file=sys.stderr, flush=True)
            return
        try:await asyncio.wait_for(account_access.ensure(str(owner), token, own_origin(request)), 12)
        except asyncio.TimeoutError:pass

    with store.connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS legacy_window_claims(source TEXT PRIMARY KEY,window TEXT)')

    def required_window(request):
        window = window_context(request)
        if not window:raise HTTPException(422, 'A Terminal window is required.')
        return window

    def signed_in(request, window, sid, max_age=None, *, remember=False):
        if max_age is None:
            binding=store.terminal_login(window_scope(scope,window))
            max_age=max(1,int(binding['expires']-time.time())) if binding else 1
        response = JSONResponse({'status':'signed-in'})
        response.set_cookie(window_cookie(cookie,window),sid,httponly=True,
                            samesite='strict',max_age=max_age,secure=request.url.scheme=='https')
        response.headers['Cache-Control']='no-store'
        if remember and remembered_login:
            binding=store.terminal_login(window_scope(scope,window))
            if binding:remembered_login.issue(request,response,binding['owner'],sid)
        return response

    @app.get('/api/auth/resumable-windows')
    async def resumable_windows(request: Request):
        """Windows a new tab may take up: those this browser is signed in to as its remembered account, last used
        first. Only windows whose login cookie this browser holds are named, so another browser learns nothing."""
        owner = remembered_login.owner(request) if remembered_login else None
        prefix, now, windows = cookie + '_w_', time.time(), []
        with store.connect() as db:
            for name, sid in request.cookies.items() if owner else ():
                window = name[len(prefix):] if name.startswith(prefix) else ''
                if WINDOW.fullmatch(window) and db.execute('SELECT 1 FROM terminal_logins l JOIN sessions s ON s.id=l.session_id '
                        'WHERE l.terminal=? AND l.owner=? AND l.session_id=? AND l.expires>? AND s.expires>?',
                        (window_scope(scope, window), owner, sid, now, now)).fetchone():
                    windows.append(window)
        used = {tab['window']:tab['lastUsed'] for tab in saved_tabs(store, {'id':owner})} if windows else {}
        return JSONResponse({'windows':sorted(windows, key=lambda window:used.get(window, 0), reverse=True)},
                            headers={'Cache-Control':'no-store'})

    @app.get('/api/auth/window-signin/status')
    async def signin_status(request: Request):
        required_window(request)
        user=await principal(request,False)
        return JSONResponse({'status':'signed-in' if user else 'signed-out'},headers={'Cache-Control':'no-store'})

    async def reusable_logins(request, destination, selected=None):
        """Only this browser's proven cookies, bound to this exact Plaza/Terminal.

        Do not search server sessions by owner or use an agent/installation login.
        A non-secret window selector never suffices without its HttpOnly cookie.
        """
        prefix=cookie+'_w_'
        accounts={}
        for name,sid in request.cookies.items():
            if not name.startswith(prefix):continue
            source=name[len(prefix):]
            if not WINDOW.fullmatch(source) or source==destination or (selected and source!=selected):continue
            binding=store.terminal_login(window_scope(scope,source))
            if not binding or not sid or not secrets.compare_digest(binding['session_id'],sid):continue
            if binding['owner'] in accounts or not store.read_session(sid):continue
            # Reuse the normal verifier, including its locked token refresh, with
            # a separate request context so the destination stays signed out.
            source_scope=dict(request.scope)
            source_scope['state']={}
            source_scope['query_string']=b''
            source_scope['headers']=[(k,v) for k,v in request.scope['headers'] if k.lower()!=b'x-terminal-window']+[(b'x-terminal-window',source.encode())]
            user=await principal(Request(source_scope),False)
            if user and user['id']==binding['owner']:
                accounts[user['id']]={'window':source,'name':user['name'],'owner':user['id'],'sid':sid}
        return list(accounts.values())

    def reuse_login(request, destination, account):
        """Share the verified session, preserving its expiry and refresh lock."""
        sid=account['sid'];now=time.time();target=window_scope(scope,destination)
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            source=db.execute('SELECT * FROM terminal_logins WHERE terminal=?',(window_scope(scope,account['window']),)).fetchone()
            session=db.execute('SELECT expires FROM sessions WHERE id=?',(sid,)).fetchone()
            if not source or source['session_id']!=sid or source['owner']!=account['owner'] or not session or min(source['expires'],session['expires'])<=now:
                raise HTTPException(401,'This Terminal login is no longer available.')
            existing=db.execute('SELECT * FROM terminal_logins WHERE terminal=? AND expires>?',(target,now)).fetchone()
            if existing and existing['owner']!=account['owner']:
                raise HTTPException(409,'Sign out of this window before switching accounts.')
            expires=min(source['expires'],session['expires'])
            db.execute('INSERT OR REPLACE INTO terminal_logins VALUES(?,?,?,?)',(target,account['owner'],sid,expires))
        return signed_in(request,destination,sid,max(1,int(expires-now)),remember=True)

    def sso_origin():
        return (connection['publicOrigin'] if connection else auth_origin or os.environ.get('ATTAS_PLAZA_AUTH_ORIGIN') or plaza_url).rstrip('/')

    def signin_page(code):
        """Plaza's own sign-in page, at the origin this browser reaches it on."""
        origin = sso_origin() if connection or auth_origin or os.environ.get('ATTAS_PLAZA_AUTH_ORIGIN') else ''
        host = os.environ.get('ATTAS_PUBLIC_ORIGIN', '').rstrip('/')
        # A public host gateway serves the page; a local one leaves it to Plaza.
        if not origin and host and urlsplit(host).hostname not in ('127.0.0.1', 'localhost', '::1'):origin = host
        return (origin or plaza_url.rstrip('/')) + '/client-signin?code=' + quote(code)

    @app.post('/api/auth/window-signin/approved')
    async def approved(request: Request):
        """Sign this window in with the login Plaza approved for this Terminal, in any browser."""
        window = required_window(request)
        binding = store.terminal_login(scope)
        if not binding:raise HTTPException(401, 'This Terminal login is no longer available.')
        # Verify (and refresh) the approved login with Plaza before sharing it.
        probe = dict(request.scope)
        probe['state'] = {'selected_session_id':binding['session_id'],'expected_session_owner':binding['owner'],
                          'remembered_login_probe':True}
        user = await principal(Request(probe), False)
        if not user or user['id'] != binding['owner']:
            raise HTTPException(401, 'This Terminal login is no longer available.')
        response = reuse_login(request, window, {'window':'','owner':binding['owner'],'sid':binding['session_id']})
        await approve_at_sign_in(request, binding['owner'], binding['session_id'])
        return response

    @app.post('/api/auth/window-session')
    async def adopt(request: Request):
        """Restore this browser's last login without changing signed-in windows."""
        window = required_window(request)
        if request.cookies.get(window_cookie(cookie,window)):
            response=JSONResponse({'status':'ready'},headers={'Cache-Control':'no-store'})
            # Upgrade an already signed-in browser's cache without extending an
            # existing credential or ignoring an explicit signed-out marker.
            if remembered_login and not request.cookies.get(remembered_login.cookie):
                user=await principal(request,False)
                if user:
                    binding=store.terminal_login(window_scope(scope,window))
                    remembered_login.issue(request,response,user['id'],binding['session_id'])
            return response
        if remembered_login and request.cookies.get(remembered_login.cookie):
            if request.cookies[remembered_login.cookie]=='signed-out':
                return JSONResponse({'status':'guest'},headers={'Cache-Control':'no-store'})
            saved=await remembered_login.restore(request,principal)
            if saved:
                user,sid,expires=saved
                response=signed_in(request,window,sid,max(1,int(expires-time.time())))
                await approve_at_sign_in(request, user['id'] if isinstance(user,dict) else user, sid)
                return response
            response=JSONResponse({'status':'guest'},headers={'Cache-Control':'no-store'})
            remembered_login.forget(request,response)
            return response
        sid = request.cookies.get(cookie,'')
        saved = store.read_session(sid)
        if not saved:return {'status':'guest'}
        me = await client.get(plaza_url+'/api/ui_auth/me',headers={'Authorization':'Bearer '+saved['access_token']})
        if not me.is_success:return {'status':'guest'}
        owner = (me.json().get('user') or {}).get('id')
        if not owner:return {'status':'guest'}
        marker = hashlib.sha256((scope+'|'+sid).encode()).hexdigest()
        window_sid = window_scope(scope,window)
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM legacy_window_claims WHERE source=?',(marker,)).fetchone():
                return {'status':'guest'}
            session = db.execute('SELECT expires FROM sessions WHERE id=? AND expires>?',(sid,time.time())).fetchone()
            if not session:return {'status':'guest'}
            db.execute('INSERT INTO legacy_window_claims VALUES(?,?)',(marker,window))
            # Refer to the existing encrypted session so refresh-token rotation
            # uses the same lock, rather than copying a single-use refresh token.
            db.execute('INSERT OR REPLACE INTO terminal_logins VALUES(?,?,?,?)',(window_sid,str(owner),sid,session['expires']))
        return signed_in(request,window,sid)

    @app.post('/api/auth/window-signin')
    async def begin(request: Request):
        window = required_window(request)
        if await principal(request,False):raise HTTPException(409, 'Sign out of this window before switching accounts.')
        body = await request.json()
        reuse = isinstance(body, dict) and body.get('reuse', True) and not body.get('provider')
        provider = body.get('provider') if isinstance(body, dict) else None
        if reuse:
            selected=body.get('sourceWindow')
            if selected is not None and (not isinstance(selected,str) or not WINDOW.fullmatch(selected)):
                raise HTTPException(422,'Choose an available account.')
            accounts=await reusable_logins(request,window,selected)
            if selected and not accounts:raise HTTPException(401,'This Terminal login is no longer available.')
            if len(accounts)==1:
                response=reuse_login(request,window,accounts[0])
                await approve_at_sign_in(request, accounts[0]['owner'], accounts[0]['sid'])
                return response
            if accounts:return {'status':'choose-account','accounts':[{'window':a['window'],'name':a['name']} for a in accounts]}
        if provider:
            config = await client.get(plaza_url+'/api/ui_auth/config')
            if not config.is_success:raise HTTPException(502, 'Plaza sign-in is unavailable.')
            if provider not in (config.json().get('oauth_providers') or []):raise HTTPException(422, 'Choose an available identity provider.')
        verifier = secrets.token_urlsafe(48)
        proof = secrets.token_urlsafe(32)
        # Plaza sends the approval back to this Terminal (RFC 8252), so a sign-in link someone else started
        # and sent to this user never becomes a session for them.
        returned = secrets.token_urlsafe(24)
        from .local_access import own_origin
        here = own_origin(request)
        response = await client.post(plaza_url+'/api/client-auth/device',json={
            'client_name':app.title+' window '+window[:8], 'code_challenge':challenge(verifier),
            'session_lifetime_seconds':LIFETIME, 'return_to':here+'/api/auth/window-signin/return?state='+returned})
        if not response.is_success:raise HTTPException(response.status_code,'Plaza browser sign-in is unavailable.')
        grant = response.json()
        store.put('_installation','window-signin',window_scope(scope,window),{
            'secret':store.encrypt({'verifier':verifier,'device':grant['device_code'],'browserProof':hashlib.sha256(proof.encode()).hexdigest(),'expires':time.time()+grant['expires_in']})})
        for stale in store.list('_installation','window-signin-return'):
            if stale['expires'] <= time.time():store.delete('_installation','window-signin-return',stale['id'])
        store.put('_installation','window-signin-return',hashlib.sha256(returned.encode()).hexdigest(),
                  {'key':window_scope(scope,window),'expires':time.time()+grant['expires_in']})
        if reuse:
            path = '/plaza-api/api/client-auth/sso-frame' if connection else '/api/client-auth/sso-frame'
            payload = {'status':'reuse-session','ssoUrl':sso_origin()+path,'userCode':grant['user_code'],
                       'nonce':secrets.token_urlsafe(24),'expiresIn':grant['expires_in'],'interval':grant['interval']}
        elif not provider:
            # Another account signs in on Plaza's own page, with any method Plaza offers.
            payload = {'status':'pending','authorizationUrl':signin_page(grant['user_code']),
                       'expiresIn':grant['expires_in'],'interval':grant['interval']}
        else:
            oauth = await client.post(plaza_url+'/api/client-auth/oauth',json={
                'device_code':grant['device_code'],'code_verifier':verifier,'provider':provider})
            if not oauth.is_success:
                store.delete('_installation','window-signin',window_scope(scope,window))
                raise HTTPException(oauth.status_code, 'Provider sign-in could not be started.')
            payload = {'status':'pending','authorizationUrl':oauth.json()['authorization_url'],
                       'expiresIn':grant['expires_in'],'interval':grant['interval']}
        response=JSONResponse(payload)
        response.set_cookie(window_cookie(cookie+'_signin',window),proof,httponly=True,
                            samesite='strict',max_age=grant['expires_in'],secure=request.url.scheme=='https')
        return response

    @app.get('/api/auth/window-signin/return')
    async def returned(request: Request):
        """Plaza's approval arrives here in the browser that approved it; its ticket completes this Terminal's sign-in only."""
        state, ticket = request.query_params.get('state', ''), request.query_params.get('ticket', '')
        key = hashlib.sha256(state.encode()).hexdigest()
        try:target = store.get('_installation','window-signin-return',key)
        except HTTPException:target = None
        page = '/signin-complete.html'
        if target and target['expires'] > time.time() and 20 <= len(ticket) <= 128:
            store.delete('_installation','window-signin-return',key)
            try:
                grant = store.decrypt(store.get('_installation','window-signin',target['key'])['secret'])
                store.put('_installation','window-signin',target['key'],{'secret':store.encrypt({**grant,'ticket':ticket})})
            except HTTPException:page += '?status=expired'
        else:page += '?status=unknown'
        return RedirectResponse(page, status_code=303, headers={'Cache-Control':'no-store','Referrer-Policy':'no-referrer'})

    @app.post('/api/auth/window-signin/poll')
    async def poll(request: Request):
        window = required_window(request)
        key = window_scope(scope,window)
        try:grant = store.decrypt(store.get('_installation','window-signin',key)['secret'])
        except HTTPException:raise HTTPException(404, 'Start a new Plaza sign-in.')
        proof=request.cookies.get(window_cookie(cookie+'_signin',window),'')
        if not proof or not secrets.compare_digest(hashlib.sha256(proof.encode()).hexdigest(),grant['browserProof']):
            raise HTTPException(403, 'Continue sign-in from the window that requested it.')
        if grant['expires'] <= time.time():
            store.delete('_installation','window-signin',key)
            raise HTTPException(401, 'Plaza sign-in request expired.')
        # A late approval must not replace a login performed while this request
        # was pending. Revalidate the account before binding any cookie.
        if await principal(request,False):raise HTTPException(409, 'This window is already signed in.')
        response = await client.post(plaza_url+'/api/client-auth/token',json={
            'device_code':grant['device'],'code_verifier':grant['verifier'],**({'ticket':grant['ticket']} if grant.get('ticket') else {})})
        if not response.is_success:raise HTTPException(response.status_code,'Plaza sign-in could not be completed.')
        result = response.json()
        if result.get('error'):
            return {'status':result['error'],'interval':result.get('interval',5)}
        return await complete_sign_in(request, window, result.get('session') or {})

    async def complete_sign_in(request, window, session):
        """Every sign-in ends here with the window's own Plaza session: check the account, bind the window, then check
        the account's approval of this Terminal."""
        key = window_scope(scope,window)
        me = await client.get(plaza_url+'/api/ui_auth/me',headers={'Authorization':'Bearer '+session.get('access_token','')})
        if me.status_code == 403:
            # The Plaza owner blocked this account's Terminals, or disabled the account: say so; no further access.
            try:detail = me.json().get('detail')
            except ValueError:detail = None
            raise HTTPException(403, detail if isinstance(detail, str) and detail else 'This account cannot use this Terminal.')
        if not me.is_success:raise HTTPException(401, 'Plaza identity is unavailable.')
        owner = (me.json().get('user') or {}).get('id')
        if not owner:raise HTTPException(401, 'Plaza identity is unavailable.')
        sid = store.session(session,lifetime=LIFETIME)
        try:store.bind_terminal(key,str(owner),sid)
        except HTTPException:
            store.end_session(sid)
            raise
        try:store.delete('_installation','window-signin',key)  # a browser sign-in this window had started ends here too
        except HTTPException:pass
        await approve_at_sign_in(request, str(owner), sid)
        response=signed_in(request,window,sid,remember=True)
        response.delete_cookie(window_cookie(cookie+'_signin',window))
        return response

    async def plaza_answer(response, fallback):
        """Plaza's own reason for a refusal, as the panel shows it."""
        try:detail = response.json().get('detail')
        except ValueError:detail = None
        return detail if isinstance(detail, str) and detail else fallback

    def account_fields(body, names, optional=()):
        if not isinstance(body, dict) or set(body) - set(names) - set(optional):
            raise HTTPException(422, 'Enter your Plaza account and password.')
        for name in names:
            if not isinstance(body.get(name), str) or not 0 < len(body[name].strip() if name != 'password' else body[name]) <= 500:
                raise HTTPException(422, 'Enter your Plaza account and password.')
        for name in optional:
            if body.get(name) is not None and (not isinstance(body[name], str) or len(body[name]) > 120):
                raise HTTPException(422, 'Enter your Plaza account and password.')
        return body

    @app.post('/api/auth/window-signin/password')
    async def password_signin(request: Request):
        """Sign this window in with a Plaza account and password typed into the Terminal, with no browser page (Alvin,
        2026-11-13). The Terminal makes the same proof-bound request a browser sign-in makes and approves it with a short
        Plaza login made from the password, then keeps only the window's own revocable session, as a browser sign-in
        does. The password and that short login are never stored or logged."""
        window = required_window(request)
        body = account_fields(await request.json(), ('identifier', 'password'))
        if await principal(request,False):raise HTTPException(409, 'Sign out of this window before switching accounts.')
        verifier = secrets.token_urlsafe(48)
        from .local_access import own_origin
        response = await client.post(plaza_url+'/api/client-auth/device',json={
            'client_name':app.title+' window '+window[:8], 'code_challenge':challenge(verifier), 'session_lifetime_seconds':LIFETIME,
            # Bound to this Terminal like a browser sign-in. Plaza's approval answer carries the ticket; no browser visits it.
            'return_to':own_origin(request)+'/api/auth/window-signin/return?state='+secrets.token_urlsafe(24)})
        if not response.is_success:raise HTTPException(502 if response.status_code >= 500 else response.status_code, 'Plaza sign-in is unavailable.')
        grant = response.json()
        signin = await client.post(plaza_url+'/api/ui_auth/signin',json={'identifier':body['identifier'].strip(),'password':body['password']})
        if signin.status_code == 429:raise HTTPException(429, 'Too many sign-in attempts. Wait a minute, then try again.')
        if signin.status_code == 403:raise HTTPException(403, await plaza_answer(signin, 'This account cannot sign in.'))
        if signin.status_code >= 500:raise HTTPException(502, 'Plaza sign-in is unavailable.')
        if not signin.is_success:raise HTTPException(401, 'Wrong account or password.')
        login = ((signin.json() or {}).get('session') or {}).get('access_token')
        if not login:raise HTTPException(502, 'Plaza sign-in is unavailable.')
        approval = await client.post(plaza_url+'/api/client-auth/approve',headers={'Authorization':'Bearer '+login},
                                     json={'user_code':grant['user_code'],'approved':True})
        if not approval.is_success:
            raise HTTPException(approval.status_code if approval.status_code in (401, 403) else 502,
                                await plaza_answer(approval, 'Plaza sign-in could not be completed.'))
        ticket = parse_qs(urlsplit((approval.json() or {}).get('redirect') or '').query).get('ticket', [''])[0]
        exchanged = await client.post(plaza_url+'/api/client-auth/token',json={
            'device_code':grant['device_code'],'code_verifier':verifier,**({'ticket':ticket} if ticket else {})})
        result = exchanged.json() if exchanged.is_success else {}
        if not (result.get('session') or {}).get('access_token'):raise HTTPException(502, 'Plaza sign-in could not be completed.')
        return await complete_sign_in(request, window, result['session'])

    @app.post('/api/auth/window-signin/register')
    async def register(request: Request):
        """Create a Plaza account from the Terminal. Plaza sends the confirmation email in the Terminal's language; once
        it is confirmed, the account signs in here with its password."""
        required_window(request)
        body = account_fields(await request.json(), ('email', 'password'), ('displayName', 'language'))
        response = await client.post(plaza_url+'/api/ui_auth/signup',json={
            'email':body['email'].strip(),'password':body['password'],
            **({'display_name':body['displayName'].strip()} if (body.get('displayName') or '').strip() else {}),
            **({'language':body['language']} if body.get('language') else {})})
        if response.status_code == 429:raise HTTPException(429, 'Too many attempts. Wait a minute, then try again.')
        if response.status_code >= 500:raise HTTPException(502, 'Plaza sign-up is unavailable.')
        if not response.is_success:raise HTTPException(response.status_code, await plaza_answer(response, 'Plaza could not create this account.'))
        result = response.json() or {}
        return {'status':'registered','confirmationRequired':bool(result.get('email_confirmation_required')),
                'confirmationSent':bool(result.get('email_confirmation_sent'))}

    @app.post('/api/auth/window-signin/email')
    async def account_email(request: Request):
        """Ask Plaza to email this account: a new confirmation link, or a link to reset its password."""
        required_window(request)
        body = account_fields(await request.json(), ('identifier', 'purpose'), ('language',))
        if body['purpose'] not in ('confirm', 'reset'):raise HTTPException(422, 'Choose which email to send.')
        path = '/api/ui_auth/resend_confirmation' if body['purpose'] == 'confirm' else '/api/ui_auth/password/forgot'
        response = await client.post(plaza_url+path,json={'identifier':body['identifier'].strip(),
                                                          **({'language':body['language']} if body.get('language') else {})})
        if response.status_code == 429:raise HTTPException(429, 'Wait a minute before asking for another email.')
        if response.status_code >= 500:raise HTTPException(502, 'Plaza could not send the email.')
        if not response.is_success:raise HTTPException(response.status_code, await plaza_answer(response, 'Plaza could not send the email.'))
        return {'status':'sent'}
