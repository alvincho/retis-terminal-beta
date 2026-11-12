/** Generic owner-declared selection dialog. Search and resolution stay upstream. */
import {validateUIBundle,resolveUIMessage} from "./phema-i18n.js";
import {createOwnerBusy} from './owner-busy.js';
import {displayDateTime} from './time-display.js';
const composition = phema => {
 const parts=phema?.sections?.flatMap(s=>s.content||[]).filter(n=>n.type==='phemacast:composition');
 if(parts?.length!==1)throw Error('A resolved selection Phema is required.');
 return parts[0];
};
const el=(tag,text='',className='')=>{const node=document.createElement(tag);node.textContent=text;node.className=className;return node;};
const effects=['writesState','externalSend','paidExecution','startsJob','opensStream'];
const readonly=ref=>ref?.kind==='pulse'&&ref.refresh?.trigger==='manual'&&ref.extensions?.['phemacast:operation-binding']&&
 effects.every(k=>ref.extensions?.['phemacast:operation-effects']?.[k]===false)&&Object.keys(ref.extensions['phemacast:operation-effects']).length===effects.length;

async function protectedSession({phema,request,toEntry,workspaceId}){
 const refs=composition(phema).references;
 if(!refs?.length||refs.some(ref=>!readonly(ref)))throw Error('Selection requires protected, effect-free owner reads.');
 const mount=await request('/api/instances',{method:'POST',body:{entry:toEntry(phema),command:'CONTENT',sourceMode:'parameters',workspaceId}});
 if(!mount?.id)throw Error('Selection mount returned no identity.');
 const path='/api/instances/'+encodeURIComponent(mount.id),capability=mount.content?.transportCapability;
 let closed=false,heartbeat;
 const close=async()=>{if(!closed){closed=true;clearInterval(heartbeat);await request(path,{method:'DELETE'});}};
 if(capability?.id!=='phemacast:neutral-operation-form'||!['1.0.0','1.1.0'].includes(capability.version)){
  await close();throw Error('Protected selection transport is unavailable.');
 }
 heartbeat=setInterval(()=>{if(!closed)request(path).catch(()=>{});},30000);
 return {close,async read(reference,inputs){
  if(closed)throw Error('Selection dialog is closed.');
  if(!refs.some(r=>r.id===reference))throw Error('Undeclared selection reference.');
  const intent=crypto.randomUUID().replaceAll('-','');
  const prepared=await request(path+'/content',{method:'POST',body:{operation:'prepare-interaction',references:[reference],inputs,requestKey:intent}});
  if(prepared.intentId!==intent||prepared.state!=='prepared'||!/^[a-f0-9]{64}$/.test(prepared.wireDigest))throw Error('Selection preparation could not be verified.');
  const result=await request(path+'/content',{method:'POST',body:{operation:'invoke-interaction',references:[reference],requestKey:intent,wireDigest:prepared.wireDigest}});
  if(result.intentId!==intent||result.wireDigest!==prepared.wireDigest)throw Error('Selection result could not be verified.');
  // The owner refused or the call did not finish: say so in the viewer's language, with the owner's reason when it gave one.
  if(result.state!=='complete'){const reason=typeof result.refusal==='string'&&result.refusal.trim()?result.refusal.trim():null;
   const uiKey=reason?'The layout owner could not open it: {0}':'The layout could not be opened. Try again.';
   throw Object.assign(Error(reason?'The layout owner could not open it: '+reason:uiKey),{uiKey,uiArgs:reason?[reason]:[]});}
  return result.value;
 }};
}

/** The starter workspace an owner publishes in its catalog (phemacast.core.starter_workspace): the one Phema with
 * the identity `getting-started` carries `phemacast:starter-workspace`, a name and the catalog keys to open in
 * order. Each is opened with no parameters through the owner's own selection, as Open in New Data Browser would.
 * Returns null when the owner publishes none; nothing here chooses what a workspace holds. */
export const STARTER_PHEMA='getting-started';
export async function readStarterWorkspace({phema,request,toEntry,workspaceId,locale='en',limit=12}){
 const entry=composition(phema).extensions?.['phemacast:selector-entry'];
 if(entry?.version!=='1.0.0'||!entry.phema)throw Error('The owner has not declared a Phema selector.');
 toEntry(entry.phema);
 const picker=composition(entry.phema).extensions?.['phemacast:selection'];
 if(picker?.version!=='1.0.0'||!picker.catalogReference||!picker.openReference)throw Error('The owner selection definition is invalid.');
 const session=await protectedSession({phema:entry.phema,request,toEntry,workspaceId});
 try{
  const catalog=await session.read(picker.catalogReference,{query:''});
  if(catalog?.schema!=='phemacast:selection-catalog/1'||!Array.isArray(catalog.items)||catalog.items.length>200)throw Error('The owner returned an invalid Phema list.');
  const open=async item=>{
   const value=await session.read(picker.openReference,{key:item.key,revision:item.revision,source_mode:'parameters',parameters:{}});
   if(!value?.phema||value.resolved!==true||value.source_mode!=='parameters')throw Error('The owner did not return a resolved Phema.');
   toEntry(value.phema);return value.phema;
  };
  const starters=catalog.items.filter(item=>item?.phemaId===STARTER_PHEMA&&item.available===true);
  if(starters.length!==1)return null;
  const document=await open(starters[0]),starter=composition(document).extensions?.['phemacast:starter-workspace'];
  if(starter?.version!=='1.0.0'||typeof starter.name!=='string'||!starter.name.trim()||!Array.isArray(starter.items)||
     !starter.items.length||starter.items.length>12||starter.items.some(item=>typeof item?.key!=='string'))
   throw Error('The starter workspace definition is invalid.');
  // The starter's name in the viewer's language, from the starter document's own messages.
  const bundles=[composition(document).presentation?.document?.extensions?.['phemacast:ui'],starters[0].extensions?.['phemacast:ui']]
   .map(bundle=>{try{return bundle?validateUIBundle(bundle):null;}catch{return null;}});
  const name=resolveUIMessage(bundles,locale,starter.name,(_locale,key)=>key).trim().slice(0,60);
  const phemas=[],skipped=[];
  for(const {key} of starter.items.slice(0,limit)){
   const item=catalog.items.find(row=>row?.key===key);
   // An entry this account cannot open stays out; the others still open.
   if(!item?.available){skipped.push(key);continue;}
   try{phemas.push(await open(item));}catch(error){skipped.push(key);console.warn('Starter workspace:',key,error.message);}
  }
  return {name,phemas,skipped};
 }finally{await session.close().catch(error=>console.error('Starter workspace cleanup:',error.message));}
}

export function installPhemaSelector({request,toEntry,workspaceId,text=x=>x,onOpen,previews=null,trustOwner=null}){
 let active;
 return {open({phema,target:initialTarget='new',current=null}){
  if(active){active.search.focus();return active.dialog;}
  const entry=composition(phema).extensions?.['phemacast:selector-entry'];
  if(entry?.version!=='1.0.0'||!entry.phema)throw Error('The owner has not declared a Phema selector.');
  toEntry(entry.phema);
  const picker=composition(entry.phema).extensions?.['phemacast:selection'];
  if(picker?.version!=='1.0.0'||!picker.catalogReference||!picker.openReference)throw Error('The owner selection definition is invalid.');
  const declared=composition(entry.phema).presentation?.document?.extensions?.['phemacast:ui'];
  const baseUI=declared?validateUIBundle(declared):null,itemUI=new WeakMap();
  const say=(key,item,args=[])=>resolveUIMessage([itemUI.get(item)||null,baseUI],document.documentElement.lang||'en',key,(_locale,key)=>text(key)).replace(/\{(\d+)\}/g,(match,i)=>args[i]??match);
  const put=(node,key,item,args=[])=>{node.textContent=say(key,item,args);node.dataset.uiKey=key;node.dataset.uiArgs=JSON.stringify(args);return node;};
  const label=(tag,key,className='',item,args=[])=>put(el(tag,'',className),key,item,args);
  const attribute=(node,name,key,item)=>{node.setAttribute(name,say(key,item));node.setAttribute('data-ui-attr-'+name,JSON.stringify({key,args:[]}));};
  const scoped=(node,bundle)=>{if(bundle)node.dataset.phemaUi=JSON.stringify(bundle);else delete node.dataset.phemaUi;};
  const dialog=el('dialog','','phema-selector');scoped(dialog,baseUI);attribute(dialog,'aria-label',picker.title||'Phema layouts');
  const busy=createOwnerBusy(dialog,{label:()=>say('Loading Phemas…')});
  const header=el('header'),heading=label('strong',picker.title||'Phema layouts'),dismiss=el('button','×');
  dismiss.type='button';attribute(dismiss,'aria-label','Close dialog');header.append(heading,dismiss);
  const searchForm=el('form','','ps-search'),search=el('input'),find=label('button','Search'),refresh=el('button','↻');
  search.type='search';attribute(search,'placeholder','Search Phemas');attribute(search,'aria-label','Search Phemas');search.maxLength=499;
  find.type='submit';refresh.type='button';attribute(refresh,'aria-label','Refresh Phema list');searchForm.append(search,find,refresh);
  const status=el('p','','ps-status');status.setAttribute('role','status');status.setAttribute('aria-live','polite');
  const body=el('div','','ps-columns'),catalog=el('aside','','ps-catalog'),detail=el('section','','ps-detail');
  catalog.setAttribute('role','listbox');attribute(catalog,'aria-label','Phemas');attribute(detail,'aria-label','Phema preview');body.append(catalog,detail);
  const footer=el('footer'),target=el('select'),targetLabel=el('label'),open=el('button','','ps-primary');targetLabel.append(label('span','Open layout in'));open.append(label('span','Open Phema'));
  attribute(target,'aria-label','Open layout in');for(const [key,value] of [['New Data Browser','new'],['Current Data Browser','current']]){const option=label('option',key);option.value=value;target.append(option);}
  target.value=initialTarget==='current'?'current':'new';targetLabel.append(target);open.type='button';open.disabled=true;
  // Opening into the current Data Browser replaces its layout and symbols; say so before it happens.
  const replaces=label('small',"Opening replaces this browser's content.",'ps-replace-note');replaces.hidden=target.value!=='current';target.onchange=()=>{replaces.hidden=target.value!=='current';};
  footer.append(targetLabel,replaces,open);dialog.append(header,searchForm,status,body,footer);
  let closed=false,loading=false,opening=false,selection,controls=[],mode,generation=0,shot,chosen=null,reopened=()=>false;
  const unwatch=previews?.watch(()=>{if(!closed&&selection)paint(selection);});previews?.load();
  const session=protectedSession({phema:entry.phema,request,toEntry,workspaceId:workspaceId()});
  const report=error=>{if(!closed){if(error?.uiKey){put(status,error.uiKey,undefined,error.uiArgs||[]);return;}delete status.dataset.uiKey;delete status.dataset.uiArgs;status.textContent=error.message||String(error);}};
  function update(){const fromSnapshot=mode?.value==='snapshot';open.disabled=loading||opening||!selection?.available||(fromSnapshot?!chosen:mode?.value!=='parameters'||controls.some(c=>!c.input.checkValidity()));}
  const when=seconds=>displayDateTime(new Date(seconds*1000).toISOString());
  function enlargeImage(src){
   const viewer=el('dialog','','layout-preview-dialog'),header=el('header'),close=el('button','×');attribute(close,'aria-label','Close dialog');
   header.append(label('strong','Screenshot taken with this snapshot'),close);const image=el('img');image.src=src;image.alt='';viewer.append(header,image);
   close.onclick=()=>viewer.close();viewer.onclose=()=>viewer.remove();document.body.append(viewer);viewer.showModal();
  }
  // The screenshot saved with the chosen snapshot; an older snapshot without one keeps the diagram.
  function paintSnapshot(item){
   const fallback=()=>{if(shot?.isConnected)shot.replaceChildren(label('p','No screenshot was saved with this snapshot','ps-shot-note'),diagram(item.preview,item));};
   if(!chosen?.screenshot){fallback();return;}
   const wanted=chosen.id;
   request('/api/snapshot-previews/'+encodeURIComponent(wanted)).then(saved=>{
    if(!shot?.isConnected||chosen?.id!==wanted)return;
    const enlarge=el('button','','ps-shot');enlarge.type='button';attribute(enlarge,'aria-label','Enlarge preview');
    const image=el('img');image.alt='';image.src=saved.image;enlarge.append(image);enlarge.onclick=()=>enlargeImage(saved.image);
    const note=el('p','','ps-shot-note');note.append(label('span','Screenshot taken with this snapshot'),document.createTextNode(' · '+when(chosen.createdAt)+' · '),label('span','Saved image, not live data'));
    const structure=el('details','','ps-structure');structure.append(label('summary','Layout structure'),diagram(item.preview,item));
    shot.replaceChildren(enlarge,note,structure);
   }).catch(fallback);
  }
  function diagram(preview,item){
   const grid=el('div','','ps-diagram');attribute(grid,'aria-label','Layout preview');
   const panels=preview?.panels||[],columns=Math.max(1,Math.min(24,Number(preview?.columns)||12));
   let rows=1;
   for(const p of panels){const r=Math.max(1,Math.min(128,Number(p.row)||1)),h=Math.max(1,Math.min(128,Number(p.rowSpan)||1));rows=Math.max(rows,r+h-1);}
   grid.style.gridTemplateColumns=`repeat(${columns},minmax(0,1fr))`;grid.style.gridTemplateRows=`repeat(${rows},minmax(0,1fr))`;
   for(const p of panels){
    const panel=label('div',p.label||'Panel','ps-panel',item);
    const col=Math.max(1,Math.min(columns,Number(p.column)||1)),width=Math.max(1,Math.min(columns-col+1,Number(p.columnSpan)||columns));
    panel.style.gridColumn=`${col}/span ${width}`;panel.style.gridRow=`${Math.max(1,Math.min(128,Number(p.row)||1))}/span ${Math.max(1,Math.min(128,Number(p.rowSpan)||1))}`;
    grid.append(panel);
   }
   if(!panels.length)grid.append(label('p','No layout preview is available.'));
   return grid;
  }
  // The last good instance the user's own Terminal saw, never live data; the diagram stands in until one exists.
  function paint(item){
   if(!shot?.isConnected)return;
   if(mode?.value==='snapshot'){paintSnapshot(item);return;}
   const saved=previews?.forPhema(item.phemaId);
   if(!saved){shot.replaceChildren(label('p','No saved screenshot yet','ps-shot-note'),diagram(item.preview,item));return;}
   const enlarge=el('button','','ps-shot');enlarge.type='button';attribute(enlarge,'aria-label','Enlarge preview');
   const image=el('img');image.alt='';image.src=saved.thumbnail;enlarge.append(image);enlarge.onclick=()=>previews.open(saved.layoutPreviewKey);
   previews.full(saved.layoutPreviewKey).then(src=>{if(image.isConnected)image.src=src;}).catch(()=>{});
   const note=el('p','','ps-shot-note');note.append(label('span','Last known preview'),document.createTextNode(' · '+previews.note(saved)+' · '),label('span','Saved image, not live data'));
   const structure=el('details','','ps-structure');structure.append(label('summary','Layout structure'),diagram(item.preview,item));
   shot.replaceChildren(enlarge,note,structure);
  }
  function show(item){
   selection=item;controls=[];chosen=null;mode=null;detail.replaceChildren();scoped(detail,itemUI.get(item));scoped(open,itemUI.get(item));
   for(const button of catalog.querySelectorAll('button'))button.setAttribute('aria-selected',String(button.dataset.key===item.key));
   shot=el('div','','ps-shot-slot');detail.append(label('h2',item.name,'',item),label('p',item.description||'','ps-description',item));
   // The Phema's own author, from Plaza; the App Consultant only serves it.
   if(typeof item.owner?.name==='string'&&item.owner.name){
    const line=el('div','','ps-owner-line'),href=item.owner.record&&trustOwner?.(item.owner.record);
    line.append(label('span','Owner: {0}','ps-owner',item,[item.owner.name]));
    // Plaza's page, where you choose to trust this owner's account; nothing changes here.
    // A separate element: translation rewrites the owner text and would drop a link inside it.
    if(href){const link=label('a','Trust this owner on Plaza','ps-owner-trust');link.href=href;link.target='_blank';link.rel='noopener';line.append(link);}
    detail.append(line);
   }
   detail.append(shot);paint(item);
   const previewButton=label('button','View Content');previewButton.type='button';
   const inspected=el('div','','ps-inspection');inspected.hidden=true;
   const table=el('table');attribute(table,'aria-label','Layout structure');
   const top=el('tr');for(const title of ['Panel','Column','Row','Width','Height'])top.append(label('th',title));table.append(top);
   for(const panel of item.preview?.panels||[]){const row=el('tr');for(const key of ['label','column','row','columnSpan','rowSpan'])row.append(key==='label'?label('td',panel[key]||'','',item):el('td',String(panel[key]??'')));table.append(row);}
   inspected.append(table);
   // Each panel by its layout title, with its saved state in the interface language.
   const PANEL_STATUS={ready:'Ready',partial:'Partial',pending:'Content pending',resolving:'Resolving content',unavailable:'Unavailable',failed:'Unavailable',blocked:'Approval required'};
   // In snapshot mode View Content shows what the chosen snapshot holds: its symbols and each panel's saved state.
   async function snapshotContent(){
    const value=await request('/api/phema-snapshots/'+encodeURIComponent(chosen.id));
    const facts=el('table');attribute(facts,'aria-label','Snapshot');
    const fact=(name,text)=>{const row=el('tr');row.append(label('th',name,'',item),el('td',text));facts.append(row);};
    fact('Saved at',when(value.capturedAt||chosen.createdAt));if(value.label)fact('Label',value.label);
    for(const [key,current] of Object.entries(value.parameters||{}))fact(item.parameterSchema?.properties?.[key]?.title||key,Array.isArray(current)?current.join(', '):String(current??''));
    const panels=el('table');attribute(panels,'aria-label','Panels');const head=el('tr');for(const title of ['Panel','Status','Rows'])head.append(label('th',title));panels.append(head);
    for(const panel of value.panels||[]){const row=el('tr');row.append(label('td',panel.title||panel.id,'',item),label('td',PANEL_STATUS[panel.status]||String(panel.status||'')),el('td',panel.rows==null?'—':String(panel.rows)));panels.append(row);}
    return [facts,panels];
   }
   previewButton.onclick=async()=>{
    if(inspected.hidden&&mode.value==='snapshot'){if(!chosen)return;try{inspected.replaceChildren(...await snapshotContent());}catch(error){report(error);return;}}
    else if(inspected.hidden)inspected.replaceChildren(table);
    inspected.hidden=!inspected.hidden;previewButton.setAttribute('aria-expanded',String(!inspected.hidden));
   };
   previewButton.setAttribute('aria-expanded','false');
   const snapshotField=el('label','','ps-snapshot');snapshotField.hidden=true;snapshotField.append(label('span','Snapshot'));
   const snapshotSelect=el('select');attribute(snapshotSelect,'aria-label','Snapshot');snapshotField.append(snapshotSelect);
   const noSnapshots=label('p','No saved snapshots yet. Open with parameters, then save one with ▣ Phema content → Save snapshot.','ps-snapshot-note');noSnapshots.hidden=true;
   const choose=value=>{chosen=value;inspected.hidden=true;previewButton.setAttribute('aria-expanded','false');paint(item);update();};
   snapshotSelect.onchange=()=>choose(snapshots.find(s=>s.id===snapshotSelect.value)||null);
   let snapshots=[];
   // The viewer's own saved Browser snapshots of this Phema, newest first.
   async function loadSnapshots(){
    try{
     const result=await request('/api/phema-snapshots?phemaId='+encodeURIComponent(item.phemaId));
     if(closed||selection!==item||mode.value!=='snapshot')return;
     snapshots=Array.isArray(result?.snapshots)?result.snapshots:[];
     snapshotSelect.replaceChildren(...snapshots.map(s=>{const option=el('option',when(s.createdAt)+(s.label?' · '+s.label:''));option.value=s.id;return option;}));
     snapshotField.hidden=!snapshots.length;noSnapshots.hidden=!!snapshots.length;choose(snapshots[0]||null);
    }catch(error){report(error);}
   }
   const modeLabel=el('label');modeLabel.append(label('span','Source mode'));mode=el('select');attribute(mode,'aria-label','Source mode');
   for(const [key,value] of [['Parameters','parameters'],['Snapshot','snapshot']]){const option=label('option',key);option.value=value;mode.append(option);}
   for(const option of mode.options)option.disabled=!(item.sourceModes||[]).includes(option.value);
   mode.value='parameters';modeLabel.append(mode);attribute(mode,'title',item.snapshotReason||'',item);
   mode.onchange=()=>{
    const fromSnapshot=mode.value==='snapshot';chosen=null;form.hidden=fromSnapshot;snapshotField.hidden=true;noSnapshots.hidden=true;inspected.hidden=true;
    previewButton.setAttribute('aria-expanded','false');paint(item);update();if(fromSnapshot)loadSnapshots();
   };
   const toolbar=el('div','','ps-preview-tools');toolbar.append(modeLabel,snapshotField,previewButton);detail.append(toolbar,noSnapshots,inspected);
   if(item.unavailableReason)detail.append(label('p',item.unavailableReason,'ps-error',item));
   const schema=item.parameterSchema||{},form=el('div','','ps-parameters');
   for(const [key,s] of Object.entries(schema.properties||{})){
    // A parameter its view keeps itself starts from its default; there is nothing to choose here.
    if(s.readOnly===true)continue;
    // Change layout starts from the open layout's own values, not the template's defaults.
    const start=reopened(item)&&Object.hasOwn(current.parameters,key)?current.parameters[key]:s.default;
    const field=el('label'),input=el(s.enum||s.oneOf?'select':'input');field.append(label('span',s.title||key,'',item));
    attribute(input,'aria-label',s.title||key,item);
    // A field's own help: its description as a tooltip, its first example as the placeholder. Values are never rewritten.
    if(typeof s.description==='string'&&s.description.trim())attribute(field,'title',s.description,item);
    const example=Array.isArray(s.examples)?s.examples[0]:undefined;
    if(s.enum||s.oneOf){for(const choice of s.oneOf||s.enum.map(value=>({const:value,title:String(value)}))){const option=choice.title?label('option',choice.title,'',item):el('option',String(choice.const));option.value=String(choice.const);input.append(option);};input.value=String(start??'');}
    else if(s.type==='boolean'){input.type='checkbox';input.checked=start===true;}
    else if(['integer','number'].includes(s.type)){input.type='number';input.step=s.type==='integer'?'1':'any';if(s.minimum!==undefined)input.min=String(s.minimum);if(s.maximum!==undefined)input.max=String(s.maximum);input.value=String(start??'');}
    else if(s.type==='string'||s.type==='array'){input.type='text';input.value=s.type==='array'?(Array.isArray(start)?start:[]).join(', '):String(start??'');if(s.maxLength)input.maxLength=s.maxLength;
     if(example!==undefined)input.placeholder=Array.isArray(example)?example.join(', '):String(example);}
    else {input.type='text';input.disabled=true;attribute(input,'placeholder','Unsupported parameter control');item={...item,available:false};selection=item;}
    input.required=(schema.required||[]).includes(key);input.oninput=update;field.append(input);form.append(field);controls.push({key,s,input});
   }
   detail.append(form);open.replaceChildren(label('span','Open'),document.createTextNode(' '),label('span',item.name,'',item));update();
  }
  async function load(){
   return busy.run(async()=>{
   const mine=++generation;loading=true;selection=null;detail.replaceChildren();catalog.replaceChildren();put(status,'Loading Phemas…');update();
   try{
    const value=await (await session).read(picker.catalogReference,{query:search.value.trim()});
    if(closed||mine!==generation)return;
    if(value?.schema!=='phemacast:selection-catalog/1'||!Array.isArray(value.items)||value.items.length>200)throw Error('The owner returned an invalid Phema list.');
    for(const item of value.items){
     if(typeof item.key!=='string'||typeof item.name!=='string')throw Error('The owner returned an invalid Phema item.');
     const button=el('button','','ps-option');button.type='button';button.setAttribute('role','option');button.dataset.key=item.key;
     const bundle=item.extensions?.['phemacast:ui'];if(bundle)itemUI.set(item,validateUIBundle(bundle));scoped(button,itemUI.get(item));button.append(label('strong',item.name,'',item));
     if(typeof item.description==='string'&&item.description.trim()){button.append(label('small',item.description,'',item));attribute(button,'title',item.description,item);}
     button.onclick=()=>show(item);catalog.append(button);
    }
    // Change layout starts on the entry this Browser opened. Its key tells it apart from another owner's copy that
    // keeps the same Phema id; a Browser saved before keys matches by id only when one item has it.
    const sameId=current&&!current.key?value.items.filter(item=>item.available&&item.phemaId===current.phemaId):[];
    reopened=item=>!!current&&(current.key?item.key===current.key:sameId.length===1&&sameId[0]===item);
    const opened=value.items.find(item=>item.available&&reopened(item));
    if(value.items.length)show(opened||value.items.find(item=>item.available)||value.items[0]);
    if(value.items.length)put(status,'{0} Phemas',undefined,[String(value.items.length)]);else put(status,'No matching Phemas.');
   }catch(error){if(mine===generation)report(error);}
   finally{if(mine===generation){loading=false;update();}}
   });
  }
  open.onclick=busy.bind(open,async()=>{
   if(open.disabled)return;opening=true;put(status,'Opening Phema…');update();
   const item=selection,parameters=Object.create(null),snapshot=mode.value==='snapshot'?chosen:null;
   for(const {key,s,input} of controls)parameters[key]=s.type==='boolean'?input.checked:['integer','number'].includes(s.type)?Number(input.value):s.type==='array'?input.value.split(',').map(x=>x.trim()).filter(Boolean):input.value;
   try{
    // A snapshot opens the same definition with the symbols it was saved with.
    const value=await (await session).read(picker.openReference,{key:item.key,revision:item.revision,source_mode:mode.value,parameters:snapshot?snapshot.parameters:parameters});
    if(closed)return;
    if(!value?.phema||value.resolved!==true||!['parameters','snapshot'].includes(value.source_mode))throw Error('The owner did not return a resolved Phema.');
    toEntry(value.phema);
    await onOpen({phema:value.phema,sourceMode:value.source_mode,target:target.value,selectionKey:item.key,...(snapshot?{snapshotId:snapshot.id}:{})});dialog.close();
   }catch(error){report(error);}finally{opening=false;update();}
  });
  searchForm.onsubmit=event=>{event.preventDefault();load();};refresh.onclick=load;dismiss.onclick=()=>dialog.close();
  dialog.addEventListener('close',()=>{closed=true;++generation;active=null;unwatch?.();busy.dispose();dialog.remove();session.then(s=>s.close()).catch(error=>console.error('Selection cleanup:',error.message));},{once:true});
  if(!document.querySelector('link[data-phema-selector-style]')){const link=document.createElement('link');link.rel='stylesheet';link.href=new URL('./phema-selector.css',import.meta.url).href;link.dataset.phemaSelectorStyle='true';document.head.append(link);}
  document.body.append(dialog);active={dialog,search};dialog.showModal();search.focus();load();return dialog;
 }};
}
