import {terminalHeaders} from './terminal-session.js';
import {t,setUIText} from './i18n.js';
let pending=false,timer;
let activateSession=null;
// The Terminal owns its UI lifecycle. Auxiliary pages may still navigate after
// authentication, but the main workspace must adopt the session in place.
export function onWindowSignIn(activate){activateSession=activate;}
// Finish any window sign-in the same way: load the account workspace and close the dialog.
export async function activateWindowSession(){if(activateSession)await activateSession();else location.reload();}
async function confirmedSession(){
  try{
    const response=await fetch('/api/auth/window-signin/status',{credentials:'same-origin',headers:terminalHeaders(),cache:'no-store',signal:AbortSignal.timeout(5000)});
    return response.ok?(await response.json()).status:null;
  }catch{return null;}
}
async function completeSignIn(status){
  clearTimeout(timer);
  setUIText(status,'Signed in. Loading your workspace…');
  try{
    if(activateSession)await activateSession();
    else location.reload();
  }catch{
    // A saved cookie is not a rendered account. Keep the dialog actionable if
    // bootstrap/restoration fails; retry can adopt the already signed-in user.
    setUIText(status,await confirmedSession()==='signed-in'?'Signed in, but the workspace could not load. Select Sign in to retry.':'Sign-in status could not be confirmed. Please retry.');
  }finally{
    pending=false;
    document.querySelectorAll('[data-window-signin]').forEach(button=>button.disabled=false);
  }
}
async function request(path,payload={}){
  const response=await fetch(path,{method:'POST',credentials:'same-origin',
    headers:terminalHeaders({'Content-Type':'application/json'}),body:JSON.stringify(payload),cache:'no-store',signal:AbortSignal.timeout(20000)})
    .catch(error=>{if(error.name==='TimeoutError')throw Error('Plaza sign-in timed out. Please try again.');throw error;});
  const body=await response.json();if(!response.ok)throw Error(body.detail||'Plaza sign-in is unavailable.');return body;
}
document.addEventListener('click',async event=>{
  const button=event.target.closest('[data-window-signin]');if(!button||pending)return;
  const status=button.closest('dialog')?.querySelector('[data-window-signin-status]')||button.parentElement.querySelector('[data-window-signin-status]');
  if(!status)return;
  pending=true;button.disabled=true;clearTimeout(timer);
  // Open synchronously with the user's click so popup blockers do not turn
  // delegated sign-in into a second link the user has to discover.
  const alternate=button.hasAttribute('data-alternate')||!!button.dataset.provider;
  const popup=alternate?window.open('about:blank','_blank'):null;if(popup)popup.opener=null;
  setUIText(status,alternate?'Opening sign-in…':'Checking your existing Plaza session…');
  const done=()=>{pending=false;if(button.isConnected)button.disabled=false;};
  async function poll(){
    if(!status.isConnected){done();return;}
    try{
      const state=await request('/api/auth/window-signin/poll');
      if(state.status==='signed-in'){completeSignIn(status);return;}
      if(['authorization_pending','slow_down'].includes(state.status)){timer=setTimeout(poll,(state.interval||5)*1000);return;}
      setUIText(status,state.status==='access_denied'?'Plaza sign-in was declined.':state.status==='expired_token'?'This sign-in request expired. Please start again.':'Sign-in status could not be confirmed. Please retry.');done();
    }catch(error){if(await confirmedSession()==='signed-in'){completeSignIn(status);return;}setUIText(status,error.message);done();}
  }
  try{
    const state=await request('/api/auth/window-signin',button.dataset.provider?{provider:button.dataset.provider}:{reuse:!alternate,...(button.dataset.reuseWindow?{sourceWindow:button.dataset.reuseWindow}:{})});
    if(state.status==='signed-in'){completeSignIn(status);return;}
    if(state.status==='choose-account'){
      setUIText(status,'Choose an account to reuse.');
      for(const account of state.accounts){const choice=document.createElement('button');choice.type='button';choice.className='button';choice.dataset.windowSignin='';choice.dataset.reuseWindow=account.window;choice.textContent=account.name;status.append(choice);}
      done();return;
    }
    if(state.status==='reuse-session'){
      const frame=document.createElement('iframe');frame.hidden=true;frame.src=state.ssoUrl;frame.dataset.previewExclude='';
      const origin=new URL(state.ssoUrl).origin;
      let timeout,retry,finished=false,ready=false;
      const cleanup=()=>{finished=true;clearTimeout(timeout);clearInterval(retry);removeEventListener('message',receive);frame.remove();};
      const fallback=async(message='Your Plaza session could not be reused. Select Sign in to continue.')=>{
        if(finished)return;cleanup();
        setUIText(status,'Checking sign-in result…');
        const verified=await confirmedSession();
        if(verified==='signed-in'){completeSignIn(status);return;}
        // Approval may have completed even if the frame's reply was lost.
        try{const result=await request('/api/auth/window-signin/poll');if(result.status==='signed-in'){completeSignIn(status);return;}}catch{}
        done();setUIText(status,verified===null?'Sign-in status could not be confirmed. Please retry.':message);
        const login=document.createElement('button');login.type='button';login.className='button';login.dataset.windowSignin='';login.dataset.alternate='';setUIText(login,'Sign in');status.append(document.createTextNode(' '),login);
      };
      const send=()=>{if(!finished&&frame.isConnected)frame.contentWindow.postMessage({type:'plaza-client-sso',code:state.userCode,nonce:state.nonce},origin);};
      const start=()=>{if(finished)return;send();if(ready)return;ready=true;clearTimeout(timeout);timeout=setTimeout(()=>fallback('Plaza session verification timed out. Your session may still be active. Please retry.'),20000);retry=setInterval(send,1000);};
      const receive=event=>{
        if(event.origin!==origin||event.source!==frame.contentWindow)return;
        if(event.data?.type==='plaza-client-sso-ready'){start();return;}
        if(event.data?.type!=='plaza-client-sso'||event.data.nonce!==state.nonce)return;
        if(event.data.status==='approved'){
          cleanup();setUIText(status,'Completing sign-in…');
          // Plaza returns the approval to this Terminal; hand its ticket to the server before polling.
          let target=null;try{target=event.data.redirect?new URL(event.data.redirect):null;}catch{}
          if(target&&target.origin===location.origin&&target.pathname==='/api/auth/window-signin/return')
            fetch(target.pathname+target.search,{credentials:'same-origin',cache:'no-store'}).catch(()=>{}).finally(poll);
          else poll();
        }
        else fallback(event.data.reason==='storage-unavailable'?'The browser blocked access to the Plaza session. Use provider sign-in to continue.':event.data.reason==='no-active-session'?'No active session was found at this Plaza address. Select Sign in to continue.':undefined);
      };
      // The ready message handles initial-load races; onload supports older
      // Plazas. Repeated messages are safe: Plaza accepts the request only once.
      addEventListener('message',receive);frame.onload=start;
      timeout=setTimeout(()=>fallback('The Plaza session connection timed out before verification. Please retry.'),20000);document.body.append(frame);return;
    }
    if(state.status==='choose-provider'){
      if(popup&&!popup.closed)popup.close();
      setUIText(status,'Choose a sign-in provider.');
      for(const provider of state.providers){const choice=document.createElement('button');choice.type='button';choice.className='button';choice.dataset.windowSignin='';choice.dataset.provider=provider;choice.textContent=provider;status.append(choice);}
      done();return;
    }
    status.replaceChildren();
    if(popup&&!popup.closed)popup.location.href=state.authorizationUrl;
    const link=document.createElement('a');link.href=state.authorizationUrl;link.target='_blank';link.rel='noopener';
    link.textContent=t('Sign in');const waiting=document.createElement('span');setUIText(waiting,'Waiting for this window’s sign-in.');status.append(link,document.createTextNode(' · '),waiting);
    timer=setTimeout(poll,(state.interval||5)*1000);
  }catch(error){
    if(popup&&!popup.closed)popup.close();
    setUIText(status,'Checking sign-in result…');
    if(await confirmedSession()==='signed-in'){completeSignIn(status);return;}
    setUIText(status,error.message==='Plaza sign-in timed out. Please try again.'?'Sign-in status could not be confirmed. Please retry.':error.message);done();
  }
});
