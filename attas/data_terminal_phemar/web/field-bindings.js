/** Transport choices belong to a Browser workspace, never to a Consultant layout. */
const record=v=>v&&typeof v==='object'&&!Array.isArray(v);
const path=v=>typeof v==='string'&&v.length<=160&&/^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*){0,8}$/.test(v)&&!v.split('.').some(p=>['__proto__','prototype','constructor'].includes(p));
export function normalizeFieldBindings(value,requirements){
 const out={};
 for(const [id,config] of Object.entries(record(value)?value:{}).slice(0,128)){
  if(!/^[\w-]{1,100}$/.test(id)||!record(config)||!Array.isArray(config.joinKeys))continue;
  const keys=config.joinKeys;if(keys.length>4||keys.some(k=>!path(k))||new Set(keys).size!==keys.length)continue;
  const req=requirements?.find(r=>r.id===id);if(requirements&&(!req||req.mode==='interactive'))continue;
  out[id]={joinKeys:[...keys],fields:{}};
  for(const [key,b] of Object.entries(record(config.fields)?config.fields:{}).slice(0,100))if((!req||req.fields.includes(key)||keys.includes(key))&&path(key)&&record(b)&&typeof b.source==='string'&&/^[\w.:-]{0,120}$/.test(b.source)&&path(b.field))out[id].fields[key]={source:b.source,field:b.field};
 }
 return out;
}
