"""Browser approval surface for proof-bound native client authorization."""
from fastapi.responses import HTMLResponse, RedirectResponse


def client_origins(plaza):
    """Client origins this Plaza's operator configured: they may frame session reuse and receive approvals."""
    import os
    from urllib.parse import urlsplit
    origins = []
    configured = getattr(plaza, 'raw_config', {}).get('client_sso_origins', [])
    candidates = configured if isinstance(configured, list) else []
    candidates = candidates + os.environ.get('PROMPITS_CLIENT_SSO_ORIGINS', '').split(',')
    for raw in candidates:
        if not isinstance(raw, str):continue
        value = raw.strip().rstrip('/')
        parsed = urlsplit(value)
        if (parsed.scheme in ('http','https') and parsed.hostname and not parsed.username
                and not parsed.password and not parsed.path and not parsed.query and not parsed.fragment
                and (parsed.scheme == 'https' or parsed.hostname in ('localhost','127.0.0.1','::1'))):
            origins.append(value)
    return origins


def complete_client_oauth(plaza, params, state):
    """Complete a proof-bound client login using only the fresh provider identity."""
    from fastapi import HTTPException
    from html import escape
    try:
        if params.get('error') or params.get('error_description'):
            raise HTTPException(401, 'Sign-in was cancelled. Return to your application to try again.')
        if not params.get('code'):
            raise HTTPException(401, 'The identity provider did not return a sign-in code.')
        plaza._client_sessions().pending(state['client_grant'])
        result = plaza._supabase_exchange_code_for_session(auth_code=params['code'],
            code_verifier=state['code_verifier'], redirect_to=state['redirect_to'])
        user = result.get('user') or {}
        if not user.get('id'):
            raise HTTPException(401, 'The identity provider did not return an account.')
        profile = plaza._sync_ui_user_profile(user, preferred_role='user',
            auth_provider=state['provider'], touch_sign_in=True)
        if not plaza._ui_user_status_is_active(profile):
            raise HTTPException(403, 'This account is disabled.')
        approved = plaza._client_sessions().approve(state['client_grant'], profile['id'], True)
        if approved.get('redirect'):
            # Back to the client that started this sign-in, with the ticket only it can use.
            return RedirectResponse(approved['redirect'], status_code=303,
                                    headers={'Cache-Control':'no-store','Referrer-Policy':'no-referrer'})
        message = 'Signed in. You can close this window and return to your application.'
        script = '<script>window.close()</script>'
    except HTTPException as exc:
        message = str(exc.detail)
        script = ''
    return HTMLResponse('<!doctype html><html><title>Sign-in</title><p role="status">'+escape(message)+'</p>'+script+'</html>',
        headers={'Cache-Control':'no-store','Referrer-Policy':'no-referrer',
                 'Content-Security-Policy':"default-src 'none'; script-src 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'"})


def install_client_approval(plaza):
    @plaza.app.get('/api/client-auth/sso-frame', response_class=HTMLResponse)
    async def sso_frame():
        import json
        origins = client_origins(plaza)
        page = SSO_PAGE.replace('__ORIGINS__', json.dumps(origins).replace('<', '\\u003c'))
        return HTMLResponse(page, headers={'Cache-Control':'no-store','Referrer-Policy':'no-referrer',
            'Content-Security-Policy':"default-src 'self'; script-src 'unsafe-inline'; frame-ancestors "+(' '.join(origins) or "'none'")+"; base-uri 'none'"})

    @plaza.app.get('/client-signin', response_class=HTMLResponse)
    async def client_signin():
        return HTMLResponse(SIGNIN_PAGE, headers={'Cache-Control': 'no-store',
            'Content-Security-Policy': "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'",
            'Referrer-Policy': 'no-referrer'})

    @plaza.app.get('/client-connect', response_class=HTMLResponse)
    async def client_connect():
        # The callback contains no credentials. Only the proof holder can exchange
        # the separately delivered device code for its revocable client session.
        return HTMLResponse(PAGE, headers={'Cache-Control': 'no-store',
            'Content-Security-Policy': "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'",
            'Referrer-Policy': 'no-referrer'})


PAGE = '''<!doctype html><html><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Connect a client · Plaza</title><style>body{font:16px system-ui;max-width:440px;margin:32px auto;padding:20px;background:#10171c;color:#e4e9e8}button,a{display:inline-block;padding:10px;margin:6px;color:inherit}button{background:#293a40;border:1px solid #59747c;border-radius:6px}small{display:block}#error{color:#efaa9e}</style>
<h1>Connect a client</h1><p id="name"></p><p id="account"></p><p id="scope"></p><p id="error" role="alert"></p>
<div id="login"><p>Sign in to Plaza, then return to this page.</p><a href="/plazas" target="_blank" rel="noopener">Open Plaza sign-in</a><button id="check">Check sign-in</button><div id="providers"></div></div>
<div id="decision" hidden><button id="allow">Allow this client</button><button id="deny">Deny</button></div>
<script>
const code=new URLSearchParams(location.search).get('code'),by=id=>document.getElementById(id);
let token='';async function call(path,body){const r=await fetch(path,{method:body?'POST':'GET',headers:{'Content-Type':'application/json',...(token?{Authorization:'Bearer '+token}:{})},...(body?{body:JSON.stringify(body)}:{})});const v=await r.json();if(!r.ok)throw Error(v.detail||'Request failed');return v;}
async function check(){try{const info=await call('/api/client-auth/request/'+encodeURIComponent(code));by('name').textContent=info.client_name;by('scope').textContent=info.scope+' · expires in '+(info.session_lifetime_seconds/3600)+' hours';token=JSON.parse(localStorage.getItem('plaza.ui.authSession')||'null')?.access_token||'';if(!token)return;const me=await call('/api/ui_auth/me');by('account').textContent='Account: '+(me.user?.display_name||me.user?.email||me.user?.id||'');by('login').hidden=true;by('decision').hidden=false;by('error').textContent='';}catch(e){by('error').textContent=e.message;}}
async function decide(approved){by('allow').disabled=by('deny').disabled=true;try{const result=await call('/api/client-auth/approve',{user_code:code,approved});location.href=result.redirect||'prompits-client://complete';}catch(e){by('error').textContent=e.message;by('allow').disabled=by('deny').disabled=false;}}
by('allow').onclick=()=>decide(true);by('deny').onclick=()=>decide(false);by('check').onclick=check;
call('/api/ui_auth/config').then(config=>{for(const provider of config.oauth_providers||[]){if(!/^[a-z0-9_-]+$/.test(provider))continue;const a=document.createElement('a');a.textContent='Continue with '+provider;a.href='/api/ui_auth/oauth/'+provider+'/start?next='+encodeURIComponent(location.pathname+location.search);by('providers').append(a);}}).catch(()=>{});
check();</script></html>'''


# Browser clients use sign-in itself as the explicit action. Merely opening a
# link must never delegate an existing account to whoever holds its device code.
SIGNIN_PAGE = '''<!doctype html><html lang="en"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sign in · Plaza</title><style>body{font:15px system-ui;max-width:420px;margin:32px auto;padding:20px;background:#10171c;color:#e4e9e8}button,a{display:inline-block;padding:9px;margin:5px 5px 5px 0;color:inherit}button{background:#293a40;border:1px solid #59747c;border-radius:6px}#error{color:#efaa9e}[hidden]{display:none!important}</style>
<h1>Sign in to Plaza</h1><p id="name"></p><p id="status" role="status"></p><p id="error" role="alert"></p>
<div id="actions" hidden><button id="continue" hidden></button><div id="providers"></div><a id="password">Plaza account sign-in</a></div>
<script>
const code=new URLSearchParams(location.search).get('code'),by=id=>document.getElementById(id),key='plaza.client-signin.'+code;
let token='',busy=false;
function session(){try{return JSON.parse(localStorage.getItem('plaza.ui.authSession')||'null')?.access_token||'';}catch{return '';}}
async function call(path,body){const r=await fetch(path,{method:body?'POST':'GET',cache:'no-store',headers:{'Content-Type':'application/json',...(token?{Authorization:'Bearer '+token}:{})},...(body?{body:JSON.stringify(body)}:{})});const v=await r.json();if(!r.ok)throw Error(v.detail||'Sign-in failed');return v;}
function started(){sessionStorage.setItem(key,JSON.stringify({token:session(),at:Date.now()}));}
async function complete(){if(busy)return;busy=true;by('actions').hidden=true;by('status').textContent='Completing sign-in…';try{const result=await call('/api/client-auth/approve',{user_code:code,approved:true});sessionStorage.removeItem(key);if(result.redirect){location.replace(result.redirect);return;}by('status').textContent='Signed in. You can close this window and return to your application.';window.close();}catch(e){by('error').textContent=e.message;by('status').textContent='';by('actions').hidden=false;busy=false;}}
async function load(){try{
 const info=await call('/api/client-auth/request/'+encodeURIComponent(code));by('name').textContent=info.client_name;
 token=session();let me=null;if(token){try{me=await call('/api/ui_auth/me');}catch{token='';}}
 let attempt;try{attempt=JSON.parse(sessionStorage.getItem(key)||'null');}catch{}
 // Only a fresh session returned after a sign-in action in this tab can finish
 // automatically. Existing sessions always require the account choice below.
 if(me&&attempt&&Date.now()-attempt.at<300000&&attempt.token!==token){await complete();return;}
 if(me){by('continue').textContent='Sign in as '+(me.user?.display_name||me.user?.email||me.user?.id);by('continue').hidden=false;by('continue').onclick=complete;}
 const next='/client-signin?code='+encodeURIComponent(code);
 by('password').href='/plazas?signin=1&next='+encodeURIComponent(next);by('password').onclick=started;
 const config=await call('/api/ui_auth/config');for(const provider of config.oauth_providers||[]){if(!/^[a-z0-9_-]+$/.test(provider))continue;const a=document.createElement('a');a.textContent='Sign in with '+provider;a.href='/api/ui_auth/oauth/'+provider+'/start?next='+encodeURIComponent(next);a.onclick=started;by('providers').append(a);}
 by('actions').hidden=false;
}catch(e){by('error').textContent=e.message;}}
load();</script></html>'''


SSO_PAGE = '''<!doctype html><html><title>Session handoff</title><script>
const allowed=__ORIGINS__;let busy=false;
addEventListener('message',async event=>{
 if(event.source!==parent||!allowed.includes(event.origin)||event.data?.type!=='plaza-client-sso'||busy)return;
 const {code,nonce}=event.data;if(!/^[A-F0-9]{10}$/.test(code)||typeof nonce!=='string'||nonce.length>128)return;
 busy=true;let status='unavailable',reason='session-check-failed',redirect;
 try{
  const session=JSON.parse(localStorage.getItem('plaza.ui.authSession')||'null');let token=session?.access_token;
  {const prefix=location.pathname.split('/api/client-auth/')[0];const headers={'Content-Type':'application/json',...(token?{Authorization:'Bearer '+token}:{})};
   let me=await fetch(prefix+'/api/ui_auth/me',{headers,cache:'no-store'});
   if(me.status===401&&session?.refresh_token){
    const refreshed=await fetch(prefix+'/api/ui_auth/refresh',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({refresh_token:session.refresh_token})});
    if(refreshed.ok){const data=await refreshed.json();const fresh=data.session||data;if(fresh.access_token){localStorage.setItem('plaza.ui.authSession',JSON.stringify(fresh));headers.Authorization='Bearer '+fresh.access_token;me=await fetch(prefix+'/api/ui_auth/me',{headers,cache:'no-store'});}}
   }
   if(me.ok){reason='approval-failed';const reply=await fetch(prefix+'/api/client-auth/approve',{method:'POST',headers,body:JSON.stringify({user_code:code,approved:true})});if(reply.ok){status='approved';redirect=(await reply.json()).redirect;}}
   else reason=me.status===401?'no-active-session':'session-check-failed';
  }
 }catch(error){if(error.name==='SecurityError')reason='storage-unavailable';}
 // Only the configured client that framed this page receives the approval's return address.
 parent.postMessage({type:'plaza-client-sso',nonce,status,...(status==='approved'?(redirect?{redirect}:{}):{reason})},event.origin);
});
// Announce readiness after installing the listener. The parent validates this
// frame's origin and window before sending its proof-bound handoff request.
for(const origin of allowed)parent.postMessage({type:'plaza-client-sso-ready'},origin);
</script></html>'''
