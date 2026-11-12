import {installPhemaEdit,mountPhemaEdit,phemaEditToolbar,togglePhemaEdit,phemaEditGesture} from './browser-phema-edit.js';
import {uiBundle} from './phema-i18n.js';
import {openBrowserLanguageDialog} from './browser-language.js';
import {ui as uiHTML,setUIText,applyUI} from './i18n.js';
import {refreshSavedLayouts} from './layout-updates.js';
import {browserDesign} from './browser-instruments.js';
import {renderBrowserPanels,installLayoutTabs} from './layout-tabs.js';
import {refreshPanels} from './panel-refresh.js';
import {layoutFetchStatus,statusHTML as fetchStatusHTML,updateStatus} from './layout-status.js';
import {connect,runtime,api,esc,attachBrowserInstance,browserState,panelHTML,settingsHTML,setBrowserLanguage,browserCompany,discussionHTML,loadCatalog} from './connected.js';
import {initializeTheme} from './theme.js';
import {layoutStyle,templateStyle,usesFreePlacement} from './layout-presentation.js';
import {bindLayoutEntry} from './layout-library.js';
await connect();loadCatalog().catch(()=>{});initializeTheme();
const root=document.querySelector('#browser-surface'),id=new URLSearchParams(location.search).get('instance');let browser,entry;
function render(b){
 root.dataset.uiLocale=b.uiLanguage||'';root.dataset.phemaUi=JSON.stringify(uiBundle(b.customLayout)||{});applyUI(root);
 if(phemaEditGesture(b))return;
 const main=root.querySelector('main');if(!main)return;
 setUIText(root.querySelector('header strong'),'{0} · {1}',{uiMessage:b.design.title,args:[]},browserCompany(b));
 let layout=main.querySelector('.browser-layout');if(!layout){main.innerHTML=uiHTML`<div class="browser-layout composed-browser-layout"></div>`;layout=main.firstElementChild;}
 let toolbar=main.querySelector('.phema-edit-toolbar');const markup=phemaEditToolbar(b);if(toolbar)toolbar.outerHTML=markup;else if(markup)main.insertAdjacentHTML('afterbegin',markup);
 layout.setAttribute('style',layoutStyle(b.design.layout));refreshPanels(layout,renderBrowserPanels(b));mountPhemaEdit(root,b);
 const status=root.querySelector('.layout-fetch-status'),value=layoutFetchStatus(browserState(b),b.design.dataRequirements);if(status){updateStatus(status,value);}
}
if(!runtime.user){root.innerHTML=uiHTML`<section class="browser-launch"><h1>Data Browser</h1>${settingsHTML()}</section>`;}
else if(!id){const result=await api('/api/instances');root.innerHTML=uiHTML`<section class="browser-launch"><h1>Data Browser</h1><p>Open a layout in <a href="${esc(runtime.terminalOrigin)}">Data Terminal</a>, or view an existing Browser instance.</p>${result.instances.map(i=>uiHTML`<p><a href="?instance=${esc(i.id)}">${esc(i.entry.name)} · ${esc(i.command)}</a></p>`).join('')}</section>`;}
else {try{
 const remote=await api('/api/instances/'+encodeURIComponent(id));entry=remote.entry;
 const saved={customLayout:entry,design:entry.design,command:remote.command,fieldBindings:remote.fieldBindings,refreshOverrides:remote.refreshOverrides};
 const check=await refreshSavedLayouts([saved],api);
 if(check.updated){entry={...saved.customLayout,design:browserDesign(saved.design,saved.instrumentOverrides)};await api('/api/instances/'+id,{method:'PUT',body:{entry}});}

 root.innerHTML=uiHTML`<header><strong>Data Browser</strong><span class="layout-fetch-status" role="status"></span><button class="button" id="browser-language" aria-label="Browser language" title="Browser language">◎</button><button class="button" id="toggle-edit" aria-pressed="false">Phema Edit</button><button class="button" id="toggle-command">⌘ Command</button><button class="button" id="browser-settings">Settings</button></header><form id="standalone-command" hidden><input name="command" aria-label="Browser command" maxlength="499"><button>Apply context</button><span role="status"></span></form><main></main>`;
 browser=await attachBrowserInstance(id,render);render(browser);
 document.querySelector('#browser-language').onclick=()=>openBrowserLanguageDialog({value:browser.uiLanguage,onChange:async value=>{await setBrowserLanguage(browser,value);root.dataset.uiLocale=value||'';applyUI(root);}});
 document.querySelector('#toggle-edit').onclick=event=>{togglePhemaEdit(browser);event.currentTarget.setAttribute('aria-pressed',String(browser.editMode));setUIText(event.currentTarget,browser.editMode?'Done editing':'Phema Edit');render(browser);};
 document.querySelector('#toggle-command').onclick=()=>{const form=document.querySelector('#standalone-command');form.hidden=!form.hidden;form.elements.command.value=browser.command;if(!form.hidden)form.elements.command.focus();};
 document.querySelector('#standalone-command').onsubmit=async event=>{event.preventDefault();const command=event.target.elements.command.value;try{const current=browser.customLayout||entry,bound=bindLayoutEntry(current,command);entry={...current,command,design:bound.dbl};await api('/api/instances/'+id,{method:'PUT',body:{entry,command}});}catch(error){event.target.querySelector('[role="status"]').textContent=error.message;}};
 root.addEventListener('click',async event=>{const action=event.target.closest('[data-action]')?.dataset.action;if(action==='range'){await api('/api/instances/'+id,{method:'PUT',body:{range:event.target.dataset.range}});}if(action==='discussion'){const dialog=document.createElement('dialog');dialog.className='connected-reader';dialog.dataset.uiLocale=browser.uiLanguage||'';dialog.innerHTML=uiHTML`<button class="button" data-dismiss>Close</button>`+await discussionHTML(browser,event.target.closest('[data-node-id]')?.dataset.nodeId);document.body.append(dialog);applyUI(dialog);dialog.showModal();dialog.querySelector('[data-dismiss]').onclick=()=>dialog.close();dialog.onclose=()=>dialog.remove();}if(action==='settings'||event.target.closest('#browser-settings')){const dialog=document.createElement('dialog');dialog.className='connected-reader';dialog.innerHTML=uiHTML`<button class="button" data-dismiss>Close</button>`+settingsHTML();document.body.append(dialog);dialog.showModal();dialog.querySelector('[data-dismiss]').onclick=()=>dialog.close();dialog.onclose=()=>dialog.remove();}});
}catch(error){root.innerHTML=uiHTML`<section class="browser-launch"><h1>Data Browser unavailable</h1><p>${esc(error.message)}</p></section>`;}}

document.addEventListener('connected:settings',()=>{const section=document.querySelector('dialog .connected-settings');if(section)section.outerHTML=settingsHTML();});

installLayoutTabs(async()=>{
 if(!browser)return;
 try{await api('/api/instances/'+id,{method:'PUT',body:{layoutTabs:browser.layoutTabs}});render(browser);}
 catch(error){const status=root.querySelector('.layout-fetch-status');if(status)status.textContent=error.message;}
},browserId=>browser?.id===browserId?browser:null);

installPhemaEdit({find:id=>browser?.id===id?browser:null,render:b=>render(b)});
