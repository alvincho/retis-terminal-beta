import {api,esc,browserState,applyBrowserPhemaEdit,loadCatalog} from './connected.js';
import {ui as html,label,attribute,applyUI,setUIText,clearUIText} from './i18n.js';
import {entryToPhema} from './phema-transfer.js';
import {editableCopy,compatibleDisplays,updateDisplay,deleteComponent,updateTabs,browserSettings} from './browser-phema-model.js';
import {setTemplatePosition,setGridSnapping,addPulseTemplate} from './layout-composition.js';
import {layoutScope,templateStyle,TEMPLATE_LABELS} from './layout-presentation.js';
import {PLAZA_PULSES} from './plaza-pulses.js';
import {openPhemaEditor} from './phema-editor.js';
import {normalizeGlobe} from './globe-model.js';

let controls;const busy=new Set();let gesture;
const working=b=>b.customLayout||browserState(b).entry;
export const phemaEditGesture=b=>gesture?.b===b;
export function phemaEditToolbar(b){return b.editMode?html`<div class="phema-edit-toolbar" data-phema-browser="${esc(b.id)}"><strong>Phema Edit</strong><button data-phema-action="add">＋ ${label('Data block')}</button><button data-phema-action="media">＋ ${label('Media / Phema')}</button><label><input type="checkbox" data-phema-snap ${b.design.layout.snapToGrid!==false?'checked':''}>${label('Snap to grid')}</label><button data-phema-action="save">${label('Save Phema')}</button><button data-phema-action="load">${label('Load Phema')}</button><span role="status" data-edit-feedback></span></div>`:'';}
function errorDialog(b,error){const dialog=document.createElement('dialog');dialog.className='phema-edit-dialog';dialog.dataset.uiLocale=b.uiLanguage||'';dialog.innerHTML=html`<p role="alert">${label(error.message)}</p><button data-close>${label('Close')}</button>`;document.body.append(dialog);applyUI(dialog);dialog.showModal();dialog.querySelector('[data-close]').onclick=()=>dialog.close();dialog.onclose=()=>dialog.remove();}
export function togglePhemaEdit(b){
 if(!b.editMode&&browserState(b).sourceMode==='snapshot'){errorDialog(b,new Error('Switch to parameter mode before editing a Phema.'));return;}
 b.editMode=!b.editMode;controls.render(b);
}
async function apply(b,next,settings){
 if(busy.has(b.id))throw new Error('An edit is being saved. Try again shortly.');
 busy.add(b.id);try{await applyBrowserPhemaEdit(b,editableCopy(next),settings);controls.render(b);}finally{busy.delete(b.id);}
}
function dialog(b,title,content){
 const node=document.createElement('dialog');node.className='phema-edit-dialog';node.dataset.uiLocale=b.uiLanguage||'';
 node.innerHTML=html`<form><header><strong>${label(title)}</strong><button type="button" data-close aria-label="Close">×</button></header><div class="phema-edit-body">${content}<p role="status" data-feedback></p></div><footer><button type="button" data-close>${label('Cancel')}</button><button class="button primary" type="submit">${label('Apply')}</button></footer></form>`;
 document.body.append(node);applyUI(node);node.showModal();node.querySelectorAll('[data-close]').forEach(n=>n.onclick=()=>node.close());node.onclose=()=>node.remove();return node;
}
function submit(node,callback){node.querySelector('form').onsubmit=async event=>{event.preventDefault();const button=node.querySelector('[type=submit]'),status=node.querySelector('[data-feedback]');button.disabled=true;status.style.color='var(--muted)';setUIText(status,'Applying changes…');applyUI(node);try{await callback(event.target.elements);node.close();}catch(error){status.style.color='var(--red)';setUIText(status,error.message);applyUI(node);}finally{button.disabled=false;}};}
function configure(b,id){
 const entry=working(b),n=entry.design.layout.children.find(n=>n.id===id)||entry.design.layout.tabbedView;
 if(!n)return;
 if(n.referenceId||n.componentId||n.media||n.type==='phemar'){openPhemaEditor(entry,next=>apply(b,next),id,{uiLanguage:b.uiLanguage});return;}
 const choices=compatibleDisplays(entry,n),req=entry.design.dataRequirements.find(r=>r.id===n.requirementId);
 const d=dialog(b,'Edit component',html`<div class="phema-edit-fields">${n===entry.design.layout.tabbedView?html`<label>${label('Tab names, one per line')}<textarea name="tabs" rows="${n.tabs.length}" required>${esc(n.tabs.map(tab=>tab.title).join('\n'))}</textarea></label>`:html`<label>${label('Title')}<input name="title" maxlength="80" value="${esc(n.title)}" required></label>`}${n.template?html`<label>${label('Display template')}<select name="template">${[...new Set([n.template,...choices])].map(k=>html`<option value="${esc(k)}" ${k===n.template?'selected':''}>${label(TEMPLATE_LABELS[k]||k)}</option>`).join('')}</select></label>`:''}${entry.design.layout.tabbedView&&n.id!==entry.design.layout.tabbedView.id?html`<label>${label('Layout tab')}<select name="tab"><option value="">${label('Top level')}</option>${entry.design.layout.tabbedView.tabs.map(tab=>html`<option value="${esc(tab.id)}" ${n.tabId===tab.id?'selected':''}>${label(tab.title)}</option>`).join('')}</select></label>`:''}${Object.entries(n.position).map(([key,v])=>html`<label>${label(({column:'Column',row:'Row',columnSpan:'Width',rowSpan:'Height'})[key])}<input name="${key}" type="number" min="1" max="${key==='row'?24:key==='rowSpan'?6:12}" step="any" value="${v}" required></label>`).join('')}</div>${req?html`<details><summary>${label('Block schema')}</summary><code>${esc(req.schema||req.modelRef?.id||'')}</code><p>${esc(req.fields.join(', '))}</p></details>`:''}`);
 submit(d,async f=>{
  const p=Object.fromEntries(Object.keys(n.position).map(k=>[k,Number(f[k].value)]));
  if(n.id===entry.design.layout.tabbedView?.id){await apply(b,updateTabs(entry,p,f.tabs.value.split('\n')));}
  else await apply(b,updateDisplay(entry,id,{title:f.title.value,template:f.template?.value,tabId:f.tab?.value,position:p}));
 });
}
async function addData(b){
 await loadCatalog();const pulses=PLAZA_PULSES.filter(p=>p.templates?.some(k=>!['discussion','pulse-chat'].includes(k)));
 const d=dialog(b,'Add data block',html`<label>${label('Search pulses')}<input name="search" type="search" autocomplete="off"></label><div class="phema-edit-fields"><label>${label('Pulse')}<select name="pulse" required></select></label><label>${label('Display template')}<select name="template" required></select></label></div>`),f=d.querySelector('form').elements;
 const templates=()=>{const p=pulses.find(p=>p.id===f.pulse.value);f.template.innerHTML=(p?.templates||[]).filter(k=>!['discussion','pulse-chat'].includes(k)).map(k=>html`<option value="${esc(k)}">${label(TEMPLATE_LABELS[k]||k)}</option>`).join('');applyUI(d);};
 const search=()=>{const q=f.search.value.toLowerCase();f.pulse.innerHTML=pulses.filter(p=>[p.name,p.schema,p.publisherId].some(v=>String(v).toLowerCase().includes(q))).map(p=>html`<option value="${esc(p.id)}">${esc(p.name)} · ${esc(p.schema)}</option>`).join('');templates();};f.search.oninput=search;f.pulse.onchange=templates;search();
 submit(d,async()=>{const entry=working(b),next=addPulseTemplate(entry,f.pulse.value,f.template.value);if(JSON.stringify(next)===JSON.stringify(entry))throw new Error('No free canvas space.');await apply(b,next);});
}
async function library(b,mode){
 const d=dialog(b,mode==='save'?'Save Phema':'Load Phema',html`<div class="phema-edit-fields"><label>${label('Location')}<select name="location"><option value="local">${label('On this device')}</option></select></label><label>${label('Saved Phema')}<select name="record"><option value="">${label('New copy')}</option></select></label>${mode==='save'?html`<label>${label('Name')}<input name="name" value="${esc(working(b).name)}" maxlength="80" required></label>`:''}</div><small>${label('Private to your signed-in account. Browser source mappings are saved separately.')}</small>`),f=d.querySelector('form').elements;
 const status=d.querySelector('[data-feedback]'),button=d.querySelector('[type=submit]');let records=[],generation=0;button.disabled=true;
 const base=()=>f.location.value==='local'?'/api/phema-library':'/api/phema-library/consultants/'+encodeURIComponent(f.location.value)+'/records';
 async function list(){const ticket=++generation;button.disabled=true;try{const data=await api(base());if(ticket!==generation)return;records=data.records;f.record.innerHTML=(mode==='save'?html`<option value="">${label('New copy')}</option>`:'')+records.map(r=>html`<option value="${esc(r.id)}">${esc(r.name)} · r${r.revision}</option>`).join('');clearUIText(status);status.textContent='';if(!records.length&&mode!=='save')setUIText(status,'No saved Phemas.');button.disabled=mode==='load'&&!records.length;applyUI(d);}catch(error){if(ticket===generation){setUIText(status,error.message);applyUI(d);}}}
 f.location.onchange=list;f.record.onchange=()=>{const r=records.find(r=>r.id===f.record.value);if(r&&f.name)f.name.value=r.name;};await list();
 // Local library remains usable while trusted connections are checked.
 api('/api/phema-library/consultants').then(data=>{if(!d.isConnected)return;f.location.insertAdjacentHTML('beforeend',data.consultants.map(c=>html`<option value="${esc(c.id)}">${esc(c.name)} · ${label('App Consultant')}</option>`).join(''));applyUI(d);}).catch(()=>{});
 submit(d,async()=>{
  if(mode==='load'){if(!f.record.value)throw new Error('Choose a saved Phema.');const record=await api(base()+'/'+encodeURIComponent(f.record.value));await apply(b,record.entry,record.browserSettings);}
  else{const selected=records.find(r=>r.id===f.record.value),next=editableCopy(working(b));next.id=selected?.id||'custom-browser-'+crypto.randomUUID();next.name=f.name.value.trim();if(!next.name)throw new Error('Enter a Phema name.');next.design.title=next.name;
   await api(base()+'/'+encodeURIComponent(next.id),{method:'PUT',body:{phema:entryToPhema(next),browserSettings:browserSettings(b),revision:selected?.revision||0}});
  }
 });
}
export function mountPhemaEdit(root,b){
 if(!root)return;const layout=root.matches('.browser-layout')?root:root.querySelector('.browser-layout');if(!layout)return;
 layout.classList.toggle('phema-editing',!!b.editMode);if(!b.editMode){layout.querySelectorAll('[data-phema-handles]').forEach(n=>n.remove());return;}
 const entry=working(b);if(!entry)return;
 for(const section of layout.querySelectorAll('[data-panel-id]')){
  if(section.closest('.nested-phema-layout'))continue;
  const id=section.dataset.panelId,node=entry.design.layout.children.find(n=>n.id===id)|| (entry.design.layout.tabbedView?.id===id?entry.design.layout.tabbedView:null);if(!node||section.querySelector(':scope > [data-phema-handles]'))continue;
  const bar=document.createElement('div');bar.dataset.phemaHandles=id;bar.dataset.phemaBrowser=b.id;bar.className='phema-edit-handles';
  bar.innerHTML=html`<button data-phema-drag="${esc(id)}" ${attribute('aria-label','Move component {0}',node.title||'Tabbed view')} title="Drag to move. Arrow keys to move; Shift + arrows to resize.">⠿</button><button data-phema-config="${esc(id)}" ${attribute('aria-label','Edit component {0}',node.title||'Tabbed view')}>⚙</button>${node.requirementId?html`<button data-block-mapping="${esc(b.id+'/'+id)}" title="Sources & field mapping" aria-label="Sources & field mapping">☷</button>`:''}${node!==entry.design.layout.tabbedView?html`<button data-phema-remove="${esc(id)}" ${attribute('aria-label','Remove component {0}',node.title)}>×</button>`:''}<button data-phema-resize="${esc(id)}" ${attribute('aria-label','Resize component {0}',node.title||'Tabbed view')}>◢</button>`;
  section.append(bar);
 }
 applyUI(root);
}
export function installPhemaEdit(options){
 controls=options;
 // Installed Globe code may update only its own authored data, via the same
 // owner-checked Browser edit path as Phema Edit. Never mutate the publication.
 document.addEventListener('browser-renderer-data',async event=>{
  const detail=event.detail,section=event.target.closest('[data-content-interaction]');if(!section||typeof detail?.resolve!=='function'||typeof detail?.reject!=='function')return;
  try{
   const b=controls.find(section.dataset.contentInteraction.split('/')[0]);if(!b)throw new Error('Browser unavailable.');
   if((browserState(b).sourceMode||b.sourceMode)==='snapshot')throw new Error('Switch to parameter mode before editing a Phema.');
   const next=structuredClone(working(b)),node=next.design.layout.children.find(n=>n.id===section.dataset.panelId);
   if(node?.capability?.id!=='attas:display.globe')throw new Error('This component cannot update Globe data.');
   node.media=normalizeGlobe(detail.value);await apply(b,next);detail.resolve();
  }catch(error){detail.reject(error);}
 });
 document.addEventListener('click',async event=>{
  const btn=event.target.closest('[data-phema-action],[data-phema-config],[data-phema-remove]');if(!btn)return;
  const b=controls.find(btn.closest('[data-phema-browser]').dataset.phemaBrowser);if(!b)return;event.preventDefault();
  try{if(btn.dataset.phemaConfig)configure(b,btn.dataset.phemaConfig);else if(btn.dataset.phemaRemove)await apply(b,deleteComponent(working(b),btn.dataset.phemaRemove));else if(btn.dataset.phemaAction==='add')await addData(b);else if(btn.dataset.phemaAction==='media')openPhemaEditor(working(b),next=>apply(b,next),'',{uiLanguage:b.uiLanguage});else await library(b,btn.dataset.phemaAction);}catch(error){errorDialog(b,error);}
 });
 document.addEventListener('change',async event=>{if(!event.target.matches('[data-phema-snap]'))return;const b=controls.find(event.target.closest('[data-phema-browser]').dataset.phemaBrowser);try{await apply(b,setGridSnapping(working(b),event.target.checked));}catch(error){errorDialog(b,error);controls.render(b);}});
 document.addEventListener('pointerdown',event=>{
  const btn=event.target.closest('[data-phema-drag],[data-phema-resize]');if(!btn||event.button!==0)return;
  const b=controls.find(btn.closest('[data-phema-browser]').dataset.phemaBrowser),id=btn.dataset.phemaDrag||btn.dataset.phemaResize,entry=working(b),node=entry.design.layout.children.find(n=>n.id===id)||entry.design.layout.tabbedView;
  const tile=btn.closest('[data-panel-id]'),canvas=tile.parentElement,box=tile.getBoundingClientRect(),style=getComputedStyle(canvas),gapX=parseFloat(style.columnGap)||0,gapY=parseFloat(style.rowGap)||0;
  gesture={b,id,entry,tile,btn,startX:event.clientX,startY:event.clientY,position:{...node.position},dx:(box.width+gapX)/node.position.columnSpan,dy:(box.height+gapY)/node.position.rowSpan,resize:!!btn.dataset.phemaResize,scope:node.tabId?layoutScope(entry.design.layout,node.tabId):layoutScope(entry.design.layout)};
  event.preventDefault();event.stopPropagation();btn.setPointerCapture(event.pointerId);
 },true);
 document.addEventListener('pointermove',event=>{if(!gesture)return;const g=gesture,snap=g.entry.design.layout.snapToGrid!==false,round=v=>snap?Math.round(v):Number(v.toFixed(2));let p={...g.position};
  if(g.resize){p.columnSpan=Math.max(1,Math.min(13-p.column,round(p.columnSpan+(event.clientX-g.startX)/g.dx)));p.rowSpan=Math.max(1,Math.min(6,25-p.row,round(p.rowSpan+(event.clientY-g.startY)/g.dy)));}
  else{p.column=Math.max(1,Math.min(13-p.columnSpan,round(p.column+(event.clientX-g.startX)/g.dx)));p.row=Math.max(1,Math.min(25-p.rowSpan,round(p.row+(event.clientY-g.startY)/g.dy)));}
  const next=setTemplatePosition(g.entry,g.id,p),candidate=next.design.layout.children.find(n=>n.id===g.id)||next.design.layout.tabbedView;g.next=next;g.tile.style.cssText=templateStyle(candidate,g.scope);
 });
 async function finish(event,cancel=false){if(!gesture)return;const g=gesture;gesture=null;try{if(!cancel&&g.next&&JSON.stringify(g.next)!==JSON.stringify(g.entry))await apply(g.b,g.next);else controls.render(g.b);}catch(error){controls.render(g.b);errorDialog(g.b,error);}}
 document.addEventListener('pointerup',event=>finish(event));document.addEventListener('pointercancel',event=>finish(event,true));
 document.addEventListener('keydown',async event=>{const btn=event.target.closest('[data-phema-drag],[data-phema-resize]');if(!btn||!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(event.key))return;event.preventDefault();event.stopPropagation();const b=controls.find(btn.closest('[data-phema-browser]').dataset.phemaBrowser),entry=working(b),id=btn.dataset.phemaDrag||btn.dataset.phemaResize,node=entry.design.layout.children.find(n=>n.id===id)||entry.design.layout.tabbedView,p={...node.position};const resize=event.shiftKey||!!btn.dataset.phemaResize,key=['ArrowLeft','ArrowRight'].includes(event.key)?resize?'columnSpan':'column':resize?'rowSpan':'row';p[key]+= ['ArrowRight','ArrowDown'].includes(event.key)?1:-1;try{await apply(b,setTemplatePosition(entry,id,p));document.querySelector('[data-phema-browser="'+b.id+'"] [data-phema-'+(btn.dataset.phemaResize?'resize':'drag')+'="'+id+'"]')?.focus();}catch(error){errorDialog(b,error);}});
}
