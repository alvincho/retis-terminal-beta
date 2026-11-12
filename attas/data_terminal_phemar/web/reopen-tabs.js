// Reopen a closed Terminal window. Each browser tab is its own Terminal window, known
// only to that tab; opening its link again brings back its workspace, keys, theme and name.
// Windows no longer needed can be selected and deleted; open ones cannot.
import {ui as uiHTML,label,setUIText,attribute,t} from './i18n.js';
import {api} from './connected.js';
import {displayDateTime} from './time-display.js';
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

export function reopenTabsButtonHTML(){
 return uiHTML`<button class="button" type="button" data-reopen-tabs>Reopen Terminal window</button>`;
}

// Windows open in this browser hold a lock named after their window (terminal-session.js).
async function openWindows(){
 try{return new Set(((await navigator.locks?.query())?.held||[]).map(lock=>lock.name?.replace(/^phemacast-terminal-window:/,'')));}
 catch{return new Set();}
}

function tabHTML(tab,open){
 const browsers=tab.workspaces.reduce((total,w)=>total+w.browsers,0),busy=tab.current||open.has(tab.window);
 const summary=[label('Last used {0}',displayDateTime(tab.lastUsed)),...tab.workspaces.map(w=>esc(w.name==='Research desk'?t('Research desk'):w.name)),
  tab.workspaces.length?(browsers===1?label('1 browser'):label('{0} browsers',browsers)):'',
  tab.scheduledRuns?(tab.scheduledRuns===1?label('1 scheduled run'):label('{0} scheduled runs',tab.scheduledRuns)):''].filter(Boolean).join(' · ');
 const state=tab.current?label('This window'):open.has(tab.window)?label('Already open'):
  uiHTML`<a class="button" href="./?terminalWindow=${esc(tab.window)}" target="_blank" rel="noopener" data-reopen-tab>Open</a>`;
 return `<li class="reopen-tab ${tab.current?'is-current':''}"><input type="checkbox" value="${esc(tab.window)}" data-reopen-select ${attribute('aria-label','Select window')} ${busy?'disabled':''}><span><strong>${tab.name?esc(tab.name):label('Unnamed window')}</strong><small>${summary}</small></span><span class="reopen-tab-state">${state}</span></li>`;
}

document.addEventListener('click',async event=>{
 if(!event.target.closest?.('[data-reopen-tabs]'))return;
 const dialog=document.createElement('dialog');dialog.className='reopen-tabs';
 dialog.innerHTML=uiHTML`<header class="reopen-tabs-header"><div><h2>Reopen Terminal window</h2><small>Each window comes back with its workspace, keys, theme and name.</small></div><button type="button" data-reopen-close aria-label="Close">×</button></header><div class="reopen-tabs-tools" data-reopen-tools hidden><label><input type="checkbox" data-reopen-all> <span>Select all</span></label><span data-reopen-count></span><button type="button" class="button" data-reopen-delete disabled>Delete</button></div><div class="reopen-tabs-confirm" data-reopen-confirm hidden><span data-reopen-question></span><button type="button" class="button danger" data-reopen-confirm-delete>Delete</button><button type="button" class="button" data-reopen-cancel>Cancel</button></div><div class="reopen-tabs-body"><ul class="reopen-tab-list" data-reopen-list></ul><p data-reopen-status role="status"></p></div>`;
 document.body.append(dialog);dialog.showModal();dialog.addEventListener('close',()=>dialog.remove(),{once:true});
 const $=selector=>dialog.querySelector(selector),boxes=()=>[...dialog.querySelectorAll('[data-reopen-select]:not(:disabled)')];
 let tabs=[];
 function selection(){
  const all=boxes(),chosen=all.filter(box=>box.checked);
  $('[data-reopen-all]').checked=!!all.length&&chosen.length===all.length;
  $('[data-reopen-all]').indeterminate=!!chosen.length&&chosen.length<all.length;
  $('[data-reopen-all]').disabled=!all.length;
  $('[data-reopen-delete]').disabled=!chosen.length;
  if(chosen.length)setUIText($('[data-reopen-count]'),'{0} selected',chosen.length);else $('[data-reopen-count]').textContent='';
  $('[data-reopen-confirm]').hidden=true;$('[data-reopen-tools]').hidden=!tabs.length;
  return chosen.map(box=>box.value);
 }
 async function load(note=''){
  try{
   const [reply,open]=await Promise.all([api('/api/terminal-tabs'),openWindows()]);tabs=reply.tabs;
   $('[data-reopen-list]').innerHTML=tabs.map(tab=>tabHTML(tab,open)).join('');
   $('[data-reopen-status]').innerHTML=note||(!tabs.some(tab=>!tab.current)?label('No other saved windows.'):'');
   selection();
  }catch(error){$('[data-reopen-status]').textContent=error.message;}
 }
 dialog.addEventListener('change',e=>{
  if(e.target.matches('[data-reopen-all]'))for(const box of boxes())box.checked=e.target.checked;
  if(e.target.matches('[data-reopen-all],[data-reopen-select]'))selection();
 });
 dialog.addEventListener('click',async e=>{
  if(e.target.closest('[data-reopen-close]'))dialog.close();
  const link=e.target.closest('[data-reopen-tab]');
  // The window now opens in its own tab and holds its lock there.
  if(link)setTimeout(()=>{const row=link.closest('.reopen-tab'),box=row.querySelector('[data-reopen-select]');box.checked=false;box.disabled=true;link.replaceWith(Object.assign(document.createElement('span'),{innerHTML:label('Already open')}));selection();},0);
  if(e.target.closest('[data-reopen-delete]')){
   const chosen=selection(),runs=tabs.filter(tab=>chosen.includes(tab.window)).reduce((total,tab)=>total+(tab.scheduledRuns||0),0);
   $('[data-reopen-question]').innerHTML=(chosen.length===1?label('Delete 1 window?'):label('Delete {0} windows?',chosen.length))+' '+label('Workspaces, keys, themes and names are removed and cannot be restored.')+(runs?' '+(runs===1?label('1 scheduled run stops.'):label('{0} scheduled runs stop.',runs)):'');
   $('[data-reopen-confirm]').hidden=false;$('[data-reopen-confirm-delete]').focus();
  }
  if(e.target.closest('[data-reopen-cancel]')){$('[data-reopen-confirm]').hidden=true;$('[data-reopen-delete]').focus();}
  if(e.target.closest('[data-reopen-confirm-delete]')){
   const chosen=selection();if(!chosen.length)return;
   $('[data-reopen-delete]').disabled=true;
   try{const {deleted}=await api('/api/terminal-tabs/delete',{method:'POST',body:{windows:chosen}});
    await load(deleted.length===1?label('Deleted 1 window.'):label('Deleted {0} windows.',deleted.length));}
   catch(error){$('[data-reopen-status]').textContent=error.message;selection();}
  }
 });
 load();
});
