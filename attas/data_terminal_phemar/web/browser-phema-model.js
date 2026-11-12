import {toComposableEntry,setTemplatePosition} from './layout-composition.js';
import {normalizeLayoutEntry} from './layout-library.js';
import {DISPLAY_ROLES,initialMapping} from './display-mapping.js';

export function editableCopy(entry){
 const next=toComposableEntry(entry);if(!next)throw new Error('Invalid Phema.');
 if(!next.extensions?.['attas:browser-edit']){
  const origin=next.id;next.id='custom-browser-'+crypto.randomUUID();
  next.extensions={...next.extensions,'attas:browser-edit':{originLayoutId:origin}};
 }
 return next;
}
export function compatibleDisplays(entry,node){
 const fields=entry.design.dataRequirements.find(r=>r.id===node.requirementId)?.fields||[];
 return Object.entries(DISPLAY_ROLES).filter(([id,role])=>role.required.every(f=>fields.includes(f))).map(([id])=>id);
}
export function updateDisplay(entry,id,{title,template,position,tabId}){
 const next=toComposableEntry(entry),node=next.design.layout.children.find(n=>n.id===id);
 if(!node)throw new Error('Unknown component.');
 if(title!==undefined){if(!title.trim())throw new Error('Enter a component title.');node.title=title.trim().slice(0,80);}
 const requirement=next.design.dataRequirements.find(r=>r.id===node.requirementId);
 if(template&&template!==node.template){
  if(!compatibleDisplays(next,node).includes(template))throw new Error('This display needs fields unavailable in the block schema.');
  if(template!=='watchlist')delete node.supplementalRequirementIds;
  node.type='template';node.template=template;node.fieldMappings=initialMapping(template,requirement?.fields);delete node.phemarType;delete node.inputs;delete node.instancePolicy;
 }
 if(tabId!==undefined){if(tabId&&!next.design.layout.tabbedView?.tabs.some(t=>t.id===tabId))throw new Error('Unknown layout tab.');if(tabId)node.tabId=tabId;else delete node.tabId;}
 const moved=position?setTemplatePosition(next,id,position):next;
 if(position&&JSON.stringify(moved.design.layout.children.find(n=>n.id===id)?.position)!==JSON.stringify(position))throw new Error('Component overlaps another block or is outside the grid.');
 const normalized=normalizeLayoutEntry(moved);if(!normalized)throw new Error('Invalid component configuration.');return normalized;
}
export function deleteComponent(entry,id){
 const next=toComposableEntry(entry),removed=next.design.layout.children.find(n=>n.id===id);
 if(!removed)throw new Error('Unknown component.');
 next.design.layout.children=next.design.layout.children.filter(n=>n.id!==id);const used=next.design.layout.children;
 for(const rid of [removed.requirementId,...(removed.supplementalRequirementIds||[])].filter(Boolean)){
  if(!used.some(n=>n.requirementId===rid||n.supplementalRequirementIds?.includes(rid))&&!next.design.dataRequirements.some(r=>r.id!==rid&&r.dependsOn===rid))next.design.dataRequirements=next.design.dataRequirements.filter(r=>r.id!==rid);
 }
 if(next.composition){next.composition.references=(next.composition.references||[]).filter(r=>used.some(n=>n.referenceId===r.id));next.composition.components=(next.composition.components||[]).filter(c=>used.some(n=>n.componentId===c.id));}
 const normalized=normalizeLayoutEntry(next);if(!normalized)throw new Error('A Phema needs at least one component.');return normalized;
}
export function updateTabs(entry,position,titles){
 const next=toComposableEntry(entry),view=next?.design.layout.tabbedView;
 if(!view||!Array.isArray(titles)||titles.length!==view.tabs.length||titles.some(title=>typeof title!=='string'||!title.trim()||title.trim().length>40))throw new Error('Invalid component configuration.');
 view.tabs.forEach((tab,index)=>tab.title=titles[index].trim());
 const moved=setTemplatePosition(next,view.id,position);
 if(JSON.stringify(moved.design.layout.tabbedView.position)!==JSON.stringify(position))throw new Error('Component overlaps another block or is outside the grid.');
 const normalized=normalizeLayoutEntry(moved);if(!normalized)throw new Error('Invalid component configuration.');return normalized;
}
export function browserSettings(b){return Object.fromEntries(['fieldBindings','refreshOverrides','contentOverrides','instrumentOverrides','parameterValues','range','layoutTabs','uiLanguage'].map(k=>[k,b[k]??(k==='range'?'1M':k==='uiLanguage'?null:{})]));}
