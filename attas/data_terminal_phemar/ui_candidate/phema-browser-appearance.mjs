import {appearanceClass,appearanceFor} from './appearance.js';
import {customHTML,mountCustom,configureRendererUI} from './composition-renderers.js';
import {MEDIA_TYPES,mediaHTML,mountScenes,mountMedia} from './composition-media.js';
import {ui as uiHTML,label,applyUI,translate,currentLocale,resolvedLocale,scopedTranslate,setUIText,attribute} from './i18n.js';
import {valueFormatter,currentDisplayFormat} from './display-format.js';
import {displayTimezone,displayTime,displayDateTime,displayTimeTitle} from './time-display.js';
import {failureMessage,failureText} from './failure-messages.js';
configureRendererUI((host,context={})=>{const locale=host?resolvedLocale(host):context.locale||currentLocale();return {locale,formatValue:valueFormatter(locale),getLocale:()=>host?resolvedLocale(host):locale,label,setMessage:setUIText,translate:(key,...args)=>host?scopedTranslate(host,key,...args):translate(locale,key,...args),attribute};});
// A kept result names when it was fetched: the time today, the date and time before today.
const keptTime=at=>displayDateTime(at).slice(0,10)===displayDateTime(Date.now()).slice(0,10)?displayTime(at):displayDateTime(at);
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export function contentPanel(panel,browser,state,style){
 if(!MEDIA_TYPES.includes(panel.type)&&panel.type!=='custom')return null;
 const sourceMode=state.sourceMode||browser.sourceMode||'parameters';
 const result=state.content?.results?.[panel.referenceId],item=Array.isArray(result?.value)?result.value[0]:result?.value;
 let value=panel.media||item;
 if(panel.type==='text'&&value&&!value.text)value={...value,text:value.comment||value.description||value.summary||value.title||JSON.stringify(value,null,2)};
 if(panel.type==='interactive')value={...panel.media,result:item,disabled:panel.media?.disabled===true||sourceMode==='snapshot'||state.status==='loading'};
 const appearance=appearanceFor(panel,browser.customLayout);
 const custom=panel.type==='custom',customBody=custom?customHTML(panel.capability,value,{locale:browser.uiLanguage||currentLocale(),security:state.security,appearance,fieldFormats:panel.fieldFormats||{}}):null,unavailable=(custom?customBody===null:!value);
 const referenceStatus=result?.status||(panel.referenceId?(state.error?'unavailable':'pending'):null);
 const waiting=panel.referenceId&&!value&&['pending','resolving'].includes(referenceStatus);
 const missing=panel.referenceId&&!value&&['unavailable','failed'].includes(referenceStatus);
 const body=referenceStatus==='blocked'&&result?.reason==='trust-unconfirmed'?uiHTML`<div class="panel-approval" role="status"><strong>Can’t confirm trust, retrying…</strong><p>Plaza hasn’t confirmed this layout’s agents yet. It reloads by itself in a few seconds.</p></div>`:referenceStatus==='blocked'?uiHTML`<div class="panel-approval" role="note"><strong>Approval required</strong><p>Approve this layout to load its panels.</p><button type="button" class="button" data-phema-permissions="${esc(browser.id)}">Approve</button></div>`:unavailable&&result?.reason==='manual'?uiHTML`<p class="panel-note">Refresh content</p>`:unavailable&&panel.fallback?mediaHTML('text',panel.fallback):waiting?uiHTML`<div class="content-loading" role="status" aria-live="polite"><span class="content-loading-spinner" aria-hidden="true"></span><span>Loading data…</span></div>`:missing?failureMessage(result?.reason||state.error)?uiHTML`<p class="panel-note">${label(failureMessage(result?.reason||state.error))}</p>`:result?.reason||state.error?mediaHTML('text',{text:result?.reason||state.error}):uiHTML`<p class="panel-note">Data unavailable. Refresh to try again.</p>`:custom?customBody||uiHTML`<p class="panel-note">Renderer unavailable</p>`:mediaHTML(panel.type,value,{label:key=>translate(browser.uiLanguage||currentLocale(),key),uiLabel:label,uiAttribute:attribute,resourcePolicy:state.security});
 // A panel showing a result kept from an earlier fetch says when it was fetched, until ↻ fetches it anew.
 const keptAt=sourceMode!=='snapshot'&&result?.status==='ready'&&result.kept===true&&result.resolvedAt?result.resolvedAt:null;
 const renderKey=JSON.stringify([panel,value,referenceStatus,result?.reason,state.error,browser.editMode,style,sourceMode,currentDisplayFormat(),displayTimezone(),keptAt]);
 return uiHTML`<section data-panel-id="${esc(panel.id)}" data-content-render="${esc(renderKey)}" data-content-interaction="${esc(browser.id+'/'+(panel.referenceId||''))}" class="template-panel composition-content ${esc(appearanceClass(appearance))} panel-${esc(panel.type)}" style="${esc(style)}"><div class="panel-heading">${uiHTML`<h3>${label(panel.title)}</h3>`}${panel.referenceId?uiHTML`<button type="button" class="block-refresh-button" data-content-refresh="${esc(browser.id+'/'+panel.referenceId)}" title="Refresh content" aria-label="Refresh content" ${sourceMode==='snapshot'||result?.status==='resolving'?'disabled':''}>↻</button>`:''}${browser.editMode?uiHTML`<button type="button" class="block-refresh-button" data-content-config="${esc(browser.id+'/'+(panel.referenceId||''))}" title="Configure component" aria-label="Phema content">⚙</button>`:''}<span class="muted" title="${esc(keptAt?displayTimeTitle(keptAt):failureText(result?.reason))}">${keptAt?label('Updated {0}',keptTime(keptAt)):label(sourceMode==='snapshot'?'Snapshot':({pending:'Pending',resolving:'Resolving',ready:'Ready',stale:'Stale',unavailable:'Unavailable',failed:'Failed'})[referenceStatus]||'Static content')}</span></div>${body}${unavailable&&panel.fallback?uiHTML`<small class="panel-note">Fallback content</small>`:''}</section>`;
}
export function mountContent(root=document){mountScenes(root);mountMedia(root);mountCustom(root);}
const SNAPSHOT_STATUS={ready:'Ready',partial:'Partial',completed:'Complete',loading:'Loading',waiting:'Waiting',unavailable:'Unavailable'};
export async function openContentSnapshots(id,request,onSaved=null,onSources=null){
 const dialog=document.createElement('dialog');dialog.className='config-dialog';
 dialog.innerHTML=uiHTML`<div class="config-dialog-header"><h2>Phema content</h2><button type="button" data-close aria-label="Close">×</button></div><div class="config-dialog-body"><label data-source-choice hidden>Data source<select name="source"></select></label><label>Source mode<select name="mode"><option value="parameters">Parameters</option><option value="snapshot">Snapshot</option></select></label><label>Snapshot<select name="snapshot"></select></label><div class="phema-snapshot-actions"><button type="button" data-view>View Content</button><button type="button" data-save>Save snapshot</button><button type="button" data-use>Apply</button></div><p role="status"></p><pre hidden></pre></div>`;
 document.body.append(dialog);dialog.addEventListener('close',()=>dialog.remove(),{once:true});dialog.querySelector('[data-close]').onclick=()=>dialog.close();dialog.showModal();
 const route='/api/instances/'+id+'/content',source=dialog.querySelector('[name=source]'),mode=dialog.querySelector('[name=mode]'),select=dialog.querySelector('[name=snapshot]'),status=dialog.querySelector('[role=status]'),preview=dialog.querySelector('pre');
 // Saved times read like Find layouts' list, in the Terminal's time zone, and the state in the interface language.
 // Data panels can read from any source this Terminal can use for their data, including a paired local copy.
 function showSources(choices){const rows=choices?.sources||[];source.replaceChildren(new Option(translate(currentLocale(),'Automatic'),''),...rows.map(row=>new Option(row.panels<choices.panels?`${row.name} · ${row.panels}/${choices.panels}`:row.name,row.id)));source.value=rows.some(row=>row.id===choices?.chosen)?choices.chosen:'';source.closest('label').hidden=!rows.length;}
 source.addEventListener('change',async()=>{source.disabled=true;try{const result=await request(route,{method:'POST',body:{operation:'source',source:source.value||null}});showSources(result.dataSources);onSources?.(result.contentOverrides);status.textContent='';}catch(e){status.textContent=e.message;}finally{source.disabled=false;}});
 async function load(){const result=await request(route);showSources(result.dataSources);mode.value=result.sourceMode||'parameters';select.innerHTML=result.snapshots.map(s=>`<option value="${esc(s.id)}">${esc(displayDateTime(new Date(s.createdAt*1000).toISOString()))} · ${esc(translate(currentLocale(),SNAPSHOT_STATUS[s.status]||s.status))}</option>`).join('');if(result.snapshots.some(s=>s.id===result.current?.snapshotId))select.value=result.current.snapshotId;}
 // One action at a time: the dialog stays locked until it finishes, so a result always belongs to the action that asked for it.
 let busy=false;
 const controls=()=>dialog.querySelectorAll('.config-dialog-body button,.config-dialog-body select');
 // View Content needs a chosen snapshot, and so does Apply in snapshot mode.
 const guard=()=>{if(busy)return;dialog.querySelector('[data-view]').disabled=!select.value;dialog.querySelector('[data-use]').disabled=mode.value==='snapshot'&&!select.value;};
 select.addEventListener('change',guard);mode.addEventListener('change',guard);
 const say=(key,...args)=>{status.textContent=translate(currentLocale(),key,...args);};
 dialog.addEventListener('click',async event=>{
  const action=event.target.closest('[data-view],[data-save],[data-use]');if(!action||busy)return;
  busy=true;controls().forEach(c=>{c.disabled=true;});dialog.setAttribute('aria-busy','true');
  try{
   if(action.matches('[data-view]')){say('Loading…');const data=await request(route,{method:'POST',body:{operation:'view',snapshotId:select.value}});preview.textContent=JSON.stringify(data,null,2);preview.hidden=false;status.textContent='';}
   if(action.matches('[data-save]')){say('Saving snapshot…');const saved=await request(route,{method:'POST',body:{operation:'capture'}});
    // The screenshot taken now is what New Data Browser shows for this snapshot; a failed capture keeps the snapshot.
    try{await onSaved?.(saved.snapshotId);}catch{}
    await load();if([...select.options].some(o=>o.value===saved.snapshotId))select.value=saved.snapshotId;
    say('Snapshot saved · {0}',displayDateTime(new Date().toISOString()));}
   if(action.matches('[data-use]')){say('Loading…');await request(route,{method:'POST',body:{sourceMode:mode.value,snapshotId:select.value}});dialog.close();}
  }catch(e){status.textContent=e.message;}
  finally{busy=false;if(dialog.isConnected){controls().forEach(c=>{c.disabled=false;});dialog.removeAttribute('aria-busy');guard();}}
 });
 try{await load();applyUI(dialog);}catch(e){status.textContent=e.message;}
 guard();
}

export async function openContentPolicy(id,reference,request,onApply=()=>{}){
 const data=await request('/api/instances/'+id+'/content'),ref=data.references[reference];if(!ref)return openContentSnapshots(id,request);
 const dialog=document.createElement('dialog');dialog.className='config-dialog';dialog.innerHTML=uiHTML`<form><header class="config-dialog-header"><strong>Configure component</strong><button type="button" data-close>×</button></header><div class="config-dialog-body phema-form-grid"><label>Refresh<select name="trigger"><option value="open">On open</option><option value="change">On change</option><option value="manual">Manual</option><option value="interval">Interval</option></select></label><label>Seconds<input name="seconds" type="number" min="60" max="86400"></label><label>Freshness (seconds)<input name="age" type="number" min="0" max="31536000"></label><label>Timeout (seconds)<input name="timeout" type="number" min="1" max="120"></label><label>On failure<select name="failure"><option value="retain">Keep last result</option><option value="unavailable">Show unavailable</option></select></label><p role="status"></p></div><footer class="config-dialog-footer"><button type="button" data-close>Cancel</button><button type="submit">Apply</button></footer></form>`;
 document.body.append(dialog);const f=dialog.querySelector('form').elements;f.trigger.value=ref.refresh?.trigger||'open';f.seconds.value=ref.refresh?.seconds||600;f.age.value=ref.maxAgeSeconds||0;f.timeout.value=ref.timeoutSeconds||20;f.failure.value=ref.onFailure||'retain';if(ref.interaction)f.trigger.disabled=true;
 dialog.querySelector('form').onsubmit=async e=>{e.preventDefault();try{const updated=await request('/api/instances/'+id+'/content',{method:'POST',body:{operation:'configure',reference,values:{refresh:{trigger:f.trigger.value,...(f.trigger.value==='interval'?{seconds:Number(f.seconds.value)}:{})},maxAgeSeconds:Number(f.age.value),timeoutSeconds:Number(f.timeout.value),onFailure:f.failure.value}}});onApply(updated.contentOverrides);dialog.close();}catch(error){dialog.querySelector('[role=status]').textContent=error.message;}};dialog.querySelectorAll('[data-close]').forEach(b=>b.onclick=()=>dialog.close());dialog.addEventListener('close',()=>dialog.remove(),{once:true});applyUI(dialog);dialog.showModal();
}
