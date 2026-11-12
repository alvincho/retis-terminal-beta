/** Generic owner-declared selection dialog. Search and resolution stay upstream. */
import {validateUIBundle,resolveUIMessage} from "./phema-i18n.js";
import {createOwnerBusy} from './owner-busy.js';
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
  if(result.intentId!==intent||result.wireDigest!==prepared.wireDigest||result.state!=='complete')throw Error('Selection result could not be verified.');
  return result.value;
 }};
}

export function installPhemaSelector({request,toEntry,workspaceId,text=x=>x,onOpen}){
 let active;
 return {open({phema,target:initialTarget='new'}){
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
  target.value=initialTarget==='current'?'current':'new';targetLabel.append(target);open.type='button';open.disabled=true;footer.append(targetLabel,open);dialog.append(header,searchForm,status,body,footer);
  let closed=false,loading=false,opening=false,selection,controls=[],mode,generation=0;
  const session=protectedSession({phema:entry.phema,request,toEntry,workspaceId:workspaceId()});
  const report=error=>{if(!closed){delete status.dataset.uiKey;delete status.dataset.uiArgs;status.textContent=error.message||String(error);}};
  function update(){open.disabled=loading||opening||!selection?.available||mode?.value!=='parameters'||controls.some(c=>!c.input.checkValidity());}
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
  function show(item){
   selection=item;controls=[];detail.replaceChildren();scoped(detail,itemUI.get(item));scoped(open,itemUI.get(item));
   for(const button of catalog.querySelectorAll('button'))button.setAttribute('aria-selected',String(button.dataset.key===item.key));
   detail.append(label('h2',item.name,'',item),label('p',item.description||'','ps-description',item),diagram(item.preview,item));
   const previewButton=label('button','View Content');previewButton.type='button';
   const inspected=el('div','','ps-inspection');inspected.hidden=true;
   const table=el('table');attribute(table,'aria-label','Layout structure');
   const top=el('tr');for(const title of ['Panel','Column','Row','Width','Height'])top.append(label('th',title));table.append(top);
   for(const panel of item.preview?.panels||[]){const row=el('tr');for(const key of ['label','column','row','columnSpan','rowSpan'])row.append(key==='label'?label('td',panel[key]||'','',item):el('td',String(panel[key]??'')));table.append(row);}
   inspected.append(table);
   previewButton.onclick=()=>{inspected.hidden=!inspected.hidden;previewButton.setAttribute('aria-expanded',String(!inspected.hidden));};
   previewButton.setAttribute('aria-expanded','false');
   const modeLabel=el('label');modeLabel.append(label('span','Source mode'));mode=el('select');attribute(mode,'aria-label','Source mode');
   for(const [key,value] of [['Parameters','parameters'],['Snapshot','snapshot']]){const option=label('option',key);option.value=value;mode.append(option);}
   for(const option of mode.options)option.disabled=!(item.sourceModes||[]).includes(option.value);
   mode.value='parameters';mode.onchange=update;modeLabel.append(mode);attribute(mode,'title',item.snapshotReason||'',item);
   const toolbar=el('div','','ps-preview-tools');toolbar.append(modeLabel,previewButton);detail.append(toolbar,inspected);
   if(item.unavailableReason)detail.append(label('p',item.unavailableReason,'ps-error',item));
   const schema=item.parameterSchema||{},form=el('div','','ps-parameters');
   for(const [key,s] of Object.entries(schema.properties||{})){
    const field=el('label'),input=el(s.enum||s.oneOf?'select':'input');field.append(label('span',s.title||key,'',item));
    attribute(input,'aria-label',s.title||key,item);
    if(s.enum||s.oneOf){for(const choice of s.oneOf||s.enum.map(value=>({const:value,title:String(value)}))){const option=choice.title?label('option',choice.title,'',item):el('option',String(choice.const));option.value=String(choice.const);input.append(option);};input.value=String(s.default??'');}
    else if(s.type==='boolean'){input.type='checkbox';input.checked=s.default===true;}
    else if(['integer','number'].includes(s.type)){input.type='number';input.step=s.type==='integer'?'1':'any';if(s.minimum!==undefined)input.min=String(s.minimum);if(s.maximum!==undefined)input.max=String(s.maximum);input.value=String(s.default??'');}
    else if(s.type==='string'||s.type==='array'){input.type='text';input.value=s.type==='array'?(s.default||[]).join(', '):String(s.default??'');if(s.maxLength)input.maxLength=s.maxLength;}
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
    if(value.items.length)show(value.items.find(item=>item.available)||value.items[0]);
    if(value.items.length)put(status,'{0} Phemas',undefined,[String(value.items.length)]);else put(status,'No matching Phemas.');
   }catch(error){if(mine===generation)report(error);}
   finally{if(mine===generation){loading=false;update();}}
   });
  }
  open.onclick=busy.bind(open,async()=>{
   if(open.disabled)return;opening=true;put(status,'Opening Phema…');update();
   const item=selection,parameters=Object.create(null);
   for(const {key,s,input} of controls)parameters[key]=s.type==='boolean'?input.checked:['integer','number'].includes(s.type)?Number(input.value):s.type==='array'?input.value.split(',').map(x=>x.trim()).filter(Boolean):input.value;
   try{
    const value=await (await session).read(picker.openReference,{key:item.key,revision:item.revision,source_mode:mode.value,parameters});
    if(closed)return;
    if(!value?.phema||value.resolved!==true||!['parameters','snapshot'].includes(value.source_mode))throw Error('The owner did not return a resolved Phema.');
    toEntry(value.phema);
    await onOpen({phema:value.phema,sourceMode:value.source_mode,target:target.value});dialog.close();
   }catch(error){report(error);}finally{opening=false;update();}
  });
  searchForm.onsubmit=event=>{event.preventDefault();load();};refresh.onclick=load;dismiss.onclick=()=>dialog.close();
  dialog.addEventListener('close',()=>{closed=true;++generation;active=null;busy.dispose();dialog.remove();session.then(s=>s.close()).catch(error=>console.error('Selection cleanup:',error.message));},{once:true});
  if(!document.querySelector('link[data-phema-selector-style]')){const link=document.createElement('link');link.rel='stylesheet';link.href=new URL('./phema-selector.css',import.meta.url).href;link.dataset.phemaSelectorStyle='true';document.head.append(link);}
  document.body.append(dialog);active={dialog,search};dialog.showModal();search.focus();load();return dialog;
 }};
}
