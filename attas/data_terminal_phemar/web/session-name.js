// A Terminal tab's own name. It is saved in this tab's Terminal settings and shown in
// the title bar and the browser tab, so several Terminal tabs can be told apart.
import {ui as uiHTML,t,attribute,setUIText} from './i18n.js';
import {runtime,api} from './connected.js';
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const DEFAULT_TITLE='Data Terminal · Research workspace',NAMED_TITLE='{0} · Data Terminal',PRODUCT_TITLE='{0} · Research workspace',NAMED_PRODUCT='{0} · {1}';
// A package that names its Terminal (Retis Terminal for the desktop app) puts that name in the tab title, so it can be told
// apart from another Terminal in the same browser; the kit keeps "Data Terminal" in the interface language.
const product=()=>{const value=typeof runtime.product==='string'?runtime.product.trim():'';return value&&value!=='Data Terminal'?value:'';};

export function sessionName(){const value=runtime.settings?.preferences?.sessionName;return typeof value==='string'?value.trim():'';}

export function sessionNameTagHTML(){
 const name=sessionName();
 return `<span class="session-name" data-session-name-label ${name?'':'hidden'}>${esc(name)}</span>`;
}

export function applySessionName(){
 const name=sessionName(),title=document.querySelector('title');
 if(title){
  // The title keeps its message key, so a language change redraws it with the name.
  const app=product(),key=name?(app?NAMED_PRODUCT:NAMED_TITLE):(app?PRODUCT_TITLE:DEFAULT_TITLE),args=name?(app?[name,app]:[name]):(app?[app]:[]);
  title.dataset.uiKey=key;title.dataset.uiArgs=JSON.stringify(args.map(esc));
  title.textContent=t(key,...args);
 }
 document.querySelectorAll('[data-session-name-label]').forEach(node=>{node.textContent=name;node.hidden=!name;});
}

export function sessionNameSettingsHTML(){
 return uiHTML`<form class="session-name-setting" data-session-name-form><h3>Session name</h3><div class="session-name-row"><input name="sessionName" maxlength="60" value="${esc(sessionName())}" ${attribute('aria-label','Session name')} ${attribute('placeholder','e.g. Morning research')}><button class="button" type="submit">Save name</button></div><small>Shown in the title bar and the browser tab.</small><p data-session-name-feedback role="status"></p></form>`;
}

document.addEventListener('submit',async event=>{
 const form=event.target.closest?.('[data-session-name-form]');if(!form)return;
 event.preventDefault();event.stopImmediatePropagation();
 const feedback=form.querySelector('[data-session-name-feedback]'),button=form.querySelector('[type="submit"]');
 button.disabled=true;
 try{
  const saved=await api('/api/settings',{method:'PUT',body:{sessionName:form.elements.sessionName.value}});
  runtime.settings.preferences={...runtime.settings.preferences,sessionName:saved.sessionName||''};
  form.elements.sessionName.value=sessionName();applySessionName();setUIText(feedback,'Saved.');
 }catch(error){feedback.textContent=error.message;}finally{button.disabled=false;}
},true);
