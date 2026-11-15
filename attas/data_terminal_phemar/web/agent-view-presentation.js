/** Derived owner presentation. Never edits a pinned Phema or stores user data. */
/** A view's status in its panel heading, in place of the container's own label while set; null restores the label. */
export function presentAgentViewStatus(root,status){
 if(!root.isConnected)return;
 const heading=root.closest('[data-panel-id]')?.querySelector(':scope > .panel-heading');if(!heading)return;
 const label=heading.querySelector(':scope > .muted:not([data-view-status])');
 let note=heading.querySelector(':scope > [data-view-status]');
 if(!status){note?.remove();if(label)label.hidden=false;return;}
 if(!note){note=document.createElement('span');note.dataset.viewStatus='';note.setAttribute('role','status');if(label)label.after(note);else heading.append(note);}
 note.className='muted view-status';note.dataset.tone=status.tone==='warning'?'warning':'info';
 note.textContent=status.text;note.title=status.text;
 if(label)label.hidden=true;
}
export function presentAgentViewTitle(root,{title}){
 if(!title||!root.isConnected)return;
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
    // The view's caption replaces the Phema title's label; the mark lets the view's next caption (say, after a
    // language change) find it again.
    else if(node.nodeType===1&&node.matches('span[data-ui-key],span[data-view-caption]')){node.textContent=title;node.removeAttribute('data-ui-key');node.removeAttribute('data-ui-args');node.dataset.viewCaption='';}
   }
  }
 }else{
  const standalone=root.closest('#browser-surface');
  const caption=standalone?.querySelector(':scope > header strong');if(caption)caption.textContent=title;
 }
}

/** Keep a derived caption through container-only chrome repaint. */
export function agentViewPresentation(root){
 let current=null,observer=null,headingObserver=null,closed=false;
 const update=value=>{
  if(closed)return;current={...(current||{}),...value};
  if(value.title)presentAgentViewTitle(root,current);
  if('status' in value){
   presentAgentViewStatus(root,current.status);
   // A repainted heading gets the view's status back while it is set.
   const panel=root.closest('[data-panel-id]');
   if(panel&&!headingObserver){headingObserver=new MutationObserver(()=>{
    if(closed||!root.isConnected||!current?.status)return;
    const heading=panel.querySelector(':scope > .panel-heading'),note=heading?.querySelector(':scope > [data-view-status]');
    if(heading&&(!note||note.textContent!==current.status.text))presentAgentViewStatus(root,current.status);
   });headingObserver.observe(panel,{childList:true,subtree:true,characterData:true});}
  }
  if(observer||!current.title)return;
  const surface=root.closest('[data-browser]')||root.closest('#browser-surface');
  const caption=surface?.querySelector('.browser-title-line strong')||(surface?.id==='browser-surface'?surface.querySelector(':scope > header strong'):null);
  if(caption){observer=new MutationObserver(()=>{if(!closed&&root.isConnected&&current?.title&&caption.textContent!==current.title)presentAgentViewTitle(root,current);});observer.observe(caption,{childList:true,subtree:true,characterData:true});}
 };
 return {update,dispose(){closed=true;observer?.disconnect();headingObserver?.disconnect();if(current?.status&&root.isConnected)presentAgentViewStatus(root,null);current=null;}};
}
