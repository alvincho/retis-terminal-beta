/** Resolve one declared, effect-free, empty-input owner entry through host grants. */
export async function resolveWorkspaceEntry({entry,request,toEntry,workspaceId,signal}){
 const content=entry?.phema?.sections?.flatMap(s=>s.content||[]).filter(s=>s.type==='phemacast:composition');
 const refs=content?.length===1?content[0].references:[];
 const ref=refs?.length===1?refs[0]:null;
 const effects=ref?.extensions?.['phemacast:operation-effects'];
 const schema=ref?.interaction?.inputSchema;
 const empty=schema?.type==='object'&&schema.additionalProperties===false&&Object.keys(schema.properties||{}).length===0&&!(schema.required||[]).length;
 if(entry?.source_mode!=='parameters'||!ref||ref.kind!=='pulse'||!ref.extensions?.['phemacast:operation-binding']||
    !effects||Object.keys(effects).length!==5||Object.values(effects).some(v=>v!==false)||!empty||Object.keys(ref.inputs||{}).length)
  throw new Error('The declared entry must be a protected, effect-free read with no inputs.');
 const check=()=>{if(signal?.aborted)throw new DOMException('Entry opening cancelled.','AbortError');};
 let mount;
 try{
  check();
  // Do not abort creation mid-response: retain its identity so cleanup always runs.
  mount=await request('/api/instances',{method:'POST',body:{entry:toEntry(entry.phema),command:'CONTENT',sourceMode:'parameters',workspaceId}});
  if(!mount?.id)throw new Error('Entry mount returned no identity.');
  check();
  const capability=mount.content?.transportCapability;
  if(capability?.id!=='phemacast:neutral-operation-form'||!['1.0.0','1.1.0'].includes(capability.version))throw new Error('Protected entry transport is unavailable.');
  const path='/api/instances/'+encodeURIComponent(mount.id)+'/content';
  const intent=crypto.randomUUID().replaceAll('-','');
  const prepared=await request(path,{method:'POST',body:{operation:'prepare-interaction',references:[ref.id],inputs:{},requestKey:intent}});
  check();
  if(prepared.intentId!==intent||prepared.state!=='prepared'||!/^[a-f0-9]{64}$/.test(prepared.wireDigest))throw new Error('Entry preparation could not be verified.');
  const result=await request(path,{method:'POST',body:{operation:'invoke-interaction',references:[ref.id],requestKey:intent,wireDigest:prepared.wireDigest}});
  check();
  if(result.intentId!==intent||result.wireDigest!==prepared.wireDigest||result.state!=='complete')throw new Error('Entry result could not be verified.');
  const value=result.value;
  if(!value?.phema||value.resolved===false||!['parameters','snapshot'].includes(value.source_mode))throw new Error('The owner did not return a resolved Phema.');
  toEntry(value.phema); // Validate composition before the visible workspace changes.
  return {phema:value.phema,sourceMode:value.source_mode};
 }finally{
  if(mount?.id)await request('/api/instances/'+encodeURIComponent(mount.id),{method:'DELETE'});
 }
}
