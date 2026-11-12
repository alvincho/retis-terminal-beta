/** Discardable form state during a panel repaint; never durable application state. */
const forbidden=/password|secret|token|api.?key|credential|__proto__|constructor|prototype/i;
function signature(field){
 return JSON.stringify([field.name,field.tagName,field.type,field.multiple,
  field.dataset.valueType,field.dataset.itemType,field.dataset.lines,
  field.min,field.max,field.minLength,field.maxLength,field.pattern]);
}
function editable(field){
 return field.matches('[data-structured-field]')&&!field.disabled&&!field.readOnly&&
  !['hidden','password','file'].includes(field.type)&&field.dataset.constant!=='true'&&!forbidden.test(field.name);
}
function interaction(form){
 return form.closest('[data-content-interaction]')?.dataset.contentInteraction||form.dataset.phemaInteraction;
}
export function captureOperationDrafts(panel){
 return [...panel.querySelectorAll('form[data-phema-interaction]')].map(form=>({
  interaction:interaction(form),
  fields:[...form.querySelectorAll('[data-structured-field]')].filter(editable).map(field=>({
   signature:signature(field),value:field.value,checked:field.checked,
   selected:field.multiple?[...field.selectedOptions].map(option=>option.value):null,
   focused:field===panel.ownerDocument.activeElement,
   start:field.selectionStart,end:field.selectionEnd
  }))}));
}
export function restoreOperationDrafts(panel,drafts){
 [...panel.querySelectorAll('form[data-phema-interaction]')].forEach(form=>{
  const matching=drafts.filter(draft=>draft.interaction===interaction(form));
  if(matching.length!==1)return;
  for(const field of form.querySelectorAll('[data-structured-field]')){
   if(!editable(field))continue;
   const saved=matching[0].fields.find(value=>value.signature===signature(field));if(!saved)continue;
   if(field.tagName==='SELECT'){
    const values=new Set([...field.options].map(option=>option.value));
    if((saved.selected||[saved.value]).some(value=>!values.has(value)))continue;
    if(field.multiple)for(const option of field.options)option.selected=saved.selected.includes(option.value);
    else field.value=saved.value;
   }else if(field.type==='checkbox')field.checked=saved.checked;
   else field.value=saved.value;
   if(saved.focused){
    field.focus({preventScroll:true});
    if(typeof saved.start==='number'&&typeof field.setSelectionRange==='function')
     try{field.setSelectionRange(saved.start,saved.end);}catch{}
   }
  }
 });
}
