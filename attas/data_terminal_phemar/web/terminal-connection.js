import {terminalHeaders} from './terminal-session.js';
import {t,setUIText,currentLocale} from './i18n.js';
import {activateWindowSession} from './window-signin.js';
// Installation credentials stay in the loopback server's encrypted vault.
let busy=false,timer=null;
// One button signs in: it starts this Terminal's request and opens Plaza in a new tab at once. Only the window
// that chose it signs itself in once Plaza approves; other windows still choose Sign in. The mark is this tab's
// own, discardable UI state.
const WANT='plaza-signin-wanted';
const wanted=()=>{try{const at=Number(sessionStorage.getItem(WANT));return !!at&&Date.now()-at<86400000;}catch{return false;}};
const want=on=>{try{on?sessionStorage.setItem(WANT,String(Date.now())):sessionStorage.removeItem(WANT);}catch{}};
// The tab opens inside the click, before Plaza answers, so a pop-up blocker lets it through.
let plazaTab=null;
function openPlazaTab(){try{const tab=window.open('about:blank','_blank');if(tab)tab.opener=null;return tab;}catch{return null;}}
// Plaza's trust page opens in the Terminal's language.
const withLanguage=url=>{try{const u=new URL(url);u.searchParams.set('lang',currentLocale());return u.href;}catch{return url;}};
async function api(path,body,method='POST') {
  const response=await fetch(path,{method,credentials:'same-origin',headers:terminalHeaders({'Content-Type':'application/json'}),body:body===undefined?undefined:JSON.stringify(body),cache:'no-store'});
  const data=await response.json();
  if(!response.ok)throw Error(data.detail||'Connection unavailable.');
  return data;
}
// The panel's renders share one live Plaza check, so Plaza is asked once.
let latest=null;   // {state, at} after a live check, or {promise} while one is running
const fresh=()=>!!latest&&!latest.promise&&Date.now()-latest.at<15000;
function liveStatus(force){
  if(latest?.promise)return latest.promise;
  if(!force&&fresh())return Promise.resolve(latest.state);
  const promise=api('/api/auth/connect/status',undefined,'GET').then(state=>{latest={state,at:Date.now()};return state;},error=>{latest=null;throw error;});
  latest={promise};return promise;
}
// One stage at a time: request access, wait for the Plaza owner, then sign in.
const REQUEST=['not-requested','rejected','cancelled','revoked','expired'];
const STAGES={
  'not-requested':{step:'Step 1 of 3 · Sign in',title:'Sign in to use {0}',text:'Sign in or create an account on {0} in the tab that opens. This window signs in as soon as {0} approves this Terminal.',action:'Sign in to {0}'},
  rejected:{step:'Step 1 of 3 · Request access',title:'The Plaza owner declined your request',text:'Request access again if you still need it.',action:'Sign in to {0}'},
  cancelled:{step:'Step 1 of 3 · Request access',title:'Your request was cancelled',text:'Request access again if you still need it.',action:'Sign in to {0}'},
  revoked:{step:'Step 1 of 3 · Request access',title:'The Plaza owner revoked this Terminal’s access',text:'Request access again to reconnect.',action:'Sign in to {0}'},
  expired:{step:'Step 1 of 3 · Request access',title:'This Terminal’s access expired',text:'Request access again to reconnect.',action:'Sign in to {0}'},
  draft:{step:'Step 1 of 3 · Finish on Plaza',title:'Finish signing in on {0}',text:'Finish on {0} in the tab that opened. This window signs in by itself when you’re done; if no tab opened, choose Open Plaza.',action:'Check status',link:'Open Plaza'},
  pending:{step:'Step 2 of 3 · Waiting for approval',title:'Waiting for the Plaza owner',text:'You’ll get an email when the owner decides. You can close this window; the Terminal keeps checking.',action:'Check status',link:'View request on Plaza'},
  scheduled:{step:'Step 3 of 3 · Sign in',title:'Access approved',text:'Sign-in opens at {0}.',action:'Check status'},
  approved:{step:'Step 3 of 3 · Sign in',title:'Access approved',text:'The Plaza owner approved this Terminal.',action:'Sign in'},
};
function plazaHost(){
  const node=document.querySelector('[data-plaza-address]');
  try{return new URL(node?.value||node?.textContent||'').host;}catch{return 'Plaza';}
}
let shown=null;  // the access step on screen, redrawn in a language chosen later
function render(state,panel,status,button){
  shown=state;
  // The Plaza owner blocked this Terminal or account: no request can be made until it is unblocked.
  const stage=state.blocked?{step:'Request access',title:state.blocked,text:'Ask the Plaza owner if you need access.',action:'Check status'}
    :STAGES[state.status]||{title:'Checking Plaza access…',text:'',action:'Check status'};
  const step=panel.querySelector('[data-access-step]'),title=panel.querySelector('[data-access-title]');
  if(step)step.textContent=stage.step?t(stage.step):'';
  if(title)title.textContent=t(stage.title,plazaHost());
  const parts=[];
  // A signed-in window's panel has no title line: the reason it is blocked leads its status.
  if(!title&&state.blocked)parts.push(t(state.blocked));
  if(state.status==='pending'&&state.ownerEmail)parts.push(t('Requested as {0}.',state.ownerEmail));
  parts.push(t(stage.text,state.status==='scheduled'&&state.validFrom?new Date(state.validFrom*1000).toLocaleString():plazaHost()));
  if(state.status==='approved')parts.push(state.ownerEmail?t('Sign in as {0} to start.',state.ownerEmail):t('Sign in to start.'));
  if(state.notifications?.some(x=>x.status==='retrying'))parts.push(t('Email delayed; Plaza will retry.'));
  if(state.checking)parts.push(t('Checking with Plaza…'));
  status.textContent=parts.filter(Boolean).join(' ');
  button.textContent=t(stage.action,plazaHost());button.dataset.stage=state.status;
  button.classList.toggle('primary',stage.action!=='Check status');
  const actions=panel.querySelector('[data-access-actions]');
  actions?.querySelectorAll('[data-access-link],[data-access-alternate]').forEach(node=>node.remove());
  if(stage.link&&state.authorizationUrl){
    const link=document.createElement('a');link.dataset.accessLink='';link.className=state.status==='draft'?'button primary':'button';
    link.href=withLanguage(state.authorizationUrl);link.target='_blank';link.rel='noopener';link.textContent=t(stage.link);
    if(!actions)status.append(' ',link);else if(state.status==='draft')button.before(link);else button.after(link);
  }
  // Other accounts sign in only once this Terminal is approved.
  if(actions&&state.status==='approved'){
    const other=document.createElement('button');other.type='button';other.className='button';other.textContent=t('Use another account');
    Object.assign(other.dataset,{windowSignin:'',alternate:'',accessAlternate:''});button.after(other);
  }
  // When Plaza can't be reached, warn above the last known step and offer another Plaza.
  panel.querySelector('[data-access-warning]')?.remove();
  if(state.offline&&panel.matches('[data-plaza-access]')){
    const warning=document.createElement('div');warning.dataset.accessWarning='';warning.className='plaza-access-warning';warning.setAttribute('role','alert');
    const heading=document.createElement('strong');heading.textContent=t('Can’t reach {0}',plazaHost());
    const text=document.createElement('p');text.textContent=t('This is the last step this Terminal saw. Try again, or connect this Terminal to another Plaza.');
    const retry=document.createElement('button');retry.type='button';retry.className='button';retry.dataset.accessRetry='';retry.textContent=t('Try again');
    warning.append(heading,text,retry);
    if(document.querySelector('input[data-plaza-address]:not(:disabled)')){
      const other=document.createElement('button');other.type='button';other.className='button';other.dataset.accessOtherPlaza='';other.textContent=t('Connect to another Plaza');warning.append(other);
    }
    panel.prepend(warning);
  }
}
async function check(action){
  const button=document.querySelector('[data-connect-plaza]');
  if(!button)return;
  // A panel drawn again while a check runs is checked once that one ends, so it never stays without its state.
  if(busy){setTimeout(()=>check(action),300);return;}
  const panel=button.closest('[data-plaza-access]')||button.parentElement;
  const status=panel.querySelector('[data-connect-status]');
  busy=true;button.disabled=true;
  try{
    // Show the saved step at once; the live answer from Plaza replaces it. The
    // panel's first render reuses the start-up check; later checks ask afresh.
    const first=!action&&!button.dataset.stage;
    if(first&&!fresh()){
      try{render({...await api('/api/auth/connect/saved',undefined,'GET'),checking:true},panel,status,button);}catch{}
    }
    let state=action==='request'?await api('/api/auth/connect',{}):await liveStatus(!first);
    if(action==='request'){
      const tab=plazaTab;plazaTab=null;
      if(state.status==='draft'&&state.authorizationUrl&&tab&&!tab.closed)tab.location.href=withLanguage(state.authorizationUrl);
      else tab?.close();
    }
    if(['rejected','cancelled','revoked','expired'].includes(state.status))want(false);
    // Approval signs in the window that chose Sign in to …; any other window chooses Sign in.
    // Sign in reuses the Terminal's approved login in any browser window.
    if((action==='signin'||wanted())&&state.status==='approved'&&!state.offline){
      if(!state.enabled)state=await api('/api/auth/connect',{});
      // A window already signed in to another account approved this Terminal for that account: it keeps its own login.
      if(state.enabled&&state.status==='approved'){state=await api('/api/auth/connect/poll',{});if(state.status==='signed-in'){want(false);if(!state.account)await api('/api/auth/window-signin/approved',{});await activateWindowSession();return;}}
    }
    render(state,panel,status,button);
    if(state.offline)timer=setTimeout(()=>check(),30000);
    // While someone finishes on Plaza, look every few seconds so this window signs in soon after.
    else if(state.enabled&&['draft','pending','scheduled'].includes(state.status))timer=setTimeout(()=>check(),state.status==='draft'&&wanted()?3000:15000);
  }catch(error){plazaTab?.close();plazaTab=null;status.textContent=t(error.message);timer=setTimeout(()=>check(),30000);}
  finally{busy=false;button.disabled=false;}
}
document.addEventListener('click',event=>{
  if(event.target.closest('[data-access-retry]')){clearTimeout(timer);check();return;}
  if(event.target.closest('[data-access-other-plaza]')){const input=document.querySelector('input[data-plaza-address]');input?.scrollIntoView({block:'center'});input?.focus();input?.select();return;}
  const button=event.target.closest('[data-connect-plaza]');if(!button)return;
  clearTimeout(timer);
  const stage=button.dataset.stage||'not-requested',blocked=!!shown?.blocked;
  if(REQUEST.includes(stage)&&!blocked&&!busy){plazaTab=openPlazaTab();want(true);}
  check(REQUEST.includes(stage)&&!blocked?'request':stage==='approved'?'signin':undefined);
});
document.addEventListener('change',async event=>{
  if(!event.target.matches('[data-network-local]'))return;
  const status=event.target.closest('.connected-settings')?.querySelector('[data-network-status]');
  try{await api('/api/network',{preferLocal:event.target.checked},'PUT');if(status)status.textContent='Connection preference saved.';}
  catch(error){if(status)status.textContent=error.message;}
});
async function initialize(){
  const connect=document.querySelector('[data-connect-plaza]:not([data-loaded])');
  if(connect){connect.dataset.loaded='true';clearTimeout(timer);check();}
  const checkbox=document.querySelector('[data-network-local]:not([data-loaded])');if(!checkbox)return;
  checkbox.dataset.loaded='true';
  try{const state=await api('/api/network',undefined,'GET');checkbox.checked=state.preferLocal;const text=state.routes.map(route=>route.route==='lan'?'Local network':route.route==='local'?'This computer':'Internet');const status=document.querySelector('[data-network-status]');if(status)status.textContent='Direct HTTPS · '+[...new Set(text)].join(', ');}
  catch{}
}
const observer=new MutationObserver(initialize);
observer.observe(document.documentElement,{subtree:true,childList:true});
initialize();

async function renderTrustedPlazas(){
  const container=document.querySelector('[data-trusted-plazas-list]:not([data-loaded])');
  if(!container)return;
  container.dataset.loaded='true';
  const status=document.querySelector('[data-trusted-plazas-status]');
  try{
    const data=await api('/api/trusted-plazas',undefined,'GET');
    container.replaceChildren();
    for(const plaza of data.plazas){
      const row=document.createElement('div');row.className='setting-row';
      const statusLabel={'not-requested':'Not requested',draft:'Request not submitted',pending:'Awaiting approval',approved:'Approved',rejected:'Rejected',cancelled:'Cancelled',revoked:'Revoked',expired:'Expired',scheduled:'Scheduled',active:'Approved'}[plaza.status]||plaza.status;
      const name=document.createElement('span');name.textContent=plaza.origin+' · '+t(statusLabel)+(plaza.current?' · '+t('Active'):'');
      // Another trusted Plaza connects this window with that Plaza's own approval, as typing its address would.
      const address=document.querySelector('[data-plaza-address-form] [data-plaza-address]');
      const connect=!plaza.current&&address?document.createElement('button'):null;
      if(connect){connect.type='button';connect.textContent=t('Connect');connect.dataset.trustedPlazaConnect=plaza.origin;connect.disabled=address.disabled;
        if(address.disabled)connect.title=t('Sign out of this window before changing its Plaza address.');
        connect.onclick=async()=>{connect.disabled=true;setUIText(status,'Checking Plaza…');try{await connectPlaza(plaza.origin);}catch(e){setUIText(status,e.message);connect.disabled=false;}};}
      const remove=document.createElement('button');remove.type='button';remove.textContent=t('Remove Plaza');
      name.title=(plaza.validFrom?new Date(plaza.validFrom*1000).toLocaleString():t('Immediately'))+' → '+(plaza.validUntil?new Date(plaza.validUntil*1000).toLocaleString():t('No expiry'));
      remove.title=t('Revoke agent trust and disconnect from this Plaza');
      remove.onclick=async()=>{remove.disabled=true;try{await api('/api/trusted-plazas/'+encodeURIComponent(plaza.id),undefined,'DELETE');row.remove();status.textContent=t('Plaza removed. Connection credentials are disabled.');if(plaza.current)location.reload();}catch(e){status.textContent=e.message;remove.disabled=false;}};
      row.append(...[name,connect,remove].filter(Boolean));container.append(row);
    }
    if(!data.plazas.length)status.textContent=t('No trusted Plazas.');
  }catch(e){status.textContent=e.message;}
}
new MutationObserver(renderTrustedPlazas).observe(document.documentElement,{childList:true,subtree:true});
renderTrustedPlazas();
// A newly connected installation asks for its language after this panel appears; redraw what t() wrote.
document.addEventListener('ui-language:changed',()=>{
  const list=document.querySelector('[data-trusted-plazas-list]');if(list){delete list.dataset.loaded;renderTrustedPlazas();}
  const button=document.querySelector('[data-connect-plaza]');
  if(button&&shown&&!busy){const panel=button.closest('[data-plaza-access]')||button.parentElement;render(shown,panel,panel.querySelector('[data-connect-status]'),button);}
});

// Changing one window's destination never copies its Plaza session or settings.
async function connectPlaza(url){await api('/api/plaza-connection',{url},'PUT');history.replaceState({},'','#plaza');location.reload();}
document.addEventListener('submit',async event=>{
 const form=event.target.closest('[data-plaza-address-form]');if(!form)return;
 event.preventDefault();event.stopImmediatePropagation();
 const status=form.querySelector('[data-plaza-address-status]'),button=form.querySelector('[type=submit]');
 if(button.disabled)return;button.disabled=true;setUIText(status,'Checking Plaza…');
 try{await connectPlaza(form.elements.url.value);}
 catch(error){setUIText(status,error.message);button.disabled=false;}
},true);
document.addEventListener('input',event=>{
 const form=event.target.closest('[data-plaza-address-form]');if(!form)return;
 const input=form.elements.url,changed=input.value.trim().replace(/\/$/,'')!==input.defaultValue.replace(/\/$/,'');
 const section=form.closest('.connected-settings');
 section?.querySelectorAll('[data-window-signin], [data-connect-plaza], [data-connected-form=signin] button').forEach(button=>button.disabled=changed);
 const status=form.querySelector('[data-plaza-address-status]');if(changed)setUIText(status,'Connect to this Plaza before signing in.');else status.replaceChildren();
});
