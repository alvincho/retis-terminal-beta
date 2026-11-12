/** The container binds an existing instance and reference; owner data chooses no URL. */
export function installControlSurfaceHost({request,findInstance,adoptPhema=null}){
 if(typeof request!=='function'||typeof findInstance!=='function')throw Error('Verified host request and instance adapters required.');
 const listener=async event=>{
  const detail=event.detail,container=event.target.closest('[data-content-interaction]');
  if(!container||!detail||typeof detail.reference!=='string'||typeof detail.resolve!=='function'||typeof detail.reject!=='function')return;
  event.stopPropagation();const browserId=container.dataset.contentInteraction.split('/')[0],instance=findInstance(browserId);
  if(!instance?.remoteId){const error=Error('This Phema is still initializing.');error.dispatchOccurred=false;detail.reject(error);return;}
  try{
   const path='/api/instances/'+encodeURIComponent(instance.remoteId)+'/content';
   const previous=await request('/api/instances/'+encodeURIComponent(instance.remoteId));
   const prior=(previous.state||previous).content?.results?.[detail.reference];
   const priorHash=JSON.stringify(prior);let processing=false;
   await request(path,{method:'POST',body:{operation:'interact',references:[detail.reference],inputs:detail.inputs}});
   const deadline=Date.now()+125000;
   while(Date.now()<deadline){
    const status=await request('/api/instances/'+encodeURIComponent(instance.remoteId));
    const result=(status.state||status).content?.results?.[detail.reference];
    if(result?.status==='resolving')processing=true;
    if(result?.status==='ready'&&result.resolvedAt!==prior?.resolvedAt){
     const value=result.value;
     if(value?.phema&&adoptPhema)await adoptPhema(instance,value.phema);
     detail.resolve(value);return;
    }
    if(['failed','unavailable'].includes(result?.status)&&(processing||JSON.stringify(result)!==priorHash))throw Error(result.reason||result.error||'Owner operation failed.');
    await new Promise(resolve=>setTimeout(resolve,250));
   }
   throw Error('Delivery is uncertain. Observe the owner receipt before another action.');
  }catch(error){detail.reject(error);}
 };
 document.addEventListener('phemacast:surface-action',listener);
 return ()=>document.removeEventListener('phemacast:surface-action',listener);
}
