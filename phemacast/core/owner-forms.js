/** Trusted action surface for owner-authored data. The host supplies transport;
 * references are data, never URLs, HTML, script or network authority. */
import {structuredForm,structuredContent} from './structured-forms.js';

export function ownerForms(forms,{invoke,onResult=()=>{},sourceMode='parameters'}={}) {
 if(!Array.isArray(forms)||forms.length>80||typeof invoke!=='function')throw Error('Bound owner forms and trusted transport required');
 const element=document.createElement('div');element.className='owner-forms';
 for(const definition of forms){
  if(!definition||typeof definition!=='object'||!definition.inputSchema)throw Error('Owner form schema is missing');
  if(definition.automatic===true||definition.trigger&&definition.trigger!=='manual')throw Error('Owner actions require an explicit user submission');
  const binding=definition.binding||definition;
  const reference=binding.registryRef||binding.operationRef;
  if(!reference||typeof reference!=='object')throw Error('Owner operation reference is missing');
  const modes=definition.sourceModes||[binding.sourceMode||sourceMode];
  if(!modes.includes(sourceMode))continue;
  const section=document.createElement('section'),heading=document.createElement('h3'),form=document.createElement('form');
  heading.textContent=definition.title||'Action';
  const disabled=Boolean(definition.disabled||definition.disabledUntilAuthenticatedChoices);
  const inputs=structuredForm(definition.inputSchema,definition.initial||{}, {disabled});
  const submit=document.createElement('button'),status=document.createElement('p'),result=document.createElement('div');
  submit.type='submit';submit.textContent=definition.submitLabel||definition.title||'Submit';submit.disabled=disabled;
  status.setAttribute('role','status');status.textContent=disabled?(definition.disabledReason||'Authorized choices are required.') : '';
  let inFlight=false,uncertain=false;
  form.onsubmit=async event=>{
   event.preventDefault();if(disabled||inFlight||uncertain)return;
   let input;try{input=inputs.value();}catch(error){status.textContent=error.message;return;}
   inFlight=true;submit.disabled=true;status.textContent='Working…';
   try{
    const reply=await invoke({reference:structuredClone(reference),binding:structuredClone(binding),input,sourceMode});
    uncertain=reply?.status==='uncertain'||reply?.executionReplayAllowed===false&&reply?.status!=='completed';
    result.replaceChildren(structuredContent(reply));
    status.textContent=uncertain?'Delivery is uncertain. Check the owner receipt before another action.':'Complete';
    onResult(reply,{binding:structuredClone(binding),input});
   }catch(error){
    // The transport marks pre-dispatch rejections explicitly. Any other error
    // may be acknowledgement loss; preserve the same inputs and never retry.
    uncertain=error?.dispatchOccurred!==false;
    status.textContent=uncertain?'Delivery is uncertain. Check the owner receipt before another action.':error.message;
   }finally{inFlight=false;submit.disabled=disabled||uncertain;}
  };
  form.append(inputs.element,submit,status,result);section.append(heading,form);element.append(section);
 }
 return {element};
}
