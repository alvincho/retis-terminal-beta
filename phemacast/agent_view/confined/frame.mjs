/** Trusted generic frame. Owner JavaScript executes only inside the WASM VM. */
import {newQuickJSWASMModuleFromVariant,newVariant} from 'quickjs-emscripten-core';
import variant from '@jitl/quickjs-wasmfile-debug-sync';
import environment from 'phemacast-runtime:environment';
import wasmText from 'phemacast-runtime:wasm';
import {DrawingSurface,safeCSS} from './surface.mjs';
import {pickTextFile} from './text_file.mjs';
const PROTOCOL='phemacast:agent-view-message/1';
const wasm=Uint8Array.from(atob(wasmText),c=>c.charCodeAt(0)).buffer;
const defaultDateOptions=JSON.stringify(new Intl.DateTimeFormat().resolvedOptions());
const modulePromise=newQuickJSWASMModuleFromVariant(newVariant(variant,{wasmBinary:wasm,wasmModule:()=>WebAssembly.compile(wasm),wasmSourceMapData:{version:3,sources:[],names:[],mappings:''}}));
function boundedJSON(value){const text=JSON.stringify(value);if(!text||text.length>300000)throw Error('View value exceeds its limit');return text;}
export function pickImage(root){
 if(!navigator.userActivation?.isActive)throw Error('Select an image from an explicit user action.');
 return new Promise((resolve,reject)=>{
  const input=document.createElement('input');input.type='file';input.accept='image/png,image/jpeg,image/webp';input.hidden=true;root.append(input);
  const finish=(value,error)=>{input.remove();error?reject(error):resolve(value);};
  input.addEventListener('cancel',()=>finish(null),{once:true});
  input.onchange=async()=>{try{
   const file=input.files?.[0];if(!file){finish(null);return;}
   if(!['image/png','image/jpeg','image/webp'].includes(file.type)||file.size>2*1024*1024)throw Error('Choose a PNG, JPEG or WebP image up to 2 MB.');
   const image=await createImageBitmap(file);if(image.width*image.height>16000000){image.close();throw Error('Choose an image up to 16 megapixels.');}
   const scale=Math.min(1,128/image.width,128/image.height),canvas=document.createElement('canvas');canvas.width=Math.max(1,Math.round(image.width*scale));canvas.height=Math.max(1,Math.round(image.height*scale));
   canvas.getContext('2d').drawImage(image,0,0,canvas.width,canvas.height);image.close();
   const value=canvas.toDataURL('image/png').split(',')[1];if(value.length>90000)throw Error('This portrait exceeds the bounded image input. Select a simpler image.');
   finish({content_base64:value});
  }catch(error){finish(null,error);}};input.click();
 });
}
export async function createConfinedRuntime(root,{script,style='',html='',context,invoke,onError=()=>{}}){
 const appearance=value=>{document.documentElement.dataset.theme=value.theme||'light';
  for(const [key,color]of Object.entries(value.colors||{}))if(/^[-a-z]{1,40}$/.test(key)&&/^#[a-f0-9]{3,8}$/i.test(color))document.documentElement.style.setProperty('--'+key,color);};
 appearance(context);
 const module=await modulePromise,runtime=module.newRuntime();runtime.setMemoryLimit(64*1024*1024);
 const vm=runtime.newContext();let closed=false,deadline=Date.now()+15000;const pending=new Set(),timers=new Set();
 runtime.setInterruptHandler(()=>closed||Date.now()>deadline);
 const error=value=>{const v=vm.dump(value);return new Error(v.message||'Owner script failed in its isolated runtime');};
 const run=code=>{deadline=Date.now()+15000;const result=vm.evalCode(code,'owner-view.js');if(result.error){const e=error(result.error);result.error.dispose();throw e;}result.value.dispose();drain();};
 const drain=()=>{if(closed)return;deadline=Date.now()+15000;for(let i=0;i<100&&runtime.hasPendingJob();i++){const result=runtime.executePendingJobs(100);if(result.error){const e=error(result.error);result.error.dispose();onError(e);break;}}};
 const deliver=(fn,args)=>{if(closed)return;try{run(fn+'(...'+boundedJSON(args)+')');}catch(e){onError(e);}};
 const surface=new DrawingSurface(root,(id,event)=>deliver('__deliverEvent',[id,event]));
 root.addEventListener('submit',event=>event.preventDefault(),true);
 const bind=(name,fn)=>{const handle=vm.newFunction(name,(...values)=>{try{const args=values.map(value=>vm.getString(value));const result=fn(...args);return result;}catch(e){return {error:vm.newError(e.message)};}});vm.setProp(vm.global,name,handle);handle.dispose();};
 bind('__surface',(method,json)=>vm.newString(JSON.stringify(surface.call(method,JSON.parse(json)))));
 bind('__context',()=>vm.newString(boundedJSON(context)));
 bind('__random',()=>vm.newString(crypto.randomUUID()));
 bind('__timer',(id,ms)=>{if(timers.size>=100)throw Error('Timer limit exceeded');const timer=setTimeout(()=>{timers.delete(timer);deliver('__deliverTimer',[id]);},Math.max(4,Math.min(30000,Number(ms)||0)));timers.add(timer);return vm.undefined;});
 bind('__format',(type,json)=>{if(json.length>4000)throw Error('Format request exceeds its limit');const [requestedLocale,requestedOptions,value]=JSON.parse(json);const locale=requestedLocale??undefined,options=requestedOptions??undefined;let result;
  if(type==='number')result=new Intl.NumberFormat(locale,options).format(value);
  else if(type==='numberOptions')result=JSON.stringify(new Intl.NumberFormat(locale,options).resolvedOptions());
  else if(type==='date')result=new Intl.DateTimeFormat(locale,options).format(value);
  else if(type==='parts')result=JSON.stringify(new Intl.DateTimeFormat(locale,options).formatToParts(value));
  else if(type==='dateOptions')result=!locale&&!options?defaultDateOptions:JSON.stringify(new Intl.DateTimeFormat(locale,options).resolvedOptions());
  else if(type==='supported'&&locale==='timeZone')result=JSON.stringify(Intl.supportedValuesOf('timeZone'));
  else throw Error('Undeclared presentation formatter');return vm.newString(result);});
 bind('__operation',(type,json)=>{
  if(!['invoke','status','set-mode','pick-image','pick-text-file','presentation','parameters','set-parameters','save-snapshot','snapshots','read-snapshot','schedule','runs','run-now','stop-run','kept-read','kept-write','kept-drop'].includes(type)||json.length>300000||pending.size>=16)throw Error('Operation is unavailable');
  const promise=vm.newPromise();pending.add(promise);
  const action=type==='pick-image'?pickImage(root):type==='pick-text-file'?pickTextFile(root):Promise.resolve().then(()=>invoke(type,JSON.parse(json)));
  Promise.resolve(action).then(value=>{
   if(closed)return;deadline=Date.now()+15000;const result=vm.evalCode('JSON.parse('+JSON.stringify(boundedJSON(value))+')');if(result.error){promise.reject(result.error);result.error.dispose();}else{promise.resolve(result.value);result.value.dispose();}
  },reason=>{if(closed)return;deadline=Date.now()+15000;const e=vm.newError(String(reason.message||reason));promise.reject(e);e.dispose();}).catch(e=>onError(e)).finally(()=>{if(!closed){pending.delete(promise);drain();promise.dispose();}});
  return promise.handle;
 });
 const dispose=()=>{if(closed)return;closed=true;for(const timer of timers)clearTimeout(timer);timers.clear();surface.dispose();for(const promise of pending)promise.dispose();pending.clear();vm.dispose();runtime.dispose();};
 try{
  const css=document.createElement('style');css.textContent=safeCSS(style);root.before(css);
  surface.markup(root,html);run(environment);run(script);
  return {dispose(){dispose();css.remove();},
   // Presentation fields only; the view's parameters and source mode stay as mounted.
   updateContext(value){if(closed)return;context={...context,...value};appearance(context);deliver('__deliverContext',[value]);},
   evaluate(code){deadline=Date.now()+15000;const r=vm.evalCode(code);if(r.error){const e=error(r.error);r.error.dispose();throw e;}const data=vm.dump(r.value);r.value.dispose();drain();return data;}};
 }catch(e){try{dispose();}catch{}throw e;}
}

// This is the only browser-facing channel. It supplies no credentials or URLs.
if(typeof window!=='undefined'){
 let port=null,view=null,sequence=0,operationTimeoutMs=35000,update=null;const pending=new Map();
 const invoke=(type,value)=>new Promise((resolve,reject)=>{const id='request-'+(++sequence);const timer=setTimeout(()=>{pending.delete(id);reject(Error('Operation timed out; inspect status before retrying'));},operationTimeoutMs);pending.set(id,{resolve,reject,timer});port.postMessage({schema:PROTOCOL,type,id,value});});
 const receive=async event=>{
  if(event.source!==parent||event.data?.schema!==PROTOCOL||event.data.type!=='connect'||event.ports.length!==1||port)return;
  removeEventListener('message',receive);port=event.ports[0];
  port.onmessage=event=>{const data=event.data;
   // A context change can arrive while the view is still starting; it is applied once the view exists.
   if(data?.schema===PROTOCOL&&data.type==='context'){if(view)view.updateContext(data.context);else update={...update,...data.context};return;}
   const request=pending.get(data?.id);if(!request)return;pending.delete(data.id);clearTimeout(request.timer);data.ok?request.resolve(data.value):request.reject(Error(data.error));};port.start();
  const spec=event.data.spec,context=event.data.context;
  if(Number.isInteger(context.operationTimeoutMs)&&context.operationTimeoutMs>=1000&&context.operationTimeoutMs<=270000)operationTimeoutMs=context.operationTimeoutMs;
  try{view=await createConfinedRuntime(document.querySelector('main'),{script:spec.assets.script.text,style:spec.assets.style?.text||'',html:spec.html,context,invoke,onError:error=>{const status=document.querySelector('[role=status]');if(status)status.textContent=error.message;}});if(update)view.updateContext(update);}
  catch(error){document.querySelector('main').textContent=error.message;}
 };
 addEventListener('message',receive);parent.postMessage({schema:PROTOCOL,type:'ready'},'*');
 addEventListener('pagehide',()=>{view?.dispose();port?.close();for(const {timer}of pending.values())clearTimeout(timer);pending.clear();});
}
