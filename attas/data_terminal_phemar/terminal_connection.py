"""Persistent Plaza enrollment; installation credentials stay in the local vault."""
import asyncio
import hashlib
import secrets
import time

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from prompits.core.client_sessions import challenge


class TerminalEnrollment:
    # The access status should report an unreachable Plaza quickly; reads keep a normal limit.
    STATUS_TIMEOUT = httpx.Timeout(10.0, connect=3.0)
    # Plaza's access token lasts at most an hour and this watcher looks every 30
    # seconds. Renewing this long before it lapses keeps requests from ever
    # carrying a lapsed token; renewing only after a 401 left a window of failed
    # requests at every expiry.
    RENEW_BEFORE = 90

    def __init__(self, store, client, registration, scope, connection, expected_owner=None, terminal=None):
        self.store, self.client, self.registration = store, client, registration
        self.scope, self.connection = scope, connection
        # An account's own request (account_access.py) must be made by that account, and names the installation it
        # belongs to, so the Plaza owner can block the whole Terminal.
        self.expected_owner, self.terminal = expected_owner, terminal
        self.key = hashlib.sha256(scope.encode()).hexdigest()
        self.root = connection['plazaOrigin'] + '/api/agent-trust'
        self.lock = asyncio.Lock()

    def saved(self):
        try:
            return self.store.decrypt(self.store.get('_installation', 'enrollment', self.key)['secret'])
        except HTTPException as exc:
            if exc.status_code != 404:
                raise
            return None

    def save(self, value):
        self.store.put('_installation', 'enrollment', self.key, {'secret': self.store.encrypt(value)})

    async def post(self, path, data, timeout=None, headers=None):
        response = await self.client.post(self.root + path, json=data, **({'timeout': timeout} if timeout else {}),
                                          **({'headers': headers} if headers else {}))
        if not response.is_success:
            detail = 'Plaza connection unavailable. Your saved request will be retried.'
            try:
                detail = response.json().get('detail') or detail
            except ValueError:
                pass
            raise HTTPException(response.status_code, detail)
        return response.json()

    def public(self, saved):
        state = saved.get('state') or {}
        return {'status': 'scheduled' if state.get('accessState')=='scheduled' else state.get('status', 'draft'), 'id': state.get('id'),
                'installation': self.scope.rsplit('|', 1)[-1], 'owner': state.get('requester'),
                'ownerEmail': state.get('email'), 'enabled': saved.get('enabled', False),
                'authorizationUrl': self.connection['authorizationUrl'] + '?request=' + state['id'] if state.get('id') else None,
                'notifications': state.get('notifications', []), 'validFrom':state.get('validFrom'),'validUntil':state.get('validUntil'),
                'blocked': state.get('blocked')}

    def last_known(self):
        """The saved request status, read locally without contacting Plaza."""
        saved = self.saved()
        if not saved or not (saved.get('state') or {}).get('id'):
            return {'status': 'not-requested', 'saved': True}
        return {**self.public(saved), 'saved': True}

    async def begin(self, here=None):
        async with self.lock:
            if self.store.terminal_login(self.scope):
                raise HTTPException(409, 'This Terminal is already signed in.')
            saved = self.saved() or {'verifier': secrets.token_urlsafe(48)}
            saved['enabled'] = True
            # Plaza returns the request to this Terminal when its user requests trust (connect_return), so a
            # review link someone else's agent started cannot make this user request trust for that agent.
            returned = secrets.token_urlsafe(24) if here else None
            if returned:
                saved['returnState'] = hashlib.sha256(returned.encode()).hexdigest()
                saved.pop('ticket', None)
            self.save(saved)  # Save before sending; a lost response can be retried.
            state = await self.post('/begin', {'agentIdentity': self.scope.rsplit('|', 1)[-1],
                **({'terminal': self.terminal} if self.terminal else {}),
                'agentName': self.registration.agent_name[:80], 'code_challenge': challenge(saved['verifier']),
                **({'return_to': here + '/api/auth/connect/return?state=' + returned} if returned else {})})
            saved['state'] = state
            self.save(saved)
            return self.public(saved)

    async def submit_with_session(self, access_token):
        """Send this Terminal's request on for the account signed in with `access_token`: Plaza takes it from that
        account's own Terminal session, and approves it on its own where it approves automatically."""
        async with self.lock:
            saved = self.saved()
            if not saved or not (saved.get('state') or {}).get('id'):
                return None
            state = await self.post('/session-submit', {'id': saved['state']['id'], 'code_verifier': saved['verifier']},
                                    headers={'Authorization': 'Bearer ' + access_token})
            saved['state'] = state
            self.save(saved)
            return state

    async def returned(self, state, ticket):
        """Plaza's ticket for this Terminal's own request: keep it and send the request on to the Plaza owner."""
        async with self.lock:
            saved = self.saved()
            expected = (saved or {}).get('returnState')
            if not expected or not 20 <= len(ticket) <= 128 or not secrets.compare_digest(expected, hashlib.sha256(state.encode()).hexdigest()):
                return 'unknown'
            saved['ticket'] = ticket
            saved.pop('returnState')
            self.save(saved)
            try:await self.confirm(saved)
            except (httpx.HTTPError, HTTPException):return 'retry'  # status() confirms with the kept ticket
            return 'requested'

    async def confirm(self, saved):
        state = await self.post('/confirm', {'id': saved['state']['id'], 'code_verifier': saved['verifier'], 'ticket': saved['ticket']})
        saved['state'] = state
        saved.pop('ticket', None)
        self.save(saved)
        return state

    async def status(self, *, connect=False):
        async with self.lock:
            saved = self.saved()
            if not saved or not (saved.get('state') or {}).get('id'):
                return {'status': 'not-requested'}
            try:
                state = await self.post('/status', {'id': saved['state']['id'], 'code_verifier': saved['verifier']},
                                        timeout=self.STATUS_TIMEOUT)
            except (httpx.HTTPError, HTTPException) as exc:
                if isinstance(exc, HTTPException) and exc.status_code < 500:
                    raise
                # Plaza is unreachable or failing: report the last saved stage
                # unchanged, without signing in or ending any session.
                return {**self.public(saved), 'offline': True}
            saved['state'] = state
            self.save(saved)
            if (self.expected_owner and state.get('requester') and state['requester'] != self.expected_owner
                    and state['status'] in ('draft', 'pending', 'approved', 'scheduled')):
                # Another account requested this window's access: withdraw it, so the window's own account can.
                try:
                    await self.post('/disconnect', {'id': state['id'], 'code_verifier': saved['verifier']})
                except (httpx.HTTPError, HTTPException):
                    pass
                # Plaza ties every request for this identity to the same proof, so the verifier stays.
                self.save({'verifier': saved['verifier']})
                from .account_access import WRONG_ACCOUNT
                raise HTTPException(409, WRONG_ACCOUNT)
            if state['status'] == 'draft' and saved.get('ticket'):
                try:state = await self.confirm(saved)
                except (httpx.HTTPError, HTTPException):return {**self.public(saved), 'offline': True}
            if state['status'] in ('revoked','expired') or state.get('accessState')=='scheduled':
                binding = self.store.terminal_login(self.scope)
                if binding:
                    self.store.end_session(binding['session_id'])
                    self.store.release_terminal(self.scope, binding['owner'])
                    await self.registration.stop()
                    self.registration._token = None
            if connect and saved.get('enabled') and state['status'] == 'approved' and state.get('accessState','active')=='active':
                binding = self.store.terminal_login(self.scope)
                session = self.store.read_session(binding['session_id']) if binding else None
                expires = (session or {}).get('expires_at')
                if session and isinstance(expires, (int, float)) and expires - time.time() < self.RENEW_BEFORE:
                    session = None
                if session:
                    me = await self.client.get(self.connection['plazaOrigin'] + '/api/ui_auth/me',
                                               headers={'Authorization': 'Bearer ' + session['access_token']})
                    if me.status_code == 403:
                        raise HTTPException(403, 'Plaza account access is disabled.')
                    if me.status_code >= 500:
                        raise HTTPException(502, 'Plaza is unavailable; your session is preserved.')
                    if me.status_code == 401:
                        session = None
                    else:
                        me.raise_for_status()
                if not session:
                    result = await self.post('/claim', {'id': state['id'], 'code_verifier': saved['verifier']})
                    if result['status'] != 'approved':
                        saved['state']['status'] = result['status']
                        self.save(saved)
                        return self.public(saved)
                    session = result['session']
                    me = await self.client.get(self.connection['plazaOrigin'] + '/api/ui_auth/me',
                                               headers={'Authorization': 'Bearer ' + session['access_token']})
                    me.raise_for_status()
                    owner = me.json()['user']['id']
                    if owner != state['requester'] or result['owner'] != owner:
                        raise HTTPException(403, 'Approved agent owner does not match the login.')
                    sid = binding['session_id'] if binding else self.store.session(session)
                    if binding:
                        # Keep the local HttpOnly session identifier stable when
                        # renewing credentials, including after a lost response.
                        with self.store.connect() as db:
                            db.execute('INSERT OR REPLACE INTO sessions VALUES(?,?,?)',
                                       (sid, self.store.encrypt(session), time.time()+86400))
                    try:
                        self.store.bind_terminal(self.scope, owner, sid)
                    except HTTPException:
                        self.store.end_session(sid)
                        raise
                    await self.registration.stop()
                    await self.registration.start()
                return {**self.public(saved), 'status': 'signed-in'}
            return self.public(saved)

    async def suspend(self):
        async with self.lock:
            saved = self.saved()
            if saved:
                saved['enabled'] = False
                self.save(saved)

    async def watch(self):
        while True:
            delay=30
            try:
                saved = self.saved()
                if saved and saved.get('enabled'):
                    await self.status(connect=True)
                    state=(self.saved() or {}).get('state',{})
                    boundaries=[t-time.time() for t in (state.get('validFrom'),state.get('validUntil')) if isinstance(t,(float,int)) and t>time.time()]
                    if boundaries:delay=min(30,max(.05,min(boundaries)))
            except Exception:
                pass  # Durable request survives offline periods; UI shows errors.
            await asyncio.sleep(delay)


def install_connection_routes(app, store, client, registration, scope, cookie, connection, principal, *, registry_path=None, accounts=None):
    enrollment = TerminalEnrollment(store, client, registration, scope, connection)
    app.state.enrollment = enrollment

    async def target(request):
        """A window signed in to an account other than the installation's first approver works on that account's own
        request (account_access.py); every other caller works on the installation's."""
        if accounts:
            from .window_sessions import window_context
            if window_context(request):
                user = await principal(request, False)
                if user and accounts.other(user['id']):
                    return accounts.enrollment(user['id']), str(user['id'])
        return enrollment, None
    import os
    registry_path = registry_path or os.environ.get('ATTAS_TRUSTED_PLAZAS_FILE')
    if registry_path:
        from .trusted_plazas import TrustedPlazas, install_trusted_plazas
        install_trusted_plazas(app, TrustedPlazas(registry_path), enrollment)

    @app.post('/api/auth/connect')
    async def begin(request: Request):
        if registry_path:
            from pathlib import Path
            TrustedPlazas(registry_path).remember(connection['publicOrigin'], scope.rsplit('|',1)[-1], Path(store.path).parent.parent)
        from .local_access import own_origin
        chosen, account = await target(request)
        value = await chosen.begin(own_origin(request))
        if account:
            accounts.watch(account)
        return value

    @app.get('/api/auth/connect/return')
    async def connect_return(request: Request):
        """The browser that requested trust on Plaza arrives here with this Terminal's ticket."""
        state, ticket = request.query_params.get('state', ''), request.query_params.get('ticket', '')
        # Plaza's tab carries no window: the kept return state names the request it belongs to.
        chosen = (accounts.find_return(state) if accounts else None) or enrollment
        outcome = await chosen.returned(state, ticket)
        if outcome == 'requested':
            # A Plaza that approves on its own has already done so: this tab says so and closes, and the
            # Terminal window that asked signs itself in.
            try:
                if (await chosen.status()).get('status') == 'approved':
                    outcome = 'approved'
            except Exception:
                pass  # the window's own check reports Plaza's answer
        page = '/signin-complete.html' if outcome == 'approved' else '/signin-complete.html?status=' + ('unknown' if outcome == 'unknown' else 'requested')
        if outcome == 'requested':
            # The request now waits for the Plaza owner: this tab goes back to Plaza's own page for it, which says so in
            # the viewer's language and keeps showing its status. It is this Terminal's configured
            # Plaza page, never an address from the request.
            review = chosen.public(chosen.saved() or {}).get('authorizationUrl')
            if review:
                page = review
        return RedirectResponse(page, status_code=303, headers={'Cache-Control':'no-store','Referrer-Policy':'no-referrer'})

    @app.get('/api/auth/connect/status')
    async def connection_status(request: Request):
        chosen, account = await target(request)
        value = await chosen.status()
        return value if account else await signed_out_name(request, value)

    async def signed_out_name(request, value):
        """A signed-out window asks to sign in as the account it last used, not the installation's first approver.
        With none known, it names no one once other accounts use this Terminal."""
        if not value.get('ownerEmail') or await principal(request, False):
            return value
        from .window_sessions import window_context, window_scope
        window = window_context(request)
        try:
            last = store.get('_installation', 'window-last-account', window_scope(scope, window)) if window else None
        except HTTPException:
            last = None
        if last and last.get('email'):
            return {**value, 'ownerEmail': last['email']}
        if accounts and accounts.accounts():
            return {**value, 'ownerEmail': None}
        return value

    @app.get('/api/auth/connect/saved')
    async def saved_status(request: Request):
        chosen, account = await target(request)
        value = chosen.last_known()
        return value if account else await signed_out_name(request, value)

    @app.post('/api/auth/connect/poll')
    async def poll(request: Request):
        chosen, account = await target(request)
        value = await chosen.status(connect=True)
        if account:
            # The window is already signed in to this account; its approval starts the account's own registration.
            if value['status'] == 'signed-in':
                await accounts.registration(account).start()
            return JSONResponse({**value, 'account': True})
        response = JSONResponse(value)
        if value['status'] == 'signed-in':
            binding = store.terminal_login(scope)
            response.set_cookie(cookie, binding['session_id'], httponly=True, samesite='strict', max_age=86400)
        return response

    @app.get('/api/network')
    async def status(request: Request):
        await principal(request)
        transport = getattr(client, 'public_network', None)
        return {'mode': 'direct-https', 'plaza': connection['publicOrigin'],
                'preferLocal': transport.prefer_local if transport else False,
                'routes': list(transport.route_status.values()) if transport else []}

    @app.put('/api/network')
    async def settings(request: Request):
        user = await principal(request)
        value = await request.json()
        if set(value) != {'preferLocal'} or type(value['preferLocal']) is not bool:
            raise HTTPException(422, 'Choose whether to prefer the local network.')
        store.put(user['id'], 'network', 'preferences', value)
        transport = getattr(client, 'public_network', None)
        if transport:
            transport.prefer_local = value['preferLocal']
        return value
