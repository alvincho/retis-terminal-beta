import {normalizeDocument,STORAGE_KEY,readWorkspaceStore} from './workspace-storage.js';
import {mergeWorkspaceDocuments} from './workspace-merge.js';
/** Server state is canonical. Local state is read once as an explicit migration. */
export class WorkspaceClient{
 constructor(request,storage){this.request=request;this.storage=storage;this.revision=0;this.queue=Promise.resolve();this.available=false;this.last=null;this.base=null;this.local=null;this.conflicted=false;}
 async load(){let saved=await this.request('/api/workspace-state');const local=readWorkspaceStore(this.storage);if(local.data){saved=await this.request('/api/workspace-state/import',{method:'POST',body:{document:local.data}});this.storage.removeItem(STORAGE_KEY);}this.base=this.local=saved.document?normalizeDocument(saved.document):null;const canonical=this.base?JSON.stringify(this.base):null;this.revision=saved.revision;this.available=true;this.conflicted=false;this.last=canonical;return saved.document;}
 save(document){const normalized=normalizeDocument(document);const job=this.queue.catch(()=>{}).then(async()=>{
  if(this.conflicted)throw new Error('Review the saved workspace conflict before saving again.');
  if(!this.available)throw new Error('Workspace server is unavailable. Changes remain open in this session.');
  let merged=this.base&&this.local?mergeWorkspaceDocuments(this.local,normalized,this.base).document:normalized;
  if(!merged){this.conflicted=true;throw new Error('Overlapping workspace edits need review. Your saved version remains intact.');}
  if(JSON.stringify(merged)!==this.last){
   let saved;
   try{saved=await this.request('/api/workspace-state',{method:'PUT',body:{document:merged,revision:this.revision}});}
   catch(error){
    if(error.status!==409)throw error;
    const latest=await this.request('/api/workspace-state');
    if(latest.revision<=this.revision){this.conflicted=true;throw error;}
    const result=latest.document&&this.base?mergeWorkspaceDocuments(this.base,merged,latest.document):{document:null};
    if(!result.document){this.conflicted=true;throw error;}
    merged=result.document;
    try{saved=await this.request('/api/workspace-state',{method:'PUT',body:{document:merged,revision:latest.revision}});}
    catch(retryError){if(retryError.status===409)this.conflicted=true;throw retryError;}
   }
   this.revision=saved.revision;this.base=merged;this.last=JSON.stringify(merged);
  }
  this.local=normalized;
 });this.queue=job;return job;}
}
