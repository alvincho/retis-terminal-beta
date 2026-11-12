/** Opaque-origin extension frame. No cookies, storage, network or host API. */
const url=new URL(location.href),moduleURL=url.searchParams.get('module');
let renderer=null;
const escape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
globalThis.phemacastSandboxRegister=value=>{renderer=value;};
window.addEventListener('message',async event=>{
 if(event.source!==parent||event.data?.type!=='renderer-init'||!event.ports[0])return;
 const port=event.ports[0],root=document.querySelector('main');
 try{await import(moduleURL);if(!renderer||renderer.id!==event.data.capability.id||renderer.version!==event.data.capability.version)throw new Error('Renderer registration mismatch');
  const context={escape,locale:event.data.locale||'en',label:escape,translate:x=>x,
   // The only bridge is validated, non-executable component data saving.
   save:value=>port.postMessage({type:'save-data',value})};
  root.innerHTML=renderer.render(event.data.value,context);renderer.mount?.(root,event.data.value,context);
  port.postMessage({type:'ready'});
 }catch{root.textContent='Renderer unavailable';port.postMessage({type:'error'});}
},{once:true});
parent.postMessage({type:'renderer-ready'},'*');
