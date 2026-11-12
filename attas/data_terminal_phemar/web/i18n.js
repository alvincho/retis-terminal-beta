import {PHEMA_MESSAGES} from './phema-ui-messages.js';
/** UI-only localization. Only explicitly authored message keys are translated.
 * Data values, API errors, report text and layout titles are never scanned.
 */
import {MESSAGES} from './ui-messages.js';
import {resolveUIMessage,scopedUIBundles} from './phema-i18n.js';
export const LANGUAGES=Object.freeze({en:'English','zh-Hant':'繁體中文','zh-Hans':'简体中文',ja:'日本語',es:'Español',fr:'Français'});
const escape=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const normalize=v=>String(v).replace(/&amp;/g,'&').replace(/&quot;/g,'"').replace(/&#39;|&apos;/g,"'").replace(/&lt;/g,'<').replace(/&gt;/g,'>').replace(/\s+/g,' ').trim();
let locale='en',storageKey='phemacast.ui-language.v1',savePreference=null,pendingSave=Promise.resolve();
export function validLocale(value){return Object.hasOwn(LANGUAGES,value);}
export function currentLocale(){return locale;}
export function translate(language,key,...args){const message=MESSAGES[key]?.[language]||PHEMA_MESSAGES[key]?.[language]||key;return message.replace(/\{(\d+)\}/g,(m,i)=>nested(args[i])?translate(language,args[i].uiMessage,...args[i].args):args[i]??m);}
export function message(key,...args){return {uiMessage:key,args};}
const nested=value=>value&&typeof value==='object'&&typeof value.uiMessage==='string'&&Array.isArray(value.args);
const prepared=value=>nested(value)?message(value.uiMessage,...value.args.map(prepared)):escape(value);
export function t(key,...args){return translate(locale,key,...args);}
export function resolvedLocale(node){for(let n=node;n;n=n.parentElement)if(validLocale(n.dataset?.uiLocale))return n.dataset.uiLocale;return locale;}
function plainMessage(key,args,language,bundles){return resolveUIMessage(bundles,language,key,translate).replace(/\{(\d+)\}/g,(m,i)=>nested(args[i])?plainMessage(args[i].uiMessage,args[i].args,language,bundles):args[i]??m);}
export function scopedTranslate(node,key,...args){
 return plainMessage(key,args,resolvedLocale(node),scopedUIBundles(node));
}
const known=key=>Object.hasOwn(MESSAGES,key)||Object.hasOwn(PHEMA_MESSAGES,key);
function literal(text,values){
 let args=[];
 const key=normalize(text).replace(/\uE000(\d+)\uE001/g,(_,i)=>'{'+(args.push(values[Number(i)])-1)+'}');
 if(!known(key))return null;
 // Never reconstruct nested UI controls or content as translated text.
 if(args.some(v=>/[<>]/.test(String(v))))return null;
 return {key,args};
}
function renderMessage(key,args,language=locale,bundles=[]){return resolveUIMessage(bundles,language,key,translate).replace(/\{(\d+)\}/g,(_,i)=>'\uE002'+i+'\uE003').split(/(\uE002\d+\uE003)/).map(part=>{
 const slot=part.match(/^\uE002(\d+)\uE003$/);const value=slot?args[Number(slot[1])]:null;return slot?(nested(value)?renderMessage(value.uiMessage,value.args,language,bundles):String(value??'')):escape(part);
 }).join('');}

/** Tagged template: translates literal UI text, never interpolated content. */
export function ui(strings,...values){
 let markup=typeof strings==='string'?strings:strings.map((s,i)=>s+(i<values.length?'\uE000'+i+'\uE001':'')).join('');
 // Native options/title cannot contain span elements.
 markup=markup.replace(/<(option|title|text|tspan)(\s[^>]*|)>([^<>]*)<\/(?:option|title|text|tspan)>/gi,(whole,tag,attrs,text)=>{
  if(attrs.includes('data-ui-key='))return whole;
  const m=literal(text,values);if(!m)return whole;
  return `<${tag}${attrs} data-ui-key="${escape(m.key)}" data-ui-args="${escape(JSON.stringify(m.args))}">${renderMessage(m.key,m.args)}</${tag}>`;
 });
 markup=markup.replace(/\b(title|aria-label|placeholder)="([^"]*)"/g,(whole,attr,text,index,full)=>{
  const tagEnd=full.indexOf('>',index);if(full.slice(index,tagEnd).includes('data-ui-attr-'+attr+'='))return whole;
  const m=literal(text,values);if(!m)return whole;
  return `${attr}="${renderMessage(m.key,m.args)}" data-ui-attr-${attr}="${escape(JSON.stringify(m))}"`;
 });
 markup=markup.replace(/>([^<>]+)</g,(whole,text,offset,full)=>{
  // Native text already annotated above, and SVG labels retain their element.
  const prefix=full.slice(0,offset+1),tag=prefix.match(/<([^<>]+)>$/)?.[1]||'';
  if(/^(?:option|title|script|style|textarea|text|tspan)\b/i.test(tag)||tag.includes('data-ui-key='))return whole;
  const m=literal(text,values);
  if(!m){
   // Icon/control slots stay intact; only surrounding authored text is eligible.
   if(!/\uE000\d+\uE001/.test(text))return whole;
   return '>'+text.split(/(\uE000\d+\uE001)/).map(part=>{
    if(/^\uE000/.test(part))return part;
    const item=literal(part,[]);if(!item)return part;
    return part.match(/^\s*/)[0]+label(item.key)+part.match(/\s*$/)[0];
   }).join('')+'<';
  }
  const lead=text.match(/^\s*/)[0],tail=text.match(/\s*$/)[0];
  return `>${lead}<span data-ui-key="${escape(m.key)}" data-ui-args="${escape(JSON.stringify(m.args))}">${renderMessage(m.key,m.args)}</span>${tail}<`;
 });
 return markup.replace(/\uE000(\d+)\uE001/g,(_,i)=>String(values[Number(i)]??''));
}

export function attribute(name,key,...args){
 if(!['title','aria-label','placeholder'].includes(name))throw new Error('Unsupported translated attribute');
 return `${name}="${escape(t(key,...args))}" data-ui-attr-${name}="${escape(JSON.stringify({key,args:args.map(prepared)}))}"`;
}
export function setUIAttribute(node,name,key,...args){node.setAttribute(name,t(key,...args));node.setAttribute('data-ui-attr-'+name,JSON.stringify({key,args:args.map(prepared)}));}
export function label(key,...args){return `<span data-ui-key="${escape(key)}" data-ui-args="${escape(JSON.stringify(args.map(prepared)))}">${renderMessage(key,args.map(prepared))}</span>`;}
export function setUIText(node,key,...args){if(!node)return;clearUIText(node);node.innerHTML=label(key,...args);}
export function clearUIText(node){delete node.dataset.uiKey;delete node.dataset.uiArgs;}
export function applyUI(root=document,language,inheritedBundles=[]){
 const nodes=[...(root.querySelectorAll?.('[data-ui-key],[data-ui-attr-title],[data-ui-attr-aria-label],[data-ui-attr-placeholder],[data-ui-attr-label]')||[])];
 if(root.matches?.('[data-ui-key],[data-ui-attr-title],[data-ui-attr-aria-label],[data-ui-attr-placeholder],[data-ui-attr-label]'))nodes.unshift(root);
 for(const node of nodes){
  // Explicit inner overrides win even when rendering a detached update fragment.
  const targetLocale=(()=>{for(let n=node;n;n=n.parentElement)if(validLocale(n.dataset?.uiLocale))return n.dataset.uiLocale;return language||resolvedLocale(root);})(),bundles=[...scopedUIBundles(node),...inheritedBundles];
  if(node.dataset.uiKey){try{const next=renderMessage(node.dataset.uiKey,JSON.parse(node.dataset.uiArgs||'[]'),targetLocale,bundles);if(node.innerHTML!==next)node.innerHTML=next;}catch{}}
  for(const attr of ['title','aria-label','placeholder','label']){
   const value=node.getAttribute('data-ui-attr-'+attr);if(!value)continue;
   try{const {key,args}=JSON.parse(value);const decode=a=>{if(nested(a))return message(a.uiMessage,...a.args.map(decode));const d=document.createElement('textarea');d.innerHTML=a;return d.value;};const text=plainMessage(key,args.map(decode),targetLocale,bundles);if(node.getAttribute(attr)!==text)node.setAttribute(attr,text);}catch{}
  }
 }
 document.querySelectorAll('[data-ui-locale]').forEach(n=>n.lang=resolvedLocale(n));
 document.querySelectorAll('select[data-ui-language]').forEach(select=>{select.value=locale;});
}
export function setLocale(value,{persist=true}={}){
 if(!validLocale(value))value='en';locale=value;
 if(typeof document==='undefined')return;
 document.documentElement.lang=locale;document.documentElement.dataset.interfaceLanguage=locale;
 if(persist){try{localStorage.setItem(storageKey,value);}catch{}if(savePreference){const save=savePreference;pendingSave=pendingSave.then(()=>save(value)).catch(()=>{document.dispatchEvent(new CustomEvent('ui-language:save-error'));});}}
 applyUI();
 document.dispatchEvent(new CustomEvent('ui-language:changed',{detail:{locale}}));
}
export function configureLocale({scope='device',saved,onSave=null}={}){
 storageKey='phemacast.ui-language.v1:'+scope;savePreference=onSave;
 let local;try{local=localStorage.getItem(storageKey);}catch{}
 setLocale(validLocale(saved)?saved:validLocale(local)?local:'en',{persist:false});
}
export function languageSettingsHTML(){return ui`<section class="ui-language-setting"><label>${label('UI language')}<select data-ui-language aria-label="UI language">${Object.entries(LANGUAGES).map(([code,name])=>`<option value="${code}" ${locale===code?'selected':''}>${name}</option>`).join('')}</select></label><small>${label('UI labels only. Data, news, reports and analyst content stay in their original language.')}</small><small data-ui-language-feedback role="status"></small></section>`;}
if(typeof document!=='undefined'){
 configureLocale();
 document.addEventListener('change',event=>{if(event.target.matches?.('[data-ui-language]'))setLocale(event.target.value);});
 window.addEventListener('storage',event=>{if(event.key===storageKey)setLocale(event.newValue,{persist:false});});
 document.addEventListener('ui-language:save-error',()=>{document.querySelectorAll('[data-ui-language-feedback]').forEach(n=>setUIText(n,'Language applies locally; account settings could not be saved.'));});
 new MutationObserver(records=>{for(const r of records)for(const n of r.addedNodes)if(n.nodeType===1)applyUI(n);}).observe(document.documentElement,{childList:true,subtree:true});
}
