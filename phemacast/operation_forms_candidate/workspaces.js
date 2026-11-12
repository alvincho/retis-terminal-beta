/** Generic discovery and mounting of inert entries declared by owning agents. */
import {createOwnerBusy} from './owner-busy.js';
function openingIndicator(text){
 if(!document.querySelector('link[data-workspace-launcher-style]')){
  const link=document.createElement('link');link.rel='stylesheet';
  link.href=new URL('./workspace-loading.css',import.meta.url).href;
  link.dataset.workspaceLauncherStyle='true';document.head.append(link);
 }
 const indicator=document.createElement('div');indicator.className='workspace-opening-status';
 indicator.dataset.workspaceOpening='true';indicator.setAttribute('role','status');
 indicator.setAttribute('aria-live','polite');indicator.setAttribute('aria-busy','true');
 const spinner=document.createElement('span');spinner.className='workspace-opening-spinner';spinner.setAttribute('aria-hidden','true');
 const message=document.createElement('span');message.textContent=text('Loading layouts…');message.dataset.uiKey='Loading layouts…';
 indicator.append(spinner,message);document.body.append(indicator);
 return ()=>indicator.remove();
}

export function installWorkspaceLauncher({request,text=x=>x,showButton=true}){
 let active,pending;
 const openPreferred=({name,resolve,present}={})=>{
  if(pending)return pending;
  const clearIndicator=openingIndicator(text);
  pending=(async()=>{
   const value=await request('/api/agent-workspaces');
   if(!Array.isArray(value?.entries))throw new Error(text('Workspace discovery returned an invalid response.'));
   const matches=value.entries.filter(e=>typeof e?.name==='string'&&e.name.trim().toLocaleLowerCase()===String(name||'').trim().toLocaleLowerCase()&&e.source_mode==='parameters'&&e.phema);
   if(matches.length!==1)throw new Error(matches.length?text('More than one matching owner entry is available. Choose it in Agent workspaces.'):String(name)+': '+text('No accessible owner entry is available.'));
   const detail=await resolve(matches[0]);
   if(present)return await present(detail);
   document.dispatchEvent(new CustomEvent('phemacast:open-result',{detail}));
  })().finally(()=>{clearIndicator();pending=null;});
  return pending;
 };
 const open=({query=''}={})=>{
  if(active){active.search.value=query;active.filter();return active.dialog;}
  const controller=new AbortController(),dialog=document.createElement('dialog');dialog.className='config-dialog';
  const busy=createOwnerBusy(dialog,{label:()=>text('Loading agent workspaces…')});
  dialog.setAttribute('aria-label',text('Agent workspaces'));
  const header=document.createElement('header');header.className='config-header';
  const title=document.createElement('h2');title.textContent=text('Agent workspaces');header.append(title);
  const close=document.createElement('button');close.type='button';close.textContent='×';close.setAttribute('aria-label',text('Close'));
  close.onclick=()=>dismiss();header.append(close);dialog.append(header);
  const main=document.createElement('div');main.className='config-dialog-body';dialog.append(main);
  const controls=document.createElement('div');controls.style.cssText='display:flex;gap:8px;align-items:center';
  const search=document.createElement('input');search.type='search';search.value=query;
  search.placeholder=text('Search agent workspaces');search.setAttribute('aria-label',text('Search agent workspaces'));
  search.style.cssText='flex:1;min-width:0';controls.append(search);
  const refresh=document.createElement('button');refresh.type='button';refresh.textContent=text('Refresh');controls.append(refresh);main.append(controls);
  const status=document.createElement('p');status.setAttribute('role','status');main.append(status);
  const list=document.createElement('div');list.style.cssText='display:flex;flex-wrap:wrap;gap:8px';main.append(list);
  let entries=[],serial=0,loading=false,error='';
  const filter=()=>{
   list.replaceChildren();
   const shown=entries.filter(entry=>entry.name.toLocaleLowerCase().includes(search.value.trim().toLocaleLowerCase()));
   status.textContent=error||(loading?text('Loading agent workspaces…'):shown.length?'':text('No matching workspace entries are available for this account.'));
   for(const entry of shown){
    const button=document.createElement('button');button.type='button';button.textContent=entry.name;
    button.title=text('Source mode: Parameters');
    button.onclick=()=>{dismiss();document.dispatchEvent(new CustomEvent('phemacast:open-result',{
     detail:{phema:entry.phema,sourceMode:entry.source_mode}}));};list.append(button);
   }
  };
  const load=async()=>{
   return busy.run(async()=>{
   const requestId=++serial;entries=[];loading=true;error='';refresh.disabled=true;filter();
   try{
    const value=await request('/api/agent-workspaces',{signal:controller.signal});
    if(!dialog.open||requestId!==serial)return;
    if(!Array.isArray(value?.entries))throw new Error(text('Workspace discovery returned an invalid response.'));
    entries=value.entries.filter(entry=>typeof entry?.name==='string'&&entry.phema&&entry.source_mode==='parameters');
   }catch(cause){if(!dialog.open||controller.signal.aborted||requestId!==serial)return;error=String(cause.message||text('Workspace discovery is unavailable.'));}
   finally{if(dialog.open&&requestId===serial){loading=false;refresh.disabled=false;filter();}}
   });
  };
  search.oninput=filter;refresh.onclick=busy.bind(refresh,load);
  const cleanup=()=>{serial++;controller.abort();busy.dispose();if(active?.dialog===dialog)active=null;dialog.remove();};
  const dismiss=()=>{dialog.close();cleanup();};
  dialog.addEventListener('cancel',event=>{event.preventDefault();dismiss();});
  dialog.addEventListener('close',cleanup,{once:true});
  active={dialog,search,filter};document.body.append(dialog);dialog.showModal();load();return dialog;
 };
 if(!showButton)return {open,openPreferred};
 if(document.querySelector('[data-agent-workspaces]'))return {open,openPreferred};
 const button=document.createElement('button');button.type='button';button.dataset.agentWorkspaces='true';
 button.className='button';button.textContent=text('Agent workspaces');
 button.style.cssText='position:fixed;right:18px;bottom:18px;z-index:80';
 document.body.append(button);
 button.onclick=()=>open();
 return {open,openPreferred};
}
