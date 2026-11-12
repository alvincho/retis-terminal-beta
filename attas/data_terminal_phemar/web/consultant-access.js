import {api,esc} from './connected.js';
import {ui,t} from './i18n.js';

export async function mountTrustedAccounts(dialog){
 const panel=document.createElement('section');panel.className='theme-setting';panel.dataset.trustedAccounts='';
 dialog.querySelector('.settings-body').insertBefore(panel,dialog.querySelector('.theme-instant-note'));
 let state;
 const render=()=>{
  panel.innerHTML=ui`<div><h3>Trusted accounts</h3><p>Only the owner and these Plaza accounts can open this App Consultant UI.</p></div>
   <div>${state.trustedAccountIds.map(id=>ui`<p><code>${esc(id)}</code> <button class="icon-button" type="button" data-remove-trusted-account="${esc(id)}" aria-label="Remove trusted account" title="Remove trusted account">×</button></p>`).join('')}
   <form data-trusted-account-form><label>Plaza account ID <input name="accountId" maxlength="128" required autocomplete="off"></label> <button class="button" type="submit">Trust account</button></form>
   <p data-trusted-account-status role="status"></p></div>`;
 };
 try{state=await api('/api/ui-access');render();}
 catch(error){panel.textContent=error.message;return;}
 let pending=false;
 async function save(accounts){
  if(pending)return;pending=true;
  panel.querySelectorAll('button,input').forEach(node=>node.disabled=true);
  try{state=await api('/api/ui-access',{method:'PUT',body:{revision:state.revision,trustedAccountIds:accounts}});render();}
  catch(error){
   panel.querySelector('[data-trusted-account-status]').textContent=t(error.message);
   // Refresh a conflicted revision before a retry without overwriting a new
   // owner's decision or retrying a mutation implicitly.
   if(error.status===409)try{state=await api('/api/ui-access');}catch{}
  }finally{pending=false;panel.querySelectorAll('button,input').forEach(node=>node.disabled=false);}
 }
 panel.addEventListener('submit',event=>{
  if(!event.target.matches('[data-trusted-account-form]'))return;event.preventDefault();
  const id=new FormData(event.target).get('accountId').trim();
  if(id)save([...new Set([...state.trustedAccountIds,id])]);
 });
 panel.addEventListener('click',event=>{
  const button=event.target.closest('[data-remove-trusted-account]');
  if(button)save(state.trustedAccountIds.filter(id=>id!==button.dataset.removeTrustedAccount));
 });
}
