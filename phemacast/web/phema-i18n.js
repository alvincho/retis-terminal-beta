/** Portable, data-only UI catalog. Never translate source content or identifiers. */
export const UI_EXTENSION='phemacast:ui';
const object=v=>v&&typeof v==='object'&&!Array.isArray(v);
const locales=/^[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$/;
const cache=new WeakMap();
export function validateUIBundle(bundle){
 if(!object(bundle)||bundle.version!==1||!locales.test(bundle.defaultLocale)||!object(bundle.messages)||Object.keys(bundle).some(k=>!['version','defaultLocale','messages'].includes(k)))throw new Error('Invalid Phema UI catalog');
 const entries=Object.entries(bundle.messages);if(entries.length>1000)throw new Error('Phema UI catalog exceeds limit');
 for(const [key,translations] of entries){
  if(!key||key.length>2000||!object(translations)||Object.keys(translations).length>20)throw new Error('Invalid Phema UI message');
  for(const [locale,text] of Object.entries(translations)){
   if(!locales.test(locale)||typeof text!=='string'||!text||text.length>4000||JSON.stringify((text.match(/\{\d+\}/g)||[]).sort())!==JSON.stringify((key.match(/\{\d+\}/g)||[]).sort()))throw new Error('Invalid Phema UI translation');
  }
 }
 return bundle;
}
export function uiBundle(entry){return entry?.extensions?.[UI_EXTENSION]||entry?.extensions?.['phemacast:layout']?.extensions?.[UI_EXTENSION]||null;}
export function resolveUIMessage(bundles,locale,key,fallback=()=>key){
 for(const bundle of bundles.filter(Boolean)){
  const values=bundle.messages?.[key];if(!values)continue;
  return values[locale]||values[locale.split('-')[0]]||((translated=>translated!==key?translated:values[bundle.defaultLocale]||key)(fallback(locale,key)));
 }
 return fallback(locale,key);
}
export function scopedUIBundles(node){
 const result=[];for(let current=node;current;current=current.parentElement){
  if(!current.hasAttribute?.('data-phema-ui'))continue;
  const raw=current.getAttribute('data-phema-ui');let saved=cache.get(current);
  if(saved?.raw!==raw){try{saved={raw,bundle:validateUIBundle(JSON.parse(raw))};}catch{saved={raw,bundle:null};}cache.set(current,saved);}
  if(saved.bundle)result.push(saved.bundle);
 }
 return result;
}
