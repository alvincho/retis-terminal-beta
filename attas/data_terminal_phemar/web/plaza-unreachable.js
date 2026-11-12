/** A signed-in window whose Plaza can't be reached waits here, in its account's language, instead of
 * starting signed out. Its sign-in stays in the Terminal; once Plaza answers, the
 * page reloads and the window starts signed in. */
import {t,currentLocale} from './i18n.js';
import {terminalHeaders} from './terminal-session.js';

export const RETRY_MS=30000;

/** Resolves only if the person chooses to open the window without signing in. */
export function waitForPlaza(host,{retryMs=RETRY_MS,reload=()=>location.reload(),now=()=>new Date()}={}){
 return new Promise(resolve=>{
  const root=document.getElementById('app')||document.body,kept=[...root.childNodes];
  const box=document.createElement('section'),heading=document.createElement('h1'),text=document.createElement('p');
  const actions=document.createElement('div'),retry=document.createElement('button'),guest=document.createElement('button'),status=document.createElement('p');
  box.className='plaza-unreachable';box.setAttribute('role','alert');box.setAttribute('aria-labelledby','plaza-unreachable-title');
  heading.id='plaza-unreachable-title';heading.textContent=t('Can’t reach {0}',host);
  text.textContent=t('This window is still signed in. It continues by itself once {0} answers.',host);
  retry.type=guest.type='button';retry.className='button primary';guest.className='button';
  retry.textContent=t('Try again');guest.textContent=t('Open without signing in');
  status.className='plaza-unreachable-status';status.setAttribute('role','status');
  actions.className='plaza-unreachable-actions';actions.append(retry,guest);
  box.append(heading,text,actions,status);root.replaceChildren(box);
  let timer=0,checking=false,done=false;
  const stop=()=>{done=true;clearTimeout(timer);removeEventListener('online',check);document.removeEventListener('visibilitychange',visible);};
  async function check(){
   if(done||checking)return;
   checking=true;clearTimeout(timer);retry.disabled=true;status.textContent=t('Checking with Plaza…');
   try{
    const response=await fetch('/api/bootstrap?discovery=background',{credentials:'same-origin',cache:'no-store',headers:terminalHeaders()});
    const body=await response.json();
    // Plaza answers, or this window no longer holds a sign-in: start again from the top.
    if(response.ok&&!body.plazaUnreachable?.windowSignedIn){stop();reload();return;}
   }catch{}
   finally{checking=false;retry.disabled=false;}
   if(done)return;
   status.textContent=t('Last checked {0}.',now().toLocaleTimeString(currentLocale()));
   timer=setTimeout(check,retryMs);
  }
  function visible(){if(!document.hidden)check();}
  retry.addEventListener('click',check);
  guest.addEventListener('click',()=>{stop();root.replaceChildren(...kept);resolve();});
  addEventListener('online',check);document.addEventListener('visibilitychange',visible);
  timer=setTimeout(check,retryMs);
 });
}
