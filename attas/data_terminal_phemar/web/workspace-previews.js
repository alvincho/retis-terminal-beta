import {toJpeg} from './preview-raster.js';
const id=(workspace,browser)=>`${workspace}:${browser}`;
const safeNode=node=>{
 if(!(node instanceof Element))return true;
 if(node.matches('input,textarea,select,form,iframe,video,audio,script,[contenteditable],[data-preview-exclude],.browser-command-bar'))return false;
 if(node instanceof HTMLImageElement){try{return new URL(node.currentSrc||node.src,location.href).origin===location.origin||node.src.startsWith('data:image/');}catch{return false;}}
 return true;
};
// A panel still showing "Refresh content" or waiting on another is blank in a capture; only a form waiting for
// the viewer's input counts as finished (Market Watch was kept with three blank panels).
const settled=r=>!r.error&&(['ready','stale'].includes(r.status)||r.status==='pending'&&r.reason==='interaction-required');
/** Only a finished, error-free instance is worth keeping as the last known good one. */
export function goodInstance(b,s,node){
 if(s.error||['unavailable','blocked','unauthorized','closed','loading'].includes(s.status)||s.content?.error)return false;
 // A sandboxed agent view draws inside its own frame, which a page capture cannot see.
 if(node.matches('[aria-busy="true"]')||node.querySelector('[aria-busy="true"],iframe[sandbox]'))return false;
 const content=Object.values(s.content?.results||{});
 if(content.length)return content.every(settled)&&content.some(r=>['ready','stale'].includes(r.status));
 return Object.values(s.results||{}).some(r=>r.rows?.length)||!b.design?.dataRequirements?.length&&!b.customLayout?.composition?.references?.length;
}

/** A bounded JPEG of one browser, without form fields, frames or other origins' images; null when too large. */
export async function screenshot(node){
 const width=node.clientWidth,height=node.clientHeight,scale=Math.min(1,960/width,600/height);
 const options={width,height,canvasWidth:Math.round(width*scale),canvasHeight:Math.round(height*scale),pixelRatio:1,skipFonts:true,filter:safeNode,backgroundColor:getComputedStyle(node).backgroundColor,style:{position:'relative',left:'0',top:'0',transform:'none',margin:'0',boxShadow:'none',contentVisibility:'visible',contain:'none',inset:'0'}};
 let image=await toJpeg(node,{...options,quality:.82});
 if(image.length>240_000)image=await toJpeg(node,{...options,quality:.5});
 return image.length>240_000?null:image;
}

/** A disposable in-memory image cache; the user's backend owns saved previews. */
export class WorkspacePreviews{
 constructor({api,user,workspace,browsers,state,saved,changed}){
  Object.assign(this,{api,user,workspace,browsers,state,saved,changed});
  this.records=new Map();this.last=new Map();this.queue=Promise.resolve();this.timer=null;this.dialog=null;
 }
 async load(){if(!this.user())return;try{const result=await this.api('/api/workspace-previews',{signal:AbortSignal.timeout(8000)});this.records=new Map(result.previews.map(p=>[id(p.workspaceId,p.previewId),p]));this.changed();}catch{/* Live workspaces remain usable if previews are offline. */}}
 schedule(){if(this.timer)return;this.timer=setTimeout(()=>{this.timer=null;this.capture().catch(()=>{});},1500);}
 capture(force=false){
  const w=this.workspace();if(!w||!this.user())return Promise.resolve();
  const candidates=this.browsers().map(b=>({b,node:document.querySelector(`[data-browser="${CSS.escape(b.id)}"]`)}));
  const job=this.queue.catch(()=>{}).then(async()=>{
   if(w.id!==this.workspace()?.id||!this.user())return;
   // A window kept hidden behind a focused one would capture as a blank picture.
   const ready=candidates.filter(({b,node})=>node?.isConnected&&node.clientWidth&&node.clientHeight&&!node.classList.contains('focus-hidden')&&goodInstance(b,this.state(b),node)&&
    (force||Date.now()-(this.last.get(id(w.id,b.previewId))||0)>=30_000));
   if(!ready.length)return;
   if(!await this.saved())return;
   for(const {b,node} of ready){
    if(!node.isConnected||w.id!==this.workspace()?.id)break;
    const key=id(w.id,b.previewId),capturedAt=Date.now();this.last.set(key,capturedAt);
    try{
     const image=await screenshot(node);
     if(!image)continue;
     const record=await this.api(`/api/workspace-previews/${encodeURIComponent(w.id)}/${encodeURIComponent(b.previewId)}`,{method:'PUT',body:{image,capturedAt},signal:AbortSignal.timeout(8000)});
     this.records.set(key,record);this.changed(record);
    }catch{/* A failed capture keeps the last successful screenshot. */}
   }
  });this.queue=job;return job;
 }
}
