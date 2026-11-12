import {ui,applyUI,setUIText,label} from './i18n.js';
const EFFECTS={read:'Reads data',generate:'Generates content',action:'Runs when you load it',send:'Sends data',payment:'Makes a payment'};
const CLASSES={public:'Public data',private:'Private data',confidential:'Confidential data'};
/** Where a link's request goes, by the agent's Plaza name: a data request sends only the symbols. */
function destination(ref,data){
 const target=ref.target||{},name=data.agentNames?.[target.agentId];
 if(target.pulse==='data_fetch')return name?label('Asks {0} for data; only the symbols are sent',name):label('Asks a data source for data; only the symbols are sent');
 return name?label('Sends its inputs to {0}',name):label('Sends its inputs to an agent on Plaza');
}
/** One plain line per link: the panel it fills, what it does, where it goes, its symbols, its data class and its points ceiling. */
function plainLine(node,ref,rule,data){
 const titles=data.panelTitles||{},title=titles[node.path?node.path+'/'+ref.id:ref.id]||titles[ref.id]||ref.id;
 const bound=value=>typeof value?.$param==='string'?data.parameters?.[value.$param.replace(/^\//,'')]:value;
 const symbols=[bound(ref.inputs?.model_request?.instruments)].flat().filter(s=>typeof s==='string'&&s);
 const points=Number(rule?.maxPoints)||0;
 return [label(title),label(EFFECTS[rule?.effect]||'Undeclared'),destination(ref,data),...(symbols.length?[label('Symbols: {0}',symbols.join(', '))]:[]),label(CLASSES[rule?.classification]||CLASSES.private),points?label('Up to {0} points',points):label('Free')].join(' · ');
}
export async function openPhemaPermissions(id,request,onChange=()=>{},scope=null){
 if(!document.querySelector('link[data-phema-permissions]')){const css=document.createElement('link');css.rel='stylesheet';css.href='/phema-permissions.css';css.dataset.phemaPermissions='';document.head.append(css);}
 const route='/api/instances/'+id+'/permissions',data=await request(route),dialog=document.createElement('dialog');dialog.className='config-dialog phema-permissions';
 dialog.innerHTML=ui`<header class="config-dialog-header"><strong>Phema permissions</strong><button type="button" data-close aria-label="Close">×</button></header><div class="config-dialog-body"><p class="hint" data-trusted hidden>Plaza trusts this layout’s owner and agents, so it loads without approval.</p><div data-scopes></div><label><input type="checkbox" name="private">Allow private data and declared sharing</label><p class="hint" data-private-hint hidden>This layout's panels load only when this box is ticked.</p><label data-local><input type="checkbox" name="local">Allow declared local network media</label><p role="status"></p></div><footer class="config-dialog-footer"><button type="button" data-revoke>Revoke</button><button type="button" data-approve>Approve</button></footer>`;
 const scopes=dialog.querySelector('[data-scopes]');
 for(const node of data.permissions.nodes){const section=document.createElement('section'),name=document.createElement('strong');name.textContent=node.path||'Phema';section.append(name);const list=document.createElement('ul');
  for(const ref of node.references){const li=document.createElement('li'),rule=node.permissions?.references?.find(r=>r.id===ref.id);li.innerHTML=plainLine(node,ref,rule,data);list.append(li);}
  for(const flow of node.permissions?.dataFlows||[]){const li=document.createElement('li');li.textContent=flow.from+(flow.path||'/*')+' → '+flow.to;list.append(li);}
  for(const origin of [...node.permissions?.mediaOrigins||[],...node.permissions?.localOrigins||[]]){const li=document.createElement('li');li.textContent=origin;list.append(li);}
  for(const req of node.permissions?.requirements||[]){const li=document.createElement('li');li.textContent=req.id+' · '+req.effect+' · '+req.fields.join(', ');list.append(li);}
 section.append(list);scopes.append(section);
 }
 // The exact links and bindings the Terminal checks stay one click away.
 const details=document.createElement('details'),heading=document.createElement('summary'),raw=document.createElement('pre');heading.innerHTML=label('Details');
 raw.textContent=JSON.stringify({references:data.permissions.nodes.map(n=>({path:n.path,references:n.references})),...(data.runtimeBindings?{runtimeBindings:data.runtimeBindings}:{})},null,2);details.append(heading,raw);scopes.append(details);
 // Panels classified private load only with private data allowed; a first approval starts ticked, a saved one keeps its choice.
 const needsPrivate=data.permissions.nodes.some(n=>[...(n.permissions?.references||[]),...(n.permissions?.requirements||[])].some(r=>r.classification!=='public'));
 dialog.querySelector('[name=private]').checked=data.approval?!!data.approval.privateData:needsPrivate;dialog.querySelector('[data-private-hint]').hidden=!needsPrivate;dialog.querySelector('[name=local]').checked=!!data.approval?.localNetwork;dialog.querySelector('[data-local]').hidden=!data.permissions.nodes.some(n=>n.permissions?.localOrigins?.length);
 // A layout Plaza trusts loads without a saved approval: nothing here to approve or revoke.
 if(data.approval?.basis==='current-plaza-agent-trust'){dialog.querySelector('[data-trusted]').hidden=false;dialog.querySelector('[data-private-hint]').hidden=true;
  dialog.querySelectorAll('[data-revoke],[data-approve]').forEach(b=>{b.hidden=true;});dialog.querySelectorAll('input[type=checkbox]').forEach(c=>{c.disabled=true;});}
 dialog.querySelectorAll('[data-close]').forEach(b=>b.onclick=()=>dialog.close());
 // One decision at a time: until the Terminal has saved it, both buttons and the boxes wait and the dialog says so.
 const status=dialog.querySelector('[role=status]'),controls=()=>dialog.querySelectorAll('[data-revoke],[data-approve],input[type=checkbox]');let busy=false;
 for(const [selector,body,change,working] of [['[data-revoke]',{revoke:true},'revoked','Revoking…'],['[data-approve]',{approve:true,fingerprint:data.fingerprint},'approved','Approving…']])dialog.querySelector(selector).onclick=async()=>{
  if(busy)return;busy=true;
  const choices={privateData:dialog.querySelector('[name=private]').checked,localNetwork:dialog.querySelector('[name=local]').checked};
  controls().forEach(c=>{c.disabled=true;});dialog.setAttribute('aria-busy','true');setUIText(status,working);
  try{await request(route,{method:'POST',body:{...body,...choices}});dialog.close();onChange(change);}
  catch(error){status.textContent=error.message;}
  finally{busy=false;dialog.removeAttribute('aria-busy');if(dialog.open)controls().forEach(c=>{c.disabled=false;});}
 };
 // Panel names read as in the Browser they belong to: its language and its layout's own translations.
 if(scope){dialog.dataset.uiLocale=scope.dataset.uiLocale||'';dialog.dataset.phemaUi=scope.dataset.phemaUi||'{}';}
 document.body.append(dialog);dialog.addEventListener('close',()=>dialog.remove(),{once:true});applyUI(dialog);dialog.showModal();
}
