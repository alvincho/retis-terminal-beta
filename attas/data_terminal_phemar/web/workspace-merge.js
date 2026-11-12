import {normalizeDocument} from './workspace-storage.js';

const copy=value=>value===undefined?undefined:structuredClone(value);
const record=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
const stable=value=>Array.isArray(value)?value.map(stable):record(value)?Object.fromEntries(Object.keys(value).sort().map(k=>[k,stable(value[k])])):value;
const same=(a,b)=>JSON.stringify(stable(a))===JSON.stringify(stable(b));
const browserKey=b=>b.previewId||b.terminalDocumentId;

/** Merge independent edits; an edit versus deletion or overlapping edit needs review. */
export function mergeWorkspaceDocuments(base,local,remote){
 const conflicts=[];
 function merge(a,l,r,path){
  if(same(l,r))return copy(l);
  if(same(l,a))return copy(r);
  if(same(r,a))return copy(l);
  if(path.endsWith('/updatedAt')&&[a,l,r].every(v=>v===undefined||typeof v==='number'))return Math.max(a||0,l||0,r||0);
  // Selection belongs to the caller's current view; it is not a content conflict.
  if(['/activeWorkspaceId','/activeIndex','/focus'].some(k=>path.endsWith(k)))return copy(l);
  if(Array.isArray(l)&&Array.isArray(r)&&Array.isArray(a)&&(/\/workspaces$/.test(path)||/\/browsers$/.test(path))){
   const key=path.endsWith('/workspaces')?w=>w.id:browserKey;
   const keyed=list=>list.every(v=>key(v))&&new Set(list.map(key)).size===list.length;
   if(![a,l,r].every(keyed)){conflicts.push(path);return;}
   const maps=[a,l,r].map(list=>new Map(list.map(v=>[key(v),v])));
   const ids=[...new Set([...l.map(key),...r.map(key),...a.map(key)])];
   return ids.map(id=>merge(...maps.map(m=>m.get(id)),path+'/'+id)).filter(v=>v!==undefined);
  }
  if(record(l)&&record(r)&&(record(a)||a===undefined)){
   const result={};
   for(const key of new Set([...Object.keys(a||{}),...Object.keys(l),...Object.keys(r)])){
    const value=merge(a?.[key],l[key],r[key],path+'/'+key);
    if(value!==undefined)result[key]=value;
   }
   if(path.includes('/workspaces/')&&Array.isArray(result.browsers)){
    const selected=browserKey(l.browsers?.[l.activeIndex]||{});
    if(selected)result.activeIndex=result.browsers.findIndex(b=>browserKey(b)===selected);
   }
   return result;
  }
  conflicts.push(path);
 }
 const document=merge(base?normalizeDocument(base):base,normalizeDocument(local),normalizeDocument(remote),'');
 if(conflicts.length)return {document:null,conflicts};
 if(document?.workspaces?.length>32||document?.workspaces?.some(w=>w.browsers.length>12))conflicts.push('/capacity');
 return {document:conflicts.length?null:normalizeDocument(document),conflicts};
}
