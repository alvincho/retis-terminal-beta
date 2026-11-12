import {label,attribute,applyUI} from './i18n.js';
import {displayDateTime} from './time-display.js';
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

export function matchingPreview(records,proposal){
 const matches=records.filter(p=>p.layout?.id===proposal.entry.id&&
  (p.layout.consultantId===proposal.agentId||!p.layout.consultantId&&proposal.entry.id.startsWith('builtin-')&&proposal.agentId==='research-studio')&&
  (p.layout.publicationId||'')===(proposal.publicationId||''));
 return matches.sort((a,b)=>Number(b.layout.revision===proposal.revision)-Number(a.layout.revision===proposal.revision)||b.capturedAt-a.capturedAt)[0]||null;
}

/** The newest capture of an instance of this Phema, opened from the owner's chooser. */
export function phemaPreview(records,phemaId){
 if(typeof phemaId!=='string'||!phemaId)return null;
 return records.filter(p=>p.layout?.id===phemaId&&!p.layout.publicationId).sort((a,b)=>b.capturedAt-a.capturedAt)[0]||null;
}

/** Last-known screenshots saved by the user's own backend; never live data. */
export class LayoutPreviews{
 constructor(api){this.api=api;this.records=[];this.images=new Map();this.pending=new Map();this.watchers=new Set();}
 changed(){for(const watcher of this.watchers)watcher();}
 watch(watcher){this.watchers.add(watcher);return()=>this.watchers.delete(watcher);}
 async load(){try{this.records=(await this.api('/api/layout-previews',{signal:AbortSignal.timeout(8000)})).previews;this.changed();}catch{/* The diagram stays available without a saved screenshot. */}}
 remember(record){if(!record.layoutPreviewKey)return;this.records=this.records.filter(p=>p.layoutPreviewKey!==record.layoutPreviewKey);this.records.push(record);this.images.delete(record.layoutPreviewKey);this.changed();}
 forPhema(phemaId){return phemaPreview(this.records,phemaId);}
 full(key){
  if(this.images.has(key))return Promise.resolve(this.images.get(key));
  if(!this.pending.has(key))this.pending.set(key,this.api('/api/layout-previews/'+key,{signal:AbortSignal.timeout(8000)}).then(p=>{this.images.set(key,p.image);return p.image;}).finally(()=>this.pending.delete(key)));
  return this.pending.get(key);
 }
 note(p){return displayDateTime(new Date(p.capturedAt).toISOString());}
 mount(root=document){for(const image of root.querySelectorAll('[data-layout-preview-image]')){const key=image.dataset.layoutPreviewImage;this.full(key).then(src=>{if(image.isConnected)image.src=src;}).catch(()=>{});}}
 async open(key){const p=this.records.find(p=>p.layoutPreviewKey===key);if(!p)return;
  const dialog=document.createElement('dialog');dialog.className='layout-preview-dialog';
  dialog.innerHTML=`<header><strong>${esc(p.title)} · ${label('Last known preview')}</strong><button ${attribute('aria-label','Close dialog')}>×</button></header><img data-layout-preview-image="${esc(key)}" src="${esc(this.images.get(key)||p.thumbnail)}" alt="${esc(p.title)}"/><p>${esc(this.note(p))} · ${label('Saved image, not live data')}</p>`;
  document.body.append(dialog);applyUI(dialog);dialog.showModal();dialog.querySelector('button').onclick=()=>dialog.close();dialog.onclose=()=>dialog.remove();this.mount(dialog);
 }
}
