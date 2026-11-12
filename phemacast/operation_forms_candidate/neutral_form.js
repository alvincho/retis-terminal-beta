import {readStructuredFields} from './structured-operation-fields.js';
/** Uninstalled generic two-phase form transport. No automatic write replay. */
import {customHTML,mountCustom} from './composition-renderers.js';
import {createOwnerBusy} from './owner-busy.js';
export const OPERATION_FORM={id:'phemacast:neutral-operation-form',version:'1.1.0'};
const pending=new Map();
const feedback=new WeakMap();
function operationFeedback(form,text){if(!feedback.has(form))feedback.set(form,createOwnerBusy(form,{label:()=>text('Running operation…')}));return feedback.get(form);}
function clearPreviousOutput(form){
 const output=form.querySelector('.composition-interaction-result');
 if(output)output.replaceChildren();
}
function displayOutput(form,value,text){
 const output=form.querySelector('.composition-interaction-result');if(!output)return;
 let html=null;
 if(value&&typeof value==='object'&&value.values&&!Array.isArray(value.values))html=customHTML({id:'phemacast:display.key-values',version:'1.0.0'},value);
 else if(Array.isArray(value?.columns)&&Array.isArray(value?.rows))html=customHTML({id:'phemacast:display.table',version:'1.0.0'},value);
 if(html!==null){output.innerHTML=html;mountCustom(output);}
 else output.textContent=text('Operation completed.');
 const navigation=html!==null?[]:Array.isArray(value?.navigation)?value.navigation: value?.phema?[{label:'Open Phema',phema:value.phema,source_mode:value.source_mode}]:[];
 for(const item of navigation.slice(0,4)){
  if(!item.phema||!['parameters','snapshot'].includes(item.source_mode))continue;
  const button=document.createElement('button');button.type='button';button.textContent=text(item.label||'Open Phema');
  button.onclick=()=>form.dispatchEvent(new CustomEvent('phemacast:open-result',{bubbles:true,detail:{phema:item.phema,sourceMode:item.source_mode}}));
  output.append(button);
 }

}
export function operationBinding(phema,path){
 const [id,...tail]=path.split('/');
 const content=(phema?.sections||[]).flatMap(s=>s.content||[]).find(n=>n.type==='phemacast:composition');
 if(tail.length){const child=content?.components?.find(c=>c.id===id);return child?operationBinding(child.phema,tail.join('/')):null;}
 return content?.references?.find(r=>r.id===id)?.extensions?.['phemacast:operation-binding']||null;
}
export function formInputs(form){
 if(form.querySelector('[data-structured-field]'))return readStructuredFields(form);
 const inputs={};
 for(const field of form.querySelectorAll('[name]')){
  if(field.disabled)continue;
  if(field.type==='checkbox')inputs[field.name]=field.checked;
  else if(field.value!=='')inputs[field.name]=['number','integer','boolean','array','object'].includes(field.dataset.valueType)?JSON.parse(field.value):field.value;
 }
 return inputs;
}
export async function submitOperationForm({form,reference,instance,request,setStatus,text=x=>x}){
 const binding=operationBinding(instance.state?.phema,reference);
 if(!binding)return false;
 const capability=instance.state?.content?.transportCapability;
 if(binding.schema!=='phemacast:pulse-operation-binding/1'||capability?.id!==OPERATION_FORM.id||!['1.0.0','1.1.0'].includes(capability?.version))
  throw new Error(text('This host does not support protected operation forms.'));
 const status=form.querySelector('[role=status]'),submit=form.querySelector('[type=submit]');
 const scope=instance.remoteId+'/'+reference,route='/api/instances/'+instance.remoteId+'/content';
 const busy=operationFeedback(form,text);
 const show=value=>setStatus(status,value),send=body=>busy.run(()=>request(route,{method:'POST',body}),{control:submit});
 let record=pending.get(scope)||restorePending(instance,reference);
 submit.disabled=true;
 clearPreviousOutput(form);
 try{
  if(record){show(text('Check the previous operation status before submitting again.'));return true;}
  record={requestKey:crypto.randomUUID().replaceAll('-',''),wireDigest:null,state:'preparing'};
  pending.set(scope,record);show(text('Preparing operation…'));
  const prepared=await send({operation:'prepare-interaction',references:[reference],inputs:formInputs(form),requestKey:record.requestKey});
  if(prepared.intentId!==record.requestKey||!/^[a-f0-9]{64}$/.test(prepared.wireDigest))throw new Error(text('Operation preparation could not be verified.'));
  record.wireDigest=prepared.wireDigest;
  if(prepared.state!=='prepared'){record.state='uncertain';show(text('Outcome unavailable. Check status; do not resend.'));return true;}
  // The original caller knows intent + exact wire before the only invocation.
  record.state='uncertain';show(text('Running operation…'));
  const result=await send({operation:'invoke-interaction',references:[reference],requestKey:record.requestKey,wireDigest:record.wireDigest});
  if(result.intentId!==record.requestKey||result.wireDigest!==record.wireDigest)throw new Error(text('Operation acknowledgement could not be verified.'));
  if(result.state==='complete'){
   show(text('Complete.'));pending.delete(scope);
   displayOutput(form,result.value,text);
  }else show(text('Outcome unavailable. Check status; do not resend.'));
  return true;
 }catch(error){
  if(record?.wireDigest){record.state='uncertain';show(text('Outcome unavailable. Check status; do not resend.'));}
  else{pending.delete(scope);show(text('Preparation failed. No operation was dispatched.')+' '+String(error.message||''));}
  return true;
 }finally{
  submit.disabled=pending.has(scope);
  mountOperationRecovery({form,reference,instance,request,setStatus,text});
 }
}

function restorePending(instance,reference){
 const scope=instance.remoteId+'/'+reference;
 const records=instance.state?.content?.pendingInteractions?.[reference];
 if(!Array.isArray(records)||records.length!==1)return null;
 const known=records[0];
 if(known.reference!==reference||!/^[A-Za-z0-9_-]{16,128}$/.test(known.requestKey)||!/^[a-f0-9]{64}$/.test(known.wireDigest))return null;
 const record={requestKey:known.requestKey,wireDigest:known.wireDigest,state:'uncertain'};
 pending.set(scope,record);return record;
}
export function mountOperationRecovery({form,reference,instance,request,setStatus,text=x=>x}){
 if(!operationBinding(instance.state?.phema,reference))return;
 const scope=instance.remoteId+'/'+reference,route='/api/instances/'+instance.remoteId+'/content';
 const known=pending.get(scope)||restorePending(instance,reference);if(!known)return;
 clearPreviousOutput(form);
 const submit=form.querySelector('[type=submit]'),status=form.querySelector('[role=status]');
 if(!submit||!status)return;
 submit.disabled=true;
 const busy=operationFeedback(form,text);
 const show=value=>setStatus(status,value),send=(body,control)=>busy.run(()=>request(route,{method:'POST',body}),{control});
 show(text('Outcome unavailable. Check status; do not resend.'));
 if(pending.get(scope)?.wireDigest&&!form.querySelector('[data-operation-status]')){
   const check=document.createElement('button');check.type='button';check.dataset.operationStatus='true';check.textContent=text('Check status');
   check.onclick=async()=>{
    const known=pending.get(scope);if(!known)return;
    check.disabled=true;
    try{
     const observed=await send({operation:'observe-interaction',references:[reference],requestKey:known.requestKey,wireDigest:known.wireDigest},check);
     if(observed.intentId!==known.requestKey||observed.wireDigest!==known.wireDigest||observed.executionReplayAllowed!==false)throw new Error('Unknown receipt');
     displayOutput(form,observed.receipt,text);
     show(text('Status checked. No operation was resent.'));
     if(['complete','completed','failed','cancelled'].includes(observed.receipt?.state)){
      if(instance.state?.content?.pendingInteractions)delete instance.state.content.pendingInteractions[reference];
      pending.delete(scope);submit.disabled=false;check.remove();
     }
    }catch{show(text('Status is unavailable. The operation will not be resent.'));}
    finally{check.disabled=false;}
   };
   status.after(check);
  }
}
