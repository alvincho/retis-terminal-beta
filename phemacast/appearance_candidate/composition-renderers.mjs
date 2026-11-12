import {installGraphRenderer} from './graph-renderer-adapter.js';
import {rowTone,appearanceClass} from './appearance.js';
import {navigationHTML,mountNavigation} from './operation-result-navigation.js';
/** Only host-installed modules register code. A Phema supplies data and an ID. */
const installed=new Map();
let uiContext=()=>({locale:'en',label:esc,translate:key=>key});
export function configureRendererUI(factory){uiContext=factory;}
// Mounted renderers, such as agent views, keep running across a full page re-render (maximize, window clicks):
// keepMounted moves them aside with an atomic move, and mountCustom puts each back into its new placeholder.
const parked=new Map(),PARKED_MS=10000;let park=null;
const parkKey=host=>{const window=host.closest('[data-browser]')?.dataset.browser;return window?window+'|'+host.dataset.installedRenderer+'|'+host.dataset.rendererValue:null;};
// Not reclaimed in time: the window or its layout changed. Removing a renderer disposes it.
const release=()=>{const now=performance.now();for(const [key,{host,at}] of parked)if(now-at>=PARKED_MS){parked.delete(key);host.remove();}};
export function keepMounted(root){
 release();
 if(typeof Element.prototype.moveBefore!=='function')return; // a plain move would reload a frame anyway
 if(!park?.isConnected){park=document.createElement('div');park.hidden=true;document.body.append(park);}
 for(const host of root.querySelectorAll('[data-installed-renderer][data-renderer-mounted]')){
  const key=parkKey(host);if(!key||parked.has(key))continue;
  park.moveBefore(host,null);parked.set(key,{host,at:performance.now()});
 }
}
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export function registerRenderer({id,version,render,mount,features=[]}){
 if(globalThis.phemacastSandboxRegister){globalThis.phemacastSandboxRegister({id,version,render,mount,features});return;}
 if(!/^[a-z][a-z0-9.-]+:[a-z][a-z0-9._-]*$/.test(id)||!/^\d+\.\d+\.\d+$/.test(version)||typeof render!=='function')throw new Error('Invalid installed renderer');
 if(installed.has(id))throw new Error('Renderer already installed');
 installed.set(id,{id,version,render,mount,features});
}
export function installedRenderers(){return [...installed.values()].map(({id,version,features})=>({id,version,features}));}
export function customHTML(capability,value,context={}){
 const renderer=installed.get(capability?.id),required=String(capability?.version||'').split('.').map(Number),offered=renderer?.version.split('.').map(Number);
 if(!renderer||offered[0]!==required[0]||offered[1]<required[1]||offered[1]===required[1]&&offered[2]<required[2])return null;
 try{return `<div data-installed-renderer="${esc(renderer.id)}" data-renderer-value="${esc(JSON.stringify(value))}">${renderer.render(value,{escape:esc,...uiContext(null,context),...context})}${navigationHTML(value,esc)}</div>`;}catch{return null;}
}
// The host's formatter applies the viewer's display format to each field's declared kind; without one a value
// shows as it arrives. Numbers line up on the right.
const plain=value=>typeof value==='object'&&value!==null?JSON.stringify(value):value;
const numeric=(value,format)=>['amount','percent','ratio','count','number'].includes(format?.kind)||typeof value==='number';
// 1.2.0: a source's per-instrument notes, such as an unknown listing, show under the rows.
registerRenderer({id:'phemacast:display.table',version:'1.2.0',features:['sort','notes'],render(value,{label,appearance,formatValue=plain,fieldFormats={}}){
 if(!Array.isArray(value?.columns)||!Array.isArray(value?.rows)||value.columns.length>50||value.rows.length>10000)throw new Error('Invalid table');
 const notes=(Array.isArray(value.notes)?value.notes:[]).filter(note=>typeof note?.message==='string'&&note.message).slice(0,50);
 const list=notes.length?`<ul class="composition-table-notes">${notes.map(note=>`<li>${typeof note.instrument==='string'&&note.instrument?esc(note.instrument)+' · ':''}${label(note.message)}</li>`).join('')}</ul>`:'';
 // An empty table that explains itself shows only its notes.
 if(!value.rows.length&&list)return `<div class="composition-custom-table">${list}</div>`;
 return `<div class="composition-custom-table"><table><thead><tr>${value.columns.map(c=>`<th><button type="button" data-custom-sort="${esc(c.key)}">${label(c.label||c.key)}</button></th>`).join('')}</tr></thead><tbody>${value.rows.map((row,index)=>`<tr>${value.columns.map((c,col)=>`<td${numeric(row[c.key],fieldFormats[c.key])?' class="composition-number"':''}>${col===0&&appearanceClass(appearance)&&rowTone(value.rowTones?.[index])?`<span class="cell-badge ${rowTone(value.rowTones[index])}">${esc(formatValue(row[c.key],fieldFormats[c.key]))}</span>`:esc(formatValue(row[c.key],fieldFormats[c.key]))}</td>`).join('')}</tr>`).join('')}</tbody></table>${list}${value.text?`<p class="panel-note">${esc(value.text)}</p>`:''}</div>`;
}});
registerRenderer({id:'phemacast:display.key-values',version:'1.1.0',render(value,{label,formatValue=plain,fieldFormats={}}){if(!value?.values||Array.isArray(value.values))throw new Error('Invalid values');return `<dl class="composition-key-values">${Object.entries(value.values).slice(0,200).map(([k,v])=>`<div class="composition-kv-item"><dt>${label(k)}</dt><dd>${esc(formatValue(v,fieldFormats[k]))}</dd></div>`).join('')}</dl>`;}});
export function mountCustom(root){
 mountNavigation(root);
 for(const host of root.querySelectorAll('[data-installed-renderer]:not([data-renderer-mounted])')){
  const key=parkKey(host),kept=key&&parked.get(key)?.host;
  if(kept){parked.delete(key);host.parentNode.moveBefore(kept,host);host.remove();continue;}
  host.dataset.rendererMounted='true';const renderer=installed.get(host.dataset.installedRenderer);try{const dispose=renderer?.mount?.(host,JSON.parse(host.dataset.rendererValue),{escape:esc,...uiContext(host)});if(typeof dispose==='function'){const observer=new MutationObserver(()=>{if(!host.isConnected){observer.disconnect();dispose();}});observer.observe(document.body,{childList:true,subtree:true});}}catch{host.dataset.rendererError='true';}}
 for(const table of root.querySelectorAll('.composition-custom-table:not([data-mounted])')){table.dataset.mounted='true';table.addEventListener('click',event=>{const button=event.target.closest('[data-custom-sort]');if(!button)return;const index=button.closest('th').cellIndex,body=table.querySelector('tbody'),direction=button.dataset.direction==='asc'?-1:1;button.dataset.direction=direction===1?'asc':'desc';[...body.rows].sort((a,b)=>direction*a.cells[index].textContent.localeCompare(b.cells[index].textContent,undefined,{numeric:true})).forEach(row=>body.append(row));});}
}
export async function installRendererModules(request){
 const failures=[];
 const {renderers=[]}=await request('/api/renderers');
 for(const renderer of renderers){
  if(installed.has(renderer.capability.id))continue;
  try{
   const url=new URL(renderer.module,location.href);if(url.origin!==location.origin||!url.pathname.startsWith('/composition-renderers/'))throw new Error('Renderer is not host-installed');
   registerRenderer({id:renderer.capability.id,version:renderer.capability.version,features:renderer.capability.features||[],
    render:()=>'<iframe data-extension-frame title="Phema component" sandbox="allow-scripts" referrerpolicy="no-referrer" style="width:100%;height:100%;min-height:200px;border:0"></iframe>',
    mount(host,value,context){
     const frame=host.querySelector('iframe'),frameURL=new URL('./renderer-frame',location.href);frameURL.searchParams.set('module',url.pathname);let bridge;
     const onMessage=event=>{if(event.source!==frame.contentWindow||event.origin!=='null'||event.data?.type!=='renderer-ready')return;
      window.removeEventListener('message',onMessage);const channel=new MessageChannel();
      bridge=channel.port1;
      channel.port1.onmessage=event=>{if(event.data?.type==='save-data'){
       // A component cannot choose a target, URL, operation or callback.
       const next=event.data.value;if(JSON.stringify(next).length>500000)return;
       host.dispatchEvent(new CustomEvent('browser-renderer-data',{bubbles:true,detail:{value:next,resolve:()=>{},reject:()=>{}}}));
      }};
      frame.contentWindow.postMessage({type:'renderer-init',value,capability:renderer.capability,locale:context.locale},'*',[channel.port2]);
     };
     window.addEventListener('message',onMessage);frame.src=frameURL.href;return ()=>{window.removeEventListener('message',onMessage);bridge?.close();};
    }});
  }catch(error){failures.push({id:renderer.capability.id,reason:error.message});}
 }
 return failures;
}

installGraphRenderer(registerRenderer);
