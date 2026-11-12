/* Isolated adoption candidate. Generic mounting only; never imported by Terminal. */
const PROTOCOL='phemacast:agent-view-message/1';
const MAX=300000;
const INSTANCE_CALLS={'parameters':'parameters','set-parameters':'setParameters','save-snapshot':'saveSnapshot','snapshots':'snapshots','read-snapshot':'readSnapshot',
 'schedule':'schedule','runs':'runs','run-now':'runNow','stop-run':'stopRun','kept-read':'keptRead','kept-write':'keptWrite','kept-drop':'keptDrop'};
// Some embedded browsers never load an opaque-origin sandboxed frame; say so instead of leaving a blank view.
const START_FAILED='The view did not start. This browser may block sandboxed frames.';
export function viewPresentation(value){
 if(!value||Array.isArray(value)||typeof value!=='object'||Object.keys(value).length!==1||typeof value.title!=='string')throw Error('Provide a view title only');
 const title=value.title.trim();
 if(!title||title.length>200||/[\u0000-\u001f\u007f-\u009f\u202a-\u202e\u2066-\u2069]/.test(title))throw Error('Use a bounded plain text view title');
 return {title};
}
const escape=value=>String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const utf64=text=>btoa(String.fromCharCode(...new TextEncoder().encode(text)));
function encoded(text){const data=new TextEncoder().encode(text);let raw='';for(let n=0;n<data.length;n+=8192)raw+=String.fromCharCode(...data.subarray(n,n+8192));return btoa(raw);}
const decode="s=>new TextDecoder().decode(Uint8Array.from(atob(s),c=>c.charCodeAt(0)))";
export function presentationContext(value={}){
 const keys=['locale','timezone','theme','density','colors','format'];
 if(!value||typeof value!=='object'||Object.keys(value).some(key=>!keys.includes(key)))throw Error('View context may contain presentation fields only');
 const context={locale:'en',timezone:Intl.DateTimeFormat().resolvedOptions().timeZone,theme:'light',density:'compact',colors:{},format:{},...value};
 if(typeof context.locale!=='string'||context.locale.length>35||typeof context.timezone!=='string'||context.timezone.length>80||typeof context.theme!=='string'||!/^[-a-z0-9]{1,40}$/i.test(context.theme)||!['compact','comfortable'].includes(context.density))throw Error('Invalid presentation context');
 new Intl.DateTimeFormat(context.locale,{timeZone:context.timezone});
 if(!context.colors||Array.isArray(context.colors)||typeof context.colors!=='object'||Object.keys(context.colors).length>64||Object.entries(context.colors).some(([name,color])=>!/^[-a-z]{1,40}$/.test(name)||!/^#(?:[a-f0-9]{3}|[a-f0-9]{4}|[a-f0-9]{6}|[a-f0-9]{8})$/i.test(color)))throw Error('Use bounded color tokens');
 // The viewer's display format (phemacast/web/value-format.js): a few short settings, never data.
 if(!context.format||Array.isArray(context.format)||typeof context.format!=='object'||Object.keys(context.format).length>8||Object.entries(context.format).some(([name,setting])=>!/^[A-Za-z]{1,20}$/.test(name)||!['string','number','boolean'].includes(typeof setting)||String(setting).length>20))throw Error('Invalid display format');
 return structuredClone(context);
}
export async function sha256(value){return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(value)))).map(b=>b.toString(16).padStart(2,'0')).join('');}
const sdk=`(()=>{
 const decode=${decode};let port=null,sequence=0,context=null;const pending=new Map();
 function apply(value){context={...context,...value};document.documentElement.lang=context.locale;
  document.documentElement.dataset.theme=context.theme;for(const [name,value] of Object.entries(context.colors||{}))if(/^[a-z-]+$/.test(name)&&/^#[a-f0-9]{3,8}$/i.test(value))document.documentElement.style.setProperty('--'+name,value);}
 function send(type,value){if(!port)throw Error('View channel is not ready');const id='request-'+(++sequence);return new Promise((resolve,reject)=>{const timer=setTimeout(()=>{pending.delete(id);reject(Error('Operation timed out; inspect status before retrying'));},35000);pending.set(id,{resolve,reject,timer});port.postMessage({schema:'${PROTOCOL}',type,id,value});});}
 Object.defineProperty(globalThis,'phemacastAgentView',{value:Object.freeze({
  requestKey:()=>Array.from(crypto.getRandomValues(new Uint8Array(16))).map(x=>x.toString(16).padStart(2,'0')).join(''),
  invoke:value=>send('invoke',value),setMode:value=>send('set-mode',value),
  status:value=>send('status',value),setPresentation:value=>send('presentation',value),
  parameters:()=>send('parameters',{}),setParameters:value=>send('set-parameters',value),
  saveSnapshot:value=>send('save-snapshot',value),snapshots:()=>send('snapshots',{}),readSnapshot:id=>send('read-snapshot',{id}),
  schedule:value=>send('schedule',value),runs:()=>send('runs',{}),runNow:value=>send('run-now',value),stopRun:value=>send('stop-run',value),
  kept:Object.freeze({read:key=>send('kept-read',{key}),write:(key,value)=>send('kept-write',{key,value}),drop:key=>send('kept-drop',{key})}),
  get context(){return context;}}),writable:false});
 addEventListener('message',function connect(event){if(event.source!==parent||event.data?.schema!=='${PROTOCOL}'||event.data.type!=='connect'||event.ports.length!==1||port)return;
  port=event.ports[0];apply(event.data.context);
  port.onmessage=message=>{const data=message.data;if(data?.schema!=='${PROTOCOL}')return;if(data.type==='event'){dispatchEvent(new CustomEvent('phemacast:event',{detail:data.value}));return;}
   if(data.type==='context'){apply(data.context);document.dispatchEvent(new CustomEvent('phemacast:context',{detail:context}));return;}const wait=pending.get(data.id);if(!wait)return;pending.delete(data.id);clearTimeout(wait.timer);data.ok?wait.resolve(data.value):wait.reject(Error(data.error||'Operation failed'));};port.start();
  dispatchEvent(new CustomEvent('phemacast:ready',{detail:context}));
  (0,eval)(decode('__OWNER_SCRIPT__'));
 });
 parent.postMessage({schema:'${PROTOCOL}',type:'ready'},'*');
})();`;
function renderReview(value,root){
 if(value==null||typeof value!=='object'){const text=String(value??'—');root.textContent=/^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/i.test(text)?'Selected item':/^[a-f0-9]{64}$/i.test(text)?'Pinned revision':text.length>500?'Bounded content ('+text.length+' characters)':text;return;}
 const list=document.createElement('dl');for(const [name,item] of Object.entries(value).slice(0,80)){
  const key=document.createElement('dt');key.textContent=name;const detail=document.createElement('dd');renderReview(item,detail);list.append(key,detail);
 }root.append(list);
}
export class AgentView {
 constructor(root,{transport,context,confirm,compositionChannel=null,onMode=()=>{},onStatus=()=>{},onPresentation=()=>{},startTimeoutMs=20000}){this.root=root;this.transport=transport;this.compositionChannel=compositionChannel;this.context=presentationContext(context);this.confirm=confirm;this.onMode=onMode;this.onStatus=onStatus;this.onPresentation=onPresentation;this.startTimeoutMs=startTimeoutMs;this.epoch=0;this.frame=null;this.port=null;this.pending=new Set();}
 async mount(spec){
  const epoch=++this.epoch;
  for(const [key,asset] of Object.entries(spec.assets)){if(await sha256(asset.text)!==asset.digest)throw Error('Owner asset integrity mismatch');}
  if(epoch!==this.epoch){this.transport('close',{},spec.mountId).catch(()=>{});return;}
  // spec was verified server-side against an approved immutable manifest. No URL
  // or operation target from the frame is ever passed directly to fetch.
  const nonce=crypto.randomUUID().replaceAll('-','');
  let runtimeText=null;
  if(spec.runtime){
   if(spec.runtime.type!=='quickjs-wasm'||!/^[a-f0-9]{64}$/.test(spec.runtime.digest)||spec.runtime.path!=='/api/agent-view-runtime/'+spec.runtime.digest+'/frame.js')throw Error('Unsupported confined runtime');
   const response=await fetch(spec.runtime.path,{credentials:'omit',redirect:'error'});if(!response.ok)throw Error('Confined runtime unavailable');runtimeText=await response.text();
   if(await sha256(runtimeText)!==spec.runtime.digest)throw Error('Confined runtime integrity mismatch');
  }
  if(this.compositionChannel&&!runtimeText)throw Error('Composition channels require the verified confined runtime.');
  const bootstrap=runtimeText||sdk.replace('__OWNER_SCRIPT__',encoded(spec.assets.script.text));
  const csp="default-src 'none'; script-src 'nonce-"+nonce+"' "+(runtimeText?"'wasm-unsafe-eval'":"'unsafe-eval'")+"; style-src "+(runtimeText?"'unsafe-inline'":"'nonce-"+nonce+"'")+"; connect-src 'none'; img-src 'none'; media-src 'none'; font-src 'none'; frame-src 'none'; worker-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'";
  const frame=document.createElement('iframe');frame.title=spec.title||'Agent view';frame.setAttribute('sandbox','allow-scripts allow-forms');frame.setAttribute('referrerpolicy','no-referrer');frame.setAttribute('credentialless','');frame.style.cssText='display:block;border:0;width:100%;height:100%;min-height:0;background:transparent';
  if(runtimeText)frame.src=spec.runtime.path.replace(/\/frame\.js$/,'/frame');
  else frame.srcdoc='<meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="'+escape(csp)+'">'+(runtimeText?'<main></main>':'<style nonce="'+nonce+'">'+(spec.assets.style?.text||'').replace(/<\/style/gi,'<\\/style')+'</style>'+spec.html)+'<script nonce="'+nonce+'">'+bootstrap.replace(/<\/script/gi,'<\\/script')+'<\/script>';
  const previous=this.frame,previousMount=this.mountId;this.cleanup?.();this.mountId=spec.mountId;this.port?.close();this.port=null;this.frame=frame;
  if(previousMount)this.transport('close',{},previousMount).catch(()=>this.onStatus('Previous view close pending'));
  const connect=event=>{
   if(this.frame!==frame||epoch!==this.epoch||event.source!==frame.contentWindow||event.origin!=='null'||event.data?.schema!==PROTOCOL||event.data.type!=='ready')return;
   removeEventListener('message',connect);const channel=new MessageChannel();this.port=channel.port1;const pending=this.pending=new Set();
   this.port.onmessage=async event=>{
    const data=event.data;
    if(this.frame!==frame||epoch!==this.epoch||data?.schema!==PROTOCOL||!/^request-\d+$/.test(data.id||'')||!['invoke','set-mode','status','presentation',...Object.keys(INSTANCE_CALLS)].includes(data.type)||JSON.stringify(data).length>MAX)return;
    if(pending.size>=16||pending.has(data.id))return;
    pending.add(data.id);let response;
    try{
     let value=data.value;
     if(data.type==='presentation'){
      const presentation=viewPresentation(value);frame.title=presentation.title;this.onPresentation(presentation);
      response={schema:PROTOCOL,id:data.id,ok:true,value:presentation};
      if(this.frame===frame&&epoch===this.epoch)this.port.postMessage(response);
      return;
     }
     if(Object.hasOwn(INSTANCE_CALLS,data.type)){
      // Parameters and kept results belong to the mounted Phema instance in this Terminal.
      if(!this.compositionChannel)throw Error('This view has no mounted Phema instance.');
      const result=await this.compositionChannel[INSTANCE_CALLS[data.type]](value);
      response={schema:PROTOCOL,id:data.id,ok:true,value:result};
      if(this.frame===frame&&epoch===this.epoch)this.port.postMessage(response);
      return;
     }
     if(data.type==='invoke'&&Object.hasOwn(value||{},'compositionReference')){
      if(!this.compositionChannel)throw Error('This view has no mounted composition channel.');
      const result=await this.compositionChannel.invoke(value);
      response={schema:PROTOCOL,id:data.id,ok:true,value:result};
      if(this.frame===frame&&epoch===this.epoch)this.port.postMessage(response);
      return;
     }
     if(data.type==='invoke'){
      const proposal=await this.transport('propose',value,spec.mountId);
      if(proposal.requiresConfirmation){if(!await this.confirm(proposal))throw Error('Action declined');const id=await this.transport('confirm',value,spec.mountId);value={...value,confirmationId:id.confirmationId};}
     }
     if(data.type==='set-mode'){
      const proposal=await this.transport('mode-propose',value,spec.mountId);let confirmationId;
      if(proposal.requiresConfirmation){if(!await this.confirm(proposal))throw Error('Mode change declined');confirmationId=(await this.transport('mode-confirm',value,spec.mountId)).confirmationId;}
      value={mode:value,...(confirmationId?{confirmationId}:{})};
     }
     const result=await this.transport(data.type,value,spec.mountId);
     if(data.type==='set-mode'){this.onMode(data.value);this.context={...this.context,sourceMode:data.value};}
     response={schema:PROTOCOL,id:data.id,ok:true,value:result};
    }catch(error){response={schema:PROTOCOL,id:data.id,ok:false,error:error.message};}
    finally{pending.delete(data.id);}
    if(this.frame===frame&&epoch===this.epoch)this.port.postMessage(response);
   };this.port.start();frame.contentWindow.postMessage({schema:PROTOCOL,type:'connect',...(runtimeText?{spec}:{}),context:{...this.context,parameters:spec.parameters,sourceMode:spec.sourceMode,
    ...(this.compositionChannel?{operationTimeoutMs:this.compositionChannel.replyTimeoutMs}:{})}},'*',[channel.port2]);
  };
  addEventListener('message',connect);
  const started=setTimeout(()=>{if(this.frame===frame&&epoch===this.epoch&&!this.port)this.onStatus(START_FAILED);},this.startTimeoutMs);
  this.cleanup=()=>{clearTimeout(started);removeEventListener('message',connect);};
  frame.addEventListener('load',()=>{this.onStatus('Mounted');});
  this.root.replaceChildren(frame);previous?.remove();
 }
 pushEvent(value){if(this.port&&JSON.stringify(value).length<=MAX&&value?.schema==='phemacast:agent-event/1')this.port.postMessage({schema:PROTOCOL,type:'event',value});}
 // A new theme (or other presentation field) reaches the open view without remounting it.
 updateContext(value){const context=presentationContext(value);this.context={...this.context,...context};this.port?.postMessage({schema:PROTOCOL,type:'context',context});}
 dispose(){this.compositionChannel?.close();if(this.mountId)this.transport('close',{},this.mountId).catch(()=>{});this.epoch++;this.cleanup?.();this.port?.close();this.port=null;this.frame?.remove();this.frame=null;this.pending.clear();}
}
export function confirmationDialog(proposal){return new Promise(resolve=>{
 const dialog=document.createElement('dialog');const title=document.createElement('strong');title.textContent='Approve '+(proposal.label||proposal.operationId);
 const fields=document.createElement('section');renderReview(proposal.input,fields);
 const yes=document.createElement('button');yes.textContent='Approve';const no=document.createElement('button');no.textContent='Cancel';
 yes.onclick=()=>{dialog.close();dialog.remove();resolve(true);};no.onclick=()=>{dialog.close();dialog.remove();resolve(false);};dialog.oncancel=event=>{event.preventDefault();no.click();};dialog.append(title,fields,yes,no);document.body.append(dialog);dialog.showModal();
});}
