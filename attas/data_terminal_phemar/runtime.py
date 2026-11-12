"""Data Terminal container: workspace, session and protected agent transport."""

# OBSOLETE BUSINESS BRANCHES — remove after their owning-agent cutovers.
# Keep only generic container/transport primitives; do not extend domain branches.
# See BUSINESS_LOGIC_OBSOLETE.json. This marker does not retire live routes.
from __future__ import annotations

import asyncio
import json
import os
import secrets
import time
import math
import re
import hashlib
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response, StreamingResponse

from .host_descriptor import feature, plaza_card, portable_host_content, attas_host_content
from .contracts import WEB, identifier, layout_operation, public_descriptor
from .launch_settings import terminal_channel, terminal_product, terminal_version
from .network import Network
from .store import Store

COOKIE = os.environ.get('ATTAS_SESSION_COOKIE', 'attas_b028_session')
# The Terminal's own release; a host that runs it from an archive without its tag names it.
VERSION = os.environ.get('ATTAS_RELEASE_VERSION') or terminal_version()
# Which line of builds it belongs to (public for the desktop app, dev for a checkout), and the package's product name.
CHANNEL = os.environ.get('ATTAS_RELEASE_CHANNEL') or terminal_channel()
PRODUCT = terminal_product()
UI_LANGUAGES = ('en', 'zh-Hant', 'zh-Hans', 'ja', 'es', 'fr')


def expired_session(response):
    if response.status_code==401:return True
    if response.status_code!=403:return False
    try:detail=response.json().get('detail','')
    except ValueError:return False
    return isinstance(detail,str) and 'token is expired' in detail.lower()


def create_app(kind='data-terminal', agent_id='data-terminal', port=28400, *, state_dir=None, plaza_url=None, http_client=None, plaza_context=None, allow_plaza_selection=True, web_dir=None):
    web = Path(web_dir or WEB).resolve()
    state_dir = Path(state_dir or os.environ.get('ATTAS_TERMINAL_STATE') or os.environ.get('ATTAS_B028_STATE', str(Path.home() / '.local/state/finmas/attas-b-028-local/state')))
    plaza_url = plaza_url or os.environ.get('ATTAS_PLAZA_URL') or os.environ.get('ATTAS_B028_PLAZA', 'http://127.0.0.1:28011')
    origin = f'http://127.0.0.1:{port}'
    if kind != 'data-terminal':
        raise ValueError('Data Terminal is a container. Start App Consultant through its independent agent service.')
    cookie = COOKIE+'_'+identifier(agent_id) if kind=='data-terminal' else COOKIE
    terminal_scope = plaza_url+'|'+agent_id
    from .window_sessions import window_context, window_scope, window_cookie, state_key, preferences as window_preferences
    def session_id(request):
        if hasattr(request.state,'selected_session_id'):return request.state.selected_session_id
        window=window_context(request)
        if window:return request.cookies.get(window_cookie(cookie,window),'')
        return request.cookies.get(cookie) or request.cookies.get(COOKIE, '')
    name = 'Data Terminal'
    if kind=='data-terminal' and agent_id!='data-terminal':
        # The id as is, so Plaza's request label matches the Agent identity it shows beside it.
        name='Data Terminal '+agent_id
    definition = public_descriptor(kind, name, origin, agent_id)
    store = Store(state_dir)
    from .remembered_login import RememberedLogin, LIFETIME as LOGIN_LIFETIME
    remembered_login = RememberedLogin(store, terminal_scope, cookie)
    from .local_access import LocalAccess, MESSAGE as LOCAL_ACCESS_MESSAGE
    local_access = LocalAccess(store, terminal_scope, cookie, COOKIE, port)
    extra_pulses = [{'name':'pulse_catalog','pulse_name':'pulse_catalog','description':'Authorized Plaza publication metadata.','cost':0,'input_schema':{'type':'object'}}]

    for pulse in extra_pulses:
        pulse['pulse_address'] = 'plaza://pulse/' + pulse['pulse_name']
        pulse['output_schema'] = {'type': 'object'}

    def descriptor_value():
        value = feature(definition)
        value['supported_pulses'].extend(extra_pulses)
        # The gateway authenticates these surfaces using its HttpOnly Plaza
        # session. No browser token delegation to the provider is needed.
        value['permissions']['authentication'] = {
            'scheme': 'plaza-session',
            'required_surfaces': [surface['id'] for surface in value['ui']['surfaces']],
            'raw_credentials': False,
        }
        return value

    def card(url):
        value = plaza_card(definition, url)
        from .ownership import registration_owner
        owner = registration_owner(kind, store, terminal_scope, state_dir)
        value.update(owner)
        value.setdefault('meta', {}).update(owner)
        value['version'] = VERSION
        value.setdefault('meta', {}).setdefault('supported_pulses', []).extend(extra_pulses)
        value['meta']['phemar_capabilities'] = [{**value['meta']['phemar_capabilities'][0], **descriptor_value()}]
        value['meta']['phemar_feature_descriptor'] = descriptor_value()
        value.setdefault('practices', []).extend([{'id':p,'name':p,'cost':0,'path':'/use_practice/'+p} for p in ('get_pulse_data','quote_pulse_data')])
        value.setdefault('meta', {})['data_terminal'] = {'version': VERSION, 'channel': CHANNEL, 'kind': kind, 'id': agent_id, 'ui': '/'}
        return value

    from .ownership import OwnerAwarePlazaClient
    registration = OwnerAwarePlazaClient(plaza_url, origin, state_dir / 'agents' / agent_id / 'identity.json', agent_name=name, card_factory=card)
    from .local_transport import create_http_client
    client = http_client or create_http_client(plaza_url=plaza_url)
    registration.telemetry.observe_client(client)
    # Plaza shows this Terminal by its session name; its registered name and identity do not change.
    from .display_name import DisplayName, label as display_label
    from .ownership import registration_owner
    registration.display_name = DisplayName(
        lambda: display_label(store, registration_owner(kind, store, terminal_scope, state_dir).get('owner_user_id')))
    from .public_transport import load_connection
    public_connection = plaza_context.get('connection') if plaza_context is not None else load_connection()
    if public_connection and kind == 'data-terminal':
        registration._client_factory = lambda: create_http_client(plaza_url=plaza_url,base_url=plaza_url)
        def active_session():
            binding = store.terminal_login(terminal_scope)
            return store.read_session(binding['session_id']) if binding else None
        registration.user_session = active_session
        registration.session_owner = lambda: (store.terminal_login(terminal_scope) or {}).get('owner','')
    network = Network(plaza_url, registration, client)
    if plaza_context is not None:
        registration._client_factory = plaza_context['registration_client']
        network.discovery_attachments = []
        network.consultant_archive_origin = None
        network.lifecycle_sources = set()
        if public_connection:
            network.local_source_origins = ()
            policy = public_connection.get('sourcePolicy', {})
            network.lifecycle_sources = set(policy.get('alwaysRunning', []) + policy.get('retainWhenCredentialsConfigured', []) + policy.get('publicOnDemand', []))
    plaza_selection = None
    local_only = plaza_context is None and os.environ.get('ATTAS_LOCAL_ONLY') == '1' and kind == 'data-terminal'
    discover_local = os.environ.get('PHEMACAST_LOCAL_DISCOVERY') == '1' and kind == 'data-terminal'
    local_token = secrets.token_urlsafe(32)
    local_owner = 'local-installation:' + agent_id
    local_service = None
    app_bundled_source = None
    network.local_only = local_only
    if discover_local or local_only:
        from phemacast.network.client import LocalClients
        network.local_clients = LocalClients(store)
        if os.environ.get('ATTAS_BUNDLED_YFINANCE_CONFIG'):
            from .bundled_source import BundledSource
            app_bundled_source = BundledSource(network.local_clients, os.environ['ATTAS_BUNDLED_YFINANCE_CONFIG'])
        else: app_bundled_source = None
    catalog_snapshots = {}
    from attas.data_terminal_phemar.browser.engine import BrowserEngine
    execution_engine = BrowserEngine(store, network)
    from .browser.processes import BrowserProcesses
    engine = BrowserProcesses(store, network) if kind=='data-terminal' else execution_engine
    if kind=='data-terminal':
        # Plaza's current trust decision approves a Phema whose links all reach agents it trusts
        # for the viewer, in every Terminal; other links still use the stored Phema approval.
        from .native_trust import NativeTrust
        engine.native_trust = engine.broker.native_trust = NativeTrust(network)
    scheduled_runs = None  # set once principal exists; Data Terminal only

    @asynccontextmanager
    async def lifespan(app):
        if not local_only:await registration.start()
        if network.local_clients:await network.local_clients.start()
        if local_service:await local_service.start()
        reaper = asyncio.create_task(engine.reap())
        runner = asyncio.create_task(scheduled_runs.loop()) if scheduled_runs else None
        enrollment = getattr(app.state, 'enrollment', None)
        enrollment_task = asyncio.create_task(enrollment.watch()) if enrollment else None
        yield
        if plaza_selection:await plaza_selection.close()
        if enrollment_task:
            enrollment_task.cancel()
            await asyncio.gather(enrollment_task, return_exceptions=True)
        reaper.cancel()
        await asyncio.gather(reaper, return_exceptions=True)
        if runner:
            runner.cancel()
            await asyncio.gather(runner, return_exceptions=True)
        await engine.close_all()
        await execution_engine.close_all()
        await registration.stop()
        if local_service:await local_service.close()
        if network.local_clients:await network.local_clients.close()
        await client.aclose()

    app = FastAPI(title=name, version=VERSION, lifespan=lifespan)
    registration.telemetry.install(app)
    app.state.store, app.state.network, app.state.engine = store, network, engine
    app.state.registration = registration
    app.state.http_client = client
    app.state.plaza_agent_id = agent_id
    from types import SimpleNamespace
    from prompits.core.call_grants import install_agent_call_grants
    async def grant_request(method,path,**kwargs):
        kwargs.pop('retries',None)
        return await client.request(method,plaza_url+path,**kwargs)


    session_checks = {}  # access-token digest -> (expiry, successful Plaza session check)
    async def principal(request, required=True):
        window=window_context(request)
        scoped=getattr(request.state,'scoped_call',None)
        if scoped:
            profile=scoped.get('user') or {}
            if not profile.get('id'):raise HTTPException(403,'This operation requires a scoped user grant.')
            authenticated={'id':profile['id'],'name':profile.get('display_name') or profile['id'],
                'token':request.headers.get('authorization','').removeprefix('Bearer '),'authentication':'scoped-call'}
            return authenticated

        if local_only:
            # Without Plaza, being this computer's Terminal user is the only identity there is.
            if not local_access.allowed(request):
                if required:raise HTTPException(403, LOCAL_ACCESS_MESSAGE)
                return None
            if not store.list(local_owner, 'workspace-state'):
                from .workspace_state import save
                try:await save(store, local_owner, {'revision':0,'document':{'version':2,'activeWorkspaceId':'local','settings':{},'workspaces':[{'id':'local','name':'Local desk','browsers':[],'activeIndex':0,'focus':False}]}})
                except HTTPException as exc:
                    if exc.status_code != 409:raise
            if app_bundled_source:await app_bundled_source.attach(local_owner)
            network.bind_local_user(local_token, local_owner)
            return {"id": local_owner, "name": "Local device", "token": local_token, "authentication": "local-device", **({'terminalWindow':window} if window else {})}
        terminal_hint=request.headers.get('x-terminal-origin')
        # Native images, media and EventSource cannot attach fetch headers.
        # These query selectors contain no credentials and still require the
        # selected Terminal's valid HttpOnly cookie (or explicit host delegation).
        event_hint=request.query_params.get('terminalOrigin')
        if terminal_hint and event_hint and terminal_hint!=event_hint:
            raise HTTPException(403,'Conflicting Terminal session context.')
        terminal_hint=terminal_hint or event_hint
        session = store.read_session(session_id(request))
        token = (session or {}).get('access_token') or (request.headers.get('authorization', '').removeprefix('Bearer ') if not window else None) or (request.cookies.get('attas_plaza_access_token') if not window and request.cookies.get(cookie)!='signed-out' else None)
        if not token:
            if required:
                raise HTTPException(401, 'Sign in to Plaza to execute data requests or save agent layouts.')
            return None
        try:
            # A successful Plaza session check is reused for 30 seconds per access token;
            # a Plaza sign-out or revocation reaches this Terminal within that time.
            checked = session_checks.get(hashlib.sha256(token.encode()).hexdigest())
            reused = bool(checked and checked[0] > time.monotonic())
            if reused:
                response = checked[1]
            else:
                response = await client.get(plaza_url + '/api/ui_auth/me', headers={'Authorization': f'Bearer {token}'})
            if expired_session(response) and session and session.get('refresh_token'):
                sid, holder = session_id(request), secrets.token_hex(16)
                for attempt in range(30):
                    latest = store.read_session(sid)
                    if not latest:
                        break
                    if latest.get('access_token') != token:
                        token = latest['access_token']
                        response = await client.get(plaza_url+'/api/ui_auth/me',headers={'Authorization':'Bearer '+token})
                        break
                    if store.claim_refresh(sid,holder):
                        try:
                            refreshed = await client.post(plaza_url+'/api/ui_auth/refresh',json={'refresh_token':latest['refresh_token']})
                            if refreshed.is_success:
                                updated=refreshed.json().get('session') or refreshed.json()
                                token=updated.get('access_token') or token
                                latest.update(updated)
                                with store.connect() as db:
                                    db.execute('UPDATE sessions SET payload=? WHERE id=?',(store.encrypt(latest),sid))
                                response=await client.get(plaza_url+'/api/ui_auth/me',headers={'Authorization':'Bearer '+token})
                        finally:
                            store.release_refresh(sid,holder)
                        break
                    await asyncio.sleep(.25)
            if response.status_code >= 500:
                raise HTTPException(502, 'Plaza authentication is temporarily unavailable.')
            if not response.is_success:
                if required:
                    raise HTTPException(401, 'Plaza session expired. Sign in again.')
                return None
            body = response.json()
            user = body.get('user') or body.get('profile') or body.get('session', {}).get('user') or {}
            id = user.get('id') or user.get('user_id')
            if not id:
                raise HTTPException(401, 'Plaza did not return an authenticated identity.')
            if not reused:
                if len(session_checks) >= 1024:
                    session_checks.clear()
                session_checks[hashlib.sha256(token.encode()).hexdigest()] = (time.monotonic() + 30, response)
            if getattr(request.state,'expected_session_owner',str(id)) != str(id):
                if required:raise HTTPException(401,'Cached Plaza identity changed. Sign in again.')
                return None
            if kind=='data-terminal':
                try:
                    if public_connection and window and required and not store.installation_owner(terminal_scope):
                        raise HTTPException(403, 'Request Plaza trust for this Terminal installation before retrieving data.')
                    if public_connection and not window and store.installation_owner(terminal_scope) != str(id):
                        raise HTTPException(403, 'Connect using the approved owner of this Terminal copy.')
                    scope=window_scope(terminal_scope,window)
                    if not getattr(request.state,'remembered_login_probe',False):
                        if not session_id(request) and (request.headers.get('authorization') or request.cookies.get('attas_plaza_access_token')):store.bind_hosted_terminal(scope,str(id),token)
                        else:store.bind_terminal(scope,str(id),session_id(request))
                except HTTPException:
                    if not required:return None
                    raise
                if public_connection:
                    try:preference=store.get(str(id),'network','preferences')
                    except HTTPException:preference={}
                    if getattr(client,'public_network',None):client.public_network.prefer_local=preference.get('preferLocal',True)
            if network.local_clients:
                if app_bundled_source:await app_bundled_source.attach(str(id))
                network.bind_local_user(token, str(id))
            authenticated = {'id': str(id), 'name': user.get('display_name') or user.get('email') or str(id), 'token': token, **({'terminalWindow':window} if window else {})}
            return authenticated
        except httpx.RequestError:
            raise HTTPException(502, 'Plaza authentication is temporarily unavailable.')

    app.state.principal = principal
    app.state.local_access = local_access
    local_access.install(app)
    if kind=='data-terminal':
        # Views may ask this Terminal to repeat one declared reference on a timer, as their user.
        from .scheduled_runs import ScheduledRuns
        scheduled_runs = app.state.scheduled_runs = ScheduledRuns(store, engine, principal, terminal_scope)
    if kind=='data-terminal' and os.environ.get('ATTAS_AGENT_VIEW_CONFIGURATION'):
        from phemacast.agent_view.container import mount_container
        mount_container(app,principal=principal,network=network,config_path=os.environ['ATTAS_AGENT_VIEW_CONFIGURATION'],
            state_dir=state_dir,window_context=window_context)
    if kind == "data-terminal" and os.environ.get("ATTAS_NATIVE_OPERATION_FORMS") == "1":
        from .native_forms import mount_native_forms
        app.state.native_forms = mount_native_forms(principal, engine, network, store)
        from .native_workspaces import mount_native_workspaces
        mount_native_workspaces(app,principal,network)
    from phemacast.agent_view.free_pulse import mount_free_pulses
    mount_free_pulses(app, principal, network, paired_connections=os.environ.get('ATTAS_AGENT_OPERATION_CONNECTIONS'))
    if not local_only:
        from .session_handoff import install_session_handoff
        if kind == 'data-terminal': install_session_handoff(app, principal, store, terminal_scope, cookie, COOKIE)
        from .window_auth import install_window_auth
        install_window_auth(app, principal, store, terminal_scope, cookie, client, plaza_url, public_connection, auth_origin=(plaza_context or {}).get('origin'), remembered_login=remembered_login)
    if network.local_clients:
        from .local_resources import install_local_routes
        install_local_routes(app, network, principal, name)
    if public_connection and kind == 'data-terminal':
        from .terminal_connection import install_connection_routes
        install_connection_routes(app,store,client,registration,terminal_scope,cookie,public_connection,principal, registry_path=(plaza_context.get('registry') or str(state_dir.parent/'trusted-plazas.json')) if plaza_context is not None else None)

    @app.post('/api/plaza-registration/forget')
    async def forget_registration(request: Request):
        """This Terminal is being removed: stop heartbeating and leave its Plaza's directory. Local only."""
        from .plaza_selection import require_local
        require_local(request, port, 'Remove this Terminal from the local Terminal.')
        return {'forgotten': await registration.forget()}


    def access_for(user):
        if local_only:return {'canConfigure': True, 'ownerId': local_owner, 'isOwner': True}
        if kind=='data-terminal' and user and user.get('terminalWindow'):
            return {'canConfigure':True,'ownerConfigured':True}
        from .ownership import configuration_access
        return {**configuration_access(kind, store, terminal_scope, state_dir, user),
}

    async def configuration_owner(request):
        user = await principal(request)
        if not access_for(user)['canConfigure']:
            raise HTTPException(403, 'Only this agent’s owner can change its configuration or layouts.')
        return user
    if kind == 'data-terminal':
        engine.refresh_user = principal
        from .source_keys import mount_source_keys
        mount_source_keys(app, principal, configuration_owner, store, network)

    @app.middleware('http')
    async def boundary(request, call_next):
        if (public_connection or discover_local or local_only) and (request.url.hostname not in ('127.0.0.1','localhost','::1') or request.url.port != port):
            return JSONResponse({'detail':'Invalid local Terminal host.'},status_code=403)
        # Browsers keep cookies per host name, so a page opened at localhost goes to the printed 127.0.0.1 address,
        # where this browser's window logins and local access live. API calls are never redirected.
        printed = urlsplit(os.environ.get('ATTAS_PUBLIC_ORIGIN', ''))
        if (request.method in ('GET', 'HEAD') and (request.url.path == '/' or request.url.path.endswith('.html'))
                and printed.hostname in ('127.0.0.1','localhost','::1') and printed.port == port
                and request.url.hostname in ('127.0.0.1','localhost','::1') and request.url.hostname != printed.hostname):
            return RedirectResponse(printed.scheme+'://'+printed.netloc+request.url.path+('?'+request.url.query if request.url.query else ''), status_code=307)
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            request_origin = request.headers.get('origin')
            allowed_origins = {str(request.base_url).rstrip('/')}
            public_origin = os.environ.get('ATTAS_PUBLIC_ORIGIN', '').rstrip('/')
            if public_origin:
                allowed_origins.add(public_origin)
            if request_origin and request_origin not in allowed_origins:
                return JSONResponse({'detail': 'Cross-origin mutation rejected.'}, status_code=403)
            raw_body = await request.body()
            if len(raw_body) > (5_000_000 if request.url.path.startswith('/api/workspace-state') else 1_000_000):
                return JSONResponse({'detail': 'Request too large.'}, status_code=413)
            if raw_body and 'application/json' in request.headers.get('content-type', ''):
                try:
                    if not isinstance(json.loads(raw_body), dict):
                        raise ValueError('Expected an object')
                except (ValueError, TypeError):
                    return JSONResponse({'detail': 'Request body must be a JSON object.'}, status_code=422)
        if local_access.enabled and local_access.gated(request.url.path) and not local_access.allowed(request):
            return JSONResponse({'detail': LOCAL_ACCESS_MESSAGE, 'localAccess': 'required'}, status_code=403)
        try:
            response = await call_next(request)
        except httpx.HTTPError:
            return JSONResponse({'detail': 'Registered upstream is unavailable.'}, status_code=502)
        except (ValueError, TypeError):
            return JSONResponse({'detail': 'Invalid request value.'}, status_code=422)
        response.headers['Cache-Control'] = 'no-store' if request.url.path.startswith('/api') else 'no-cache'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'same-origin'
        # Delegated sign-in frames are restricted to the configured Plaza.
        auth_origin=urlsplit(os.environ.get('ATTAS_PLAZA_AUTH_ORIGIN') or plaza_url)
        auth_origin=auth_origin.scheme+'://'+auth_origin.netloc
        response.headers.setdefault('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob: https:; media-src 'self' blob: https:; connect-src 'self' https: http:; frame-src 'self' https://www.youtube-nocookie.com "+auth_origin+"; font-src 'self' data:; worker-src 'self' blob:; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'self'")
        response.headers['X-Frame-Options']='SAMEORIGIN'
        response.headers['Permissions-Policy']='camera=(), microphone=(), geolocation=(), payment=(), usb=()'
        # Public static JS is code, never account data; opaque extension frames
        # can import installed modules but cannot fetch authenticated APIs.
        if request.url.path.endswith('.js') and not request.url.path.startswith('/api/'):
            response.headers['Access-Control-Allow-Origin']='*'
        if request.url.path.startswith('/api/'):local_access.refresh(request, response)
        return response

    @app.get('/health')
    async def health():
        return {'status': 'ok', 'kind': kind, 'agentId': agent_id, 'version': VERSION, 'channel': CHANNEL, 'plazaRegistered': registration.registered, 'plazaState': registration.state, 'instances': len(engine.instances)}

    @app.get('/api/phemar/feature')
    async def descriptor():
        return {'status': 'success', 'feature': descriptor_value()}

    @app.get('/api/phemar/phemacast-host-content')
    async def host_content():
        return portable_host_content(definition)

    @app.get('/api/phemar/attas-host-content')
    async def attas_content():
        return attas_host_content(definition)

    @app.get('/api/sub-phemars')
    async def attachments():
        return {'status':'success','contract':'phemacast.sub-phemar-host/v1','sub_phemars':[], 'browserRuntime':'terminal-child-process', 'browserAgents':0}

    @app.post('/api/sub-phemars/validate')
    async def validate_child(request: Request):
        from phemacast.agents.sub_phemar import validate_sub_phemar_descriptor
        await principal(request)
        body = await request.json()
        return validate_sub_phemar_descriptor(body.get('candidate'))

    @app.get('/api/sub-phemars/spec')
    async def host_spec():
        from phemacast.agents.sub_phemar import build_sub_phemar_spec
        return build_sub_phemar_spec()

    async def dispatch_pulse(pulse_name, parameters, request):
        if pulse_name == definition.descriptor_pulse:
            return descriptor_value()
        if pulse_name == definition.portable_content_pulse:
            return portable_host_content(definition)
        if pulse_name == definition.attas_content_pulse:
            return attas_host_content(definition)
        if pulse_name == 'pulse_catalog':
            return await catalog(request)
        if pulse_name == 'accept_sub_phemar':
            from phemacast.agents.sub_phemar import validate_sub_phemar_descriptor
            return {**validate_sub_phemar_descriptor(parameters.get('candidate')), 'attached':False, 'attachmentPolicy':'Deployment manifest'}
        raise HTTPException(404, 'Unsupported agent pulse.')

    @app.post('/api/phemar/pulses/{pulse_name}')
    async def local_pulse(pulse_name: str, request: Request):
        parameters = await request.json()
        if not isinstance(parameters, dict):
            raise HTTPException(422, 'Pulse input must be an object.')
        return await dispatch_pulse(pulse_name,parameters,request)

    @app.post('/use_practice/{practice}')
    async def remote_pulse(practice: str, request: Request):
        if practice not in ('get_pulse_data','quote_pulse_data'):
            raise HTTPException(404)
        message = await request.json()
        content = message.get('content') or {}
        if not isinstance(content, dict):
            raise HTTPException(422, 'Pulse content must be an object.')
        pulse_name, parameters = content.get('pulse_name'), content.get('params', {})
        supported = {p.get('pulse_name') or p.get('id') for p in descriptor_value()['supported_pulses']}
        if pulse_name not in supported or not isinstance(parameters,dict):
            raise HTTPException(422, 'Unsupported pulse or invalid parameters.')
        if practice == 'quote_pulse_data':
            return {'status':'success','result':{'status':'quoted','quote_id':secrets.token_hex(16),'points':0,'cost_points':0,'currency':'plaza_point','agent_id':registration.agent_id,'pulse_name':pulse_name,'valid_until':datetime.fromtimestamp(time.time()+300,timezone.utc).isoformat(),'input_fingerprint':hashlib.sha256(json.dumps(parameters,sort_keys=True).encode()).hexdigest()}}
        scope = dict(request.scope)
        if message.get('caller_user_token'):
            scope['headers'] = [(k,v) for k,v in scope['headers'] if k.lower()!=b'authorization'] + [(b'authorization',('Bearer '+str(message['caller_user_token'])).encode())]
        forwarded = Request(scope)
        forwarded._body = json.dumps(parameters).encode()
        return {'status':'success','result':await dispatch_pulse(pulse_name,parameters,forwarded)}

    async def discover(user):
        try:
            entries = await network.trusted_agents((user or {}).get('token', ''))
        except httpx.HTTPError:
            entries = []
        consultants = [a for a in entries if a['meta'].get('data_terminal', {}).get('kind') == 'app-consultant']
        terminal_origins = [a['address'] for a in entries if a['meta'].get('data_terminal', {}).get('kind') == 'data-terminal']
        cards = [{'id': a['meta']['data_terminal']['id'], 'name': a['meta'].get('phemar_feature_descriptor', {}).get('name') or a['name'], 'url': a['address'] + '/app-consultant.html', 'plazaId': a['id']} for a in consultants]
        if public_connection:
            from urllib.parse import quote
            for card, agent in zip(cards, consultants):
                card['url'] = public_connection['publicOrigin']+'/phemar-proxy/'+quote(agent['address'],safe='')+'/app-consultant.html'
        return {'consultants':cards, 'terminalOrigins':terminal_origins}

    @app.get('/api/discovery')
    async def discovery(request: Request):
        return await discover(await principal(request, False))

    def installation_language():
        """The language chosen for this installation before anyone signed in, or None."""
        try:return store.get('_installation', 'ui-language', 'terminal').get('uiLanguage')
        except HTTPException:return None

    @app.put('/api/installation-language')
    async def set_installation_language(request: Request):
        # Asked once on a new installation, before anyone signs in, so only the Terminal's own page may set it.
        from .plaza_selection import require_local
        require_local(request, port, 'Choose the language from the local Terminal.')
        if 'application/json' not in request.headers.get('content-type', ''):
            raise HTTPException(415, 'Use a JSON request.')
        body = await request.json()
        if not isinstance(body, dict) or set(body) != {'uiLanguage'} or body['uiLanguage'] not in UI_LANGUAGES:
            raise HTTPException(422, 'Unsupported UI language.')
        store.put('_installation', 'ui-language', 'terminal', {'uiLanguage': body['uiLanguage']})
        return {'uiLanguage': body['uiLanguage']}

    @app.get('/api/bootstrap')
    async def bootstrap(request: Request):
        user = await principal(request, False)
        directory = {'consultants':[], 'terminalOrigins':[]} if request.query_params.get('discovery') == 'background' else await discover(user)
        consultant_cards, terminal_origins = directory['consultants'], directory['terminalOrigins']
        return {'kind': kind, 'agentId': agent_id, 'name': name, 'version': VERSION, 'channel': CHANNEL, 'product': PRODUCT, 'terminalOrigin': origin if kind=='data-terminal' else os.environ.get('ATTAS_B028_TERMINAL', 'http://127.0.0.1:28400'), 'terminalOrigins':terminal_origins, 'terminalSession':{'singleLogin':kind=='data-terminal','windowScoped':bool(window_context(request)), 'windowId':window_context(request), 'inUse':bool(store.terminal_login(window_scope(terminal_scope,window_context(request)))) if kind=='data-terminal' else False}, 'plaza': {'url': public_connection['publicOrigin'] if public_connection else plaza_url, 'canChangeAddress':kind=='data-terminal' and request.url.hostname in ('127.0.0.1','localhost','::1') and request.url.port==port, 'registered': registration.registered, 'state': registration.state, 'connectionMode':'local-only' if local_only else 'direct-https' if public_connection else 'configured', 'trusted':not public_connection or bool(store.installation_owner(terminal_scope)), 'delegatedLogin':bool(public_connection), 'localOnly':local_only, 'localDiscovery':bool(network.local_clients)}, 'user': {k: v for k, v in user.items() if k != 'token'} if user else None, 'permissions': access_for(user), 'consultants': consultant_cards, 'installationLanguage': {'value': installation_language(), 'canChoose': kind=='data-terminal' and request.url.hostname in ('127.0.0.1','localhost','::1') and request.url.port==port}, 'localAccess': local_access.status(request)}

    @app.post('/api/auth/signin')
    async def signin(request: Request):
        if local_only:raise HTTPException(405, 'This Terminal uses local pairing; start a Plaza-connected Terminal to sign in.')
        if public_connection:
            raise HTTPException(405, 'Use browser-based Plaza sign-in for this connection.')
        body = await request.json()
        if set(body) - {'identifier', 'password'} or not all(isinstance(body.get(k), str) and 0 < len(body[k]) <= 500 for k in ('identifier', 'password')):
            raise HTTPException(422, 'Identifier and password are required.')
        remote = await client.post(plaza_url + '/api/ui_auth/signin', json=body)
        result = remote.json()
        if not remote.is_success:
            return JSONResponse({'detail': result.get('detail', 'Plaza sign-in failed.')}, remote.status_code)
        session = result.get('session') or result
        if not session.get('access_token'):
            raise HTTPException(502, 'Plaza did not return a usable session.')
        sid = store.session(session,lifetime=LOGIN_LIFETIME)
        if kind=='data-terminal':
            me=await client.get(plaza_url+'/api/ui_auth/me',headers={'Authorization':'Bearer '+session['access_token']})
            me.raise_for_status()
            owner=(me.json().get('user') or {}).get('id')
            if not owner:
                store.end_session(sid)
                raise HTTPException(401,'Plaza identity unavailable.')
            try:store.bind_terminal(window_scope(terminal_scope,window_context(request)),str(owner),sid)
            except HTTPException:
                store.end_session(sid)
                raise
        response = JSONResponse({'status': 'signed-in'})
        window=window_context(request)
        age=max(1,int((store.terminal_login(window_scope(terminal_scope,window)) or {})['expires']-time.time()))
        response.set_cookie(window_cookie(cookie,window), sid, httponly=True, samesite='strict', max_age=age, secure=request.url.scheme=='https')
        if not window and cookie!=COOKIE:response.set_cookie(COOKIE,sid,httponly=True,samesite='strict',max_age=age,secure=request.url.scheme=='https')
        remembered_login.issue(request,response,str(owner),sid)
        return response

    @app.post('/api/auth/signout')
    async def signout(request: Request):
        enrollment = getattr(app.state, 'enrollment', None)
        window=window_context(request)
        if enrollment and not window:
            await enrollment.suspend()
        user = await principal(request, False)
        sid=session_id(request)
        session=store.read_session(sid)
        scope=window_scope(terminal_scope,window)
        with store.connect() as db:
            shared=bool(db.execute('SELECT 1 FROM terminal_logins WHERE session_id=? AND terminal!=? AND expires>?',(sid,scope,time.time())).fetchone())
        if not shared and session and session.get('client_session_id') and user:
            revoked = await client.post(plaza_url+'/api/client-auth/revoke',json={'id':session['client_session_id']},headers={'Authorization':'Bearer '+user['token']})
            revoked.raise_for_status()
        binding=store.terminal_login(scope)
        owner=user['id'] if user else binding['owner'] if binding and binding['session_id']==sid else None
        if owner:
            if window and kind=='data-terminal':await engine.close_window(owner,window)
            else:await engine.close_owner(owner)
            store.release_terminal(scope,owner)
        if not shared:store.end_session(sid)
        if public_connection and not window:
            await registration.stop()
            registration._token = None
        response = JSONResponse({'status': 'signed-out'})
        remembered_login.forget(request,response,signed_out=True)
        if window:
            store.delete('_installation','window-signin',scope)
            response.delete_cookie(window_cookie(cookie+'_signin',window))
        if kind=='data-terminal':response.set_cookie(window_cookie(cookie,window),'signed-out',httponly=True,samesite='strict',max_age=LOGIN_LIFETIME,secure=request.url.scheme=='https')
        else:response.delete_cookie(window_cookie(cookie,window))
        if not window and cookie!=COOKIE and request.cookies.get(COOKIE)==sid:response.delete_cookie(COOKIE)
        return response

    @app.get('/api/catalog')
    async def catalog(request: Request):
        user = await principal(request, False)
        scope=hashlib.sha256((user or {}).get('token','').encode()).hexdigest()
        snapshot_id=request.query_params.get('snapshot')
        if snapshot_id:
            snapshot=catalog_snapshots.get(snapshot_id)
            if not snapshot or snapshot['scope']!=scope or snapshot['expires']<time.monotonic():
                raise HTTPException(409,'Catalog snapshot expired or belongs to another session. Refresh the catalog.')
            result={**snapshot['result']}
        else:
            try:result = await network.catalog((user or {}).get('token', ''))
            except HTTPException as exc:
                # Plaza turns away a window that has not signed in; say so instead of a gateway error.
                if not user and exc.status_code>=500:raise HTTPException(401,'Sign in to load layouts.') from exc
                raise
            snapshot_id=secrets.token_hex(16)
            for key in list(catalog_snapshots):
                if catalog_snapshots[key]['expires']<time.monotonic():catalog_snapshots.pop(key)
            while len(catalog_snapshots)>=8:catalog_snapshots.pop(next(iter(catalog_snapshots)))
            catalog_snapshots[snapshot_id]={'scope':scope,'expires':time.monotonic()+120,'result':result}
        result['snapshot']=snapshot_id
        query = request.query_params.get('q', '').lower()
        if query:
            result['pulses'] = [p for p in result['pulses'] if query in json.dumps(p).lower()]
        offset = max(0, int(request.query_params.get('offset', 0)))
        limit = max(1, min(500, int(request.query_params.get('limit', 500))))
        result['total'] = len(result['pulses'])
        result['pulses'] = result['pulses'][offset:offset + limit]
        return result


    @app.post('/api/layout-updates')
    async def layout_updates(request: Request):
        from .layout_updates import check_layout_updates
        user=await principal(request)
        body=await request.json()
        return await check_layout_updates(network,client,user,body.get('layouts'))





    @app.get('/api/capabilities')
    async def browser_capabilities():
        from .phema_transfer import advertisement
        return advertisement()

    @app.post('/api/phema/validate')
    async def validate_phema(request: Request):
        user=await principal(request)
        from .phema_transfer import accept, advertisement, compatible, negotiation_phema
        from phemacast.core.composition import composition, walk_compositions
        body = await request.json()
        phema, _ = await accept(body, check_capabilities=False)
        advertised=body.get('capabilities') or advertisement()
        checked=compatible(phema,advertised)
        try:
            requirements=[c for _,current in walk_compositions(negotiation_phema(phema)) for c in composition(current).get('capabilities',[])]
            response=await client.post(plaza_url+'/api/capabilities/negotiate',headers={'Authorization':'Bearer '+user['token']},json={'requirements':requirements,'advertisement':advertised},timeout=5)
            if response.is_success:
                registry=response.json()
                evidence={(m['id'],m['version']):m for m in registry.get('matched',[])}
                checked['matched']=[{**m,**evidence.get((m['id'],m['version']),{})} for m in checked.get('matched',[])]
                checked['registry']='available'
            else:checked['registry']='unavailable'
        except httpx.HTTPError:checked['registry']='unavailable'
        return {'valid': True, 'compatibility': checked}

    @app.get('/api/reference-sources')
    async def reference_sources(request: Request):
        user = await principal(request)
        return {'sources': [{'id': a['id'], 'name': a['name'], 'availability': a.get('availability', 'unknown'), 'pulses':[{'name':p.get('name') or p.get('pulse_name'),'description':p.get('description',''),'inputSchema':p.get('input_schema',{})} for p in a.get('meta',{}).get('supported_pulses',[]) if isinstance(p,dict)]} for a in await network.trusted_agents(user['token'])]}

    @app.get('/api/reference-sources/{source}/snapshots')
    async def reference_snapshots(source: str, request: Request):
        user=await principal(request);agent=await network.agent(identifier(source),user['token']);await network.require_trusted(agent, user['token'])
        from .content_references import require_owner
        await require_owner(network,user,agent['id'])
        response=await network.agent_request(agent,user['token'],'GET','/api/phema-snapshots',timeout=20)
        response.raise_for_status()
        return {'snapshots':response.json().get('snapshots',[])}

    @app.get('/api/reference-sources/{source}/snapshots/{snapshot}')
    async def reference_snapshot(source: str, snapshot: str, request: Request):
        user=await principal(request)
        return await network.resolve_reference({'id':'preview','kind':'phemar-snapshot','target':{'agentId':identifier(source),'snapshotId':identifier(snapshot)}},{},user,store)

    @app.get('/api/composition-assets')
    async def composition_assets(request: Request):
        user=await principal(request)
        return {'assets':store.list(user['id'],'composition-asset')}

    from .workspace_previews import install_preview_routes
    install_preview_routes(app, store, principal)

    @app.get('/api/workspace-state')
    async def workspace_state(request: Request):
        user = await principal(request)
        from .workspace_state import read, initialize_window
        initialize_window(store, user['id'], state_key(user))
        return read(store, user['id'], state_key(user))

    @app.get('/api/workspace-library')
    async def workspace_library(request: Request):
        user=await principal(request)
        from .workspace_state import project
        key=request.query_params.get('key')
        if key:
            if key!='default' and not re.fullmatch(r'window\.[a-f0-9]{32}',key):raise HTTPException(422,'Invalid saved Terminal identifier.')
            record=store.get(user['id'],'workspace-state',key)
            return {'document':project(record['document'])}
        return {'records':[{'key':r['id'],'updatedAt':r['updatedAt'],
                            'workspaces':[{'id':w['id'],'name':w['name'],'browsers':len(w['browsers'])} for w in r['document']['workspaces']]}
                           for r in store.list(user['id'],'workspace-state')]}

    @app.get('/api/terminal-tabs')
    async def terminal_tabs(request: Request):
        user = await principal(request)
        from .window_sessions import saved_tabs
        return {'tabs':saved_tabs(store, user)}

    @app.post('/api/terminal-tabs/delete')
    async def delete_terminal_tabs(request: Request):
        user = await principal(request)
        from .window_sessions import WINDOW, forget_tabs
        body = await request.json()
        windows = body.get('windows') if isinstance(body, dict) else None
        if not isinstance(windows, list) or not 0 < len(windows) <= 500 or not all(isinstance(w, str) and WINDOW.fullmatch(w) for w in windows):
            raise HTTPException(422, 'Choose saved windows to delete.')
        windows = sorted(set(windows))
        if user.get('terminalWindow') in windows:
            raise HTTPException(409, 'This window cannot delete itself.')
        for window in windows:
            # Sign the window out here first, so nothing it started keeps running or stays signed in.
            if kind == 'data-terminal':
                await engine.close_window(user['id'], window)
            scope = window_scope(terminal_scope, window)
            binding = store.terminal_login(scope)
            if binding and binding['owner'] == user['id']:
                store.release_terminal(scope, user['id'])
                with store.connect() as db:
                    shared = db.execute('SELECT 1 FROM terminal_logins WHERE session_id=? AND expires>?', (binding['session_id'], time.time())).fetchone()
                if binding['session_id'] and not shared:
                    store.end_session(binding['session_id'])
            store.delete('_installation', 'window-signin', scope)
        forget_tabs(store, user['id'], windows)
        return {'deleted': windows}

    @app.get('/api/workspace-state/revisions')
    async def workspace_revisions(request: Request):
        user = await principal(request)
        return {'records':[{k:r[k] for k in ('id','revision','updatedAt')} for r in store.list(user['id'],'workspace-revision') if r['id'].startswith(state_key(user)+':')]}

    @app.post('/api/workspace-state/rollback')
    async def workspace_rollback(request: Request):
        user = await principal(request); body = await request.json()
        if type(body.get('previousRevision')) is not int or type(body.get('revision')) is not int:
            raise HTTPException(422, 'Choose a saved revision and provide the current revision.')
        from .workspace_state import rollback_workspace
        from .workspace_state import project
        result = rollback_workspace(store,user['id'],body['previousRevision'],body['revision'],key=state_key(user))
        return {**result,'document':project(result['document'])}

    @app.put('/api/workspace-state')
    async def save_workspace_state(request: Request):
        user=await principal(request)
        from .workspace_state import save, read
        if request.headers.get('X-Phemacast-Model-Contract') != 'model-requirement/1':
            from phemacast.browser.requirement_documents import map_requirements
            found=[]
            current=read(store,user['id'],state_key(user)).get('document') or {}
            for workspace in current.get('workspaces',[]):
                for browser in workspace.get('browsers',[]):
                    if browser.get('customLayout'):
                        def inspect(req):
                            if req.get('modelRef'):found.append(True)
                            return req
                        map_requirements(browser['customLayout'],inspect)
            if found:raise HTTPException(409,'Refresh this older client before saving model-based workspaces. Your current workspace is preserved.')
        return await save(store,user['id'],await request.json(),key=state_key(user))

    @app.post('/api/workspace-state/import')
    async def import_workspace_state(request: Request):
        user=await principal(request)
        from .workspace_state import migrate
        return await migrate(store,user['id'],await request.json(),state_key=state_key(user))

    @app.get('/api/workspace-state/conflicts')
    async def workspace_conflicts(request: Request):
        user=await principal(request)
        from .workspace_state import project
        return {'records':[{**r,'document':project(r['document'])} for r in store.list(user['id'],'workspace-conflict') if r.get('windowKey','default')==state_key(user)]}

    @app.put('/api/composition-assets/{asset}/{version}')
    async def save_composition_asset(asset: str, version: str, request: Request):
        user=await principal(request);body=await request.json()
        from phemacast.core.composition_media import validate_media
        try: value=validate_media(body.get('value'),body.get('kind'))
        except ValueError as exc:raise HTTPException(422,str(exc)) from exc
        return store.put(user['id'],'composition-asset',identifier(asset)+':'+identifier(version),{'kind':body['kind'],'value':value},0)


    local_config_path = os.environ.get('PHEMACAST_LOCAL_RESOURCE_CONFIG')
    @app.get('/api/report-media/{publisher}/{report}/{media}')
    async def report_media(publisher:str,report:str,media:str,request:Request):
        from .report_media import read_media
        from fastapi.responses import Response
        user=await principal(request)
        content,mime=await read_media(network,user,publisher,report,media)
        return Response(content,media_type=mime,headers={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff','Content-Security-Policy':"sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:; base-uri 'none'"})


    @app.get('/api/settings')
    async def settings(request: Request):
        user = await principal(request)
        preferences = {k:v for k,v in window_preferences(store,user).items()
            if k in {'uiLanguage','displayTimezone','specifiedTimezone','marketAppearance','theme','customTheme','sessionName','displayFormat'}}
        from .billing import history
        billing = history(store, user['id'])
        # Settings shows the latest few; the whole history opens on request.
        return {'preferences': preferences, 'connections': [], 'usage': store.usage(user['id']), 'billing': billing[:10],
                'billingTotal': len(billing), 'billingToReconcile': sum(1 for row in billing if row['canReconcile'])}

    @app.get('/api/agent-trust/overview')
    async def agent_trust_overview(request: Request):
        from .agent_trust_view import trusted
        user = await principal(request)
        return JSONResponse(await trusted(network, user), headers={'Cache-Control': 'private, no-store'})

    @app.get('/api/agent-trust/history')
    async def agent_trust_history(request: Request):
        from .agent_trust_view import history
        user = await principal(request)
        try:
            limit = int(request.query_params.get('limit', '10'))
            before = request.query_params.get('before')
            before = float(before) if before else None
        except ValueError:
            raise HTTPException(422, 'Use a number for limit and before.') from None
        if not 1 <= limit <= 200:
            raise HTTPException(422, 'Use a limit of 1–200.')
        return JSONResponse(await history(network, user, limit=limit, before=before), headers={'Cache-Control': 'private, no-store'})

    @app.get('/api/billing/history')
    async def billing_history(request: Request):
        user = await principal(request)
        from .billing import history
        return {'rows': history(store, user['id'])}

    @app.get('/api/account/transactions')
    async def transactions(request: Request):
        from .transactions import account_transactions
        user = await principal(request)
        result = await account_transactions(network, user)
        return JSONResponse(result, headers={'Cache-Control': 'private, no-store'})

    @app.post('/api/billing/{id}/reconcile')
    async def reconcile_billing(id: str, request: Request):
        from .billing import reconcile
        user = await principal(request)
        return await reconcile(network,store,user,identifier(id))





    @app.put('/api/settings')
    async def update_settings(request: Request):
        user = await configuration_owner(request)
        body = await request.json()
        allowed = {'uiLanguage','displayTimezone','specifiedTimezone','marketAppearance','theme','customTheme','sessionName','displayFormat'}
        if not isinstance(body, dict) or set(body) - allowed:
            raise HTTPException(422, 'Application settings belong to the owning agent workspace.')
        from .market_appearance import validate_market_appearance, validate_display_timezone
        if 'marketAppearance' in body: validate_market_appearance(body['marketAppearance'])
        if 'displayTimezone' in body: validate_display_timezone(body['displayTimezone'])
        if 'specifiedTimezone' in body: validate_display_timezone(body['specifiedTimezone'], allow_modes=False)
        if 'displayFormat' in body:
            from phemacast.core.display_format import validate_display_format
            try:validate_display_format(body['displayFormat'])
            except ValueError as exc:raise HTTPException(422, str(exc)) from None
        if 'uiLanguage' in body and body['uiLanguage'] not in UI_LANGUAGES:
            raise HTTPException(422, 'Unsupported UI language.')
        if body.get('theme','dark') not in ('dark','light','system','custom'):
            raise HTTPException(422, 'Unsupported theme.')
        from .window_appearance import validate_custom_theme, session_name
        if 'customTheme' in body: validate_custom_theme(body['customTheme'])
        if 'sessionName' in body: body['sessionName'] = session_name(body['sessionName'])
        old = window_preferences(store, user)
        # Retain legacy application settings byte-for-byte until their additive
        # migration; the container cannot interpret or update those fields.
        return store.put(user['id'], 'settings', state_key(user) if user.get('terminalWindow') else 'preferences',
            {k:v for k,v in {**old,**body}.items() if k not in ('id','revision','updatedAt')})





    # OBSOLETE: App Consultant private application-library implementation in Terminal.
    # Owning-agent replacement is required; see BUSINESS_LOGIC_OBSOLETE.md.

    @app.api_route('/api/instances{rest:path}', methods=['GET', 'POST', 'PUT', 'DELETE'])
    async def instances(rest: str, request: Request):
        user = await principal(request)
        if kind!='data-terminal':
            raise HTTPException(404,'Browser processes belong to Data Terminal. Open its Browser surface.')
        parts = rest.strip('/').split('/')
        if len(parts) >= 2 and parts[1] == 'runs':
            return await scheduled_runs.instance_route(identifier(parts[0]), parts[2:], request, user)
        if len(parts) == 2 and parts[1] == 'kept':
            from .view_kept import instance_route as kept_route
            return await kept_route(engine, store, identifier(parts[0]), request, user)
        return await engine.route(rest, request, user)

    @app.api_route('/api/scheduled-runs{rest:path}', methods=['GET', 'POST'])
    async def scheduled(rest: str, request: Request):
        """This Terminal window's scheduled runs, for its runs list."""
        user = await principal(request)
        if not scheduled_runs:
            raise HTTPException(404, 'Scheduled runs belong to Data Terminal.')
        return await scheduled_runs.account_route([p for p in rest.strip('/').split('/') if p], request, user)

    @app.get('/')
    async def root():
        return FileResponse(web / 'index.html')


    @app.get('/renderer-frame')
    async def renderer_frame(request: Request):
        from fastapi.responses import HTMLResponse
        module=request.query_params.get('module','')
        if not re.fullmatch(r'/composition-renderers/[A-Za-z0-9_-]+/[a-f0-9]{64}\.js',module):raise HTTPException(403,'Select an installed renderer.')
        return HTMLResponse('<!doctype html><html><head><meta charset="utf-8"></head><body><main></main><script type="module" src="/renderer-frame.js"></script></body></html>',headers={
            'Content-Security-Policy':"sandbox allow-scripts; default-src 'none'; script-src 'self'; style-src 'unsafe-inline'; img-src data:; connect-src 'none'; frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'self'",'Referrer-Policy':'no-referrer'})

    @app.get('/api/renderers')
    async def renderer_catalog():
        from phemacast.core.renderer_plugins import public_renderers
        return {'renderers':public_renderers()}

    from .ui_revision import UIRevision
    ui_revision = UIRevision(web)
    manual_refresh_only = os.environ.get('ATTAS_TERMINAL_MANUAL_REFRESH_ONLY') == '1'
    migration_window = os.environ.get('ATTAS_TERMINAL_RELOAD_WINDOW', '')

    @app.get('/api/ui-version')
    async def ui_version(request: Request):
        # Older clients auto-reload when the revision changes. During an
        # explicitly approved installation only the selected window may carry
        # its temporary draft across that one migration. Other views stay live.
        if manual_refresh_only and request.headers.get('x-terminal-manual-refresh') != '1':
            if not migration_window or request.headers.get('x-terminal-window') != migration_window:
                raise HTTPException(409, 'Use manual Phema refresh in this Terminal window.')
        from starlette.concurrency import run_in_threadpool
        return await run_in_threadpool(ui_revision.read)

    @app.get('/composition-renderers/{key}/{filename}')
    async def renderer_module(key: str, filename: str):
        from phemacast.core.renderer_plugins import installed_renderers
        selected=next((r for r in installed_renderers() if r['key']==key and filename==r['sha256']+'.js'),None)
        if not selected:raise HTTPException(404)
        return FileResponse(selected['path'],media_type='text/javascript',headers={'Cache-Control':'public, max-age=31536000, immutable'})

    @app.get('/composition-vendor/{filename:path}')
    async def composition_vendor(filename: str):
        directory=Path(__file__).resolve().parents[2]/'phemacast/web/vendor'
        path=(directory/filename).resolve()
        if not path.is_relative_to(directory.resolve()) or path.suffix not in ('.js', '.wasm') or not path.is_file():raise HTTPException(404)
        return FileResponse(path)

    @app.get('/{filename}')
    async def asset(filename: str):
        if '/' in filename or '\\' in filename or filename.startswith('.') or filename.endswith('.mjs') or filename.endswith('.test.js'):
            raise HTTPException(404)
        path = web / filename
        if path.suffix not in ('.html', '.js', '.css') or not path.is_file():
            raise HTTPException(404)
        return FileResponse(path)

    # Verify a scoped call before the Consultant's owner/trusted-account guard;
    # Plaza selection stays outermost so it selects the destination first.
    install_agent_call_grants(SimpleNamespace(app=app,agent_id=lambda:registration.agent_id,plaza_url=plaza_url,
        _plaza_request_async=grant_request,_ensure_token_valid=lambda:{'Authorization':'Bearer '+(registration._token or '')}))
    if kind == 'data-terminal' and allow_plaza_selection:
        from .plaza_selection import PlazaSelection, PlazaSelectionMiddleware
        from functools import partial
        from .trusted_plazas import TrustedPlazas
        trusted = TrustedPlazas(os.environ['ATTAS_TRUSTED_PLAZAS_FILE']) if os.environ.get('ATTAS_TRUSTED_PLAZAS_FILE') else None
        plaza_selection = PlazaSelection(app, store, state_dir, agent_id, port, public_connection['publicOrigin'] if public_connection else plaza_url, principal, partial(create_app, web_dir=web), trusted)
        app.state.plaza_selection = plaza_selection
        plaza_selection.install()
        app.add_middleware(PlazaSelectionMiddleware, manager=plaza_selection)
    return app
