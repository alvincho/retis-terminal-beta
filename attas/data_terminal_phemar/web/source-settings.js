// Terminal settings → Data sources. Keys are saved in this Terminal only and are
// sent only to the vendor hosts shown on each row; no agent receives them.
import {ui as html,label,setUIText} from './i18n.js';
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let api,onSaved,rows=null,pending=null,warning='';

function status(source){
 if(!source.accessible)return 'Not accessible';
 return source.credential==='none'?'Public · no key required':'Ready to request';
}

function keyFields(source){
 if(!source.ownKeySupported)return html`<p>${label(source.credential==='none'?'This source provides public data without a key.':'This source does not publish a vendor API your Terminal can call yet.')}</p>`;
 const hosts=source.vendorHosts.join(', ');
 return html`<details class="manual-connection" ${source.keyConfigured?'':'open'}><summary>Advanced: manual API key connection</summary><label>API key<input type="password" name="apiKey" autocomplete="new-password" value="" aria-label="API key"></label>${source.keyConfigured?html`<small>${label('Saved key: {0} · Leave blank to keep the saved key.',source.maskedKey)}</small>`:source.keyInOtherWindow?html`<small class="source-key-elsewhere">${label('Your key for this source is saved in another Terminal window. Switch to that tab, or enter the key here for this window.')}</small>`:html`<small>${label(source.credential==='required'?'No key configured in this Terminal.':'Optional for this public source.')}</small>`}${hosts?html`<small class="source-vendor-hosts">${label('Sent only to {0}',hosts)}</small>`:''}${source.keyConfigured?html`<label class="source-remove-key"><input type="checkbox" name="removeKey"> Remove saved key</label>`:''}</details>`;
}

function row(source){
 const choice=source.sourceKeySupported?html`<label>Credential choice<select name="mode"><option value="own" ${source.mode==='own'?'selected':''}>Use my key</option><option value="source" ${source.mode==='source'?'selected':''}>Use source key</option></select></label>`:html`<input type="hidden" name="mode" value="own">`;
 return html`<details class="source-config-row" data-source-row="${esc(source.sourceId)}" data-source-search="${esc((source.name+' '+source.sourceId).toLowerCase())}"><summary><span class="source-config-name"><strong>${esc(source.name)}</strong><small>${esc(source.sourceId)}</small></span><span>${source.ownKeySupported||source.sourceKeySupported?label(source.mode==='source'?'Use source key':'Use my key'):''}</span><span class="source-access-status ${source.accessible?'available':'unavailable'}" title="${esc(source.reason)}">${label(status(source))}</span><span class="source-config-icon" aria-label="Configure source" title="Configure source">⚙</span></summary><form data-source-config="${esc(source.sourceId)}">${choice}<div class="source-key-config" ${source.mode==='source'?'hidden':''}>${keyFields(source)}</div><div class="source-owned-status" ${source.mode==='source'?'':'hidden'}>${label('The source operator’s own key serves the data it owns.')}</div><p class="source-config-reason">${source.reason?label(source.reason):''}</p>${source.ownKeySupported||source.sourceKeySupported?html`<button class="button" type="submit">Save source</button>`:''}<p data-source-feedback role="status"></p></form></details>`;
}

function filter(container){
 const query=container.querySelector('[data-source-filter]').value.trim().toLowerCase();
 let count=0;
 for(const item of container.querySelectorAll('[data-source-row]')){item.hidden=!item.dataset.sourceSearch.includes(query);if(!item.hidden)count++;}
 setUIText(container.querySelector('[data-source-count]'),'{0} sources',count);
}

function paint(){
 // LLM providers an agent publishes keep their keys the same way, listed after the data sources.
 const data=(rows||[]).filter(r=>r.kind!=='llm'),llm=(rows||[]).filter(r=>r.kind==='llm');
 const llmSection=llm.length?html`<h4 class="source-llm-heading">LLM keys</h4><p class="hint">Keys for the LLM providers agents publish. Each key is sent only to its provider; no agent receives it.</p>`+llm.map(row).join(''):'';
 for(const container of document.querySelectorAll('[data-terminal-sources]')){
  container.querySelector('[data-source-list]').innerHTML=data.map(row).join('')+llmSection;
  container.querySelector('[data-source-warning]').textContent=warning;
  filter(container);
 }
}

async function load(){
 if(pending)return pending;
 pending=(async()=>{try{const result=await api('/api/sources');rows=result.sources;warning=result.warning||'';paint();}catch(error){warning=error.message;paint();}finally{pending=null;}})();
 return pending;
}

export function sourceSettingsHTML(){
 queueMicrotask(()=>load());
 return html`<section data-terminal-sources><div class="source-list-toolbar"><label>Filter by name<input type="search" data-source-filter autocomplete="off" aria-label="Filter by name"></label><span data-source-count>${rows?label('{0} sources',rows.length):label('Loading sources…')}</span><button type="button" class="button" data-refresh-sources>Refresh</button></div><details class="source-list-help"><summary>How to Use</summary><p>Your keys stay in this Terminal and are sent only to vendor APIs. Agents never receive them.</p><p>Each row names the vendor hosts that receive your key. If a source later names another host, save your key again to allow it.</p><p>Sources with third-party data use your own key. Use source key appears only for data the source’s operator owns.</p></details><div data-source-list>${(rows||[]).map(row).join('')}</div><p data-source-warning role="status">${esc(warning)}</p></section>`;
}

export function installSourceSettings(deps){
 ({api,onSaved}=deps);
 document.addEventListener('input',event=>{if(event.target.matches('[data-source-filter]'))filter(event.target.closest('[data-terminal-sources]'));});
 document.addEventListener('click',event=>{if(event.target.closest('[data-refresh-sources]'))load();});
 document.addEventListener('change',event=>{
  if(!event.target.matches('[data-source-config] select[name="mode"]'))return;
  const form=event.target.form,shared=event.target.value==='source';
  form.querySelector('.source-key-config').hidden=shared;
  form.querySelector('.source-owned-status').hidden=!shared;
 });
 document.addEventListener('submit',async event=>{
  const form=event.target.closest('[data-source-config]');if(!form)return;
  event.preventDefault();event.stopImmediatePropagation();
  const button=form.querySelector('[type="submit"]'),feedback=form.querySelector('[data-source-feedback]');
  if(button)button.disabled=true;
  setUIText(feedback,'Saving…');
  try{
   const data=new FormData(form),body={mode:data.get('mode')||'own'};
   if(data.get('removeKey'))body.removeKey=true;
   else if(body.mode==='own'&&data.get('apiKey')?.trim())body.apiKey=data.get('apiKey').trim();
   await api('/api/sources/'+encodeURIComponent(form.dataset.sourceConfig),{method:'PUT',body});
   if(form.elements.apiKey)form.elements.apiKey.value='';
   // Updating this list keeps the settings dialog open and the page in place.
   await load();onSaved?.();
   const current=document.querySelector(`[data-source-config="${CSS.escape(form.dataset.sourceConfig)}"]`);
   if(current){current.closest('details').open=true;setUIText(current.querySelector('[data-source-feedback]'),'Saved.');}
  }catch(error){feedback.textContent=error.message;}finally{if(button)button.disabled=false;}
 },true);
}
