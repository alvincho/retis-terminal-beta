/** Preserve bounded, opaque named inputs; publishers validate their meaning. */
export function normalizeNamedLists(value){
 if(!value||typeof value!=='object'||Array.isArray(value))return {};
 const result={};
 for(const [key,items] of Object.entries(value).slice(0,128)){
  if(['__proto__','prototype','constructor'].includes(key)||key.length>200||!Array.isArray(items)||items.length>1024||items.some(item=>typeof item!=='string'||item.length>1000))continue;
  result[key]=[...items];
 }
 return result;
}
