/** Confined views may use only references already declared in their mounted Phema.
 * The ordinary authenticated composition endpoint remains the authority. This
 * adapter accepts no URL, target, identity, credential or new scope from code.
 */
import {sha256} from './host.mjs';
const stable=value=>value&&typeof value==='object'?Array.isArray(value)?value.map(stable):Object.fromEntries(Object.keys(value).sort().map(key=>[key,stable(value[key])])):value;
const encoded=value=>JSON.stringify(stable(value));
const HASH=/^[a-f0-9]{64}$/;
const failures={
 'authentication-required':'Source authentication expired. Reload this Phema to refresh its session.',
 'access-denied':'Source access was denied. Check the current account and source permissions.',
 'request-rejected':'The source rejected the request.',
 'input-rejected':'The source rejected the supplied inputs.',
 'request-conflict':'The source reports a conflicting or unfinished request. Check its result before retrying.',
 'source-unavailable':'The source is unavailable.',
 'source-busy':'The source is busy. Wait for its current request to finish.',
 'source-timeout':'The source timed out. Check its result before retrying.',
 'source-failed':'The source failed before returning a verified result.',
};
const scopeParts=state=>[state.parameterValues||{},state.instrumentOverrides||{},state.contentOverrides||{},state.entry?.design?.params||{}];
const mountScope=state=>encoded([state.generation??null,...scopeParts(state)]);
const STALE='The selected Phema has changed or closed. Reload this view.';
const NAME=/^[A-Za-z][A-Za-z0-9_]{0,63}$/;
const transient=error=>[408,425,429,500,502,503,504].includes(error?.status)||
 error?.name==='TypeError'&&/fetch|network|load failed/i.test(error.message||'');
function noSecrets(value){
 if(Array.isArray(value)){value.forEach(noSecrets);return;}
 if(value&&typeof value==='object')for(const [key,child] of Object.entries(value)){
  if(/^(?:__proto__|prototype|constructor|password|secret|token|access_token|refresh_token|authorization|cookie|api_key|caller_.*token)$/i.test(key))throw Error('Credentials and transport identities are not view inputs.');
  noSecrets(child);
 }
}
function content(phema,path){
 let value=phema;
 for(const component of path){const composition=value?.sections?.flatMap(section=>section.content||[]).find(item=>item.type==='phemacast:composition');value=composition?.components?.find(item=>item.id===component)?.phema;}
 const composition=value?.sections?.flatMap(section=>section.content||[]).find(item=>item.type==='phemacast:composition');
 if(!composition)throw Error('Mounted composition is unavailable.');return composition;
}
export class CompositionViewChannel{
 constructor({request,instance,prefix='',reference,timeoutMs=255000,onStale=()=>{},parameters=null}){
  if(typeof request!=='function'||typeof instance!=='function')throw Error('Actual container adapters are required.');
  this.request=request;this.instance=instance;this.onStale=onStale;this.updateParameters=parameters;this.prefix=prefix;this.reference=structuredClone(reference);this.timeoutMs=timeoutMs;
  const declaration=reference.extensions?.['phemacast:composition-references'];
  if(declaration?.version!=='1.0.0'||!Array.isArray(declaration.bindings)||declaration.bindings.length>32||!declaration.bindings.length)throw Error('A bounded declared reference channel is required.');
  this.bindings=new Map();for(const row of declaration.bindings){
   if(Object.keys(row).sort().join(',')!=='id,schemaDigest'||!/^[-A-Za-z0-9_]{1,100}$/.test(row.id)||!HASH.test(row.schemaDigest)||this.bindings.has(row.id))throw Error('Invalid declared reference binding.');
   this.bindings.set(row.id,row.schemaDigest);
  }
  this.pending=new Map();this.tail=Promise.resolve();this.closed=false;this.phemaDigest=null;this.scopeDigest=null;this.mountDigest=null;
  this.replyTimeoutMs=Math.min(270000,this.timeoutMs+15000);
 }
 async initialize(){
  let selected;
  const deadline=Date.now()+30000;
  while(!selected?.remoteId){
   const current=this.instance();
   if(this.closed||selected&&current!==selected)throw this.stale();
   if(current?.cancelled||current?.state?.status==='unavailable')throw Error(current?.state?.error||'The Phema could not initialize.');
   if(current)selected=current;
   if(selected?.remoteId)break;
   if(Date.now()>=deadline)throw Error('The Phema did not finish initializing. Reload this view.');
   await new Promise(resolve=>setTimeout(resolve,50));
  }
  this.remoteId=selected.remoteId;
  const state=await this.read();this.phemaDigest=await sha256(encoded(state.phema));this.scopeDigest=await sha256(mountScope(state));
  this.mountDigest=await sha256(encoded(scopeParts(state)));
  await this.verify(state);
 }
 async verify(state){
  const node=content(state.phema,this.prefix?this.prefix.split('/').filter(Boolean):[]);
  for(const [id,hash] of this.bindings){
   const ref=node.references?.find(row=>row.id===id);
   if(!ref||ref.kind!=='pulse'||ref.target.agentId!==this.reference.agentId||ref.refresh?.trigger!=='manual'||!ref.interaction||
    await sha256(encoded(ref.interaction.inputSchema))!==hash)throw Error('The mounted reference differs from the declared owner view.');
  }
 }
 async queued(task){
  const next=this.tail.catch(()=>{}).then(task);this.tail=next;
  try{return await next;}finally{if(this.tail===next)this.tail=Promise.resolve();}
 }
 paths(){return new Set([...this.bindings.keys()].map(id=>(this.prefix?this.prefix+'/':'')+id));}
 /** This instance's Phema parameter values, kept by the Terminal. */
 async parameters(){return this.queued(async()=>({values:structuredClone((await this.read()).parameterValues||{})}));}
 /** Set named parameter values (null removes one). The Terminal saves them with its workspace. */
 async setParameters(values){
  if(this.closed)throw this.stale();
  if(!values||typeof values!=='object'||Array.isArray(values)||!Object.keys(values).length||Object.keys(values).length>64||Object.keys(values).some(key=>!NAME.test(key)))throw Error('Send named Phema parameter values.');
  if(encoded(values).length>280000)throw Error('Parameter values exceed the bound.');noSecrets(values);
  if(typeof this.updateParameters!=='function')throw Error('This Terminal cannot save view parameters.');
  return this.queued(async()=>{
   const before=await this.read(),expected={...(before.parameterValues||{})};
   for(const [key,value] of Object.entries(values)){if(value===null)delete expected[key];else expected[key]=structuredClone(value);}
   await this.updateParameters(structuredClone(values));
   return {values:await this.adopt(expected)};
  });
 }
 async adopt(expected){
  // The Terminal re-runs the instance with the new values; accept only that change.
  const deadline=Date.now()+30000,want=encoded(expected);
  for(;;){
   if(this.closed)throw this.stale();
   const current=await this.current();
   const value=await this.request('/api/instances/'+encodeURIComponent(current.remoteId));
   const state=value.state||value;
   if((state.sourceMode||'parameters')!==this.reference.sourceMode)throw this.stale('The Phema source mode changed. Reload this view.');
   if(encoded(state.parameterValues||{})===want){
    await this.verify(state);
    this.remoteId=current.remoteId;this.phemaDigest=await sha256(encoded(state.phema));
    this.scopeDigest=await sha256(mountScope(state));this.mountDigest=await sha256(encoded(scopeParts(state)));
    return structuredClone(state.parameterValues||{});
   }
   if(Date.now()>=deadline)throw this.stale('The Terminal did not apply the parameter values. Reload this view.');
   await new Promise(resolve=>setTimeout(resolve,250));
  }
 }
 /** Keep one exact result this view received, as a content snapshot of its instance. */
 async saveSnapshot(value){
  if(!value||typeof value!=='object'||Object.keys(value).some(key=>!['compositionReference','receipt','label'].includes(key))||!this.bindings.has(value.compositionReference))throw Error('Save a result of a declared composition reference.');
  if(typeof value.receipt!=='string'||!value.receipt||value.receipt.length>200)throw Error('Name the exact result to save by its receipt.');
  const label=value.label===undefined?undefined:String(value.label).trim();
  if(label!==undefined&&(!label||label.length>120||/[\u0000-\u001f\u007f]/.test(label)))throw Error('Use a short plain label.');
  const path=(this.prefix?this.prefix+'/':'')+value.compositionReference;
  return this.queued(async()=>{
   await this.read();
   const saved=await this.request('/api/instances/'+encodeURIComponent(this.remoteId)+'/content',{method:'POST',
    body:{operation:'capture',references:[path],receipt:value.receipt,...(label?{label}:{})}});
   return {snapshotId:saved.snapshotId};
  });
 }
 /** Results this view keeps for itself in this Terminal under short names: null when none, or when the Phema or
  * this view's release changed since. Unlike parameters, keeping one re-runs nothing. */
 async keptRead(value){return this.kept('read',value);}
 async keptWrite(value){
  if(encoded(value?.value??null).length>300000)throw Error('A kept view result is limited to 300 KB.');noSecrets(value?.value);
  return this.kept('write',value);
 }
 async keptDrop(value){return this.kept('drop',value);}
 async kept(operation,value){
  if(this.closed)throw this.stale();
  const key=value?.key;if(typeof key!=='string'||!/^[A-Za-z0-9_.:-]{1,128}$/.test(key))throw Error('Name the kept result.');
  return this.request('/api/instances/'+encodeURIComponent(this.remoteId)+'/kept',{method:'POST',
   body:{operation,key,...(operation==='write'?{value:value.value}:{}),...(this.release?{release:this.release}:{})}});
 }
 /** Results this view saved for its instance's Phema, newest first. */
 async snapshots(){
  return this.queued(async()=>{
   await this.read();
   const value=await this.request('/api/instances/'+encodeURIComponent(this.remoteId)+'/content'),paths=this.paths();
   return {snapshots:(value.snapshots||[]).filter(row=>row.references?.length&&row.references.every(path=>paths.has(path)))
    .map(row=>({id:row.id,createdAt:row.createdAt,label:row.label??null,references:row.references.map(path=>path.slice(this.prefix?this.prefix.length+1:0))}))};
  });
 }
 async readSnapshot(value){
  const id=value?.id;if(typeof id!=='string'||!/^[A-Za-z0-9_-]{1,128}$/.test(id))throw Error('Choose a saved result.');
  return this.queued(async()=>{
   await this.read();
   const saved=await this.request('/api/instances/'+encodeURIComponent(this.remoteId)+'/content',{method:'POST',body:{operation:'view',snapshotId:id}});
   const paths=this.paths(),results=Object.fromEntries(Object.entries(saved.results||{}).filter(([path])=>paths.has(path))
    .map(([path,result])=>[path.slice(this.prefix?this.prefix.length+1:0),result]));
   if(!Object.keys(results).length)throw Error('That saved result belongs to another view.');
   noSecrets(results);
   return {id,label:saved.label??null,capturedAt:saved.capturedAt??null,results};
  });
 }
 /** Ask this Terminal to call one declared reference on a timer, as this viewer, even with no view open.
  * Starting a run with an existing key replaces its inputs and keeps its carry-over value. */
 async schedule(value){
  const fields=['compositionReference','key','label','everySeconds','input','keyField','carry'];
  if(!value||typeof value!=='object'||Object.keys(value).some(key=>!fields.includes(key))||!this.bindings.has(value.compositionReference))throw Error('Schedule a declared composition reference.');
  if(encoded(value.input??null).length>200000||encoded(value.carry??null).length>200000)throw Error('Run inputs exceed the bound.');
  noSecrets(value.input);noSecrets(value.carry);
  const {compositionReference,...run}=value;
  return this.queued(async()=>{
   await this.read();
   return this.visible(await this.request(this.runsPath(),{method:'POST',body:{...run,reference:this.pathOf(compositionReference)}}));
  });
 }
 /** This view's runs: those of its own declared references, with each one's latest result. */
 async runs(){
  return this.queued(async()=>{
   await this.read();
   const value=await this.request(this.runsPath()),paths=this.paths();
   return {runs:(value.runs||[]).filter(run=>paths.has(run.reference)).map(run=>this.visible(run))};
  });
 }
 /** One call of a running run now, with this call's inputs; its result continues the run's carry-over. */
 async runNow(value){
  if(!value||typeof value!=='object'||Object.keys(value).some(key=>!['id','input'].includes(key))||!/^[a-f0-9]{32}$/.test(value.id||''))throw Error('Choose a run of this view.');
  if(!value.input||typeof value.input!=='object'||Array.isArray(value.input)||encoded(value.input).length>200000)throw Error('Send the call inputs as one bounded object.');
  noSecrets(value.input);
  return this.queued(async()=>{
   await this.read();
   return this.visible(await this.request(this.runsPath()+'/'+value.id+'/now',{method:'POST',body:{input:value.input}}));
  });
 }
 /** Stop a run; `forget` also removes it with its carry-over and latest result. */
 async stopRun(value){
  if(!value||typeof value!=='object'||Object.keys(value).some(key=>!['id','forget'].includes(key))||!/^[a-f0-9]{32}$/.test(value.id||''))throw Error('Choose a run of this view.');
  return this.queued(async()=>{
   await this.read();
   return this.visible(await this.request(this.runsPath()+'/'+value.id+'/stop',{method:'POST',body:{forget:value.forget===true}}));
  });
 }
 runsPath(){return '/api/instances/'+encodeURIComponent(this.remoteId)+'/runs';}
 pathOf(id){return (this.prefix?this.prefix+'/':'')+id;}
 visible(run){
  if(run?.reference&&!this.paths().has(run.reference))throw Error('That run belongs to another view.');
  noSecrets(run);
  const {reference,...rest}=run||{};
  return reference?{...rest,compositionReference:reference.slice(this.prefix?this.prefix.length+1:0)}:rest;
 }
 stale(message=STALE){this.onStale();return Error(message);}
 async current(){
  // The Terminal may replace this panel's instance, e.g. after a restart. Wait for the replacement.
  const deadline=Date.now()+30000;
  for(;;){
   const current=this.instance();
   if(this.closed||current?.cancelled)throw this.stale();
   if(current?.remoteId)return current;
   if(current?.state?.status==='unavailable'||Date.now()>=deadline)throw this.stale();
   await new Promise(resolve=>setTimeout(resolve,250));
  }
 }
 async rebind(current){
  // Reconnect only to the same Phema with the same mounted parameters, and never
  // while a dispatched call's receipt is still owed by the replaced instance.
  if(!this.phemaDigest||[...this.pending.values()].some(record=>record.dispatched))throw this.stale();
  const value=await this.request('/api/instances/'+encodeURIComponent(current.remoteId));
  const state=value.state||value;
  if(this.closed||this.instance()?.remoteId!==current.remoteId||(state.sourceMode||'parameters')!==this.reference.sourceMode||
   await sha256(encoded(state.phema))!==this.phemaDigest||await sha256(encoded(scopeParts(state)))!==this.mountDigest)throw this.stale();
  this.remoteId=current.remoteId;this.scopeDigest=await sha256(mountScope(state));
 }
 async read(){
  if(this.closed)throw this.stale();
  const current=await this.current();
  if(current.remoteId!==this.remoteId)await this.rebind(current);
  const value=await this.request('/api/instances/'+encodeURIComponent(this.remoteId));
  const state=value.state||value;
  if((state.sourceMode||'parameters')!==this.reference.sourceMode)throw this.stale('The Phema source mode changed. Reload this view.');
  if(this.phemaDigest&&await sha256(encoded(state.phema))!==this.phemaDigest)throw this.stale('The Phema changed. Reload this view before another operation.');
  if(this.scopeDigest&&await sha256(mountScope(state))!==this.scopeDigest)throw this.stale('The mounted parameters or generation changed. Reload this view before another operation.');
  return state;
 }
 async invoke(message){
  // References share the instance's interaction lifecycle. Waiting requests
  // do not start a host deadline or prepare another active intent.
  const next=this.tail.catch(()=>{}).then(()=>this.invokeSelected(message));this.tail=next;
  try{return await next;}finally{if(this.tail===next)this.tail=Promise.resolve();}
 }
 async invokeSelected(message){
  if(this.closed||!message||Object.keys(message).sort().join(',')!=='compositionReference,input,sourceMode'||
   message.sourceMode!=='parameters'||message.sourceMode!==this.reference.sourceMode||!this.bindings.has(message.compositionReference))throw Error('Undeclared composition interaction.');
  if(encoded(message.input).length>280000)throw Error('Interaction inputs exceed the bound.');noSecrets(message.input);
  const id=message.compositionReference,path=(this.prefix?this.prefix+'/':'')+id;
  if(this.pending.has(id))throw Error('The previous interaction is still processing. Try again when it finishes.');
  const before=await this.read();const prior=encoded(before.content?.results?.[path]||{});
  const inputDigest=await sha256(encoded(message.input));
  const record={input:structuredClone(message.input),dispatched:false,processing:false};this.pending.set(id,record);
  try{
   const deadline=Date.now()+this.timeoutMs;
   // A lost POST acknowledgement cannot prove that nothing was dispatched.
   // Observe its exact receipt; never send the mutation again.
   record.dispatched=true;
   try{await this.request('/api/instances/'+encodeURIComponent(this.remoteId)+'/content',{
    method:'POST',body:{operation:'interact',references:[path],inputs:message.input}});}
   catch(error){
    if(!transient(error)){if([400,401,403,404,409,413,422].includes(error?.status))this.pending.delete(id);throw error;}
   }
   while(Date.now()<deadline){
    let state;
    try{state=await this.read();}
    catch(error){if(!transient(error))throw error;await new Promise(resolve=>setTimeout(resolve,250));continue;}
    const result=state.content?.results?.[path];
    if(result?.status==='resolving')record.processing=true;
    if(result?.status==='ready'&&result.value?.channel_receipt===message.input.request_key){this.pending.delete(id);noSecrets(result.value);if(result.value.error)throw Error(result.value.error);return result.value;}
    if(['blocked','failed','unavailable'].includes(result?.status)){
     // Updated resolvers bind all outcomes. Retain compatibility with older
     // resolvers, but never attribute a different request's outcome to this one.
     const exact=HASH.test(result.interactionDigest||'')?result.interactionDigest===inputDigest:
      record.processing||encoded(result)!==prior;
     if(exact){
      if(!['source-failed','source-timeout','request-conflict'].includes(result.reason))this.pending.delete(id);
      throw Error(failures[result.reason]||result.reason||result.error||'The publisher rejected this operation.');
     }
    }
    await new Promise(resolve=>setTimeout(resolve,250));
   }
   throw Error('Delivery is uncertain. Check the owning agent history before another action.');
  }catch(error){error.dispatchOccurred=record.dispatched;throw error;}
  // A settled call never blocks later ones. An uncertain request is reported and
  // never resent; each new request carries its own key, which the publisher
  // deduplicates. Only a call still in flight holds the link.
  finally{this.pending.delete(id);}
 }
 close(){this.closed=true;}
}
