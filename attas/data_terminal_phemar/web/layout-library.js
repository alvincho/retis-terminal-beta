/** Structural projection of publisher-provided content; no application presets. */
const object=v=>v!==null&&typeof v==='object'&&!Array.isArray(v);
const copy=v=>structuredClone(v);
export function validateLayoutEntry(entry){
 const errors=[];
 if(!object(entry)||typeof entry.id!=='string'||!entry.id||entry.id.length>128)errors.push('A provided content identity is required.');
 if(typeof entry?.name!=='string'||!entry.name||entry.name.length>150)errors.push('A provided content name is required.');
 const design=entry?.design,grid=design?.layout;
 if(!object(design)||!object(design.params)||!object(grid)||grid.type!=='grid'||!Array.isArray(grid.children)||grid.children.length>64)errors.push('A bounded provided layout is required.');
 if(!Array.isArray(design?.dataRequirements)||design.dataRequirements.length>64)errors.push('Provided data requirements must be bounded.');
 const ids=new Set();
 for(const node of grid?.children||[]){
  if(!object(node)||typeof node.id!=='string'||ids.has(node.id))errors.push('Provided surface identities must be unique.');
  ids.add(node?.id);
  if(node?.position&&!['column','row','columnSpan','rowSpan'].every(k=>Number.isFinite(node.position[k])&&node.position[k]>0))errors.push('Provided surface geometry is invalid.');
 }
 return {valid:!errors.length,errors};
}
export function normalizeLayoutEntry(entry){
 if(!validateLayoutEntry(entry).valid)return null;
 return copy(entry);
}
/** A publisher's layout kind becomes a CSS class in the page; anything but a plain kind name shows as content. */
export function layoutKind(id){
 return typeof id==='string'&&/^[a-z][a-z0-9-]{0,39}$/.test(id)?id:'content';
}
export function bindLayoutEntry(entry){
 const clean=normalizeLayoutEntry(entry);
 if(!clean)throw new TypeError('Cannot display invalid publisher-provided content.');
 return {layoutId:layoutKind(clean.baseLayoutId),ticker:'',tickers:[],rationale:clean.description||'',requirements:clean.design.dataRequirements,dbl:clean.design};
}
export function filterLayouts(entries,query=''){
 const words=String(query).toLocaleLowerCase().trim().split(/\s+/).filter(Boolean);
 return (Array.isArray(entries)?entries:[]).filter(entry=>words.every(word=>[entry.name,entry.description].join(' ').toLocaleLowerCase().includes(word)));
}
