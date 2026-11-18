import {terminalHeaders} from './terminal-session.js';
import {setUIText} from './i18n.js';
// Programs on this computer (Retis MAS Manager and the components it runs): found by Bonjour, used at once, no pairing.
const REFRESH_MS=15000;
const el=(tag,text,translate=false)=>{const node=document.createElement(tag);if(text!==undefined){if(translate)setUIText(node,text);else node.textContent=text;}return node;};
async function api(path,body){const response=await fetch('/api/local-resources'+path,{method:body===undefined?'GET':'POST',credentials:'same-origin',headers:terminalHeaders({'Content-Type':'application/json'}),body:body===undefined?undefined:JSON.stringify(body)});const value=await response.json().catch(()=>({}));if(!response.ok)throw Error(value.detail||'Local resource unavailable.');return value;}
async function refresh(root,scan=false){
 const status=root.querySelector('[data-local-status]');
 // Discovery can take a few seconds: the section says it is loading rather than stay blank.
 const first=!root.dataset.loaded;if(first)setUIText(status,'Loading…');
 try{
  const value=await api(scan?'/scan':'',scan?{}:undefined);const list=root.querySelector('[data-local-list]');list.replaceChildren();
  if(first){root.dataset.loaded='true';}
  status.replaceChildren();
  for(const row of value.resources){
   const state=el('small');state.dataset.localState='running';state.append(row.kind,' · ',el('span','Running',true));
   const line=el('div');line.className='setting-row';const label=el('span');label.append(el('strong',row.name),state);line.append(label);list.append(line);
  }
  if(!value.resources.length)setUIText(status,value.status==='unavailable'?'Bonjour is unavailable on this computer.':'Nothing runs on this computer yet. Start components in Retis MAS Manager.');
 }catch(error){root.dataset.loaded='true';setUIText(status,error.message);}
}
const observer=new MutationObserver(()=>{
 for(const root of document.querySelectorAll('[data-local-resources]:not([data-ready])')){
  root.dataset.ready='true';root.querySelector('[data-local-scan]').addEventListener('click',()=>refresh(root,true));refresh(root);
  // Keep the list current while the settings stay open.
  const timer=setInterval(()=>{if(!root.isConnected){clearInterval(timer);return;}if(!document.hidden)refresh(root);},REFRESH_MS);
 }
});
observer.observe(document.documentElement,{subtree:true,childList:true});
