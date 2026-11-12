/** Derived owner presentation. Never edits a pinned Phema or stores user data. */
export function presentAgentViewTitle(root,{title}){
 if(!root.isConnected)return;
 const panel=root.closest('[data-panel-id]');
 const heading=panel?.querySelector('.panel-heading h3');if(heading)heading.textContent=title;
 // A view controls its panel. Only an unambiguous single-panel composition
 // carries that title through to its enclosing window and navigation caption.
 const layout=panel?.parentElement;
 if(!layout||layout.querySelectorAll(':scope > [data-panel-id]').length!==1)return;
 const surface=root.closest('[data-browser]');
 if(surface&&surface.querySelectorAll('[data-panel-id]').length!==1)return;
 if(surface){
  const caption=surface.querySelector('.browser-title-line strong')||(surface.id==='browser-surface'?surface.querySelector(':scope > header strong'):null);if(caption)caption.textContent=title;
  surface.setAttribute('aria-label',title+' browser');
  for(const button of document.querySelectorAll('.browser-nav-item,.window-tabs button')){
   if(button.dataset.id!==surface.dataset.browser)continue;
   const name=button.querySelector('strong');if(name)name.textContent=title;
   if(button.closest('.window-tabs'))for(const node of button.childNodes){
    if(node.nodeType===3&&node.textContent.trim())node.textContent=title;
    else if(node.nodeType===1&&node.matches('span[data-ui-key]')){node.textContent=title;node.removeAttribute('data-ui-key');node.removeAttribute('data-ui-args');}
   }
  }
 }else{
  const standalone=root.closest('#browser-surface');
  const caption=standalone?.querySelector(':scope > header strong');if(caption)caption.textContent=title;
 }
}

/** Keep a derived caption through container-only chrome repaint. */
export function agentViewPresentation(root){
 let current=null,observer=null,closed=false;
 const update=value=>{
  if(closed)return;current=value;presentAgentViewTitle(root,value);
  if(observer)return;
  const surface=root.closest('[data-browser]')||root.closest('#browser-surface');
  const caption=surface?.querySelector('.browser-title-line strong')||(surface?.id==='browser-surface'?surface.querySelector(':scope > header strong'):null);
  if(caption){observer=new MutationObserver(()=>{if(!closed&&root.isConnected&&current&&caption.textContent!==current.title)presentAgentViewTitle(root,current);});observer.observe(caption,{childList:true,subtree:true,characterData:true});}
 };
 return {update,dispose(){closed=true;observer?.disconnect();current=null;}};
}
