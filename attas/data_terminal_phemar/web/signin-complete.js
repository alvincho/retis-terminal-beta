// Plaza returned this window's approval or trust request to the Terminal (window_auth.py, terminal_connection.py).
// The page reads in the language chosen for the Terminal on this computer, or else the browser's.
import {setLocale,validLocale,t} from './i18n.js';
function language(){
 try{for(let i=0;i<localStorage.length;i++){const key=localStorage.key(i);if(key?.startsWith('phemacast.ui-language.v1')&&validLocale(localStorage.getItem(key)))return localStorage.getItem(key);}}catch{}
 const browser=(navigator.languages?.length?navigator.languages:[navigator.language]).map(String);
 return browser.map(l=>/^zh-(TW|HK|MO|Hant)/i.test(l)?'zh-Hant':/^zh/i.test(l)?'zh-Hans':l.slice(0,2)).find(validLocale)||'en';
}
setLocale(language(),{persist:false});
const status=new URLSearchParams(location.search).get('status');
const text={unknown:'This request did not start in this Terminal, so nothing was signed in or requested. Close this window.',
  requested:'Trust requested. Return to your Terminal; it signs in by itself once the Plaza owner approves.',
  expired:'This sign-in request expired. Close this window and sign in again from your Terminal.'};
document.getElementById('status').textContent=t(text[status]||'Signed in. You can close this window and return to your Terminal.');
if(!text[status])setTimeout(()=>window.close(),800);
