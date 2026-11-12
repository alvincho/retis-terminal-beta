/* One opt-in generic Phema renderer candidate; no application adapters. */
import {AgentView,confirmationDialog} from './host.mjs';
import {createOwnerBusy} from '../../operation_forms_candidate/owner-busy.js';
const HASH=/^[a-f0-9]{64}$/;
function resolved(value){
 if(Array.isArray(value))return value.forEach(resolved);
 if(value&&typeof value==='object'){
  if(Object.keys(value).length===1&&Object.hasOwn(value,'$param'))throw Error('Resolve Phema parameters before mounting an agent view');
  Object.values(value).forEach(resolved);
 }
}
export function validateAgentViewReference(value){
 const required=['agentId','viewId','release','manifestDigest','parameters','sourceMode'],optional=['extensions'];
 if(!value||typeof value!=='object'||required.some(key=>!Object.hasOwn(value,key))||Object.keys(value).some(key=>![...required,...optional].includes(key)))throw Error('Provide a canonical agent-view reference');
 // A reference either pins one exact release or follows the release its publisher advertises now.
 const follows=value.release==='current'&&value.manifestDigest==='current';
 if(typeof value.agentId!=='string'||!value.agentId||value.agentId.length>200||typeof value.viewId!=='string'||!value.viewId||value.viewId.length>128||!follows&&(!HASH.test(value.release)||!HASH.test(value.manifestDigest)))throw Error('Pin the publisher, view, release and manifest digest');
 if(!['parameters','snapshot'].includes(value.sourceMode)||!value.parameters||Array.isArray(value.parameters)||typeof value.parameters!=='object')throw Error('Select an explicit source mode and resolved parameters');
 if(value.extensions&&Object.keys(value.extensions).some(key=>!/^([a-z][a-z0-9.-]+):[A-Za-z0-9._-]+$/.test(key)))throw Error('Preserve only namespaced reference extensions');
 if(JSON.stringify(value).length>300000)throw Error('View reference exceeds its limit');resolved(value.parameters);
 return structuredClone(value);
}
export function interfaceMountAcceptance(review){
 if(!HASH.test(review?.reviewDigest))throw Error('Invalid host review digest');
 const choices=review.deploymentApproval??review.disclosure,keys=['codeExecution','privateData','localNetwork'];
 if(!choices||keys.some(key=>typeof choices[key]!=='boolean'))throw Error('Invalid host interface disclosure');
 return {reviewDigest:review.reviewDigest,...Object.fromEntries(keys.map(key=>[key,choices[key]]))};
}
export class AgentViewNode {
 constructor(root,{request,context,compositionChannel=null,confirm=confirmationDialog,onStatus=()=>{},onMode=()=>{},onPresentation=()=>{},hostRelease=null,runtimeDigest=null,startTimeoutMs}){
  if(typeof request!=='function')throw Error('A verified host request adapter is required');
  if(hostRelease!==null&&!HASH.test(hostRelease))throw Error('Pin an approved generic host release');
  if(hostRelease!==null&&!HASH.test(runtimeDigest))throw Error('Pin the generic host runtime digest');
  this.hostRelease=hostRelease;this.runtimeDigest=runtimeDigest;
  this.request=request;this.context=context;this.createCompositionChannel=compositionChannel;this.epoch=0;
  this.busy=createOwnerBusy(root,{locale:()=>this.context?.locale||'en'});
  this.view=new AgentView(root,{context,confirm,onStatus,onMode,onPresentation,startTimeoutMs,transport:(type,value,mountId)=>{
   if(!/^[A-Za-z0-9_-]{1,200}$/.test(mountId)||!['propose','confirm','mode-propose','mode-confirm','set-mode','invoke','status','close'].includes(type))throw Error('Undeclared host view target');
   return request('/api/agent-views/mounts/'+mountId+'/'+type,value);
  }});
 }
 async reload(reference){
  return this.busy.run(async()=>{
  const value=validateAgentViewReference(reference),epoch=++this.epoch;
  let channel=null;
  if(value.extensions?.['phemacast:composition-references']){
   if(typeof this.createCompositionChannel!=='function')throw Error('This client has not installed composition reference channels.');
   channel=this.createCompositionChannel(value);await channel.initialize();
  }
  const reviewed=await this.request('/api/agent-views/reviews',{publisherAgentId:value.agentId,viewId:value.viewId,
   manifestDigest:value.manifestDigest,parameters:value.parameters,sourceMode:value.sourceMode,...(this.hostRelease?{hostRelease:this.hostRelease}:{})});
  if(epoch!==this.epoch)return false;
  const follows=value.manifestDigest==='current';
  if(follows?!HASH.test(reviewed.manifestDigest)||!HASH.test(reviewed.release):reviewed.manifestDigest!==value.manifestDigest||reviewed.release!==value.release)throw Error('Owner review differs from the pinned Phema release');
  // Opening an accepted Phema is the interface action. The host has already
  // checked publisher trust, registry contracts, assets and this user/window.
  // Individual effects still use their ordinary scoped grants and confirmation.
  const approval=interfaceMountAcceptance(reviewed);
  if(!/^[A-Za-z0-9_-]{1,200}$/.test(reviewed.reviewId))throw Error('Invalid host review receipt');
  // Results the view keeps apply to this exact release only.
  if(channel)channel.release=reviewed.manifestDigest;
  const spec=await this.request('/api/agent-views/reviews/'+reviewed.reviewId+'/approve',approval);
  if(!/^[A-Za-z0-9_-]{1,200}$/.test(spec.mountId))throw Error('Invalid host mount receipt');
  if(epoch!==this.epoch){await this.request('/api/agent-views/mounts/'+spec.mountId+'/close',{});return false;}
  if(spec.manifestDigest!==reviewed.manifestDigest)throw Error('Mounted view differs from the reviewed exact release');
  if(this.hostRelease&&(spec.hostRelease!==this.hostRelease||spec.runtime?.digest!==this.runtimeDigest)){await this.request('/api/agent-views/mounts/'+spec.mountId+'/close',{});throw Error('Mounted generic host or runtime differs from the selected release');}
  this.view.compositionChannel?.close();this.view.compositionChannel=channel;
  await this.view.mount(spec);this.reference=value;return true;
  });
 }
 pushEvent(value){this.view.pushEvent(value);}
 updateContext(value){this.context=value;this.view.updateContext(value);}
 dispose(){this.epoch++;this.view.dispose();this.busy.dispose();}
}
