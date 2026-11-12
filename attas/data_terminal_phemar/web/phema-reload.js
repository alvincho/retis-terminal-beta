/** Revalidate installed UI and saved layouts; reload only after state is safe. */
export function watchPhemaChanges(request,{blocked=()=>false,beforeReload=async()=>true,
 reload=()=>location.reload(),refreshLayouts=async()=>{},intervalMs=5000,hiddenIntervalMs=15000,timeoutMs=10000}={}){
 let closed=false,reading=false,revision=null,timer,controller,reloading=false;
 async function check(){
  if(closed||reading||reloading)return;
  clearTimeout(timer);reading=true;controller=new AbortController();
  const timeout=setTimeout(()=>controller.abort(),timeoutMs);
  try{
   const current=await request('/api/ui-version',{signal:controller.signal});
   if(closed)return;
   if(typeof current.revision!=='string'||! /^[a-f0-9]{64}$/.test(current.revision))throw new Error('Invalid UI revision.');
   if(revision===null)revision=current.revision;
   if(!blocked()){
    if(current.revision!==revision){
     if(await beforeReload()&&!closed&&!blocked()){reloading=true;reload();}
    }else await refreshLayouts();
   }
  }catch{
   // A failed read retains the running view and retries on the next check.
  }finally{
   clearTimeout(timeout);controller=null;reading=false;
   if(!closed&&!reloading)timer=setTimeout(check,globalThis.document?.hidden?hiddenIntervalMs:intervalMs);
  }
 }
 function visible(){if(!globalThis.document?.hidden)check();}
 globalThis.document?.addEventListener?.('visibilitychange',visible);
 check();
 return {check,close(){closed=true;clearTimeout(timer);controller?.abort();globalThis.document?.removeEventListener?.('visibilitychange',visible);}};
}

// A one-use guest draft carries temporary UI state over a code reload. Account
// workspaces continue to use their server state as the source of truth.
const draftKey='phema-reload-draft.v1';
export function saveReloadDraft(storage,document,now=Date.now()){
 storage.setItem(draftKey,JSON.stringify({expires:now+60000,document}));
}
export function takeReloadDraft(storage,now=Date.now()){
 const raw=storage.getItem(draftKey);storage.removeItem(draftKey);
 if(!raw)return null;
 try{const draft=JSON.parse(raw);return typeof draft.expires==='number'&&draft.expires>=now&&draft.expires<=now+60000?normalizeDocument(draft.document):null;}catch{return null;}
}
import {normalizeDocument} from './workspace-storage.js';
