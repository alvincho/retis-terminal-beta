import {terminalHeaders} from './terminal-session.js';
import {setUIText} from './i18n.js';
// Pairing secrets remain in the Terminal's encrypted server store.
const REFRESH_MS=15000;
const el=(tag,text,translate=false)=>{const node=document.createElement(tag);if(text!==undefined){if(translate)setUIText(node,text);else node.textContent=text;}return node;};
async function api(path,body){const response=await fetch('/api/local-resources'+path,{method:body===undefined?'GET':'POST',credentials:'same-origin',headers:terminalHeaders({'Content-Type':'application/json'}),body:body===undefined?undefined:JSON.stringify(body)});const value=await response.json().catch(()=>({}));if(!response.ok)throw Error(value.detail||'Local resource unavailable.');return value;}
async function refresh(root,scan=false){
 const status=root.querySelector('[data-local-status]');
 // Discovery can take a few seconds: the section says it is loading rather than stay blank.
 const first=!root.dataset.loaded;if(first)setUIText(status,'Loading…');
 try{
  const value=await api(scan?'/scan':'',scan?{}:undefined);const list=root.querySelector('[data-local-list]');list.replaceChildren();
  if(first){root.dataset.loaded='true';status.replaceChildren();}
  for(const row of value.resources){
   // A paired resource that discovery no longer finds is offline. Without discovery the Terminal can't tell, so it says nothing.
   const offline=row.paired&&!row.identityChanged&&row.online===false&&value.status==='discovering';
   const state=el('small');state.dataset.localState=offline?'offline':row.identityChanged?'identity-changed':row.paired?'paired':'discovered';state.append(row.kind);
   for(const key of [row.identityChanged?'Identity changed':row.paired?'Paired':'Discovered',...(offline?['Offline']:[])])state.append(' · ',el('span',key,true));
   const line=el('div');line.className='setting-row';const label=el('span');label.append(el('strong',row.name),state);line.append(label);
   const button=el('button',row.paired?'Disconnect':'Pair',true);button.type='button';button.disabled=!!row.identityChanged;
   button.addEventListener('click',async()=>{
    button.disabled=true;root.dataset.busy='true';
    try{
     if(row.paired){await api('/revoke',{resourceId:row.id});await refresh(root);return;}
     const grant=await api('/pair',{resourceId:row.id});
     if(grant.status==='paired'){setUIText(status,'Paired. This resource is available to this Terminal account.');await refresh(root);return;}
     // An older resource still asks its owner to approve the code.
     status.replaceChildren(el('strong','Pairing code: '+grant.code),el('p','Ask the resource owner to approve this code on its host. Expires in 5 minutes.'),el('small','Certificate: '+grant.fingerprint));
     const end=Date.now()+300000;
     while(root.isConnected&&Date.now()<end){await new Promise(r=>setTimeout(r,5000));const result=await api('/poll',{id:grant.id});if(result.status==='paired'){setUIText(status,'Paired. This resource is available to this Terminal account.');await refresh(root);return;}}
     setUIText(status,'Pairing expired. Try again.');
    }catch(error){setUIText(status,error.message);}finally{button.disabled=false;delete root.dataset.busy;}
   });line.append(button);list.append(line);
  }
  if(!value.resources.length)setUIText(status,value.status==='unavailable'?'Bonjour is unavailable on this network.':'No resources discovered. Enable local connections on the source or consultant.');
 }catch(error){root.dataset.loaded='true';setUIText(status,error.message);}
}
const observer=new MutationObserver(()=>{
 for(const root of document.querySelectorAll('[data-local-resources]:not([data-ready])')){
  root.dataset.ready='true';root.querySelector('[data-local-scan]').addEventListener('click',()=>refresh(root,true));refresh(root);
  // Keep each row's online state current while the settings stay open; never during a pairing or disconnect.
  const timer=setInterval(()=>{if(!root.isConnected){clearInterval(timer);return;}if(!root.dataset.busy&&!document.hidden)refresh(root);},REFRESH_MS);
 }
});
observer.observe(document.documentElement,{subtree:true,childList:true});
