import {terminalHeaders} from './terminal-session.js';
import {t,setUIText,currentLocale} from './i18n.js';
import {activateWindowSession} from './window-signin.js';
// A window signs in with the Plaza account and password typed here (Alvin, 2026-11-13): the Terminal finishes the
// sign-in with Plaza and keeps only the window's own session (window_auth.py). Nothing typed here is stored.
const UNCONFIRMED='Confirm your email before signing in';
async function post(path,body){
  const response=await fetch(path,{method:'POST',credentials:'same-origin',headers:terminalHeaders({'Content-Type':'application/json'}),
    body:JSON.stringify(body),cache:'no-store',signal:AbortSignal.timeout(30000)})
    .catch(error=>{throw Error(error.name==='TimeoutError'?'Plaza sign-in timed out. Please try again.':'Plaza sign-in is unavailable.');});
  let data={};try{data=await response.json();}catch{}
  if(!response.ok)throw Error(typeof data.detail==='string'&&data.detail?data.detail:'Plaza sign-in is unavailable.');
  return data;
}
const FIELDS=`<label><span data-label="identifier"></span><input name="identifier" autocomplete="username" required maxlength="320" spellcheck="false" autocapitalize="off"></label>
<label><span data-label="password"></span><input name="password" type="password" autocomplete="current-password" required maxlength="500"></label>
<label data-register-only hidden><span data-label="displayName"></span><input name="displayName" autocomplete="name" maxlength="120"></label>
<div class="password-signin-actions"><button class="button primary" type="submit" data-password-submit></button><button type="button" class="link-button" data-password-mode></button><button type="button" class="link-button" data-password-reset></button></div>
<p data-password-status role="status"></p>`;
function mode(form,next){
  const register=next==='register';form.dataset.mode=next;
  const identifier=form.elements.identifier,password=form.elements.password;
  identifier.type=register?'email':'text';identifier.autocomplete=register?'email':'username';
  password.autocomplete=register?'new-password':'current-password';
  form.querySelector('[data-register-only]').hidden=!register;
  setUIText(form.querySelector('[data-label=identifier]'),register?'Email':'Account or email');
  setUIText(form.querySelector('[data-label=password]'),'Password');
  setUIText(form.querySelector('[data-label=displayName]'),'Name (optional)');
  setUIText(form.querySelector('[data-password-submit]'),register?'Create account':'Sign in');
  setUIText(form.querySelector('[data-password-mode]'),register?'I have an account':'Create an account');
  const reset=form.querySelector('[data-password-reset]');reset.hidden=register;setUIText(reset,'Forgot password?');
}
// The sign-in form for a signed-out window, prefilled with the account it last used when there is one.
export function passwordForm({account=''}={}){
  const form=document.createElement('form');form.className='password-signin';form.dataset.passwordSignin='';
  form.innerHTML=FIELDS;mode(form,'signin');
  if(account)form.elements.identifier.value=account;
  return form;
}
async function windowStatus(){
  try{const response=await fetch('/api/auth/window-signin/status',{credentials:'same-origin',headers:terminalHeaders(),cache:'no-store',signal:AbortSignal.timeout(5000)});
    return response.ok?(await response.json()).status:null;}catch{return null;}
}
function busy(form,on){form.querySelectorAll('button,input').forEach(node=>node.disabled=on);}
async function sendEmail(form,purpose){
  const status=form.querySelector('[data-password-status]'),identifier=form.elements.identifier.value.trim();
  if(!identifier){setUIText(status,'Enter your account first.');form.elements.identifier.focus();return;}
  busy(form,true);
  try{
    await post('/api/auth/window-signin/email',{identifier,purpose,language:currentLocale()});
    setUIText(status,purpose==='confirm'?'Confirmation email sent. Open its link, then sign in here.':'If {0} has a Plaza account, a link to reset its password is on its way.',identifier);
  }catch(error){status.textContent=t(error.message);}
  finally{busy(form,false);}
}
document.addEventListener('submit',async event=>{
  const form=event.target.closest('[data-password-signin]');if(!form)return;
  event.preventDefault();event.stopImmediatePropagation();
  const status=form.querySelector('[data-password-status]'),values=Object.fromEntries(new FormData(form));
  const identifier=String(values.identifier||'').trim(),password=String(values.password||'');
  if(!identifier||!password){setUIText(status,'Enter your Plaza account and password.');return;}
  const register=form.dataset.mode==='register';
  busy(form,true);setUIText(status,register?'Creating your account…':'Signing in…');
  try{
    if(register){
      const result=await post('/api/auth/window-signin/register',{email:identifier,password,displayName:String(values.displayName||'').trim()||null,language:currentLocale()});
      mode(form,'signin');form.elements.password.value='';form.elements.identifier.value=identifier;
      setUIText(status,!result.confirmationRequired?'Account created. Sign in to continue.':result.confirmationSent?'We sent a confirmation email to {0}. Open its link, then sign in here.':'Account created, but the confirmation email could not be sent. Try again later, or ask the Plaza owner.',identifier);
      busy(form,false);return;
    }
    await post('/api/auth/window-signin/password',{identifier,password});
  }catch(error){
    // A window this panel still shows signed out may already be signed in (another tab, or a login Plaza accepts
    // again after an unblock): load it below instead of asking the person to sign out first.
    if(register||await windowStatus()!=='signed-in'){
      busy(form,false);status.textContent=t(error.message);
      // An account whose email is not confirmed yet can ask Plaza to send the confirmation again from here.
      if(error.message.startsWith(UNCONFIRMED)){
        const again=document.createElement('button');again.type='button';again.className='link-button';again.dataset.passwordResend='';
        setUIText(again,'Send the confirmation email again');status.append(' ',again);
      }
      form.elements.password.select();
      return;
    }
  }
  form.elements.password.value='';
  setUIText(status,'Signed in. Loading your workspace…');
  // The window is signed in; a workspace that can't load yet (this Terminal still waits for the Plaza owner) says why.
  try{await activateWindowSession();}
  catch(error){busy(form,false);status.textContent=t(error.message);}
},true);
document.addEventListener('click',event=>{
  const form=event.target.closest('[data-password-signin]');if(!form)return;
  const status=form.querySelector('[data-password-status]');
  if(event.target.closest('[data-password-mode]')){status.replaceChildren();mode(form,form.dataset.mode==='register'?'signin':'register');form.elements.identifier.focus();}
  else if(event.target.closest('[data-password-reset]'))sendEmail(form,'reset');
  else if(event.target.closest('[data-password-resend]'))sendEmail(form,'confirm');
});
