import {ui,LANGUAGES,validLocale,applyUI,setUIText} from './i18n.js';
/** Presentation only: language updates never recreate a Browser or reload data. */
export function openBrowserLanguageDialog({value=null,onChange}){
 const dialog=document.createElement('dialog');dialog.className='connected-reader browser-language-dialog';dialog.dataset.uiLocale=validLocale(value)?value:'';
 dialog.innerHTML=ui`<h2>Browser language</h2><p>Follow the Terminal language, or choose a language for this browser only.</p><label>UI language<select data-browser-language aria-label="Browser language"><option value="">Follow Terminal</option>${Object.entries(LANGUAGES).map(([code,name])=>`<option value="${code}">${name}</option>`).join('')}</select></label><p>UI labels only. Data, news, reports and analyst content stay in their original language.</p><p role="status"></p><button class="button" data-close>Done</button>`;
 dialog.querySelector('select').value=validLocale(value)?value:'';
 dialog.querySelector('[data-close]').onclick=()=>dialog.close();dialog.onclose=()=>dialog.remove();
 dialog.querySelector('select').onchange=async event=>{
  const next=event.target.value||null;event.target.disabled=true;
  try{await onChange(next);value=next;dialog.dataset.uiLocale=next||'';setUIText(dialog.querySelector('[role="status"]'),'Saved.');applyUI(dialog);}
  catch(error){event.target.value=value||'';setUIText(dialog.querySelector('[role="status"]'),'Could not save browser language. Try again.');}
  finally{event.target.disabled=false;}
 };
 document.body.append(dialog);applyUI(dialog);dialog.showModal();dialog.querySelector('select').focus();
 return dialog;
}
/** A new installation asks for its language once; resolves when the dialog closes. */
export function openLanguageChoice({onChoose}){
 const dialog=document.createElement('dialog');dialog.className='connected-reader language-choice-dialog';dialog.setAttribute('aria-labelledby','language-choice-title');
 dialog.innerHTML=ui`<h2 id="language-choice-title">Choose your language</h2><p>You can change it later in Terminal settings.</p><div class="language-choices">${Object.entries(LANGUAGES).map(([code,name])=>`<button type="button" lang="${code}" data-language="${code}">${name}</button>`).join('')}</div>`;
 const closed=new Promise(resolve=>{dialog.onclose=()=>{dialog.remove();resolve();};});
 dialog.querySelectorAll('[data-language]').forEach(button=>{button.onclick=async()=>{
  dialog.querySelectorAll('[data-language]').forEach(other=>{other.disabled=true;});
  try{await onChoose(button.dataset.language);}finally{dialog.close();}
 };});
 document.body.append(dialog);applyUI(dialog);dialog.showModal();dialog.querySelector('[data-language]').focus();
 return closed;
}
