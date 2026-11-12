import {refreshPhemaFrame} from './phema-frame-refresh.js';
import {reloadBrowserPhema} from './connected.js';
import {installPhemaEdit,mountPhemaEdit,phemaEditToolbar,togglePhemaEdit,phemaEditGesture} from './browser-phema-edit.js';
import {uiBundle} from './phema-i18n.js';

import {normalizeResearchState} from './workspace-storage.js';
import {normalizeContentOverrides} from './content-policies.js';
import {openBrowserLanguageDialog,openLanguageChoice} from './browser-language.js';
import {ui as uiHTML,label,attribute,t,applyUI,validLocale,setUIAttribute,setLocale} from './i18n.js';
import {refreshSavedLayouts} from './layout-updates.js';
import {watchPhemaChanges,saveReloadDraft,takeReloadDraft} from './phema-reload.js';
import {normalizeFieldBindings} from './field-bindings.js';
import {normalizeRefreshOverrides,refreshPolicy,refreshLabel,refreshFields} from './block-refresh.js';
import {renderBrowserPanels,installLayoutTabs} from './layout-tabs.js';
import {layoutFetchStatus,statusHTML as fetchStatusHTML,updateStatus} from './layout-status.js';
import {refreshPanels} from './panel-refresh.js';
import {keepMounted} from './composition-renderers.js';
import {captureOperationDrafts,restoreOperationDrafts} from './operation-form-drafts.js';
const operationDraftScopes=new WeakMap();
import {normalizeInstrumentOverrides} from './browser-instruments.js';
import {providerOrigin,consultantURL} from './host-context.js';
import {runtime, connect, api, scopedStorage, statusHTML, settingsHTML, syncBrowsers, setBrowserLanguage, browserState, browserCompany, panelHTML, loadCatalog, discussionHTML} from './connected.js';
import {runningPhemas} from './scheduled-runs.js';
await connect();
import {installWorkspaceLauncher,openingIndicator} from './agent-workspaces.js';
import {resolveWorkspaceEntry,workspaceEntryReference} from './workspace-entry-navigation.js';
import {installPhemaSelector,readStarterWorkspace} from './phema-selector.js';
import {LayoutPreviews} from './layout-previews.js';
const workspaceLauncher=installWorkspaceLauncher({request:api,text:t,showButton:false});
const layoutPreviews=new LayoutPreviews(api);
// A Phema's owner you don't trust yet can be trusted on Plaza's own page, from its signed owner record.
const trustOwner=record=>{
 if(!runtime.plaza.url||!record||record.account===runtime.user?.id)return null;
 const raw=new TextEncoder().encode(JSON.stringify(record)),b64=btoa(String.fromCharCode(...raw)).replaceAll('+','-').replaceAll('/','_').replace(/=+$/,'');
 return new URL(runtime.plaza.url).origin+'/trusted-agents/accounts?owner='+b64;
};
const phemaSelector=installPhemaSelector({request:api,toEntry:phemaToEntry,workspaceId:()=>currentWorkspace().id,text:t,onOpen:openSelectedPhema,previews:layoutPreviews,trustOwner});
import {WorkspaceClient} from './workspace-client.js';
import {WorkspacePreviews} from './workspace-previews.js';



import {hydratePhemaPayload,phemaToEntry} from './phema-transfer.js';
import {terminalURL,terminalWindow} from './terminal-session.js';
import {onWindowSignIn} from './window-signin.js';
let workspaceClient=new WorkspaceClient(api,scopedStorage());
import { initializeTheme, applyAccountTheme, renderThemeSettings } from './theme.js';
import { sessionNameTagHTML, applySessionName, sessionNameSettingsHTML } from './session-name.js';
import { reopenTabsButtonHTML } from './reopen-tabs.js';

import { renderPlazaStatus, closePlazaStatus } from './plaza-status.js';
import { normalizeFrame, moveFrame, resizeFrame, tileFrames, cascadeFrame, freeFrame } from './window-geometry.js';
import { readWorkspaceStore, STORAGE_KEY, normalizeMessages, normalizeDiscussionDrafts, normalizeInteractionDrafts } from './workspace-storage.js';
import { normalizeLayoutEntry, bindLayoutEntry, layoutKind } from './layout-library.js';

import { layoutStyle, templateStyle, layoutScope, usesFreePlacement, renderDesignMap } from './layout-presentation.js';
import { toComposableEntry } from './layout-composition.js';
import { PLAZA_PULSES } from './plaza-pulses.js';

initializeTheme();

const $ = (s) => document.querySelector(s);
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt = (v, digits = 2) => Number(v).toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits });
const signed = (v) => `${v >= 0 ? '+' : ''}${fmt(v)}%`;
const paths = {
  sidebar: uiHTML`<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16m6-11-3 3 3 3"/>`,
  grid: uiHTML`<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>`,
  agents: uiHTML`<circle cx="6" cy="12" r="2.5"/><circle cx="18" cy="6" r="2.5"/><circle cx="18" cy="18" r="2.5"/><path d="m8.2 10.9 7.6-3.8M8.2 13.1l7.6 3.8"/>`,
  sparkle: uiHTML`<path d="m12 3 2.4 6.6L21 12l-6.6 2.4L12 21l-2.4-6.6L3 12l6.6-2.4L12 3Z"/><path d="m20 2 .7 2.3L23 5l-2.3.7L20 8l-.7-2.3L17 5l2.3-.7L20 2Z"/>`,
  plus: uiHTML`<path d="M12 5v14M5 12h14"/>`, close: uiHTML`<path d="m6 6 12 12M6 18 18 6"/>`,
  edit: uiHTML`<path d="m16 3 5 5-12 12-6 1 1-6L16 3Zm-3 3 5 5"/>`,
  chart: uiHTML`<path d="M4 3v17h17M7 14l4-5 4 3 6-7"/>`,
  settings: uiHTML`<path d="M4 7h16M4 17h16"/><circle cx="9" cy="7" r="3"/><circle cx="16" cy="17" r="3"/>`,
  arrow: uiHTML`<path d="M5 12h14m-5-5 5 5-5 5"/>`, down: uiHTML`<path d="m7 10 5 5 5-5"/>`,
  globe: uiHTML`<circle cx="12" cy="12" r="9"/><ellipse cx="12" cy="12" rx="4" ry="9"/><path d="M3 12h18"/>`,
  book: uiHTML`<path d="M3 4h7l2 2 2-2h7v15h-7l-2 2-2-2H3V4Zm9 2v15"/>`,
  copy: uiHTML`<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M15 8V4H4v11h4"/>`,
  expand: uiHTML`<path d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5"/>`,
  command: uiHTML`<path d="M9 9V6a3 3 0 1 0-3 3h12a3 3 0 1 0-3-3v12a3 3 0 1 0 3-3H6a3 3 0 1 0 3 3V9Z"/>`,
  check: uiHTML`<path d="m5 12 4 4L19 6"/>`, clock: uiHTML`<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>`,
  file: uiHTML`<path d="M14 3H5v18h14V8l-5-5Z M14 3v6h5M8 13h8M8 17h5"/>`,
  chat: uiHTML`<path d="M21 11a9 9 0 0 1-9 9H4l-2 2V11a9 9 0 0 1 19 0Z"/><path d="M7 10h9M7 14h6"/>`,
  key: uiHTML`<circle cx="8" cy="9" r="5"/><path d="m12 13 8 8m-4-4 3-3m-6 0 3-3"/>`,
  layers: uiHTML`<path d="m12 3 10 5-10 5L2 8l10-5Zm-10 9 10 5 10-5M2 16l10 5 10-5"/>`,
  wallet: uiHTML`<rect x="3" y="5" width="18" height="15" rx="2"/><path d="M3 8V4l14-2v3m4 7h-6v5h6"/>`,
  calendar: uiHTML`<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M7 2v6M17 2v6M3 11h18M8 15h3"/>`,
  search: uiHTML`<circle cx="10" cy="10" r="6"/><path d="m15 15 6 6"/>`,
  refresh: uiHTML`<path d="M20 7v5h-5M4 17v-5h5M6 7a7 7 0 0 1 12-2l2 2M4 17l2 2a7 7 0 0 0 12-2"/>`,
  save: uiHTML`<path d="M19 21H5a2 2 0 0 1-2-2V3h14l4 4v12a2 2 0 0 1-2 2ZM7 3v6h9V3M7 21v-8h10v8"/>`,
};
function icon(name, cls = '') { return uiHTML`<svg class="icon ${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name] || paths.grid}</svg>`; }
const layoutIcon = { market:'globe', research:'search', comparison:'layers', portfolio:'wallet', event:'calendar' };
const ui = { modal: null, settingsTab: 'workspace', recommendation: null, prompt: '', target: 'new', focus: false, activeId: null, browsers: [], connections: [], density: 'comfortable', sidebarCollapsed: matchMedia('(max-width:720px)').matches, arrange: false, terminalCommandOpen:false, terminalDraft:'' };
const narrowViewport=matchMedia('(max-width:720px)');
let narrowSidebarOpen=false;
const sidebarIsCollapsed=()=>narrowViewport.matches?!narrowSidebarOpen:ui.sidebarCollapsed;
narrowViewport.addEventListener('change',()=>{narrowSidebarOpen=false;if(document.querySelector('.app-body'))render();});
Object.assign(ui, { workspaces:[], workspaceId:null, storageError:null });
Object.assign(ui, { proposals:[], proposal:null });


const previews=new WorkspacePreviews({api,user:()=>runtime.user,workspace:()=>currentWorkspace(),browsers:()=>ui.browsers,state:browserState,saved:persistPreviewWorkspace,changed:record=>{if(record)layoutPreviews.remember(record);}});

let lastFocus = null;
let nextId = 1;
let chartId = 0;
let topZ = 1;
let gesture = null;
let ignoreDragClick = false;
let sessionTransition=false;

function createBrowser(command,customLayout=null){
 command=String(command??'').replace(/[\u0000-\u001f\u007f]/g,' ').trim().slice(0,499);
 const original=customLayout;customLayout=customLayout?normalizeLayoutEntry(customLayout):null;
 const rec=customLayout?bindLayoutEntry(customLayout,command):{layoutId:'content',ticker:'',tickers:[],dbl:{id:'empty-container',title:'Data Browser',params:{},dataRequirements:[],layout:{id:'empty-grid',type:'grid',columns:12,children:[]}}};
 return {id:'browser-'+nextId++,previewId:crypto.randomUUID(),uiLanguage:null,command,layoutId:rec.layoutId,ticker:rec.ticker,tickers:rec.tickers,design:rec.dbl,customLayout,range:'1M',showRequirements:false,paused:false,selectedReport:null,messages:[],frame:null,z:++topZ,commandOpen:false,commandDraft:command,...(original&&!customLayout?{unsupportedLayout:original}:{})};
}
function openWorkspacePhema(entry){
  let browser=ui.browsers.find(b=>b.customLayout?.id===entry.id);
  if(!browser){
    if(ui.browsers.length>=12)throw new Error('Close a browser before opening '+entry.name+'.');
    browser=createBrowser('CONTENT',entry);browser.sourceMode='parameters';ui.browsers.push(browser);
  }
  ui.activeId=browser.id;ui.focus=true;
  return browser;
}
// Set when a signed-in window loaded no saved workspace: it then opens the starter workspace (openStarterWorkspace).
let starterPending=false;
async function restore({strict=false}={}) {
  let saved;starterPending=false;
  try {
    const reloadDraft=takeReloadDraft(scopedStorage());
    if(runtime.user){saved=await workspaceClient.load();ui.storageError=null;}
    else {const result=readWorkspaceStore(scopedStorage());saved=reloadDraft||result.data;ui.storageError=result.error;}
  } catch(error) { ui.storageError = error.message;if(strict)throw error;const local=readWorkspaceStore(scopedStorage());saved=local.data; }
  if (saved) {
    ui.density = saved.settings.density;
    if (typeof saved.settings.sidebarCollapsed === 'boolean') ui.sidebarCollapsed = saved.settings.sidebarCollapsed;
    ui.workspaces = saved.workspaces.map(w => {
      const browsers = w.browsers.map(b => ({ ...createBrowser(b.command,b.customLayout), terminalDocumentId:b.terminalDocumentId,previewId:b.previewId||crypto.randomUUID(),range:b.range,sourceMode:b.sourceMode||'parameters',contentSnapshotId:b.contentSnapshotId,selectionKey:typeof b.selectionKey==='string'?b.selectionKey:null,uiLanguage:validLocale(b.uiLanguage)?b.uiLanguage:null, frame:b.frame, z:b.z, messages:b.messages||[], discussionDrafts:b.discussionDrafts||{},interactionDrafts:normalizeInteractionDrafts(b.interactionDrafts),fieldBindings:normalizeFieldBindings(b.fieldBindings),contentOverrides:normalizeContentOverrides(b.contentOverrides),refreshOverrides:normalizeRefreshOverrides(b.refreshOverrides),instrumentOverrides:normalizeInstrumentOverrides(b.instrumentOverrides),parameterValues:normalizeResearchState(b.parameterValues),researchView:normalizeResearchState(b.researchView),commandDraft:b.commandDraft??b.command,commandOpen:b.commandOpen===true,layoutTabs:b.layoutTabs, layoutPublication:b.layoutPublication,consultantId:b.consultantId || null,unsupportedLayout:b.unsupportedLayout || null }));
      topZ = Math.max(topZ, ...browsers.map(b => b.z));
      const workspace={ id:w.id, name:w.name, updatedAt:w.updatedAt, browsers, activeId:browsers[w.activeIndex]?.id || null, focus:w.focus, arrange:browsers.length>0 && browsers.every(b=>!b.frame), terminalDraft:'' };
      workspace.lastSnapshot=workspaceContent(workspace);
      return workspace;
    });
    selectWorkspace(ui.workspaces.find(w=>w.id===saved.activeWorkspaceId) || ui.workspaces[0]);
  } else {
    // No saved workspace: start empty. Signed in, with the account's workspace server answering, the window then
    // opens what its Plaza's App Consultant publishes as the starter workspace.
    const workspace = { id:crypto.randomUUID(), name:'Research desk', updatedAt:Date.now(), browsers:[], activeId:null, focus:false, arrange:true, terminalDraft:'' };
    ui.workspaces = [workspace];
    selectWorkspace(workspace);
    starterPending=!!runtime.user&&!ui.storageError;
  }
}
// The starter workspace: the Phemas the App Consultant's Getting started document names, each opened through the
// App Consultant's own selection like Open in New Data Browser. The Terminal chooses none of them itself. It runs
// once, and only while the empty workspace is untouched; without a published starter the window stays empty.
async function openStarterWorkspace(){
  if(!starterPending)return;
  starterPending=false;
  const workspace=currentWorkspace(),untouched=()=>currentWorkspace()===workspace&&!workspace.browsers.length;
  try{
    const starter=await workspaceLauncher.openPreferred({name:'App Consultant',
      resolve:entry=>resolveWorkspaceEntry({entry,request:api,toEntry:phemaToEntry,workspaceId:workspace.id}),
      present:detail=>readStarterWorkspace({phema:detail.phema,request:api,toEntry:phemaToEntry,workspaceId:workspace.id,
        locale:document.documentElement.lang||'en',limit:12})});
    if(!starter||!untouched())return;
    if(!starter.phemas.length)throw new Error(t('None of its Phemas is available to this account.'));
    if(starter.name&&workspace.name==='Research desk'){workspace.name=starter.name;render();}
    for(const phema of starter.phemas){
      if(currentWorkspace()!==workspace)break;  // the viewer moved to another workspace meanwhile
      await openSelectedPhema({phema,sourceMode:'parameters',target:'new'});
    }
    // The list's first Phema is the one in front.
    const first=workspace.browsers[0];
    if(first&&currentWorkspace()===workspace){ui.activeId=first.id;first.z=++topZ;render();await persist();}
  }catch(error){
    console.warn('Starter workspace:',error.message);
    toast(t('The starter workspace could not open: {0}',error.message));
  }
}
const currentWorkspace = () => ui.workspaces.find(w=>w.id===ui.workspaceId);
function workspaceSnapshot(w) {
  return { id:w.id, name:w.name, updatedAt:w.updatedAt, browsers:w.browsers.map(b=>({command:b.command,terminalDocumentId:b.terminalDocumentId,previewId:b.previewId,range:b.range,sourceMode:b.sourceMode||'parameters',contentSnapshotId:b.contentSnapshotId,selectionKey:b.selectionKey||null,uiLanguage:validLocale(b.uiLanguage)?b.uiLanguage:null,frame:b.frame,z:b.z,messages:b.messages||[],discussionDrafts:b.discussionDrafts||{},interactionDrafts:normalizeInteractionDrafts(b.interactionDrafts),fieldBindings:normalizeFieldBindings(b.fieldBindings),contentOverrides:normalizeContentOverrides(b.contentOverrides),refreshOverrides:normalizeRefreshOverrides(b.refreshOverrides),instrumentOverrides:normalizeInstrumentOverrides(b.instrumentOverrides),parameterValues:normalizeResearchState(b.parameterValues),researchView:normalizeResearchState(b.researchView),commandDraft:b.commandDraft??b.command,commandOpen:b.commandOpen===true,layoutTabs:b.layoutTabs,...(b.customLayout?{customLayout:b.customLayout}:{}),...(b.layoutPublication?{layoutPublication:b.layoutPublication}:{}),...(b.consultantId?{consultantId:b.consultantId}:{}),...(b.unsupportedLayout?{unsupportedLayout:b.unsupportedLayout}:{})})), activeIndex:w.browsers.findIndex(b=>b.id===w.activeId), focus:w.focus };
}
function workspaceContent(w) {
  const {updatedAt,...snapshot}=workspaceSnapshot(w);
  return JSON.stringify(snapshot);
}
function captureWorkspace() {
  const w = currentWorkspace();
  if (!w) return;
  Object.assign(w, { browsers:ui.browsers, activeId:ui.activeId, focus:ui.focus, arrange:ui.arrange, terminalDraft:ui.terminalDraft });
  const serialized = workspaceContent(w);
  if (serialized !== w.lastSnapshot) { w.updatedAt=Date.now(); w.lastSnapshot=serialized; }
}
function selectWorkspace(w) {
  ui.workspaceId=w.id;
  ui.browsers=w.browsers;
  ui.activeId=w.activeId;
  ui.focus=w.focus && !!w.activeId;
  ui.arrange=w.arrange;
  ui.terminalDraft=w.terminalDraft;
  ui.terminalCommandOpen=false;
}
async function persist() {
  // Never save a guest workspace into an account while its saved state loads.
  if(sessionTransition)return false;
  captureWorkspace();
  const document={version:2,activeWorkspaceId:ui.workspaceId,workspaces:ui.workspaces.map(workspaceSnapshot),settings:{density:ui.density,sidebarCollapsed:ui.sidebarCollapsed}};
  try {
    if(runtime.user)await workspaceClient.save(document);
    ui.storageError=runtime.user?null:'Sign in to save workspaces.';
  } catch(error) { ui.storageError=error.message; }
  const status=$('.local-status');
  if(status) {
    status.innerHTML=`${icon(ui.storageError?'clock':'check')} ${label(ui.storageError?'Not saved · session only':'Saved to your account')}`;
    status.title=ui.storageError || 'Workspaces follow your Plaza account.';
  }
  const toggle=$('.workspace-toggle');
  if(toggle) {toggle.classList.toggle('storage-unavailable',!!ui.storageError);toggle.title=ui.storageError || `Workspaces · ${currentWorkspace()?.name}`;}
  return !ui.storageError;
}
async function persistPreviewWorkspace(){
  if(!runtime.user||sessionTransition)return false;
  captureWorkspace();
  const document={version:2,activeWorkspaceId:ui.workspaceId,workspaces:ui.workspaces.map(workspaceSnapshot),settings:{density:ui.density,sidebarCollapsed:ui.sidebarCollapsed}};
  try{await workspaceClient.save(document);return true;}
  catch{return false;}
}
function loadWorkspace(id) {
  const next=ui.workspaces.find(w=>w.id===id);
  if(!next || next.id===ui.workspaceId)return;
  captureWorkspace();
  closeModal();
  selectWorkspace(next);
  render();
  $('.workspace-toggle')?.focus();
  toast(`${next.name} loaded.${ui.storageError?' Changes remain in this session.':''}`);
}
function submitWorkspaceName(form, create) {
  const input=form.elements.name;
  const name=input.value.trim();
  const conflict=ui.workspaces.some(w=>w.name.toLocaleLowerCase()===name.toLocaleLowerCase() && (create || w.id!==ui.workspaceId));
  const error=!name?'Enter a workspace name.':name.length>60?'Use a name of 60 characters or fewer.':conflict?'A workspace with this name already exists.':create && ui.workspaces.length>=32?'This Terminal supports up to 32 workspaces.':'';
  input.setCustomValidity(error);
  if(error){input.reportValidity();return;}
  if(create) {
    captureWorkspace();
    const w={id:crypto.randomUUID(),name,updatedAt:Date.now(),browsers:[],activeId:null,focus:false,arrange:false,terminalDraft:''};
    ui.workspaces.push(w);
    closeModal();
    selectWorkspace(w);
    render();
    $('.empty-workspace button')?.focus();
    toast(`${name} created. Open a browser to get started.`);
  } else {
    currentWorkspace().name=name;
    render();
    renderModal();
    $('#workspace-rename-form input')?.focus();
    toast(ui.storageError?'Workspace renamed for this session.':`${name} renamed and saved.`);
  }
}
const active = () => ui.browsers.find(b => b.id === ui.activeId);
function toast(message) { $('#toast').textContent = message; $('#toast').classList.add('visible'); clearTimeout(toast.timer); toast.timer = setTimeout(() => $('#toast').classList.remove('visible'), 3500); }

function canvasBounds() {
  const canvas = $('.window-canvas');
  return { width: canvas?.clientWidth || 1, height: canvas?.clientHeight || 1 };
}
function placeWindow(node, frame, z) {
  if (!node) return;
  Object.assign(node.style, { left:`${frame.x}px`,top:`${frame.y}px`,width:`${frame.width}px`,height:`${frame.height}px`,zIndex:String(z) });
}
let observedCanvas = null;
let observedSize = '';
const canvasObserver = new ResizeObserver(() => {
  const size = `${observedCanvas?.clientWidth}:${observedCanvas?.clientHeight}`;
  if (observedCanvas?.clientWidth && size !== observedSize && !gesture) {
    observedSize = size;
    layoutWindows();
    persist();
  }
});
function layoutWindows() {
  const canvas = $('.window-canvas');
  if (!canvas || !canvas.clientWidth) return;
  if (observedCanvas !== canvas) {
    canvasObserver.disconnect(); observedCanvas=canvas; observedSize=`${canvas.clientWidth}:${canvas.clientHeight}`; canvasObserver.observe(canvas);
  }
  if (ui.arrange && ui.browsers.length) {
    const frames = tileFrames(ui.browsers.length, canvasBounds());
    ui.browsers.forEach((b,i)=>{b.frame=frames[i]});
    ui.arrange=false;
  }
  const bounds=canvasBounds();
  ui.browsers.forEach((b,i)=>{
    b.frame=normalizeFrame(b.frame || cascadeFrame(i,bounds),bounds);
    const node=canvas.querySelector(`[data-browser="${b.id}"]`);
    placeWindow(node,ui.focus?{x:0,y:0,width:bounds.width,height:bounds.height}:b.frame,b.z);
    // Focus shows one window; the others stay mounted behind it, so switching keeps each Phema's state.
    node?.classList.toggle('focus-hidden',ui.focus&&b.id!==ui.activeId);
  });
}
function bringToFront(id) {
  const b=ui.browsers.find(item=>item.id===id);
  if(!b)return;
  ui.activeId=id; b.z=++topZ;
  document.querySelectorAll('.browser-window').forEach(node=>{
    node.classList.toggle('is-active',node.dataset.browser===id);
    if(node.dataset.browser===id)node.style.zIndex=String(b.z);
  });
  document.querySelectorAll('.window-tabs button').forEach(node=>node.classList.toggle('active',node.dataset.id===id));
  document.querySelectorAll('.browser-nav-item').forEach(node=>node.classList.toggle('active',node.dataset.id===id));
  persist();
}

// Any press inside a window brings it forward, before a panel can keep the press to itself.
document.addEventListener('pointerdown',e=>{
  if(e.button!==0 || ui.modal || gesture)return;
  const windowNode=e.target.closest('.browser-window');
  if(windowNode)bringToFront(windowNode.dataset.browser);
},true);
// A press inside an embedded frame (an agent view, a video) never reaches this page, but the frame takes focus.
// Nothing tells the page when focus moves on to another frame, so it looks while focus stays inside frames.
let framedFocus=null,framedWatch=0;
function followFramedFocus(){
  const frame=document.activeElement;
  if(frame?.tagName!=='IFRAME'){clearInterval(framedWatch);framedWatch=0;framedFocus=null;return;}
  if(frame===framedFocus)return;
  framedFocus=frame;
  const windowNode=frame.closest('.browser-window');
  if(windowNode&&!ui.modal&&!gesture&&windowNode.dataset.browser!==ui.activeId)bringToFront(windowNode.dataset.browser);
}
addEventListener('blur',()=>setTimeout(()=>{followFramedFocus();if(framedFocus&&!framedWatch)framedWatch=setInterval(followFramedFocus,200);}));
document.addEventListener('pointerdown',e=>{
  if(e.button!==0 || ui.modal || gesture)return;
  const windowNode=e.target.closest('.browser-window');
  if(!windowNode)return;
  const resize=e.target.closest('[data-resize-window]');
  const title=e.target.closest('[data-drag-window]');
  const id=windowNode.dataset.browser;
  if(ui.focus || (!resize && (!title || e.target.closest('.window-tools'))))return;
  const b=active();
  const capture=resize||title;
  gesture={id,node:windowNode,capture,pointerId:e.pointerId,startX:e.clientX,startY:e.clientY,frame:{...b.frame},bounds:canvasBounds(),edge:resize?.dataset.edge,moved:false};
  capture.setPointerCapture(e.pointerId);
  document.body.classList.add('window-manipulating');
  windowNode.classList.add(resize?'is-resizing':'is-dragging');
  if(resize)e.preventDefault();
});
document.addEventListener('pointermove',e=>{
  if(!gesture || gesture.pointerId!==e.pointerId)return;
  const dx=e.clientX-gesture.startX,dy=e.clientY-gesture.startY;
  if(Math.abs(dx)+Math.abs(dy)<3 && !gesture.moved)return;
  gesture.moved=true;
  const b=ui.browsers.find(item=>item.id===gesture.id);
  b.frame=gesture.edge?resizeFrame(gesture.frame,gesture.edge,dx,dy,gesture.bounds):moveFrame(gesture.frame,dx,dy,gesture.bounds);
  placeWindow(gesture.node,b.frame,b.z);
  e.preventDefault();
});
function finishGesture(e) {
  if(!gesture || e.pointerId!==gesture.pointerId)return;
  const g=gesture;
  gesture=null;
  if(e.type==='pointercancel'){
    const b=ui.browsers.find(item=>item.id===g.id);b.frame=g.frame;placeWindow(g.node,b.frame,b.z);
  }
  if(g.capture.hasPointerCapture(e.pointerId))g.capture.releasePointerCapture(e.pointerId);
  g.node.classList.remove('is-dragging','is-resizing');
  document.body.classList.remove('window-manipulating');
  ignoreDragClick=g.moved;
  // The pointer's synthetic click follows pointerup synchronously.
  setTimeout(()=>{ignoreDragClick=false},0);
  persist();
}
document.addEventListener('pointerup',finishGesture);
document.addEventListener('pointercancel',finishGesture);
document.addEventListener('click',e=>{
  if(ignoreDragClick){e.preventDefault();e.stopImmediatePropagation();ignoreDragClick=false;}
},true);
document.addEventListener('dblclick',e=>{
  const title=e.target.closest('[data-drag-window]');
  if(!title||e.target.closest('.window-tools'))return;
  ui.activeId=title.dataset.dragWindow;ui.focus=!ui.focus;render();
});
document.addEventListener('keydown',e=>{
  const title=e.target.closest('[data-drag-window]');
  if(!title || !e.altKey || !['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(e.key) || ui.focus)return;
  e.preventDefault();
  const b=ui.browsers.find(item=>item.id===title.dataset.dragWindow);
  const dx=e.key==='ArrowLeft'?-16:e.key==='ArrowRight'?16:0;
  const dy=e.key==='ArrowUp'?-16:e.key==='ArrowDown'?16:0;
  b.frame=e.shiftKey?resizeFrame(b.frame,'se',dx,dy,canvasBounds()):moveFrame(b.frame,dx,dy,canvasBounds());
  bringToFront(b.id);placeWindow(title.closest('.browser-window'),b.frame,b.z);persist();
});


function miniLayout(layout) {
  return uiHTML`<div class="mini-layout mini-${layout.id}" aria-hidden="true">${layout.panels.slice(0,5).map((p,i)=>uiHTML`<span class="mini-cell cell-${i}">${i===0?uiHTML`<i></i><i></i><i></i>`:''}</span>`).join('')}</div>`;
}

function renderTerminalCommands() {
  return uiHTML`<div class="terminal-command-menu"><button class="icon-button terminal-command-toggle ${ui.terminalCommandOpen?'is-open':''}" data-action="terminal-command" aria-label="Terminal commands" aria-expanded="${ui.terminalCommandOpen}" aria-controls="terminal-command-panel" title="Terminal commands · ⌘/Ctrl K">${icon('command')}</button>${ui.terminalCommandOpen?uiHTML`
    <section id="terminal-command-panel" class="terminal-command-panel" aria-label="Terminal command controls">
      <div class="terminal-command-heading"><span>Terminal commands</span><button class="icon-button" data-action="close-terminal-command" aria-label="Close Terminal commands">${icon('close')}</button></div>
      <form id="command-form" class="command-input">${icon('command')}<input id="command" name="command" aria-label="Ticker or command" value="${esc(ui.terminalDraft)}" autocomplete="off" spellcheck="false" maxlength="499" placeholder="Command for the active browser…"/><kbd>⌘ K</kbd><button type="submit" aria-label="Run command">${icon('arrow')}</button></form>
      <div class="terminal-command-actions"><button data-action="new" aria-label="New browser">${icon('plus')} New browser</button><button data-action="save" aria-label="Save workspace">${icon('save')} Save workspace</button><button data-action="tile" aria-label="Tile all browsers">${icon('grid')} Tile all browsers</button><button data-action="focus" aria-label="Focus active browser">${icon('expand')} Focus active browser</button><button data-action="help" aria-label="Command reference">${icon('book')} Command reference</button></div>
    </section>`:''}</div>`;
}

function closeTerminalCommands(restoreFocus=false) {
  ui.terminalCommandOpen=false;
  $('#terminal-command-panel')?.remove();
  const toggle=$('.terminal-command-toggle');
  toggle?.setAttribute('aria-expanded','false');
  toggle?.classList.remove('is-open');
  if(restoreFocus)toggle?.focus();
}
function openTerminalCommands() {
  closePlazaStatus();ui.terminalCommandOpen=true;render();$('#command')?.focus();
}
function toggleBrowserCommand(b,open=!b.commandOpen) {
  b.commandOpen=open;
  render();
  if(open)$(`#command-${b.id}`)?.focus();
  else $(`[data-action="browser-command"][data-id="${b.id}"]`)?.focus();
}

function render() {
  const operationDrafts=new Map([...document.querySelectorAll('#app [data-browser]')].map(node=>[
    node.dataset.browser,{scope:operationDraftScopes.get(node),fields:captureOperationDrafts(node)}]));
  chartId = 0;
  const sidebarCollapsed=sidebarIsCollapsed();
  document.documentElement.dataset.density = ui.density;
  keepMounted($('#app'));
  $('#app').innerHTML = uiHTML`
    <header class="topbar"><button class="icon-button sidebar-toggle" data-action="toggle-sidebar" ${attribute("aria-label",sidebarCollapsed?"Expand sidebar":"Collapse sidebar")} aria-expanded="${!sidebarCollapsed}" aria-controls="terminal-sidebar">${icon('sidebar')}</button><a class="brand" href="#" aria-label="${esc((runtime.product||'Data Terminal')+' home')}"><span class="brand-mark"><i></i><i></i><i></i></span><strong>${esc(runtime.product||'Data Terminal')}</strong><span class="beta">${esc(runtime.version)}${runtime.channel&&runtime.channel!=='dev'?' · '+esc(runtime.channel):''}</span></a>${sessionNameTagHTML()}
      <div class="top-actions">${statusHTML()}<span class="demo-indicator"><i></i> Beta</span><span class="workspace-control"><button class="icon-button workspace-toggle" data-action="workspaces" aria-label="Workspaces" aria-haspopup="dialog" title="Workspaces · ${esc(currentWorkspace().name)}">${icon('layers')}</button></span>${renderTerminalCommands()}<button class="icon-button" data-action="settings" aria-label="Open settings">${icon('settings')}</button><button class="avatar" data-action="plaza" aria-label="Plaza account">${esc(runtime.user?.name?.slice(0,2).toUpperCase()||'G')}</button></div>
    </header>
    <div class="app-body ${sidebarCollapsed?'sidebar-collapsed':'sidebar-expanded'}"><aside class="sidebar" id="terminal-sidebar"><div class="sidebar-top"><span class="nav-label">WORKSPACE</span><button class="nav-item selected workspace-nav" data-action="workspaces" aria-label="${esc(currentWorkspace().name)}" aria-haspopup="dialog" title="Switch workspace · ${esc(currentWorkspace().name)}">${icon('grid')}<span>${esc(currentWorkspace().name)}</span><span class="nav-count">${ui.browsers.length}</span></button><button class="nav-item" data-action="agent-workspaces" aria-label="Agent workspaces">${icon('agents')}<span>Agent workspaces</span></button></div>
      <div class="browser-nav"><div class="nav-label label-row">OPEN BROWSERS<button class="tiny-button" data-action="new" aria-label="Add Data Browser">${icon('plus')}</button></div>${ui.browsers.map((b,i)=>uiHTML`<button class="browser-nav-item ${b.id===ui.activeId?'active':''}" data-action="activate" data-id="${b.id}" data-phema-ui="${esc(JSON.stringify(uiBundle(b.customLayout)||{}))}"><span class="browser-number">${String(i+1).padStart(2,'0')}</span><span><strong>${label(b.design.title)}</strong><small>${esc(b.ticker)} <span>· Data Browser</span>${runningPhemas().has(browserState(b).phema?.id)?uiHTML` <span class="browser-running">· Running</span>`:''}</small></span></button>`).join('')}${ui.browsers.length?'':uiHTML`<p class="sidebar-empty">Your browsers will appear here.</p>`}<button class="add-browser-link" data-action="new">${icon('plus')} New Data Browser</button></div>
      <div class="sidebar-bottom"><button class="nav-item" data-action="settings" aria-label="Terminal settings" title="Terminal settings">${icon('settings')}<span>Terminal settings</span></button><div class="local-status">${icon('check')} Saved to your account</div></div>
    </aside>
    <main class="main" aria-label="Data Browser workspace">
      <p id="window-help" class="sr-only">Drag the title bar to move a browser and its edges to resize. With the title bar focused, use Alt and arrow keys to move, or Alt Shift and arrow keys to resize.</p><div class="browser-grid window-canvas ${ui.focus?'focused':''} ${ui.browsers.length===1?'single':''}">${ui.browsers.map(renderBrowser).join('')}${ui.browsers.length?'':uiHTML`<div class="empty-workspace">${icon('layers')}<h2>Make room for your next idea.</h2><p>Get layout recommendations from App Consultants, then open one in a Data Browser.</p><button class="button primary" data-action="new">${icon('plus')} Open your first browser</button></div>`}</div>
      <div class="window-tabs" aria-label="Open browser windows">${ui.browsers.map((b,i)=>uiHTML`<button data-action="activate" data-id="${b.id}" class="${b.id===ui.activeId?'active':''}" data-phema-ui="${esc(JSON.stringify(uiBundle(b.customLayout)||{}))}" title="Show ${esc(b.design.title)} ${esc(b.ticker)}"><span>${String(i+1).padStart(2,'0')}</span>${label(b.design.title)}<small>${esc(b.ticker)}</small></button>`).join('')}</div>
    </main></div>`;
  applySessionName();
  for(const b of ui.browsers){
    const node=document.querySelector(`[data-browser="${CSS.escape(b.id)}"]`);if(!node)continue;
    const scope=JSON.stringify([b.customLayout?.id,b.customLayout?.revision,b.customLayout?.composition,b.layoutId]);
    const previous=operationDrafts.get(b.id);
    if(previous?.scope===scope)restoreOperationDrafts(node,previous.fields);
    operationDraftScopes.set(node,scope);
  }
  layoutWindows();
  for(const b of ui.browsers)mountPhemaEdit(document.querySelector(`[data-browser="${CSS.escape(b.id)}"]`),b);
  
  persist();
  syncBrowsers(ui.browsers.filter(b=>!b.unsupportedLayout),ui.workspaceId,renderData);
  previews.schedule();
}

document.addEventListener('focusout',event=>{if(event.target.matches?.('[data-requirement-source],[data-source-type]'))setTimeout(renderData,0);});
document.addEventListener('keydown',event=>{if(event.key==='Escape'&&event.target.matches?.('[data-requirement-source],[data-source-type]'))setTimeout(()=>event.target.blur(),0);});

installLayoutTabs(()=>{syncBrowsers(ui.browsers,ui.workspaceId,renderData);renderData();persist();},id=>ui.workspaces.flatMap(w=>w.browsers).find(b=>b.id===id));
function renderPhemaBrowser(b){
 const node=document.querySelector(`[data-browser="${CSS.escape(b.id)}"]`);if(!node)return;
 const toolbar=node.querySelector('.phema-edit-toolbar'),markup=phemaEditToolbar(b);
 if(toolbar)toolbar.outerHTML=markup;else if(markup)node.querySelector('.browser-layout').insertAdjacentHTML('beforebegin',markup);
 const button=node.querySelector('[data-action="browser-edit"]');if(button){button.setAttribute('aria-pressed',String(!!b.editMode));button.innerHTML=b.editMode?'✓':'⚙';setUIAttribute(button,'aria-label',b.editMode?'Exit browser edit mode':'Enter browser edit mode');setUIAttribute(button,'title',b.editMode?'Done editing':'Phema Edit');}
 const title=node.querySelector('.browser-title-line strong');if(title)title.innerHTML=label(b.design.title);
 const layout=node.querySelector('.browser-layout');layout.style.cssText=layoutStyle(b.design.layout);layout.className='browser-layout layout-'+layoutKind(b.layoutId)+(b.design.layout.columns===12?' composed-browser-layout':'')+(usesFreePlacement(b.design.layout)?' free-placement':'');
 renderData();persist();syncBrowsers(ui.browsers,ui.workspaceId,renderData);
}
document.addEventListener('browser:phema-edited',event=>{const b=ui.browsers.find(b=>b.id===event.detail?.id);if(b)renderPhemaBrowser(b);});
function renderData() {
  previews.schedule();
  // Source updates only replace data panels; chrome, command inputs and active
  // pointer captures retain their DOM identity and user interaction state.
  for(const b of ui.browsers){
    
    const node=document.querySelector(`[data-browser="${CSS.escape(b.id)}"]`);
    if(!node||b.unsupportedLayout)continue;
    const container=node.querySelector('.browser-layout');
    if(phemaEditGesture(b))continue;
    refreshPanels(container,renderBrowserPanels(b));
    mountPhemaEdit(node,b);
    node.dataset.uiLocale=b.uiLanguage||'';node.dataset.phemaUi=JSON.stringify(uiBundle(b.customLayout)||{});applyUI(node);
    const company=node.querySelector('.browser-company');if(company)company.textContent=browserCompany(b);
    const count=node.querySelector('[data-requirement-count]');if(count)count.innerHTML=label('{0} data requirements',(browserState(b).entry?.design.dataRequirements||b.design.dataRequirements).length);
    const status=node.querySelector('.layout-fetch-status'),value=layoutFetchStatus(browserState(b),(browserState(b).entry?.design.dataRequirements||b.design.dataRequirements).filter(r=>!browserState(b).visibleRequirementIds||browserState(b).visibleRequirementIds.includes(r.id)));if(status){updateStatus(status,value);}
  }
}

function renderBrowser(b) {
  if(b.unsupportedLayout)return uiHTML`<article class="browser-window" data-browser="${b.id}"><header class="browser-chrome" data-drag-window="${b.id}"><strong>Unsupported saved layout</strong><button data-action="close" data-id="${b.id}">Close</button></header><div class="settings-body"><p>The original layout has been preserved. Export this workspace to recover or migrate it.</p></div></article>`;
  // Render the consultant's actual DBL proposal, rather than choosing a layout
  // inside the browser. The registry only supplies display metadata.
  const def = {

    name: b.design.title,
    columns: b.design.layout.columns,
    areas: b.design.layout.areas,
    panels: b.design.layout.children.map(node => ({
      ...node, type: (node.type === 'phemar'||node.template==='pulse-chat') ? 'discussion' : node.template,
    })),
    requirements: browserState(b).entry?.design.dataRequirements||b.design.dataRequirements,
  };
  const inst = {name:browserCompany(b)};

  return uiHTML`<article class="browser-window ${b.id===ui.activeId?'is-active':''}" data-browser="${b.id}" data-phema-ui="${esc(JSON.stringify(uiBundle(b.customLayout)||{}))}" data-ui-locale="${esc(b.uiLanguage||'')}" aria-label="${esc(def.name)} browser ${esc(b.ticker)}">
    <header class="browser-chrome" data-drag-window="${b.id}" tabindex="0" aria-label="Move ${esc(b.ticker)} window" aria-describedby="window-help" title="Drag to move. Double-click to maximize. Alt + arrows to move; Alt + Shift + arrows to resize."><button class="browser-title" data-action="activate" data-id="${b.id}"><span class="window-icon">${icon(layoutIcon[b.layoutId])}</span><span class="browser-identity"><span class="browser-title-line"><strong>${label(def.name)}</strong><span class="symbol-tag">${esc(b.ticker)}</span><small class="browser-instance">· Browser ${ui.browsers.indexOf(b)+1}</small></span><small class="browser-subtitle"><span class="browser-company instrument-name" title="${esc(inst?.name || (b.layoutId==='portfolio'?'Main portfolio':b.layoutId==='comparison'?'Peer comparison':''))}">${esc(inst?.name || (b.layoutId==='portfolio'?'Main portfolio':b.layoutId==='comparison'?'Peer comparison':''))}</span></small></span></button><span class="layout-fetch-status" role="status" title="${esc(layoutFetchStatus(browserState(b),(browserState(b).entry?.design.dataRequirements||b.design.dataRequirements).filter(r=>!browserState(b).visibleRequirementIds||browserState(b).visibleRequirementIds.includes(r.id))).detail)}">${fetchStatusHTML(layoutFetchStatus(browserState(b),(browserState(b).entry?.design.dataRequirements||b.design.dataRequirements).filter(r=>!browserState(b).visibleRequirementIds||browserState(b).visibleRequirementIds.includes(r.id))))}</span><div class="window-tools">${b.layoutConflict?uiHTML`<button class="icon-button" data-action="review-layout-update" data-id="${esc(b.id)}" title="Review layout update" aria-label="Review layout update">!</button>`:''}<button class="icon-button" data-phema-permissions="${esc(b.id)}" aria-label="Phema permissions" title="Phema permissions">♧</button><button class="icon-button" data-content-settings="${esc(b.id)}" aria-label="Phema content" title="Phema content">▣</button><button class="icon-button" data-action="browser-language" data-id="${b.id}" aria-label="Browser language" title="Browser language">${icon('globe')}</button><button class="icon-button" data-action="browser-edit" data-id="${b.id}" ${attribute("aria-label",b.editMode?"Exit browser edit mode":"Enter browser edit mode")} aria-pressed="${!!b.editMode}" ${attribute("title",b.editMode?"Done editing":"Phema Edit")}>${b.editMode?'✓':'⚙'}</button><button class="icon-button ${b.commandOpen?'is-open':''}" data-action="browser-command" data-id="${b.id}" ${attribute("aria-label",b.commandOpen?"Hide command bar for browser {0}":"Show command bar for browser {0}",ui.browsers.indexOf(b)+1)} aria-expanded="${b.commandOpen}" aria-controls="command-form-${b.id}" ${attribute("title",b.commandOpen?"Hide browser command bar":"Show browser command bar")}>${icon('command')}</button><button class="icon-button" data-action="reload-phema" data-id="${b.id}" aria-label="Reload Phema" title="Reload Phema">${icon('refresh')}</button><button class="icon-button" data-action="change-layout" data-id="${b.id}" aria-label="Change layout for ${esc(b.ticker)}" title="Change layout">${icon('sparkle')}</button><button class="icon-button" data-action="duplicate" data-id="${b.id}" aria-label="Duplicate ${esc(b.ticker)} browser" title="Duplicate browser">${icon('copy')}</button><button class="icon-button" data-action="maximize" data-id="${b.id}" aria-label="Focus ${esc(b.ticker)} browser" title="Focus browser">${icon('expand')}</button><button class="icon-button close-browser" data-action="close" data-id="${b.id}" aria-label="Close ${esc(b.ticker)} browser" title="Close browser">${icon('close')}</button></div></header>
    ${b.commandOpen?uiHTML`<form id="command-form-${b.id}" class="browser-command-form command-input" data-browser-id="${b.id}">${icon('command')}<input id="command-${b.id}" name="command" aria-label="Command for browser ${ui.browsers.indexOf(b)+1}" value="${esc(b.commandDraft)}" placeholder="Ticker or command…" autocomplete="off" spellcheck="false" maxlength="499"/><button type="submit" aria-label="Run command in browser ${ui.browsers.indexOf(b)+1}">${icon('arrow')}</button><button type="button" data-action="browser-command" data-id="${b.id}" aria-label="Hide command bar for browser ${ui.browsers.indexOf(b)+1}">${icon('close')}</button></form>`:''}


    ${phemaEditToolbar(b)}
    <div class="browser-layout layout-${layoutKind(b.layoutId)} ${b.design.layout.columns===12?'composed-browser-layout':''} ${usesFreePlacement(b.design.layout)?'free-placement':''}" style="${esc(layoutStyle(b.design.layout))}">${renderBrowserPanels(b)}</div>
    ${b.showRequirements?renderRequirements(def):''}
    <footer class="browser-footer"><button data-action="requirements" data-id="${b.id}">${icon('layers')} <span data-requirement-count>${label('{0} data requirements',def.requirements.length)}</span> ${icon('down')}</button><span>Source timestamps in each panel</span></footer>
    ${ui.focus?'':['n','s','e','w','ne','nw','se','sw'].map(edge=>uiHTML`<div class="resize-handle resize-${edge}" data-resize-window="${b.id}" data-edge="${edge}" aria-hidden="true"></div>`).join('')}
  </article>`;
}

function panelHeader(p, extra='') { return uiHTML`<div class="panel-heading"><h3>${label(p.title)}</h3>${extra}</div>`; }

function renderRequirements(def) {
  return uiHTML`<div class="requirements-inline"><div class="requirements-caption">Requested by App Consultant <span>Source independent</span></div>${def.requirements.map(r=>uiHTML`<div class="requirement-line"><span>${icon('check')} ${label(r.label)}</span><small>${r.mode==='stream'?'Stream capable':'Snapshot'} · ${r.essential?'Required':'Optional'}</small></div>`).join('')}<p>Requirements are resolved against registered sources. Missing requirements remain visible.</p></div>`;
}




async function requestWorkspaceSearch(command='',target='new',current=null){closeModal();return workspaceLauncher.openPreferred({name:'App Consultant',resolve:entry=>resolveWorkspaceEntry({entry,request:api,toEntry:phemaToEntry,workspaceId:currentWorkspace().id}),present:detail=>{previews.capture(true).catch(()=>{});return phemaSelector.open({...detail,target,current});}}).catch(error=>toast(error.message));}
// The open layout's Phema and its current parameter values, so Change layout starts from them.
function currentLayout(b){
 const entry=b?.customLayout;if(!entry?.id)return null;
 return {phemaId:entry.id,key:b.selectionKey||null,parameters:Object.fromEntries(Object.entries(entry.design?.params||{}).filter(([,s])=>s&&typeof s==='object'&&'default' in s).map(([key,s])=>[key,structuredClone(s.default)]))};
}






function tickerOnly(command) {
  return /^\^?[A-Za-z0-9]{1,12}(?:[.-][A-Za-z0-9]+)*(?:=[A-Za-z]+)?$/.test(command.trim()) && !/^(HELP|MARKETS?|WATCHLIST|PORTFOLIO|COMPARE|REVIEW|MAIN|EVENT|EARNINGS|NEWS|CHART|PRICE|PRICES|RESEARCH|REPORTS)$/i.test(command.trim());
}

function openModal(type, id) {
  if(type==='consultant'){if(id)ui.activeId=id;return requestWorkspaceSearch('',id?'current':'new',id?currentLayout(active()):null);}
  closePlazaStatus();
  closeTerminalCommands();
  lastFocus = document.activeElement;
  ui.modal = type;
  if (id) ui.activeId=id;
  
  renderModal();
}
function closeModal() {
  const wasWorkspace=ui.modal==='workspaces';
  ui.modal=null; $('#overlay').innerHTML='';
  if(lastFocus?.isConnected) lastFocus.focus();
  else $(wasWorkspace?'.workspace-toggle':'.terminal-command-toggle')?.focus();
}
function dialogFrame(title, body, wide = false) {
  return uiHTML`<dialog class="modal ${wide?'wide-modal':''} ${ui.modal==='workspaces'?'workspace-modal':''} ${ui.modal==='consultant'?'consultant-modal':''}" ${attribute("aria-label",title)}><div class="modal-top"><span>${icon(ui.modal==='settings'?'settings':ui.modal==='workspaces'?'layers':'sparkle')} ${label(title)}</span><button class="icon-button" data-action="dismiss" aria-label="Close dialog">${icon('close')}</button></div>${body}</dialog>`;
}
function renderWorkspaceManager() {
  const current=currentWorkspace();
  return uiHTML`<div class="workspace-manager"><p class="workspace-intro">Workspaces follow your Plaza account.</p>
    <section class="workspace-current" aria-label="Current workspace"><h3>Current workspace</h3><form id="workspace-rename-form" class="workspace-name-form"><label for="workspace-name">Workspace name</label><input id="workspace-name" name="name" aria-label="Workspace name" value="${esc(current.name)}" maxlength="60" required autocomplete="off"/><button class="button subtle" type="submit">Rename workspace</button></form><div class="workspace-save-row"><span class="workspace-storage-status">${icon(ui.storageError?'clock':'check')} ${label(ui.storageError?'Not saved · session only':'Saved to your account')}</span><button class="button subtle" data-action="save-current-workspace" aria-label="Save current workspace">${icon('save')} Save current workspace</button></div></section>
    ${ui.storageError?uiHTML`<p class="workspace-error" role="alert">${esc(ui.storageError)}</p>`:''}
    <div class="workspace-save-row"><a class="button" href="./?new-window" target="_blank" rel="noopener" data-new-terminal>New Terminal window</a>${reopenTabsButtonHTML()}<button class="button" data-workspace-action="saved-library">Load saved workspace</button><button class="button" data-workspace-action="export">Export workspace</button><label class="button">Import workspace<input type="file" accept="application/json,.json" data-workspace-import hidden></label>${readWorkspaceStore(localStorage).data?uiHTML`<button class="button" data-workspace-action="legacy">Import legacy workspaces</button>`:''}<button class="button" data-workspace-action="conflicts">Review saved drafts</button><button class="button" data-workspace-action="history">Workspace history</button><button class="button" data-workspace-action="duplicate">Duplicate current</button><button class="button" data-workspace-action="delete">Delete current</button></div><div class="section-label">WORKSPACES <span>${ui.workspaces.length}</span></div><div class="workspace-list" aria-label="Saved workspaces">${ui.workspaces.map(w=>uiHTML`<div class="workspace-entry ${w.id===ui.workspaceId?'is-current':''}" data-workspace-id="${esc(w.id)}"><button class="workspace-load" data-action="load-workspace" data-workspace-id="${esc(w.id)}" aria-label="Load workspace ${esc(w.name)}" ${w.id===ui.workspaceId?'disabled':''}>${icon('grid')}<span class="workspace-entry-text"><strong title="${esc(w.name)}">${esc(w.name)}</strong><small>${w.browsers.length} ${w.browsers.length===1?'browser':'browsers'} · ${ui.storageError?'Open in this session':w.updatedAt?`Saved ${esc(new Date(w.updatedAt).toLocaleString(undefined,{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}))}`:label('Saved to your account')}</small></span>${w.id===ui.workspaceId?uiHTML`<span class="workspace-active-label">Current</span>`:icon('arrow')}</button></div>`).join('')}</div>
    <form id="workspace-create-form" class="workspace-create-form"><label for="new-workspace-name">New workspace</label><input id="new-workspace-name" name="name" aria-label="New workspace name" placeholder="e.g. Asia markets" maxlength="60" required autocomplete="off"/><button class="button primary" type="submit">${icon('plus')} Create workspace</button></form></div>`;
}
function renderModal() {
  if (!ui.modal) return;
  let html='';
  if(ui.modal==='workspaces') {
    html=dialogFrame('Workspaces',renderWorkspaceManager());
  } else if(ui.modal==='settings') {
    html=dialogFrame('Terminal settings',uiHTML`<div class="settings-body terminal-settings"><div class="settings-tabs" role="tablist" aria-label="Terminal settings">${['workspace','sources','connections','runs','plaza'].map(t=>uiHTML`<button type="button" role="tab" id="settings-tab-${t}" aria-controls="settings-panel-${t}" aria-selected="${ui.settingsTab===t}" tabindex="${ui.settingsTab===t?'0':'-1'}" class="${ui.settingsTab===t?'selected':''}" data-action="settings-tab" data-tab="${t}">${label(t==='plaza'?'Plaza account':t==='sources'?'Data sources':t==='runs'?'Scheduled runs':t[0].toUpperCase()+t.slice(1))}</button>`).join('')}</div>${settingsContent()}</div>`);
  } else if(ui.modal==='help') {
    html=dialogFrame('Command reference',uiHTML`<div class="settings-body"><p>Choose an agent workspace to provide a Phema.</p></div>`);
  } else if(ui.modal==='dbl') {
    html=dialogFrame('Layout definition',uiHTML`<div class="settings-body"><p>View content in its owning agent workspace.</p></div>`);
  } else if(ui.modal==='discussion') {
    const b=active();
    html=dialogFrame('Pulse chat',discussionHTML(b,ui.discussionNodeId));

  }
  const existing=$('#overlay .consultant-modal');
  
  $('#overlay').innerHTML=html;
  const dialog=$('#overlay dialog');
  if(ui.modal==='discussion'){dialog.dataset.uiLocale=active()?.uiLanguage||'';applyUI(dialog);}
  dialog.showModal();
  
  dialog.addEventListener('cancel', e=>{e.preventDefault();closeModal()});
  dialog.addEventListener('click', e=>{if(e.target===dialog){const r=dialog.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)closeModal()}});
}

function settingsContent() {
  return ['workspace','sources','connections','runs','plaza'].map(tab=>uiHTML`<div class="settings-panel" id="settings-panel-${tab}" role="tabpanel" aria-labelledby="settings-tab-${tab}" ${ui.settingsTab===tab?'':'hidden'}>${tab==='workspace'?(runtime.permissions?.canConfigure===true?sessionNameSettingsHTML():'')+renderThemeSettings():''}${settingsHTML(tab)}</div>`).join('');
}
function selectSettingsTab(tab) {
  if(!['workspace','sources','connections','runs','plaza'].includes(tab))return;
  ui.settingsTab=tab;
  document.querySelectorAll('[data-action="settings-tab"]').forEach(button=>{
    const selected=button.dataset.tab===tab;
    button.classList.toggle('selected',selected);
    button.setAttribute('aria-selected',String(selected));
    button.tabIndex=selected?0:-1;
    document.getElementById(button.getAttribute('aria-controls')).hidden=!selected;
  });
  document.getElementById('settings-tab-'+tab)?.focus({preventScroll:true});
}

function runCommand(command){return workspaceLauncher.open({query:String(command||'').trim()});}

document.addEventListener('submit',e=>{
  if(e.target.id==='workspace-create-form' || e.target.id==='workspace-rename-form') {e.preventDefault();submitWorkspaceName(e.target,e.target.id==='workspace-create-form');return;}
  if(e.target.id==='command-form') {e.preventDefault();runCommand(new FormData(e.target).get('command'));}
  if(e.target.matches('.browser-command-form')) {e.preventDefault();runCommand(new FormData(e.target).get('command'),e.target.dataset.browserId,true);}
  
});
document.addEventListener('input',e=>{
  if(e.target.closest('#workspace-create-form,#workspace-rename-form'))e.target.setCustomValidity('');
  if(e.target.id==='command')ui.terminalDraft=e.target.value;
  const form=e.target.closest('.browser-command-form');
  if(form && e.target.name==='command'){
    const b=ui.browsers.find(item=>item.id===form.dataset.browserId);
    if(b)b.commandDraft=e.target.value;
  }
});
document.addEventListener('change',e=>{
  if(e.target.id==='open-target')ui.target=e.target.value;
  if(e.target.id==='density'){ui.density=e.target.value;render();}
});
document.addEventListener('keydown',e=>{
  if(e.target.matches('[data-action="settings-tab"]')&&['ArrowLeft','ArrowRight','Home','End'].includes(e.key)){
    e.preventDefault();
    const tabs=['workspace','sources','connections','runs','plaza'],index=tabs.indexOf(ui.settingsTab);
    selectSettingsTab(tabs[e.key==='Home'?0:e.key==='End'?2:(index+(e.key==='ArrowRight'?1:2))%3]);
    return;
  }
  if((e.metaKey||e.ctrlKey)&&e.key.toLowerCase()==='k'){e.preventDefault();if(ui.modal)closeModal();openTerminalCommands();}
  if(e.key==='Escape'&&!ui.modal){
    if(ui.terminalCommandOpen){e.preventDefault();closeTerminalCommands(true);return;}
    const form=e.target.closest('.browser-command-form');
    if(form){const b=ui.browsers.find(item=>item.id===form.dataset.browserId);if(b){e.preventDefault();toggleBrowserCommand(b,false);}}
  }
  if((e.key==='Enter'||e.key===' ')&&e.target.matches('tr[data-action]')){e.preventDefault();e.target.click();}
});
document.addEventListener('click',async e=>{
  if(ui.terminalCommandOpen&&!e.target.closest('.terminal-command-menu'))closeTerminalCommands();
  const el=e.target.closest('[data-action]');if(!el)return;
  const action=el.dataset.action, id=el.dataset.id, b=ui.browsers.find(x=>x.id===id);
  if(action==='terminal-command'){if(ui.terminalCommandOpen)closeTerminalCommands(true);else openTerminalCommands();return;}
  if(action==='close-terminal-command'){closeTerminalCommands(true);return;}
  if(action==='reload-phema'&&b){
   el.disabled=true;el.setAttribute('aria-busy','true');
   try{await refreshPhemaFrame(document,b,{read:target=>reloadBrowserPhema(target,{readLayout:()=>refreshSavedLayouts([target],api)}),render:renderBrowserPanels,
    mounted:async(target,node)=>{node.dataset.phemaUi=JSON.stringify(uiBundle(target.customLayout)||{});
     const title=node.querySelector('.browser-title-line strong');if(title)title.innerHTML=label(target.design.title);
     const nav=document.querySelector('.browser-nav-item[data-id="'+CSS.escape(target.id)+'"] strong');if(nav)nav.innerHTML=label(target.design.title);
     const tab=document.querySelector('.window-tabs [data-id="'+CSS.escape(target.id)+'"]');if(tab)for(const text of tab.childNodes)if(text.nodeType===3&&text.textContent.trim())text.textContent=target.design.title;
     const layout=node.querySelector('.browser-layout');layout.style.cssText=layoutStyle(target.design.layout);
     layout.className='browser-layout layout-'+layoutKind(target.layoutId)+(target.design.layout.columns===12?' composed-browser-layout':'')+(usesFreePlacement(target.design.layout)?' free-placement':'');
     mountPhemaEdit(node,target);applyUI(node);await persist();}});}
   catch(error){toast(error.message);}finally{el.disabled=false;el.removeAttribute('aria-busy');}return;
  }
  if(action==='browser-language'){if(b)openBrowserLanguageDialog({value:b.uiLanguage,onChange:async value=>{await setBrowserLanguage(b,value);const node=document.querySelector('[data-browser="'+b.id+'"]');node.dataset.uiLocale=value||'';applyUI(node);await persist();}});return;}
  if(action==='review-layout-update'&&b?.layoutConflict){
    const conflict=b.layoutConflict,dialog=document.createElement('dialog');dialog.className='config-dialog';
    dialog.innerHTML=uiHTML`<div class="config-dialog-header"><h2>Review layout update</h2></div><div class="config-dialog-body"><p>The new layout changes mapped datasets. Keep this revision or open the update in a separate Browser with fresh mappings.</p><p>${esc(conflict.requirements.join(', '))}</p><button data-view>View Content</button><pre hidden></pre></div><footer class="config-dialog-footer"><button data-keep>Keep current revision</button><button data-open>Open updated copy</button></footer>`;
    document.body.append(dialog);dialog.onclose=()=>dialog.remove();dialog.querySelector('[data-keep]').onclick=()=>dialog.close();dialog.querySelector('[data-view]').onclick=()=>{const pre=dialog.querySelector('pre');pre.textContent=JSON.stringify(conflict.entry,null,2);pre.hidden=false;};
    dialog.querySelector('[data-open]').onclick=()=>{if(ui.browsers.length>=12){toast('Close a browser before opening another. The preview supports 12.');return;}const copy=createBrowser(b.command,conflict.entry);copy.consultantId=conflict.agentId;copy.layoutPublication={publicationId:conflict.publicationId,revision:conflict.revision};ui.browsers.push(copy);ui.activeId=copy.id;dialog.close();render();};dialog.showModal();return;
  }
  if(action==='browser-edit'){if(b)togglePhemaEdit(b);return;}
  if(action==='browser-command'){if(b)toggleBrowserCommand(b);return;}
  if(el.closest('.terminal-command-actions'))closeTerminalCommands();
  if(action==='toggle-sidebar'){if(narrowViewport.matches)narrowSidebarOpen=!narrowSidebarOpen;else ui.sidebarCollapsed=!ui.sidebarCollapsed;render();$('.sidebar-toggle')?.focus();return;}
  if(action==='agent-workspaces'){closeModal();workspaceLauncher.open();return;}
  if(action==='new'||action==='library'){openModal('consultant');return;}
  if(action==='workspaces'){await persist();openModal('workspaces');return;}
  if(action==='load-workspace'){loadWorkspace(el.dataset.workspaceId);return;}
  if(action==='save-current-workspace'){
    previews.capture(true).catch(()=>{});
    const saved=await persist();
    renderModal();
    $('[data-action="save-current-workspace"]')?.focus();
    toast(saved?`${currentWorkspace().name} saved to your account.`:ui.storageError);
    return;
  }
  if(action==='settings'||action==='plaza'){ui.settingsTab=action==='plaza'?'plaza':'workspace';openModal('settings');return;}
  if(action==='help'){openModal('help');return;}
  
  
  if(action==='dismiss'){closeModal();return;}
  if(action==='workspace'){ui.focus=false;render();return;}
  if(action==='activate'){bringToFront(id);if(ui.focus)layoutWindows();else if(el.closest('.browser-nav,.window-tabs'))$(`[data-browser="${id}"]`)?.scrollIntoView({block:'nearest'});return;}
  if(action==='save'){const ok=await persist();toast(ok?'Workspace saved to your account.':ui.storageError);if(ok)previews.capture(true).catch(()=>{});return;}
  if(action==='tile'||action==='focus'){ui.focus=action==='focus';if(action==='tile')ui.arrange=true;render();return;}
  if(action==='maximize'){ui.activeId=id;ui.focus=!ui.focus;render();return;}
  if(action==='close'){ui.browsers=ui.browsers.filter(x=>x.id!==id);if(ui.activeId===id)ui.activeId=ui.browsers[0]?.id;if(!ui.browsers.length)ui.focus=false;render();return;}
  if(action==='history'){
  try{
   await persist();const [{records},current]=await Promise.all([api('/api/workspace-state/revisions'),api('/api/workspace-state')]);
   const dialog=document.createElement('dialog');dialog.className='config-dialog';
   dialog.innerHTML=uiHTML`<header class="config-header"><h2>Workspace history</h2><button data-close-history aria-label="Close">×</button></header><div class="config-dialog-body">${records.sort((a,b)=>b.revision-a.revision).map(r=>uiHTML`<p>${esc(new Date(r.updatedAt*1000).toLocaleString())} · ${esc(r.revision)} <button data-restore-revision="${r.revision}">Restore revision</button></p>`).join('')}<p data-history-status role="status"></p></div>`;
   document.body.append(dialog);dialog.showModal();dialog.addEventListener('close',()=>dialog.remove(),{once:true});
   dialog.addEventListener('click',async e=>{if(e.target.closest('[data-close-history]'))dialog.close();const value=e.target.closest('[data-restore-revision]')?.dataset.restoreRevision;if(value!==undefined){try{await api('/api/workspace-state/rollback',{method:'POST',body:{previousRevision:Number(value),revision:current.revision}});location.reload();}catch(error){dialog.querySelector('[data-history-status]').textContent=error.message;}}});
  }catch(error){toast(error.message);}return;
 }
 if(action==='duplicate'){if(ui.browsers.length>=12){toast('This Terminal supports up to 12 browsers.');return;}const copy=createBrowser(b.command,b.customLayout);copy.range=b.range;copy.uiLanguage=b.uiLanguage;copy.fieldBindings=normalizeFieldBindings(b.fieldBindings);copy.refreshOverrides=normalizeRefreshOverrides(b.refreshOverrides);copy.contentOverrides=normalizeContentOverrides(b.contentOverrides);copy.instrumentOverrides=normalizeInstrumentOverrides(b.instrumentOverrides);copy.parameterValues=normalizeResearchState(b.parameterValues);copy.researchView=normalizeResearchState(b.researchView);copy.layoutPublication=b.layoutPublication?{...b.layoutPublication}:undefined;copy.consultantId=b.consultantId;copy.frame={...b.frame,x:b.frame.x+28,y:b.frame.y+28};ui.browsers.push(copy);ui.activeId=copy.id;ui.focus=false;render();toast('Independent browser copy opened.');return;}
  if(action==='change-layout'){openModal('consultant',id);ui.target='current';renderModal();return;}
  if(action==='range'){b.range=el.dataset.range;render();return;}
  if(action==='requirements'){b.showRequirements=!b.showRequirements;render();return;}
  if(action==='select-ticker'){workspaceLauncher.open({query:String(el.dataset.ticker||'')});return;}
  if(action==='ticker-research'){runCommand(`${el.dataset.ticker} RESEARCH`);return;}
  
  
  if(action==='inspect-dbl'){ui.modal='dbl';renderModal();return;}
  
  
  if(action==='settings-tab'){selectSettingsTab(el.dataset.tab);return;}
  if(action==='run-example'){closeModal();runCommand(el.dataset.command);return;}
  if(action==='discussion'){ui.discussionNodeId=el.dataset.nodeId;openModal('discussion',id);return;}
});


await restore();

render();
if(location.hash==='#plaza'){ui.settingsTab='plaza';openModal('settings');history.replaceState({},'',location.pathname+location.search);}
// At start, open Plaza account when this window has no Plaza or isn't signed in to its public Plaza.
// A window whose sign-in expires reloads, so this also covers losing the sign-in.
const openPlazaAccount=()=>{if(!document.querySelector('dialog[open]')){ui.settingsTab='plaza';openModal('settings');}};
const plazaAccountAtStart=()=>{if((!runtime.plaza.localOnly&&!runtime.plaza.url)||(runtime.plaza.delegatedLogin&&!runtime.user))openPlazaAccount();};
// A new installation asks for its language first, then shows Plaza account in that language.
const installationLanguage=runtime.installationLanguage||{};
if(installationLanguage.canChoose&&!installationLanguage.value&&!runtime.settings.preferences?.uiLanguage)
 openLanguageChoice({onChoose:async value=>{
  if(runtime.user)await api('/api/installation-language',{method:'PUT',body:{uiLanguage:value}}).catch(()=>{});
  setLocale(value);  // signed out, this saves the installation's language; signed in, the account's
 }}).then(()=>{plazaAccountAtStart();openStarterWorkspace();});
else {plazaAccountAtStart();openStarterWorkspace();}


const phemaWatcher=watchPhemaChanges(api,{
 blocked:()=>sessionTransition||!!gesture||ui.browsers.some(b=>b.editMode||phemaEditGesture(b))||
  !!document.querySelector('dialog[open],[data-tv-busy="true"]')||document.activeElement?.matches('input,textarea,select,[contenteditable="true"]'),
 beforeReload:async()=>{
  if(runtime.user)return persist();
  captureWorkspace();
  saveReloadDraft(scopedStorage(),{version:2,activeWorkspaceId:ui.workspaceId,
   workspaces:ui.workspaces.map(workspaceSnapshot),settings:{density:ui.density,sidebarCollapsed:ui.sidebarCollapsed}});
  return true;
 },
 refreshLayouts:async()=>{
  if(!runtime.user)return;
  const browsers=ui.browsers.filter(b=>b.id===ui.activeId),result=await refreshSavedLayouts(browsers,api);
  if(result.updated){render();await persist();}
 }
});
window.addEventListener('pagehide',()=>phemaWatcher.close(),{once:true});

document.addEventListener('connected:settings',()=>{if(ui.modal==='settings')renderModal();});
// Mark open browsers whose Phema has a running scheduled run, in place, so open views stay mounted.
document.addEventListener('scheduled-runs:changed',()=>{
  const running=runningPhemas();
  for(const b of ui.browsers){
    const small=document.querySelector(`.browser-nav-item[data-id="${CSS.escape(b.id)}"] small`);if(!small)continue;
    const on=running.has(browserState(b).phema?.id);let mark=small.querySelector('.browser-running');
    if(on&&!mark){mark=document.createElement('span');mark.className='browser-running';mark.textContent=' '+t('· Running');small.append(mark);}
    if(!on&&mark)mark.remove();
  }
});

loadCatalog().catch(error=>{runtime.catalogError=error.message;});

onWindowSignIn(async()=>{
 sessionTransition=true;
 try{
  await connect({requireUser:true});
  // Recreate account-scoped clients instead of keeping the guest cache/revision.
  const request=(path,options={})=>api(path,{...options,...(!options.method||options.method==='GET'?{signal:AbortSignal.timeout(10000)}:{})});
  workspaceClient=new WorkspaceClient(request,scopedStorage());
  await restore({strict:true});
  applyAccountTheme();
  
  closeModal();
  sessionTransition=false;
  render();
  // Say which account this window now uses, so a finished sign-in is not mistaken for nothing happening.
  if(runtime.user?.name||runtime.user?.email)toast(t('Signed in as {0}.',runtime.user.name||runtime.user.email));
  // An account with no saved workspace yet starts on its Plaza's starter workspace.
  openStarterWorkspace();
  // Browsers opened before this sign-in (or before access was approved) load their data now, the same as ↻ Reload Phema on each.
  const opened=new Set(ui.browsers.map(b=>b.id));
  setTimeout(()=>{for(const button of document.querySelectorAll('[data-action="reload-phema"][data-id]'))if(opened.has(button.dataset.id)&&!button.disabled)button.click();},0);
  loadCatalog().catch(error=>{runtime.catalogError=error.message;});
  // Layout checks must not delay account adoption or a usable workspace.
  const browsers=ui.workspaces.flatMap(w=>w.browsers);
  refreshSavedLayouts(browsers,api).then(result=>{
   if(result.updated){render();toast(`${result.updated} layout${result.updated===1?'':'s'} updated from App Consultant.`);}
  }).catch(()=>{});
 }finally{sessionTransition=false;}
});

document.addEventListener('click',async event=>{
 const action=event.target.closest('[data-workspace-action]')?.dataset.workspaceAction;if(!action)return;
 if(action==='saved-library'){
  try{
   await persist();const {records}=await api('/api/workspace-library');
   const dialog=document.createElement('dialog');dialog.className='config-dialog';
   dialog.innerHTML=uiHTML`<div class="config-dialog-body"><h2>Saved account workspaces</h2>${records.map((r,i)=>r.workspaces.map(w=>uiHTML`<p><strong>${esc(w.name)}</strong> · ${w.browsers} ${label('browsers')} <button data-load-account-workspace="${i}" data-workspace-id="${esc(w.id)}">Load copy</button></p>`).join('')).join('')}<p data-library-status role="status"></p><button data-library-close>Close</button></div>`;
   document.body.append(dialog);dialog.showModal();dialog.addEventListener('close',()=>dialog.remove(),{once:true});
   dialog.addEventListener('click',async e=>{
    if(e.target.closest('[data-library-close]'))dialog.close();
    const button=e.target.closest('[data-load-account-workspace]');if(!button)return;
    button.disabled=true;
    try{
     const {document}=await api('/api/workspace-library?key='+encodeURIComponent(records[Number(button.dataset.loadAccountWorkspace)].key));
     const selected=document.workspaces.find(w=>w.id===button.dataset.workspaceId);
     const copy={...selected,id:crypto.randomUUID(),name:(selected.name+' · copy').slice(0,60),updatedAt:Date.now(),browsers:selected.browsers.map(b=>({...b,previewId:crypto.randomUUID()}))};
     const imported=await api('/api/workspace-state/import',{method:'POST',body:{document:{version:2,activeWorkspaceId:copy.id,workspaces:[copy],settings:{}}}});
     await api('/api/workspace-state',{method:'PUT',body:{revision:imported.revision,document:{...imported.document,activeWorkspaceId:copy.id}}});
     location.reload();
    }catch(error){dialog.querySelector('[data-library-status]').textContent=error.message;button.disabled=false;}
   });
  }catch(error){toast(error.message);}
  return;
 }
 captureWorkspace();const w=currentWorkspace();
 if(action==='export'){const blob=new Blob([JSON.stringify({contract:'attas.workspace/1',workspace:workspaceSnapshot(w)},null,2)],{type:'application/json'});const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=w.name+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000);}
 if(action==='legacy'){const legacy=readWorkspaceStore(localStorage);if(!legacy.data){toast(legacy.error||'No legacy workspace found.');return;}if(ui.workspaces.length+legacy.data.workspaces.length>32){toast('Workspace limit reached.');return;}for(const saved of legacy.data.workspaces){const copy={...saved,id:crypto.randomUUID(),name:saved.name+' imported',browsers:saved.browsers.map(b=>({...createBrowser(b.command,b.customLayout),frame:b.frame,terminalDocumentId:b.terminalDocumentId,range:b.range,sourceMode:b.sourceMode||'parameters',contentSnapshotId:b.contentSnapshotId,uiLanguage:validLocale(b.uiLanguage)?b.uiLanguage:null,messages:normalizeMessages(b.messages),discussionDrafts:normalizeDiscussionDrafts(b.discussionDrafts),interactionDrafts:normalizeInteractionDrafts(b.interactionDrafts),fieldBindings:normalizeFieldBindings(b.fieldBindings),contentOverrides:normalizeContentOverrides(b.contentOverrides),refreshOverrides:normalizeRefreshOverrides(b.refreshOverrides),instrumentOverrides:normalizeInstrumentOverrides(b.instrumentOverrides),parameterValues:normalizeResearchState(b.parameterValues),researchView:normalizeResearchState(b.researchView),commandDraft:b.commandDraft??b.command,commandOpen:b.commandOpen===true,layoutTabs:b.layoutTabs,layoutPublication:b.layoutPublication,consultantId:b.consultantId,unsupportedLayout:b.unsupportedLayout})),updatedAt:Date.now()};copy.activeId=copy.browsers[0]?.id;while(ui.workspaces.some(w=>w.name===copy.name))copy.name+=' copy';ui.workspaces.push(copy);}render();renderModal();toast('Legacy workspaces copied into this account.');}
 if(action==='conflicts'){try{const {records}=await api('/api/workspace-state/conflicts');const dialog=document.createElement('dialog');dialog.className='config-dialog';dialog.innerHTML=uiHTML`<div class="config-dialog-body"><h2>Review saved drafts</h2>${records.map(r=>uiHTML`<p>${esc(new Date(r.updatedAt*1000).toLocaleString())} · ${r.document.workspaces.length} <button data-merge-draft="${esc(r.id)}">Merge saved draft</button></p>`).join('')}<button data-close-draft>Close</button></div>`;document.body.append(dialog);dialog.showModal();dialog.addEventListener('close',()=>dialog.remove(),{once:true});dialog.addEventListener('click',async e=>{if(e.target.closest('[data-close-draft]'))dialog.close();const id=e.target.closest('[data-merge-draft]')?.dataset.mergeDraft;if(id){try{await api('/api/workspace-state/import',{method:'POST',body:{document:records.find(r=>r.id===id).document}});location.reload();}catch(error){toast(error.message);}}});}catch(error){toast(error.message);}return;}
 if(action==='history'){
  try{
   await persist();const [{records},current]=await Promise.all([api('/api/workspace-state/revisions'),api('/api/workspace-state')]);
   const dialog=document.createElement('dialog');dialog.className='config-dialog';
   dialog.innerHTML=uiHTML`<header class="config-header"><h2>Workspace history</h2><button data-close-history aria-label="Close">×</button></header><div class="config-dialog-body">${records.sort((a,b)=>b.revision-a.revision).map(r=>uiHTML`<p>${esc(new Date(r.updatedAt*1000).toLocaleString())} · ${esc(r.revision)} <button data-restore-revision="${r.revision}">Restore revision</button></p>`).join('')}<p data-history-status role="status"></p></div>`;
   document.body.append(dialog);dialog.showModal();dialog.addEventListener('close',()=>dialog.remove(),{once:true});
   dialog.addEventListener('click',async e=>{if(e.target.closest('[data-close-history]'))dialog.close();const value=e.target.closest('[data-restore-revision]')?.dataset.restoreRevision;if(value!==undefined){try{await api('/api/workspace-state/rollback',{method:'POST',body:{previousRevision:Number(value),revision:current.revision}});location.reload();}catch(error){dialog.querySelector('[data-history-status]').textContent=error.message;}}});
  }catch(error){toast(error.message);}return;
 }
 if(action==='duplicate'){if(ui.workspaces.length>=32){toast('Workspace limit reached.');return;}const copy=structuredClone(w);copy.id=crypto.randomUUID();copy.name=w.name+' copy';while(ui.workspaces.some(x=>x.name===copy.name))copy.name+=' copy';copy.browsers=copy.browsers.map(b=>({...b,id:'browser-'+nextId++}));copy.activeId=copy.browsers[0]?.id;ui.workspaces.push(copy);selectWorkspace(copy);render();renderModal();}
 if(action==='delete'){ui.workspaces=ui.workspaces.filter(x=>x.id!==w.id);if(!ui.workspaces.length)ui.workspaces.push({id:crypto.randomUUID(),name:'Research desk',browsers:[],activeId:null,focus:false,arrange:true,updatedAt:Date.now()});selectWorkspace(ui.workspaces[0]);render();renderModal();}
});
document.addEventListener('change',async event=>{
 if(!event.target.matches('[data-workspace-import]'))return;
 try{const f=event.target.files[0];if(!f||f.size>500000)throw new Error('Workspace must be smaller than 500 KB.');const doc=JSON.parse(await f.text());if(ui.workspaces.length>=32)throw new Error('Workspace limit reached.');if(doc.contract!=='attas.workspace/1')throw new Error('Unsupported workspace format.');const w=doc.workspace;if(!Array.isArray(w.browsers)||w.browsers.length>12)throw new Error('Invalid browser list.');const browsers=w.browsers.map(b=>{const unsupported=b.unsupportedLayout||(b.customLayout&&!normalizeLayoutEntry(b.customLayout)?b.customLayout:null);return {...createBrowser(typeof b.command==='string'?b.command:'AAPL',unsupported?null:b.customLayout),uiLanguage:validLocale(b.uiLanguage)?b.uiLanguage:null,frame:b.frame&&['x','y','width','height'].every(k=>Number.isFinite(b.frame[k])&&b.frame[k]>=0)?b.frame:null,range:['1D','1W','1M','3M','1Y'].includes(b.range)?b.range:'1M',messages:normalizeMessages(b.messages),discussionDrafts:normalizeDiscussionDrafts(b.discussionDrafts),interactionDrafts:normalizeInteractionDrafts(b.interactionDrafts),fieldBindings:normalizeFieldBindings(b.fieldBindings),contentOverrides:normalizeContentOverrides(b.contentOverrides),refreshOverrides:normalizeRefreshOverrides(b.refreshOverrides),instrumentOverrides:normalizeInstrumentOverrides(b.instrumentOverrides),parameterValues:normalizeResearchState(b.parameterValues),researchView:normalizeResearchState(b.researchView),commandDraft:b.commandDraft??b.command,commandOpen:b.commandOpen===true,layoutTabs:b.layoutTabs,layoutPublication:b.layoutPublication,consultantId:b.consultantId,unsupportedLayout:unsupported};});const imported={id:crypto.randomUUID(),name:String(w.name||'Imported workspace').slice(0,60),browsers,activeId:browsers[0]?.id,focus:false,arrange:false,updatedAt:Date.now()};while(ui.workspaces.some(x=>x.name===imported.name))imported.name+=' copy';captureWorkspace();ui.workspaces.push(imported);selectWorkspace(imported);render();renderModal();}catch(error){toast(error.message);}
});

document.addEventListener('browser:interaction',()=>persist());





installPhemaEdit({find:id=>ui.browsers.find(b=>b.id===id),render:renderPhemaBrowser});

// An agent's workspace entry is one effect-free read with no inputs, so opening it is
// the request for it: run it now and open the workspace it returns. If that fails, the
// entry opens as declared, with its own button.
async function openedEntry(detail){
 const entry={phema:detail.phema,source_mode:detail.sourceMode};
 if(!workspaceEntryReference(entry))return detail;
 const clear=openingIndicator(t,'Opening workspace…');
 try{return {...detail,...await resolveWorkspaceEntry({entry,request:api,toEntry:phemaToEntry,workspaceId:currentWorkspace().id})};}
 catch(error){toast(error.message);return detail;}
 finally{clear();}
}

// Generic owner result handoff. Mount authorization still runs on the server.
document.addEventListener('phemacast:open-result',async event=>{
 try{
  let {phema,sourceMode}=event.detail||{};
  if(!phema||!['parameters','snapshot'].includes(sourceMode))throw new Error('Resolved owner Phema required.');
  ({phema,sourceMode}=await openedEntry({phema,sourceMode}));
  const next=phemaToEntry(phema),browser=openWorkspacePhema(next);
  browser.customLayout=toComposableEntry(normalizeLayoutEntry(next));
  browser.design=bindLayoutEntry(browser.customLayout,browser.command).dbl;
  // A Phemar's already-resolved inert snapshot remains explicitly labeled.
  // Browser-capture snapshot mode is a separate server-backed selection.
  browser.sourceMode=sourceMode;browser.contentSnapshotId=null;
  render();if(!await persist())toast(ui.storageError);
 }catch(error){toast(error.message);}
});

// The generic selector returns only an explicitly chosen, owner-resolved Phema.
async function openSelectedPhema({phema,sourceMode,target,snapshotId=null,selectionKey=null}){
 if(!phema||!['parameters','snapshot'].includes(sourceMode))throw new Error('Resolved owner Phema required.');
 // Snapshot mode opens this account's own saved Browser snapshot, chosen in New Data Browser.
 if(sourceMode==='snapshot'&&!/^[a-f0-9]{64}$/.test(snapshotId||''))throw new Error('Choose a saved snapshot.');
 ({phema,sourceMode}=await openedEntry({phema,sourceMode}));
 const previous=target==='current'?active():null;
 if(!previous&&ui.browsers.length>=12)throw new Error('Close a browser before opening another.');
 const entry=phemaToEntry(phema),browser=createBrowser('CONTENT',entry);
 // Opening it is the request for its data; panels that wait for ↻ load once (never saved).
 browser.sourceMode=sourceMode;browser.contentSnapshotId=sourceMode==='snapshot'?snapshotId:null;browser.loadOnOpen=sourceMode==='parameters';
 // The list entry it came from, so Change layout starts on it and not on a copy with the same id.
 browser.selectionKey=typeof selectionKey==='string'?selectionKey:null;
 if(previous){browser.id=previous.id;browser.frame=previous.frame;browser.z=previous.z;browser.uiLanguage=previous.uiLanguage;ui.browsers[ui.browsers.indexOf(previous)]=browser;}
 // A new window goes beside the open ones, never on top: a free tile, or all windows tiled again.
 else{const frame=freeFrame(ui.browsers.map(x=>x.frame),canvasBounds());if(frame)browser.frame=frame;else ui.arrange=true;ui.browsers.push(browser);}
 ui.activeId=browser.id;ui.focus=false;render();if(!await persist())toast(ui.storageError);
}
