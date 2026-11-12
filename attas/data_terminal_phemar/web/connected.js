import './agent-view-renderer.js';
import {submitOperationForm,mountOperationRecovery,formInputs} from './neutral-operation-form.js';
import {reloadBrowserPhema as reloadPhemaOnly} from './browser-reload.js';
import {openPhemaPermissions} from './phema-permissions.js';
import './local-resources.js';
import {chatForm,selectTarget,parseArguments,targetKey} from './pulse-chat.js';
import {normalizeContentOverrides} from './content-policies.js';
import {requestFailure} from './failure-messages.js';
import {installRendererModules} from './composition-renderers.js';
import {ui as uiHTML,label,message,t,configureLocale,languageSettingsHTML,setUIText,clearUIText,attribute,validLocale,applyUI} from './i18n.js';
import {entryToPhema,phemaToEntry,browserCapabilities,hydratePhemaPayload,setBrowserAdvertisement} from './phema-transfer.js';
import {contentPanel,mountContent,openContentSnapshots,openContentPolicy} from './phema-browser.js';
import {screenshot} from './workspace-previews.js';
import {normalizeFieldBindings} from './field-bindings.js';
import {applyDisplayTimezone,displayTime,displayDateTime,displayTimeTitle,timezoneSettingsHTML} from './time-display.js';
import {applyMarketAppearance,marketSettingsHTML,MARKET_DEFAULTS,valueDirection} from './market-appearance.js';
import {applyDisplayFormat,displayFormatSettingsHTML,displayFormatValues} from './display-format.js';
import {normalizeRefreshOverrides,refreshPolicy,refreshLabel,refreshFields,refreshMessage,refreshTooltip} from './block-refresh.js';
import {browserDesign} from './browser-instruments.js';
import {layoutKind} from './layout-library.js';
import {hostedPath,providerOrigin} from './host-context.js';
import {terminalHeaders,terminalWindow,terminalURL} from './terminal-session.js';
import {watchBrowserInstance} from './browser-updates.js';
import {openingLoad,permissionReferences} from './open-refresh.js';
import {normalizeMessages,normalizeDiscussionDrafts,normalizeInteractionDrafts} from './workspace-storage.js';
import {setPulseCatalog,setCatalogError} from './plaza-pulses.js';
import {sourceSettingsHTML,installSourceSettings} from './source-settings.js';
import {pointsRowsHTML,openPointsHistory,spendingRows} from './points-history.js';
import {scheduledRunsHTML,installScheduledRuns} from './scheduled-runs.js';
import {agentTrustHTML,installAgentTrust} from './agent-trust.js';
import {watchRefresh,failedRefreshes} from './refresh-outcome.js';
export const esc = value => String(value ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
import './terminal-connection.js';
import './window-signin.js';
export const runtime={user:null,plaza:{},consultants:[],kind:'data-terminal',catalog:{publishers:[],pulses:[]},settings:{preferences:{},connections:[]}};
export async function api(path, options={}) {
  const requestedBy=runtime.user?.id;
  if(options.body?.entry && (/^\/api\/instances(?:\/|$)/.test(path))){
    const {entry,...body}=options.body;
    options={...options,body:{...body,phema:entryToPhema(entry),capabilities:browserCapabilities()}};
  }
  const response=await fetch(path,{credentials:'same-origin',...options,headers:terminalHeaders({'Content-Type':'application/json','X-Phemacast-Model-Contract':'model-requirement/1',...options.headers}),body:options.body===undefined?undefined:JSON.stringify(options.body)});
  const body=await response.json();
  // A guest request can finish after sign-in. It must not discard the newly
  // adopted account or restart its document.
  if(response.status===401&&requestedBy&&runtime.user?.id===requestedBy){runtime.user=null;location.reload();}
  if(!response.ok){const detail=typeof body.detail==='string'?body.detail:body.detail?.message||JSON.stringify(body.detail||body);const error=new Error(requestFailure(detail));error.status=response.status;error.detail=detail;throw error;}
  return hydratePhemaPayload(body);
}

// Sign-in needs this computer's user: the printed address carries the proof (terminal-session.js).
function localAccessNotice(){
  if(typeof document==='undefined'||document.querySelector('[data-local-access-notice]'))return;
  const note=document.createElement('p');note.setAttribute('role','alert');note.className='local-access-notice';note.dataset.localAccessNotice='';
  setUIText(note,'To sign in, open this Terminal from the address that "python3 terminal.py up" printed, or run "python3 terminal.py open".');
  document.body.prepend(note);
}

export async function connect({requireUser=false}={}) {
  const request=requireUser?(path,options={})=>api(path,{...options,signal:AbortSignal.timeout(10000)}):api;
  try {
    const companion=/\/(?:app-consultant|producer-browser|strategy-consultant)\.html$/.test(location.pathname);
    if(terminalWindow()&&!companion)await request('/api/auth/window-session',{method:'POST',body:{}}).catch(()=>{});
    const bootstrap=await request('/api/bootstrap?discovery=background');
    if(requireUser&&!bootstrap.user)throw new Error('Sign-in status could not be confirmed. Please retry.');
    Object.assign(runtime,bootstrap);
    if(bootstrap.localAccess==='required')localAccessNotice();
    const rendererFailures=runtime.user?await installRendererModules(request):[],advertised=await request('/api/capabilities');
    advertised.capabilities=advertised.capabilities.filter(c=>!rendererFailures.some(f=>f.id===c.id));
    setBrowserAdvertisement(advertised);
    if(runtime.user)runtime.settings=await request('/api/settings');
    delete runtime.error;
  } catch(error) {runtime.error=error.message;if(requireUser)throw error;}
  // The account's language comes first; before anyone signs in, the installation's language.
  const installation=runtime.installationLanguage||{};
  configureLocale({scope:'terminal:'+new URL(runtime.plaza.url||location.origin).origin+':'+(runtime.user?.id||'guest')+':'+terminalWindow(),saved:runtime.settings.preferences?.uiLanguage||installation.value,
   onSave:runtime.user?async value=>{await api('/api/settings',{method:'PUT',body:{uiLanguage:value}});runtime.settings.preferences.uiLanguage=value;}
    :installation.canChoose?async value=>{await api('/api/installation-language',{method:'PUT',body:{uiLanguage:value}});installation.value=value;}:null});
  applyMarketAppearance(runtime.settings.preferences?.marketAppearance);
  applyDisplayTimezone(runtime.settings.preferences?.displayTimezone);
  applyDisplayFormat(runtime.settings.preferences?.displayFormat);
  refreshDiscovery();
  watchRegistration();
  return runtime;
}
// Until this window's Plaza lists the Terminal, the top bar checks every few seconds and turns Connected without a reload.
let registrationWatch=0;
function watchRegistration(){
 if(registrationWatch||runtime.plaza.localOnly||runtime.plaza.registered||!runtime.plaza.url||typeof document==='undefined')return;
 registrationWatch=setInterval(async()=>{
  try{
   const response=await fetch('/health',{credentials:'same-origin',cache:'no-store',headers:terminalHeaders()});if(!response.ok)return;
   const health=await response.json();
   if(health.plazaRegistered){clearInterval(registrationWatch);registrationWatch=0;}
   if(health.plazaRegistered===runtime.plaza.registered&&health.plazaState===runtime.plaza.state)return;
   Object.assign(runtime.plaza,{registered:!!health.plazaRegistered,state:health.plazaState});
   const shown=document.querySelector('.topbar details.plaza-status');if(!shown)return;
   const next=document.createElement('template');next.innerHTML=statusHTML();
   const status=next.content.firstElementChild;status.open=shown.open;shown.replaceWith(status);
  }catch{}
 },5000);
}
async function refreshDiscovery(){
 try{Object.assign(runtime,await api('/api/discovery'));document.dispatchEvent(new CustomEvent('plaza:discovery'));}catch{}
}
export async function loadCatalog() {
 try {
  let offset=0,pulses=[],result;
  do {
    result=await api('/api/catalog?offset='+offset+'&limit=500'+(result?.snapshot?'&snapshot='+encodeURIComponent(result.snapshot):''));pulses.push(...result.pulses);offset+=result.pulses.length;
  } while(offset<result.total && result.pulses.length);
  runtime.catalog={...result,pulses};setPulseCatalog(runtime.catalog);
  runtime.catalogError='';document.dispatchEvent(new CustomEvent('catalog:updated'));
  return runtime.catalog;
 } catch(error) {setCatalogError(error.message);runtime.catalogError=error.message;document.dispatchEvent(new CustomEvent('catalog:updated'));throw error;}
}
document.addEventListener('catalog:refresh',()=>loadCatalog().catch(()=>{}));
export function scopedStorage(storage=sessionStorage) {
  const scope='attas-b028:'+new URL(runtime.plaza.url||location.origin).origin+':'+(runtime.user?.id||'guest')+':'+terminalWindow()+':';
  return {getItem:key=>storage.getItem(scope+key),setItem:(key,value)=>storage.setItem(scope+key,value),removeItem:key=>storage.removeItem(scope+key)};
}
const REGISTRY_STATES={registered:'Registered',success:'Registered',idle:'Not registered',retry:'Retrying',unauthorized:'Not authorized',configuration_error:'Configuration error'};
// The top bar names the Plaza this window is connecting to, or is connected to, by its host.
const plazaHost=()=>{try{return new URL(runtime.plaza.url).host;}catch{return '';}};
export function statusHTML() {
  if(runtime.plaza.localOnly)return uiHTML`<details class="plaza-status is-demo-connected"><summary aria-label="Local connection status"><span class="plaza-status-dot"></span>Local / <strong>Ready</strong></summary><section class="plaza-status-popover"><p>Using paired local Phemacast resources. Plaza login is not required.</p><button data-action="settings">Terminal settings</button></section></details>`;
  const host=plazaHost();
  return uiHTML`<details class="plaza-status ${runtime.plaza.registered?'is-demo-connected':''}"><summary aria-label="Plaza connection status" title="${esc(runtime.plaza.url||'')}"><span class="plaza-status-dot"></span><span class="plaza-status-label">${host?`<span class="plaza-status-name" data-plaza-name>${esc(host)}</span>`:'Plaza'} <span class="plaza-status-separator" aria-hidden="true">/</span> <strong>${label(runtime.plaza.registered?'Connected':host?'Connecting':'Not connected')}</strong></span></summary><section class="plaza-status-popover"><h3>${label(runtime.user?'Signed in':'Guest')}</h3><p>${runtime.user?.name?esc(runtime.user.name):label('Sign in to retrieve data and save consultant layouts.')}</p><dl class="plaza-status-facts">${host?uiHTML`<div><dt>Plaza address</dt><dd title="${esc(runtime.plaza.url)}">${esc(host)}</dd></div>`:''}<div><dt title="This Terminal's registration in the Plaza directory">Plaza directory</dt><dd>${label(REGISTRY_STATES[runtime.plaza.state]||runtime.plaza.state||'Not registered')}</dd></div><div><dt title="App Consultants this Terminal found on Plaza. They provide the layouts you can open.">Connected App Consultants</dt><dd>${runtime.consultants.length}</dd></div>${runtime.user?uiHTML`<div><dt>Available Plaza points</dt><dd data-plaza-points>—</dd></div>`:''}</dl><button data-action="plaza">Account settings ↗</button>${runtime.user?uiHTML`<p><a href="./account.html#transactions" target="_blank" rel="noopener">Transaction history ↗</a></p>`:''}</section></details>`;
}
const publisherName=id=>runtime.catalog.publishers.find(p=>p.id===id)?.name||'';
// Points spending reads Plaza's ledger, which keeps every charge (agents such as the Strategy Producer
// charge there), with this Terminal's own executions; a short cache keeps re-renders from reading it again.
let spending={at:0,rows:null,error:'',loading:null};
const fresh=()=>spending.at&&Date.now()-spending.at<30000;
function loadSpending({force=false}={}){
 if(spending.loading)return spending.loading;
 // A failed read also counts as an answer for a short while, so a redraw does not ask again at once.
 if(!force&&fresh())return spending.rows?Promise.resolve(spending.rows):Promise.reject(new Error(spending.error));
 const done=next=>{spending={...next,at:Date.now(),loading:null};document.dispatchEvent(new CustomEvent('connected:settings'));};
 spending.loading=(async()=>{
  const own=(await api('/api/billing/history')).rows||[];
  let ledger=null,error='';
  try{ledger=await api('/api/account/transactions');}catch(failure){error=failure.message||'Plaza did not answer.';}
  done({rows:spendingRows(own,ledger),error});
  return spending.rows;
 })().catch(failure=>{if(spending.loading)done({rows:null,error:failure.message||'Points spending could not be read.'});throw failure;});
 return spending.loading;
}
function pointsSpendingHTML(){
 if(!spending.loading&&!fresh())loadSpending().catch(()=>{});
 const all=spending.rows||runtime.settings.billing||[],rows=all.slice(0,10),total=spending.rows?all.length:(runtime.settings.billingTotal??rows.length);
 const older=(runtime.settings.billingToReconcile||0)-rows.filter(row=>row.canReconcile).length;
 const note=!spending.rows?(spending.error&&fresh()?uiHTML`<p class="points-warning" title="${esc(spending.error)}">Points spending could not be read. Open settings again in a moment.</p>`:uiHTML`<p class="points-note" role="status">Reading Plaza charges…</p>`):spending.error?uiHTML`<p class="points-warning" title="${esc(spending.error)}">Plaza charges could not be read; only this Terminal’s own payments are listed.</p>`:'';
 if(!total)return spending.rows?uiHTML`${note}<p>No points spent yet.</p>`:note;
 return uiHTML`${note}<div class="points-recent-head"><small>${label('Latest {0} of {1}',rows.length,total)}</small><button type="button" class="button" data-connected-action="points-history">Open history</button></div>${pointsRowsHTML(rows,publisherName)}${older>0?`<p class="points-warning">${label('{0} older executions need checking. Open the history to reconcile them.',older)}</p>`:''}`;
}
export function settingsHTML(tab='all') {
 const p=runtime.settings.preferences||{},hosted=location.pathname.startsWith('/phemar-proxy/');
 const canConfigure=runtime.permissions?.canConfigure===true;
 const account=runtime.plaza.localOnly?uiHTML`<p>Local device · Plaza is not required</p>`:hosted&&!terminalWindow()?uiHTML`<p>${esc(runtime.user?.name||'Sign in through Attas')}</p><p>Plaza login is managed by Attas.</p><a class="button" href="/" target="_top">Open Attas account</a>${runtime.kind==='data-terminal'?uiHTML`<p><a href="${esc(hostedPath("/settings.html",location))}" target="_blank" rel="noopener">Open Terminal account settings</a> to disconnect this Terminal before switching accounts.</p>`:''}`:runtime.user?uiHTML`<p>${esc(runtime.user.name)}</p><button class="button" data-connected-action="signout">Sign out</button>${runtime.plaza.delegatedLogin?uiHTML`<p><label><input type="checkbox" data-network-local checked> Prefer verified sources on this local network</label></p><p data-network-status role="status">Direct HTTPS · no tunnel</p>`:''}`:runtime.plaza.delegatedLogin?uiHTML`<section class="plaza-access" data-plaza-access><p><small data-access-step></small><br><strong data-access-title>Checking Plaza access…</strong></p><p data-connect-status role="status"></p><div data-access-actions><button class="button primary" data-connect-plaza>Request access</button><p data-window-signin-status role="status"></p></div></section>`:uiHTML`<button class="button primary" data-window-signin>Sign in to Plaza</button><button class="button" data-window-signin data-alternate>Use another account</button><p data-window-signin-status role="status"></p><details class="manual-connection"><summary>Advanced: manual sign-in</summary><form data-connected-form="signin"><label>Plaza identifier<input name="identifier" autocomplete="username" required></label><label>Password<input name="password" type="password" autocomplete="current-password" required></label><button class="button primary">Sign in to Plaza</button></form></details>`;
 const restricted=runtime.user?uiHTML`<h3>Agent settings</h3><p>Only this agent’s owner can change its configuration, layouts, and data-source connections.</p>`:uiHTML`<p>Sign in to Plaza to manage connections and save settings.</p>`;
 const sections={
  workspace:languageSettingsHTML()+(canConfigure?uiHTML`${timezoneSettingsHTML(p.displayTimezone,p.specifiedTimezone)}${displayFormatSettingsHTML(p.displayFormat)}${marketSettingsHTML(p.marketAppearance)}`:''),
  connections:(runtime.user&&!runtime.plaza.localOnly?agentTrustHTML():uiHTML`<p>Sign in to Plaza to see the agents you trust and your connection history.</p>`)+uiHTML`<p>Open an agent workspace to manage its connections and data policy.</p>`,
  sources:runtime.user||runtime.plaza.localOnly?sourceSettingsHTML():uiHTML`<p>Sign in to Plaza to manage connections and save settings.</p>`,
  runs:runtime.user||runtime.plaza.localOnly?scheduledRunsHTML():uiHTML`<p>Sign in to Plaza to see this window's scheduled runs.</p>`,
  plaza:uiHTML`<h3>Plaza account</h3>${!runtime.plaza.localOnly&&(runtime.plaza.url||runtime.plaza.canChangeAddress)?(runtime.plaza.canChangeAddress&&!hosted?uiHTML`<form class="plaza-address-form" data-plaza-address-form><label>Plaza address<input name="url" type="url" value="${esc(runtime.plaza.url)}" data-plaza-address required autocomplete="url" spellcheck="false" ${runtime.user?'disabled':''}></label><button type="submit" class="button" ${runtime.user?'disabled':''}>Connect</button><p data-plaza-address-status role="status">${runtime.user?label('Sign out of this window before changing its Plaza address.'):!runtime.plaza.url?label('Enter a Plaza address to connect this Terminal.'):''}</p></form>`:uiHTML`<dl class="plaza-login-address"><dt>Plaza address</dt><dd data-plaza-address>${esc(runtime.plaza.url)}</dd></dl>`):''}${runtime.plaza.delegatedLogin?uiHTML`<section data-trusted-plazas><h3>Trusted Plazas</h3><div data-trusted-plazas-list></div><p data-trusted-plazas-status role="status"></p><details><summary>How to connect</summary><p>Request access, wait for the Plaza owner to approve this Terminal, then sign in. Each window uses one Plaza at a time; choose Connect beside a trusted Plaza to switch this window. Each Plaza keeps its own approval and credentials.</p></details></section>`:''}${runtime.kind==='data-terminal'?uiHTML`<p>Each Terminal window has its own Plaza login, settings and open Phemas.</p>`:''}${account}${runtime.user&&runtime.plaza.delegatedLogin&&!runtime.plaza.trusted?uiHTML`<button class="button" data-connect-plaza>Request access</button><p data-connect-status role="status"></p>`:''}${runtime.user?uiHTML`<p><a class="button" href="./account.html#profile" target="_blank" rel="noopener">Account profile ↗</a> <a class="button" href="./account.html#transactions" target="_blank" rel="noopener">Transaction history ↗</a></p>`:''}${canConfigure?uiHTML`<h3>Points spending</h3><p>Execution payments use Plaza points. Each owning agent controls its operations, permissions and data policy.</p>${pointsSpendingHTML()}`:''}`
 };
 const local=runtime.plaza.localDiscovery?uiHTML`<section data-local-resources><h3>Local Phemacast resources</h3><button type="button" data-local-scan>Discover</button><details><summary>How to connect</summary><p>Enable local connections and Bonjour on a source or consultant. Pair here, then ask its owner to approve the displayed code on that resource’s host. Pairing grants only the operations the owner shares. No Plaza sign-in is required in local mode.</p></details><div data-local-list></div><p data-local-status role="status"></p></section>`:'';
 const content=tab==='all'?sections.workspace+local+sections.connections+sections.sources+sections.plaza:(tab==='connections'?local:'')+(sections[tab]||'');
 return uiHTML`<section class="connected-settings">${content}<p data-connected-feedback role="status"></p></section>`;
}
document.addEventListener('submit',async event=>{
 const form=event.target.closest('[data-connected-form]');if(!form)return;event.preventDefault();event.stopImmediatePropagation();
 const feedback=form.parentElement.querySelector('[data-connected-feedback]')||document.querySelector('[data-connected-feedback]');
 const values=Object.fromEntries(new FormData(form));
 try {
  if(form.dataset.connectedForm==='signin'){await api('/api/auth/signin',{method:'POST',body:values});location.reload();return;}
  if(form.dataset.connectedForm==='time-display')await api('/api/settings',{method:'PUT',body:{displayTimezone:values.timeZoneMode==='specified'?values.specifiedTimezone:values.timeZoneMode,specifiedTimezone:form.elements.specifiedTimezone.value}});
  if(form.dataset.connectedForm==='display-format')await api('/api/settings',{method:'PUT',body:{displayFormat:displayFormatValues(values)}});
  if(form.dataset.connectedForm==='market-appearance'){await api('/api/settings',{method:'PUT',body:{marketAppearance:{upColor:values.upColor,downColor:values.downColor,colorChanges:values.colorChanges==='on',flashEnabled:values.flashEnabled==='on',flashColor:values.flashColor,flashDurationMs:Math.round(Number(values.flashDuration)*1000)}}});}
  runtime.settings=await api('/api/settings');applyMarketAppearance(runtime.settings.preferences.marketAppearance);applyDisplayTimezone(runtime.settings.preferences.displayTimezone);applyDisplayFormat(runtime.settings.preferences.displayFormat);if(form.dataset.connectedForm==='display-format')rendering();if(form.dataset.connectedForm==='time-display'){try{scopedStorage().setItem('display-timezone',runtime.settings.preferences.displayTimezone);}catch{}rendering();}if(form.dataset.connectedForm==='market-appearance'){try{scopedStorage().setItem('market-appearance',JSON.stringify(runtime.settings.preferences.marketAppearance));}catch{}setUIText(form.querySelector('[data-market-feedback]'),'Saved.');}if(feedback)setUIText(feedback,'Saved.');
  document.dispatchEvent(new CustomEvent('connected:settings'));
 }catch(error){if(feedback){clearUIText(feedback);feedback.textContent=error.message;}}
},true);
document.addEventListener('click',async event=>{
 const button=event.target.closest('[data-connected-action]');if(!button)return;
 try {
  if(button.dataset.connectedAction==='signout'){await api('/api/auth/signout',{method:'POST',body:{}});location.reload();}
  if(button.dataset.connectedAction==='points-history'){openPointsHistory({load:async()=>({rows:await loadSpending()}),agentName:publisherName});return;}
  if(button.dataset.connectedAction==='reconcile'){await api('/api/billing/'+button.dataset.id+'/reconcile',{method:'POST',body:{}});runtime.settings=await api('/api/settings');spending.at=0;document.dispatchEvent(new CustomEvent('connected:settings'));return;}
 }catch(error){const f=document.querySelector('[data-connected-feedback]');if(f)f.textContent=error.message;}
});


const nodes=new Map();let latest=[];let currentWorkspace;let rendering=()=>{};
document.addEventListener('catalog:updated',()=>rendering());
// Opening the account menu reads the balance from Plaza, which keeps the points.
document.addEventListener('toggle',async event=>{
 const cell=event.target.matches?.('details.plaza-status[open]')&&event.target.querySelector('[data-plaza-points]');if(!cell)return;
 try{const {account}=await api('/api/account/transactions');cell.textContent=Number(account.balance).toLocaleString();}catch{cell.textContent='—';}
},true);
window.addEventListener('pagehide',()=>{for(const node of nodes.values()){node.events?.close();if(node.remoteId&&!node.viewOnly)fetch('/api/instances/'+node.remoteId,{method:'DELETE',credentials:'same-origin',keepalive:true,headers:terminalHeaders()}).catch(()=>{});}});
export function syncBrowsers(browsers,workspaceId,onUpdate) {
 latest=browsers;currentWorkspace=workspaceId;rendering=onUpdate;
 if(!runtime.user)return;
 const ids=new Set(browsers.map(b=>b.id));
 for(const [id,entry] of nodes)if(!ids.has(id)){entry.cancelled=true;entry.events?.close();if(entry.remoteId)api('/api/instances/'+entry.remoteId,{method:'DELETE'}).catch(()=>{});nodes.delete(id);}
 for(const b of browsers){
  const design=browserDesign(b.design,b.instrumentOverrides);
  b.fieldBindings=normalizeFieldBindings(b.fieldBindings,browserState(b).entry?.design.dataRequirements||design.dataRequirements);
  const signature=JSON.stringify([design,b.customLayout?.composition,b.instrumentOverrides,b.parameterValues,b.layoutTabs]);let entry=nodes.get(b.id);let released=Promise.resolve();
  if(entry?.signature===signature){
    if(entry.remoteId&&entry.range!==b.range){
      entry.range=b.range;const range=b.range;
      entry.rangeUpdate=(entry.rangeUpdate||Promise.resolve()).catch(()=>{}).then(()=>{
        if(entry.cancelled)return;
        return api('/api/instances/'+entry.remoteId,{method:'PUT',body:{range}}).then(remote=>{if(!entry.cancelled&&entry.range===range){entry.state=remote;rendering();}});
      }).catch(error=>{entry.state.error=error.message;rendering();});
    }
    continue;
  }
  if(entry?.remoteId&&entry.designSignature===JSON.stringify(design)){
    entry.signature=signature;
    api('/api/instances/'+entry.remoteId,{method:'PUT',body:{entry:{...b.customLayout,design},layoutTabs:b.layoutTabs,parameterValues:b.parameterValues,instrumentOverrides:b.instrumentOverrides}}).then(remote=>{entry.state=remote;rendering();}).catch(error=>{entry.state.error=error.message;rendering();});continue;
  }
  if(entry){entry.cancelled=true;entry.events?.close();if(entry.remoteId)released=api('/api/instances/'+entry.remoteId,{method:'DELETE'}).catch(()=>{});}
  entry={signature,designSignature:JSON.stringify(design),range:b.range,state:{status:'loading',results:{}}};nodes.set(b.id,entry);
  if(!b.customLayout){nodes.delete(b.id);continue;}
  const layout=b.customLayout;
  entry.ready=released.then(()=>api('/api/instances',{method:'POST',body:{workspaceId,layoutTabs:b.layoutTabs,parameterValues:b.parameterValues,instrumentOverrides:b.instrumentOverrides,sourceMode:b.sourceMode||'parameters',snapshotId:b.contentSnapshotId,uiLanguage:b.uiLanguage||null,fieldBindings:normalizeFieldBindings(b.fieldBindings),contentOverrides:normalizeContentOverrides(b.contentOverrides),refreshOverrides:normalizeRefreshOverrides(b.refreshOverrides),range:b.range,command:b.command,messages:normalizeMessages(b.messages),entry:{...layout,design}}})).then(async remote=>{
    if(entry.cancelled){await api('/api/instances/'+remote.id,{method:'DELETE'});return;}
    entry.remoteId=remote.id;entry.state=remote;if((b.uiLanguage||null)!==(remote.uiLanguage||null))await api('/api/instances/'+remote.id,{method:'PUT',body:{uiLanguage:b.uiLanguage||null}});if(entry.range!==b.range)syncBrowsers(latest,currentWorkspace,rendering);
    entry.events=watchBrowserInstance(api,'/api/instances/'+remote.id,state=>{entry.state=state;retryUnconfirmedTrust(b,state);noticeFailedRefresh(b.id,entry,state);
      if(state.phema){const current=phemaToEntry(state.phema);if(JSON.stringify(current)!==JSON.stringify(b.customLayout)){
        b.customLayout=current;b.design=browserDesign(current.design,state.instrumentOverrides);b.layoutId=layoutKind(current.baseLayoutId);
        b.parameterValues=state.parameterValues||{};b.instrumentOverrides=state.instrumentOverrides||{};b.layoutTabs=state.layoutTabs||{};
        const design=browserDesign(b.design,b.instrumentOverrides);entry.designSignature=JSON.stringify(design);entry.signature=JSON.stringify([design,current.composition,b.instrumentOverrides,b.parameterValues,b.layoutTabs]);
        document.dispatchEvent(new CustomEvent('browser:phema-edited',{detail:{id:b.id}}));document.dispatchEvent(new CustomEvent('browser:interaction'));
      }}
const contentChanged=b.sourceMode!==(entry.state.sourceMode||'parameters')||(entry.state.sourceMode==='snapshot'&&b.contentSnapshotId!==entry.state.content?.snapshotId);b.sourceMode=entry.state.sourceMode||'parameters';b.contentSnapshotId=b.sourceMode==='snapshot'?entry.state.content?.snapshotId:undefined;if(contentChanged)document.dispatchEvent(new CustomEvent('browser:interaction'));b.uiLanguage=entry.state.uiLanguage||null;const messages=normalizeMessages(entry.state.messages);if(JSON.stringify(messages)!==JSON.stringify(b.messages||[])){b.messages=messages;document.dispatchEvent(new CustomEvent('browser:interaction'));}loadOpenedPanels(entry,b);rendering();},error=>{
      if(entry.cancelled)return;entry.state.transport='Reconnecting';
      if(error.status===404){entry.signature=null;entry.designSignature=null;syncBrowsers(latest,currentWorkspace,rendering);return;}
      if(error.status===401)location.reload();else rendering();
    },{initial:remote});loadOpenedPanels(entry,b);rendering();
  }).catch(error=>{entry.state={status:'unavailable',error:error.message,results:{}};rendering();});
 }
}
/** The picture saved with a Browser snapshot, shown when New Data Browser offers that snapshot. */
async function saveSnapshotScreenshot(browserId,snapshotId){
 const node=document.querySelector(`[data-browser="${CSS.escape(browserId)}"]`);
 if(!/^[a-f0-9]{64}$/.test(snapshotId||'')||!node?.isConnected||!node.clientWidth||!node.clientHeight||node.classList.contains('focus-hidden'))return;
 const image=await screenshot(node);
 if(image)await api('/api/snapshot-previews/'+snapshotId,{method:'PUT',body:{image,capturedAt:Date.now()},signal:AbortSignal.timeout(8000)});
}
/** Once per Phema opened from New Data Browser, its panels that wait for ↻ load together, as one ↻ for each. */
function loadOpenedPanels(entry,b){
 if(!b.loadOnOpen||!entry.remoteId||entry.cancelled)return;
 const references=openingLoad(entry.state,b.customLayout);if(!references)return;
 b.loadOnOpen=false;
 if(references.length)api('/api/instances/'+entry.remoteId+'/content',{method:'POST',body:{references}}).then(()=>rendering()).catch(()=>{});
}
/** After ♧ approves or revokes, the panels say so at once, without ↻: revoked ones ask for approval
 * again; approved ones show Loading while their reads load again, as one ↻ for each. */
function reloadAfterPermission(entry,b,change='approved'){
 if(!entry?.remoteId||entry.cancelled)return;
 const id=entry.remoteId;
 // The Terminal's own view of the decision replaces this page's, whatever the next poll brings.
 const reread=()=>api('/api/instances/'+id).then(state=>{if(!entry.cancelled&&entry.remoteId===id){entry.state=state;rendering();}}).catch(()=>{});
 // A snapshot shows its saved content; approving or revoking never loads live data.
 if(entry.state?.sourceMode==='snapshot'){reread();return;}
 const results=entry.state?.content?.results||{};
 const references=change==='revoked'?[]:permissionReferences(entry.state?.phema||b?.customLayout,results);
 const shown=(ref,result)=>change==='revoked'?{status:'blocked',reason:'permission-required'}
  :references.includes(ref)||result?.status==='blocked'?{...result,status:'resolving',reason:undefined}:result;
 if(entry.state?.content)entry.state={...entry.state,content:{...entry.state.content,results:Object.fromEntries(Object.entries(results).map(([ref,result])=>[ref,shown(ref,result)]))}};
 rendering();
 if(!references.length){reread();return;}
 api('/api/instances/'+id+'/content',{method:'POST',body:{references}}).then(()=>rendering()).catch(()=>reread());
}
/** Update the same Browser process; presentation changes do not reconnect data. */
export async function applyBrowserPhemaEdit(b,next,settings){
 let node=nodes.get(b.id);if(node?.ready&&!node.remoteId)await node.ready;node=nodes.get(b.id);if(!node?.remoteId)throw new Error('Browser is still initializing. Try again shortly.');
 const body={entry:next,command:next.command,parameterValues:b.parameterValues||{},instrumentOverrides:b.instrumentOverrides||{},layoutTabs:b.layoutTabs||{},...(settings||{})};
 const remote=await api('/api/instances/'+node.remoteId,{method:'PUT',body});
 b.customLayout=next;b.layoutId=layoutKind(next.baseLayoutId);b.design=browserDesign(next.design,remote.instrumentOverrides);b.command=remote.command;b.commandDraft=remote.command;b.ticker=remote.entry.design.params.ticker?.default||remote.entry.design.params.portfolioId?.default||'';b.tickers=remote.entry.design.params.tickers?.default||[];
 for(const key of ['fieldBindings','refreshOverrides','contentOverrides','instrumentOverrides','parameterValues','layoutTabs','range','uiLanguage'])b[key]=remote[key]??(key==='range'?'1M':key==='uiLanguage'?null:{});
 delete b.layoutConflict;delete b.layoutPublication;delete b.consultantId;
 node.state=remote;node.events?.accept(remote);const design=browserDesign(b.design,b.instrumentOverrides);
 node.designSignature=JSON.stringify(design);node.signature=JSON.stringify([design,next.composition,b.instrumentOverrides,b.parameterValues,b.layoutTabs]);node.range=b.range;
 document.dispatchEvent(new CustomEvent('browser:interaction'));rendering();return remote;
}
// A Browser with no layout has nothing to load: it says so instead of Loading forever.
export function browserState(b){const state=nodes.get(b.id)?.state||{status:runtime.user?(b.customLayout?'loading':'empty'):'Sign in to retrieve data',results:{}};return b.layoutConflict?{...state,layoutConflict:b.layoutConflict}:state;}
export function browserRequirements(b){return browserState(b).entry?.design.dataRequirements||b.design.dataRequirements;}
export function browserPanels(b){return [...b.design.layout.children,...Object.values(browserState(b).nestedLayouts||{}).flatMap(entry=>entry.design.layout.children)];}
export function browserCompany(b){const values=Object.values(browserState(b).results||{});return values.flatMap(v=>v.rows||[]).find(r=>r.ticker===b.ticker&&r.name)?.name||b.ticker;}

// Editors may stay open while polling replaces a worker after a host restart.
// Resolve its current handle at submission, not when the dialog was opened.
async function requestBrowserInstance(b,suffix='',options={}){
 for(let attempt=0;attempt<2;attempt++){
  let node=nodes.get(b.id);
  if(node?.ready&&!node.remoteId)await node.ready;
  node=nodes.get(b.id);
  if(!latest.includes(b)||!node?.remoteId)throw new Error('Browser is connecting. Try again shortly.');
  try{
   const value=await api('/api/instances/'+node.remoteId+suffix,options);
   if(!suffix&&options.method==='PUT'){node.state=value;node.events?.accept(value);}
   return value;
  }catch(error){
   // This specific pool error occurs before execution. Do not replay other
   // failures, or resurrect an explicitly closed/view-only Browser.
   if(attempt||error.status!==404||error.message!=='Browser unavailable in this Terminal window.'||node.viewOnly||!latest.includes(b))throw error;
   if(nodes.get(b.id)===node){node.signature=null;node.designSignature=null;syncBrowsers(latest,currentWorkspace,rendering);}
  }
 }
}
export function panelHTML(panel,b,style){
 const state=browserState(b);
 if(b.customLayout?.composition){const content=contentPanel(panel,b,state,style);requestAnimationFrame(()=>mountProtectedContent());if(content!==null)return content;}
 return uiHTML`<section class="template-panel" data-panel-id="${esc(panel.id)}"><p>Open publisher-provided content from an agent workspace.</p></section>`;
}
function replyText(value) {
 if(typeof value==='string') {try {const parsed=JSON.parse(value);if(parsed&&typeof parsed==='object')return replyText(parsed);}catch{}return value;}
 if(!value||typeof value!=='object')return String(value??'');
 for(const key of ['outgoing_text','answer','text','response','message','content','result','output'])if(value[key]!=null)return replyText(value[key]);
 return JSON.stringify(value,null,2);
}

// A short note in the Terminal's toast line.
function notice(text){const toast=document.getElementById('toast');if(!toast)return;toast.textContent=text;toast.classList.add('visible');clearTimeout(notice.timer);notice.timer=setTimeout(()=>toast.classList.remove('visible'),3500);}
// Plaza did not answer the trust check: the panels say so, and the Browser reloads once, a few seconds later, as ↻ would.
const trustRetries=new Map();
const refreshWatches=new Map();
// A ↻ the Terminal accepted that ends with only earlier data says so, as a ↻ it couldn't accept does.
function noticeFailedRefresh(browserId,entry,state){if(failedRefreshes(refreshWatches,browserId,entry.remoteId,state).length)notice(t('Couldn’t get new data. The panel still shows earlier data; press ↻ again once the connection is back.'));}
/** Panels that failed only because the user's own paired local source stopped reload by themselves once it is back,
 * as one ↻ for each. A paired copy that is down is never replaced by Plaza's source, and a local
 * source costs nothing; panels reading from Plaza still wait for ↻. Retries back off from 15 seconds to 2 minutes. */
const LOCAL_DOWN='local-source-unreachable',localRetries=new Map();
export function localSourcePanels(state){
 if(state?.sourceMode==='snapshot')return [];
 return Object.entries(state?.content?.results||{}).filter(([,r])=>['unavailable','failed','stale'].includes(r?.status)&&r.reason===LOCAL_DOWN).map(([ref])=>ref);
}
export async function retryLocalSources(now=Date.now()){
 const waiting=[];
 for(const [id,entry] of nodes){
  const references=entry.remoteId&&!entry.cancelled?localSourcePanels(entry.state):[];
  if(!references.length){localRetries.delete(id);continue;}
  const last=localRetries.get(id);
  if(!last||now>=last.next)waiting.push([id,entry,references,last?.attempts||0]);
 }
 if(!waiting.length)return;
 let value;try{value=await api('/api/local-resources');}catch{return;}
 const paired=(value?.resources||[]).filter(r=>r.paired&&!r.identityChanged);
 // Discovery says when a paired source is back; without discovery, a quiet ↻ asks the source itself.
 const back=value?.status==='discovering'?paired.some(r=>r.online===true):paired.length>0;
 if(!back)return;
 for(const [id,entry,references,attempts] of waiting){
  localRetries.set(id,{attempts:attempts+1,next:now+Math.min(15000*2**attempts,120000)});
  api('/api/instances/'+entry.remoteId+'/content',{method:'POST',body:{references}}).then(()=>rendering()).catch(()=>{});
 }
}
setInterval(()=>{if(!document.hidden)retryLocalSources().catch(()=>{});},15000);
function retryUnconfirmedTrust(b,state){
 if(!state?.security?.trustUnconfirmed||trustRetries.has(b.id))return;
 trustRetries.set(b.id,setTimeout(()=>{trustRetries.delete(b.id);document.querySelector('[data-action="reload-phema"][data-id="'+CSS.escape(b.id)+'"]')?.click();},5000));
}
document.addEventListener('click',async event=>{
 const button=event.target.closest('[data-content-refresh],[data-content-settings],[data-content-config],[data-phema-permissions]');if(!button)return;
 const [browserId,...parts]=(button.dataset.contentRefresh||button.dataset.contentSettings||button.dataset.contentConfig||button.dataset.phemaPermissions).split('/');const reference=parts.join('/');let entry=nodes.get(browserId);
 // After a connection comes back the Browser is opened again; a ↻ pressed meanwhile runs once it is ready.
 if(!entry?.remoteId&&entry?.ready&&button.dataset.contentRefresh){button.disabled=true;try{await entry.ready;}catch{}finally{button.disabled=false;}entry=nodes.get(browserId);}
 if(!entry?.remoteId){if(button.dataset.phemaPermissions)notice(t('Permissions open once this layout has loaded.'));else if(button.dataset.contentRefresh)notice(t('This layout is still opening. Press ↻ again in a moment.'));return;}
 try{if(button.dataset.phemaPermissions){await openPhemaPermissions(entry.remoteId,api,change=>reloadAfterPermission(entry,latest.find(b=>b.id===browserId),change),button.closest('[data-browser]'));}else if(button.dataset.contentConfig){await openContentPolicy(entry.remoteId,reference,api,values=>{const b=latest.find(b=>b.id===browserId);if(b){b.contentOverrides=normalizeContentOverrides(values);document.dispatchEvent(new CustomEvent('browser:interaction'));}});}else if(reference){button.disabled=true;const answer=await api('/api/instances/'+entry.remoteId+'/content',{method:'POST',body:{references:[reference]}});watchRefresh(refreshWatches,browserId,entry.remoteId,reference,answer);rendering();}else await openContentSnapshots(entry.remoteId,api,snapshotId=>saveSnapshotScreenshot(browserId,snapshotId),values=>{const b=latest.find(b=>b.id===browserId);if(b){b.contentOverrides=normalizeContentOverrides(values);document.dispatchEvent(new CustomEvent('browser:interaction'));}});}
 catch(error){button.title=error.message;
  // A ↻ that can't reach its data says so, and the layout shows it still has the earlier data.
  if(button.dataset.contentRefresh&&reference){notice(t('Couldn’t get new data. The panel still shows earlier data; press ↻ again once the connection is back.'));if(entry.state&&(!error.status||error.status>=500)){entry.state.transport='Reconnecting';rendering();}}}
 finally{button.disabled=false;}
});
function renderMessages(messages) {return messages.map(m=>uiHTML`<article class="conversation-message"><strong>${m.role==='user'?label('You'):esc(runtime.catalog.publishers.find(p=>p.id===m.agentId)?.name||t('Pulser'))}${m.pulseName?' · '+esc(m.pulseName):''}</strong><div style="white-space:pre-wrap">${esc(replyText(m.text))}</div></article>`).join('');}
function rememberChat(form){
 const b=latest.find(b=>b.id===form.dataset.conversation);if(!b)return;
 const nodeId=form.elements.nodeId.value,prior=b.interactionDrafts?.[nodeId]||{};
 const active={agentId:form.elements.agentId.value,pulseId:form.elements.pulseId.value,text:form.elements.text.value,arguments:Object.fromEntries([...form.querySelectorAll('[data-argument]')].map(f=>[f.dataset.argument,f.value])),...(form.querySelector('[data-raw-arguments]')?{rawArguments:form.querySelector('[data-raw-arguments]').value}:{})};
 b.interactionDrafts=normalizeInteractionDrafts({...b.interactionDrafts,[nodeId]:{...active,targets:{...prior.targets,[targetKey(active.agentId,active.pulseId)]:active}}});
 document.dispatchEvent(new CustomEvent('browser:interaction'));
}
document.addEventListener('input',event=>{const form=event.target.closest('[data-conversation]');if(form&&!['agentId','pulseId'].includes(event.target.name))rememberChat(form);});
document.addEventListener('change',event=>{
 const form=event.target.closest('[data-conversation]');if(!form||!['agentId','pulseId'].includes(event.target.name)||form.dataset.pending)return;
 const b=latest.find(b=>b.id===form.dataset.conversation);if(!b)return;
 const nodeId=form.elements.nodeId.value,prior=b.interactionDrafts?.[nodeId]||{};
 const agentId=form.elements.agentId.value,pulseId=event.target.name==='agentId'?'':form.elements.pulseId.value;
 b.interactionDrafts=normalizeInteractionDrafts({...b.interactionDrafts,[nodeId]:{agentId,pulseId,targets:prior.targets||{}}});
 const surface=form.closest('dialog')||form.parentElement.parentElement;form.parentElement.outerHTML=discussionHTML(b,nodeId);applyUI(surface);document.dispatchEvent(new CustomEvent('browser:interaction'));
});
export function discussionHTML(b,nodeId='') {
 const child=browserPanels(b).find(n=>n.id===nodeId)||browserPanels(b).find(n=>n.type==='phemar'||n.template==='pulse-chat');
 if(!child)return uiHTML`<p>No Pulse chat in this layout.</p>`;
 const saved=b.interactionDrafts?.[child.id]||{},target=selectTarget(runtime.catalog,child,saved),requirement=browserRequirements(b).find(r=>r.id===child.requirementId);
 const history=(browserState(b).messages||b.messages||[]).filter(m=>m.nodeId===child.id&&(!m.agentId||m.agentId===target.agentId)&&(!m.pulseId||m.pulseId===target.pulse?.id));
 return uiHTML`<div class="settings-body pulse-chat"><h2>${esc(child.title||'Pulse chat')}</h2>${chatForm({catalog:runtime.catalog,child,saved,arguments:requirement?.arguments||{},draft:b.discussionDrafts?.[child.id]||'',browserId:b.id,snapshot:(browserState(b).sourceMode||b.sourceMode)==='snapshot',label})}<div class="conversation-history" data-conversation-result>${renderMessages(history)}</div></div>`;
}
document.addEventListener('submit',async e=>{
 const f=e.target.closest('[data-conversation]');if(!f)return;e.preventDefault();e.stopImmediatePropagation();if(f.dataset.pending)return;
 const feedback=f.querySelector('[role=status]'),out=f.parentElement.querySelector('[data-conversation-result]'),button=f.querySelector('[data-chat-submit]');
 try{
  const id=nodes.get(f.dataset.conversation)?.remoteId;if(!id)throw new Error('Sign in and wait for the Browser instance.');
  const argumentsValue=parseArguments([...f.querySelectorAll('[data-argument]')].map(field=>({key:field.dataset.argument,type:field.dataset.argumentType,value:field.value})),f.querySelector('[data-raw-arguments]')?.value);
  rememberChat(f);const body={nodeId:f.elements.nodeId.value,agentId:f.elements.agentId.value,pulseId:f.elements.pulseId.value,text:f.elements.text.value,arguments:argumentsValue};
  f.dataset.pending='true';const controls=[...f.elements].map(node=>[node,node.disabled]);controls.forEach(([node])=>node.disabled=true);f._controls=controls;setUIText(feedback,'Waiting for Pulser…');
  const r=await api('/api/instances/'+id+'/conversation',{method:'POST',body});
  const selected=r.messages.filter(m=>(!m.agentId||m.agentId===body.agentId)&&(!m.pulseId||m.pulseId===body.pulseId));out.innerHTML=renderMessages(selected);
  const b=latest.find(b=>b.id===f.dataset.conversation);if(b){b.messages=normalizeMessages([...(b.messages||[]).filter(m=>m.nodeId!==body.nodeId),...r.messages]);b.discussionDrafts??={};delete b.discussionDrafts[body.nodeId];const remote=nodes.get(b.id);if(remote?.state)remote.state.messages=b.messages;}
  f.elements.text.value='';rememberChat(f);setUIText(feedback,'Complete');
 }catch(error){clearUIText(feedback);feedback.textContent=error.message;}
 finally{delete f.dataset.pending;f._controls?.forEach(([node,disabled])=>node.disabled=disabled);delete f._controls;}
},true);

export async function attachBrowserInstance(id,onUpdate) {
 const remote=await api('/api/instances/'+encodeURIComponent(id));
 const b={customLayout:remote.phema?phemaToEntry(remote.phema):remote.entry,contentOverrides:remote.contentOverrides||{},instrumentOverrides:remote.instrumentOverrides||{},sourceMode:remote.sourceMode,layoutTabs:remote.layoutTabs||{},parameterValues:remote.parameterValues||{},uiLanguage:remote.uiLanguage||null,fieldBindings:remote.fieldBindings||{},refreshOverrides:remote.refreshOverrides||{},id:remote.id,layoutId:remote.entry.baseLayoutId,design:remote.entry.design,range:remote.range,command:remote.command,ticker:remote.entry.design.params.ticker?.default||remote.entry.design.params.tickers?.default?.join(' / ')||remote.entry.design.params.portfolioId?.default||''};
 try{const saved=JSON.parse(scopedStorage().getItem('browser-draft:'+b.id)||'{}');b.discussionDrafts=normalizeDiscussionDrafts(saved.discussionDrafts);b.interactionDrafts=normalizeInteractionDrafts(saved.interactionDrafts);}catch{}
 rendering=()=>onUpdate(b);
 latest.push(b);const entry={remoteId:remote.id,state:remote,viewOnly:true};nodes.set(b.id,entry);
 entry.events=watchBrowserInstance(api,hostedPath('/api/instances/'+remote.id,location),state=>{entry.state=state;retryUnconfirmedTrust(b,state);noticeFailedRefresh(b.id,entry,state);const contentChanged=b.sourceMode!==(entry.state.sourceMode||'parameters')||(entry.state.sourceMode==='snapshot'&&b.contentSnapshotId!==entry.state.content?.snapshotId);b.sourceMode=entry.state.sourceMode||'parameters';b.contentSnapshotId=b.sourceMode==='snapshot'?entry.state.content?.snapshotId:undefined;if(contentChanged)document.dispatchEvent(new CustomEvent('browser:interaction'));b.layoutTabs=entry.state.layoutTabs||{};b.parameterValues=entry.state.parameterValues||{};b.uiLanguage=entry.state.uiLanguage||null;b.fieldBindings=entry.state.fieldBindings||{};b.refreshOverrides=entry.state.refreshOverrides||{};b.customLayout=entry.state.phema?phemaToEntry(entry.state.phema):entry.state.entry;b.instrumentOverrides=entry.state.instrumentOverrides||{};b.contentOverrides=entry.state.contentOverrides||{};b.design=entry.state.entry.design;b.range=entry.state.range;b.command=entry.state.command;b.ticker=b.design.params.ticker?.default||b.design.params.tickers?.default?.join(' / ')||b.design.params.portfolioId?.default||'';onUpdate(b);},error=>{entry.state.transport='Reconnecting';if(error.status===404)entry.state.error='This browser instance has closed.';onUpdate(b);},{initial:remote});
 return b;
}

document.addEventListener('browser:interaction',()=>{for(const b of latest)if(nodes.get(b.id)?.viewOnly)try{scopedStorage().setItem('browser-draft:'+b.id,JSON.stringify({discussionDrafts:b.discussionDrafts,interactionDrafts:b.interactionDrafts}));}catch{}});

document.addEventListener('click',async event=>{
 const button=event.target.closest('[data-block-config],[data-block-refresh]');if(!button)return;
 const [browserId,id]=(button.dataset.blockConfig||button.dataset.blockRefresh).split('/');
 const b=latest.find(b=>b.id===browserId),remote=nodes.get(browserId);if(!b||!remote)return;
 const send=async body=>{const state=await requestBrowserInstance(b,'',{method:'PUT',body});b.fieldBindings=state.fieldBindings||{};b.refreshOverrides=state.refreshOverrides||{};document.dispatchEvent(new CustomEvent('browser:interaction'));rendering();};
 const dialog=document.createElement('dialog');dialog.className='connected-reader block-settings';dialog.dataset.uiLocale=b.uiLanguage||'';
 document.body.append(dialog);dialog.onclose=()=>dialog.remove();
 if(button.dataset.blockRefresh){
  button.disabled=true;
  try{await send({refreshRequirement:id});dialog.remove();}catch(error){dialog.innerHTML=uiHTML`<p role="alert">${esc(error.message)}</p><button data-dismiss>Close</button>`;dialog.querySelector('button').onclick=()=>dialog.close();dialog.showModal();}finally{button.disabled=false;}return;
 }
 const panel=browserPanels(b).find(p=>p.id===id);if(!panel){dialog.remove();return;}
 const reqs=browserRequirements(b).filter(r=>[panel.requirementId,...(panel.supplementalRequirementIds||[])].includes(r.id)&&r.mode!=='interactive');
 dialog.innerHTML=uiHTML`<form><h2>${label('{0} · Settings',message(panel.title))}</h2><p>Updates for this browser. App Consultant maintains the layout defaults.</p>${reqs.map(r=>uiHTML`<fieldset data-rid="${esc(r.id)}"><legend>${label(r.label)}</legend><label><input type="checkbox" name="useDefault" ${b.refreshOverrides?.[r.id]?'':'checked'}> ${label('Use App Consultant default ({0})',refreshMessage(r))}</label><div class="config-grid">${refreshFields(refreshPolicy(r,b.refreshOverrides))}</div></fieldset>`).join('')}<p>Manual waits for Refresh or Generate. Templates sharing a dataset share its schedule.</p><p role="alert"></p><footer><button type="button" data-dismiss>Cancel</button><button type="submit">Apply</button></footer></form>`;
 const update=()=>{for(const field of dialog.querySelectorAll('fieldset')){const useDefault=field.querySelector('[name="useDefault"]').checked,mode=field.querySelector('select');mode.disabled=useDefault;field.querySelector('[name="refreshSeconds"]').disabled=useDefault||mode.value!=='interval';}};
 dialog.onchange=update;update();dialog.querySelector('[data-dismiss]').onclick=()=>dialog.close();
 dialog.querySelector('form').onsubmit=async e=>{e.preventDefault();const overrides=normalizeRefreshOverrides(b.refreshOverrides);for(const field of dialog.querySelectorAll('fieldset')){const rid=field.dataset.rid;if(field.querySelector('[name="useDefault"]').checked)delete overrides[rid];else{const mode=field.querySelector('select').value;overrides[rid]={refreshMode:mode,...(mode==='interval'?{refreshSeconds:Number(field.querySelector('[name="refreshSeconds"]').value)}:{})};}}const submit=dialog.querySelector('[type="submit"]');submit.disabled=true;try{await send({refreshOverrides:overrides});dialog.close();}catch(error){dialog.querySelector('[role="alert"]').textContent=error.message;}finally{submit.disabled=false;}};
 dialog.showModal();
});

document.addEventListener('click',event=>{if(!event.target.closest('[data-market-defaults]'))return;const form=event.target.closest('form');for(const [key,value] of Object.entries(MARKET_DEFAULTS)){const field=form.elements[key==='flashDurationMs'?'flashDuration':key];if(typeof value==='boolean')field.checked=value;else field.value=key==='flashDurationMs'?value/1000:value;}setUIText(form.querySelector('[data-market-feedback]'),'Defaults restored. Save to apply.');});
window.addEventListener('storage',event=>{if(event.key===`attas-b028:${new URL(runtime.plaza.url||location.origin).port}:${runtime.user?.id||'guest'}:${terminalWindow()}:market-appearance`){try{applyMarketAppearance(JSON.parse(event.newValue||'{}'));}catch{}}});
window.addEventListener('focus',async()=>{if(!runtime.user)return;try{const settings=await api('/api/settings');runtime.settings.preferences.displayTimezone=settings.preferences.displayTimezone;runtime.settings.preferences.specifiedTimezone=settings.preferences.specifiedTimezone;applyDisplayTimezone(settings.preferences.displayTimezone);runtime.settings.preferences.displayFormat=settings.preferences.displayFormat;applyDisplayFormat(settings.preferences.displayFormat);rendering();runtime.settings.preferences.marketAppearance=settings.preferences.marketAppearance;applyMarketAppearance(settings.preferences.marketAppearance);}catch{}});

window.addEventListener('storage',async event=>{if(event.key===`attas-b028:${new URL(runtime.plaza.url||location.origin).origin}:${runtime.user?.id||'guest'}:${terminalWindow()}:display-timezone`&&runtime.user){try{const settings=await api('/api/settings');runtime.settings.preferences.displayTimezone=settings.preferences.displayTimezone;runtime.settings.preferences.specifiedTimezone=settings.preferences.specifiedTimezone;applyDisplayTimezone(settings.preferences.displayTimezone);rendering();}catch{}}});


export async function setBrowserLanguage(b,value){
 if(value!==null&&!validLocale(value))throw new Error('Unsupported UI language.');
 const node=nodes.get(b.id);
 if(node?.remoteId)await api('/api/instances/'+node.remoteId,{method:'PUT',body:{uiLanguage:value}});
 b.uiLanguage=value;
 document.dispatchEvent(new CustomEvent('browser:interaction'));
}

document.addEventListener('submit',async event=>{
 const form=event.target.closest('[data-phema-interaction]');if(!form)return;event.preventDefault();event.stopImmediatePropagation();
 const [browserId,...parts]=form.closest('[data-content-interaction]').dataset.contentInteraction.split('/'),reference=parts.join('/'),instance=nodes.get(browserId),status=form.querySelector('[role=status]'),submit=form.querySelector('[type=submit]');
 let nativeHandled=false;try{if(!instance?.remoteId)throw new Error('Browser is still initializing.');nativeHandled=await submitOperationForm({form,reference,instance,request:api,setStatus:setUIText,text:t});if(nativeHandled)return;const inputs=formInputs(form);submit.disabled=true;await api('/api/instances/'+instance.remoteId+'/content',{method:'POST',body:{operation:'interact',references:[reference],inputs}});setUIText(status,'Waiting for publisher…');}catch(error){status.textContent=error.message;}finally{if(!nativeHandled)submit.disabled=false;}
},true);

// Mode changes are drafts until the existing server-backed settings form is saved.
document.addEventListener('change',event=>{if(!event.target.matches('[name=timeZoneMode]'))return;const form=event.target.closest('[data-connected-form="time-display"]');if(!form)return;const specified=event.target.value==='specified';form.querySelector('[data-specified-zone]').hidden=!specified;form.elements.specifiedTimezone.disabled=!specified;});

export async function reloadBrowserPhema(b,{readLayout}={}) {
 if(!runtime.user)return {guest:true};
 return reloadPhemaOnly(b,{readLayout,request:(suffix='',options={})=>requestBrowserInstance(b,suffix,options),
  isCurrent:()=>latest.includes(b),entry:()=>({...b.customLayout,design:browserDesign(b.design,b.instrumentOverrides)}),
  accept:remote=>{const node=nodes.get(b.id);if(!node)return;node.state=remote;node.events?.accept(remote);
   const design=browserDesign(b.design,b.instrumentOverrides);node.designSignature=JSON.stringify(design);
   node.signature=JSON.stringify([design,b.customLayout?.composition,b.instrumentOverrides,b.parameterValues,b.layoutTabs]);
   node.range=b.range;rendering();}});
}

function mountProtectedContent(){
 mountContent();
 for(const form of document.querySelectorAll('[data-phema-interaction]')){
  const section=form.closest('[data-content-interaction]');if(!section)continue;
  const [browserId,...parts]=section.dataset.contentInteraction.split('/'),instance=nodes.get(browserId);
  if(instance?.remoteId)mountOperationRecovery({form,reference:parts.join('/'),instance,request:api,setStatus:setUIText,text:t});
 }
}

export function compositionViewInstance(root){
 const surface=root.closest('[data-content-interaction]');
 const [browserId,...path]=(surface?.dataset.contentInteraction||'').split('/');
 return {instance:nodes.get(browserId),prefix:path.filter(Boolean).join('/')};
}
// A mounted view's parameter values are Browser state: save them with the workspace and
// let the ordinary sync re-run the instance with them. Null removes a value.
export function setCompositionViewParameters(root,values){
 const surface=root.closest('[data-content-interaction]');
 const [browserId]=(surface?.dataset.contentInteraction||'').split('/');
 const b=latest.find(item=>item.id===browserId);if(!b)throw new Error('This view is not open in a Browser.');
 const next={...(b.parameterValues||{})};
 for(const [key,value] of Object.entries(values)){if(value===null)delete next[key];else next[key]=structuredClone(value);}
 b.parameterValues=next;syncBrowsers(latest,currentWorkspace,rendering);
 document.dispatchEvent(new CustomEvent('browser:interaction'));
 return structuredClone(next);
}
installSourceSettings({api});
installAgentTrust({api});
installScheduledRuns({api,signedIn:()=>!!(runtime.user||runtime.plaza.localOnly),onChange:()=>document.dispatchEvent(new CustomEvent('scheduled-runs:changed'))});
