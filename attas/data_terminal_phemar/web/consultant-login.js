import {terminalHeaders,terminalWindow} from './terminal-session.js';
import {MESSAGES} from './ui-messages.js';
const system=navigator.language||'en';
const language=/^zh-(TW|HK|Hant)/i.test(system)?'zh-Hant':/^zh/i.test(system)?'zh-Hans':system.split('-')[0];
const t=key=>MESSAGES[key]?.[language]||key;
document.documentElement.lang=language;
document.querySelector('h1').textContent=t('App Consultant');
const status=document.querySelector('[data-login-status]'),button=document.querySelector('[data-consultant-signin]');
button.textContent=t('Sign in to Plaza');
const retry=document.querySelector('[data-consultant-retry]');retry.textContent=t('Retry');
const message=document.querySelector('[data-login-message]');message.textContent=t(message.textContent);
retry.addEventListener('click',()=>location.reload());
let pending=false,timer;
async function call(path){
 const response=await fetch(path,{method:'POST',credentials:'same-origin',cache:'no-store',
  headers:terminalHeaders({'Content-Type':'application/json'}),body:'{}'});
 const body=await response.json();if(!response.ok)throw Error(body.detail||t('Plaza sign-in is unavailable.'));return body;
}
button.addEventListener('click',async()=>{
 if(pending)return;
 pending=true;button.disabled=true;status.textContent='';
 try{
  // Starting a separate Consultant login must not sign out the launching
  // Terminal or pick up another Terminal window's account.
  if(new URLSearchParams(location.search).get('consultantSession')==='own'&&terminalWindow())
   await call('./api/auth/signout');
  const url=new URL(location.href);url.searchParams.delete('terminalOrigin');
  url.searchParams.set('consultantSession','own');url.searchParams.set('terminalWindow',crypto.randomUUID().replaceAll('-',''));
  history.replaceState({},'',url);
  const result=await call('./api/auth/window-signin');
  const link=document.createElement('a');link.href=result.authorizationUrl;link.target='_blank';link.rel='noopener';
  link.textContent=t('Open Plaza sign-in');status.replaceChildren(link,document.createTextNode(' · '+t('Waiting for this window’s sign-in.')));
  async function poll(){
   try{
    const next=await call('./api/auth/window-signin/poll');
    if(next.status==='signed-in'){location.reload();return;}
    if(['authorization_pending','slow_down'].includes(next.status)){timer=setTimeout(poll,(next.interval||5)*1000);return;}
    throw Error(t('Plaza sign-in was declined.'));
   }catch(error){status.textContent=t(error.message);pending=false;button.disabled=false;}
  }
  timer=setTimeout(poll,(result.interval||5)*1000);
 }catch(error){status.textContent=t(error.message);pending=false;button.disabled=false;}
});
window.addEventListener('pagehide',()=>clearTimeout(timer),{once:true});
