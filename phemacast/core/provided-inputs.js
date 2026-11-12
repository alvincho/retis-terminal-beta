/** Generic supplied-input operation controller. Owner Phema bindings select the
 * connection, request clock and result projection; this module knows no domain. */
const pointer=(value,path)=>path.slice(1).split('/').reduce((item,key)=>item?.[key.replaceAll('~1','/').replaceAll('~0','~')],value);
export function createProvidedInputOperations(api,onUpdate=()=>{}) {
 const cache=new Map();
 function read(key,identity,parameters,contract){
  const signature=JSON.stringify([identity,parameters,contract]);const old=cache.get(key);
  if(old?.signature===signature)return old;
  const entry={signature,status:'loading',controller:new AbortController()};old?.controller.abort();cache.set(key,entry);
  const inputs=structuredClone(parameters);
  (async()=>{
   const catalog=await api('/api/agent-operations',{signal:entry.controller.signal});
   const candidates=catalog.publishers.flatMap(publisher=>publisher.operations.filter(operation=>operation.output_schema?.properties?.schema?.const===contract).map(operation=>({publisher,operation})));
   if(!candidates.length)throw new Error('The requested agent operation is unavailable.');
   const selections=new Set(candidates.map(({operation})=>JSON.stringify([operation.binding?.selection_url,operation.binding?.selection_pointer])));
   if(selections.size!==1)throw new Error('Agent definitions disagree about the selection binding.');
   const binding=candidates[0].operation.binding;
   if(!/^\/api\/[A-Za-z0-9/_-]+$/.test(binding?.selection_url||'')||!binding.selection_pointer?.startsWith('/'))throw new Error('Agent selection binding is invalid.');
   const configured=await api(binding.selection_url,{signal:entry.controller.signal});
   const selected=pointer(configured,binding.selection_pointer);
   if(!selected)throw new Error('Select an agent in its workspace before requesting this operation.');
   const chosen=candidates.find(({publisher})=>publisher.id===selected);
   if(!chosen)throw new Error('The selected agent does not publish the requested operation.');
   const clock=chosen.operation.binding.request_clock_property;
   if(clock&&inputs[clock]==null){
    if(chosen.operation.input_schema?.properties?.[clock]?.format!=='date-time')throw new Error('Agent request clock binding is invalid.');
    inputs[clock]=new Date().toISOString();
   }
   const returned=await api('/api/agent-operations/invoke',{method:'POST',body:{reference:chosen.operation.reference,parameters:inputs},signal:entry.controller.signal});
   if(cache.get(key)!==entry)return;
   const projection=chosen.operation.binding.result_pointer;
   if(!projection?.startsWith('/'))throw new Error('Agent result binding is invalid.');
   entry.result=pointer(returned.result,projection);entry.status='ready';onUpdate();
  })().catch(error=>{if(cache.get(key)!==entry||entry.controller.signal.aborted)return;entry.status='unavailable';entry.error=error.message;onUpdate();});
  return entry;
 }
 return {read,drop(key){cache.get(key)?.controller.abort();cache.delete(key);},clear(){for(const entry of cache.values())entry.controller.abort();cache.clear();}};
}
