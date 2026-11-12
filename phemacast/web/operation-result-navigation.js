/** Explicit owner-result navigation. Supplied content never installs code. */
export function navigationHTML(value,escape){
 const items=Array.isArray(value?.navigation)?value.navigation:value?.phema?[{label:'View Content',phema:value.phema,source_mode:value.source_mode}]:[];
 return items.slice(0,4).filter(item=>item?.phema&&['parameters','snapshot'].includes(item.source_mode))
  .map(item=>`<button type="button" data-phema-result="${escape(JSON.stringify({phema:item.phema,sourceMode:item.source_mode}))}">${escape(item.label||'View Content')}</button>`).join('');
}
export function mountNavigation(root){
 for(const button of root.querySelectorAll('[data-phema-result]:not([data-result-mounted])')){
  button.dataset.resultMounted='true';button.onclick=()=>{
   const result=JSON.parse(button.dataset.phemaResult);
   button.dispatchEvent(new CustomEvent('phemacast:open-result',{bubbles:true,detail:result}));
  };
 }
}
