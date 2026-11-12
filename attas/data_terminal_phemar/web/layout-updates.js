import {normalizeLayoutEntry,bindLayoutEntry} from './layout-library.js';
import {normalizeFieldBindings} from './field-bindings.js';
import {normalizeRefreshOverrides} from './block-refresh.js';
import {normalizeInstrumentOverrides} from './browser-instruments.js';

// Runtime binding/security projections may differ from a publication. Compare
// successive verified source contents so unchanged checks do not remount it.
const checkedSources=new WeakMap();
const canonical=value=>JSON.stringify(value,(_key,item)=>item&&typeof item==='object'&&!Array.isArray(item)
 ?Object.fromEntries(Object.keys(item).sort().map(key=>[key,item[key]])):item);

export function layoutReference(browser){
 if(browser.customLayout?.extensions?.['attas:browser-edit'])return null;
 const id=browser.customLayout?.id;
 if(!id||browser.unsupportedLayout)return null;
 return {layoutId:id,...(browser.consultantId?{agentId:browser.consultantId}:{}),...(browser.layoutPublication?.publicationId?{publicationId:browser.layoutPublication.publicationId}:{})};
}
export function applyLayoutUpdate(browser,update){
 if(update.status!=='available'||browser.sourceMode==='snapshot')return false;
 if(browser.consultantId&&update.agentId!==browser.consultantId)return false;
 if(browser.layoutPublication?.publicationId&&update.publicationId!==browser.layoutPublication.publicationId)return false;
 if(browser.layoutPublication?.publicationId&&browser.layoutPublication.revision>update.revision)return false;
 const entry=normalizeLayoutEntry(update.entry);if(!entry||entry.id!==browser.customLayout?.id)return false;
 const source=canonical(entry);
 if(checkedSources.get(browser)===source)return false;
 const bound=bindLayoutEntry(entry,browser.command),requirements=bound.dbl.dataRequirements;
 const previous=new Map(browser.design.dataRequirements.map(r=>[r.id,r])),current=new Map(requirements.map(r=>[r.id,r]));
 const conflicts=[];
 for(const [id,binding] of Object.entries(browser.fieldBindings||{})){
  if(id.startsWith('nested-')){if(JSON.stringify(browser.customLayout.composition?.components)!==JSON.stringify(entry.composition?.components))conflicts.push(id);continue;}
  const old=previous.get(id),next=current.get(id);
  if(!next||old?.schema!==next.schema||Object.keys(binding.fields||{}).some(field=>old?.fields.includes(field)&&!next.fields.includes(field)&&!(binding.joinKeys||[]).includes(field)))conflicts.push(id);
 }
 for(const id of Object.keys(browser.instrumentOverrides||{}))if(!id.startsWith('nested-')&&!current.get(id)?.editableInBrowser)conflicts.push(id);
 if(conflicts.length){browser.layoutConflict={revision:update.revision,requirements:[...new Set(conflicts)],entry,agentId:update.agentId,publicationId:update.publicationId};return false;}
 delete browser.layoutConflict;
 const ids=new Set(requirements.map(r=>r.id));
 const changed=canonical(browser.design)!==canonical(bound.dbl)||canonical(browser.customLayout)!==source;
 browser.customLayout=entry;browser.design=bound.dbl;browser.layoutId=bound.layoutId;
 browser.ticker=bound.ticker;browser.tickers=bound.tickers;
 browser.consultantId=update.agentId;
 browser.layoutPublication={publicationId:update.publicationId,revision:update.revision};
 browser.fieldBindings=normalizeFieldBindings(browser.fieldBindings,requirements);
 browser.refreshOverrides=Object.fromEntries(Object.entries(normalizeRefreshOverrides(browser.refreshOverrides)).filter(([id])=>ids.has(id)));
 browser.instrumentOverrides=Object.fromEntries(Object.entries(normalizeInstrumentOverrides(browser.instrumentOverrides)).filter(([id])=>requirements.some(r=>r.id===id&&r.editableInBrowser)));
 checkedSources.set(browser,source);
 return changed;
}
export async function refreshSavedLayouts(browsers,request){
 const targets=browsers.filter(b=>layoutReference(b));if(!targets.length)return {updated:0,unavailable:0};
 const references=targets.map(layoutReference);
 try{
  const response=await request('/api/layout-updates',{method:'POST',body:{layouts:references}});
  let updated=0,unavailable=0;
  for(const [index,b] of targets.entries()){
   if(b.editMode||JSON.stringify(layoutReference(b))!==JSON.stringify(references[index]))continue;
   const result=response.results?.find(r=>r.index===index);
   if(!result||result.status!=='available'){unavailable++;continue;}
   try{if(applyLayoutUpdate(b,result))updated++;}catch{unavailable++;}
  }
  return {updated,unavailable};
 }catch{return {updated:0,unavailable:targets.length};}
}
