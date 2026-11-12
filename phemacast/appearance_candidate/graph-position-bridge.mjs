/** Only Phema-declared protected operations; uncertain writes are observe-only. */
const pending=new Map();
function authored(phema,prefix){const c=phema?.sections?.flatMap(s=>s.content||[]).find(n=>n.type==='phemacast:composition');if(!prefix)return c?.presentation?.document;const [id,...tail]=prefix.split('/');return authored(c?.components?.find(c=>c.id===id)?.phema,tail.join('/'));}
export function installGraphPositionBridge({instanceOf,request,bindingOf}){
 const listener=async event=>{
  const host=event.target.closest('[data-content-interaction]');if(!host)return;
  const{instance,prefix}=instanceOf(host),doc=authored(instance?.state?.phema,prefix),graph=doc?.design?.layout?.children?.find(n=>n.id===host.dataset.panelId);
  const declared=graph?.extensions?.['phemacast:graph-position']?.reference,reference=[prefix,declared].filter(Boolean).join('/');
  const capability=instance?.state?.content?.transportCapability;
  if(!instance?.remoteId||!declared||declared!==event.detail.reference||!bindingOf(instance.state.phema,reference)||capability?.id!=='phemacast:neutral-operation-form'){event.detail.reject(Error('Declared protected position operation unavailable.'));return;}
  const scope=instance.remoteId+'/'+reference,route='/api/instances/'+instance.remoteId+'/content',send=body=>request(route,{method:'POST',body});
  let known=pending.get(scope);const records=instance.state.content.pendingInteractions?.[reference];
  if(!known&&records?.length===1){const r=records[0];if(r.reference===reference&&/^[A-Za-z0-9_-]{16,128}$/.test(r.requestKey)&&/^[a-f0-9]{64}$/.test(r.wireDigest)){known={requestKey:r.requestKey,wireDigest:r.wireDigest};pending.set(scope,known);}}
  try{
   if(event.detail.observe){
    if(!known?.wireDigest)throw Error('No known position receipt. Refresh view.');
    const observed=await send({operation:'observe-interaction',references:[reference],...known});
    if(observed.intentId!==known.requestKey||observed.wireDigest!==known.wireDigest||observed.executionReplayAllowed!==false)throw Error('Unknown receipt');
    const terminal=['complete','completed','failed','cancelled'].includes(observed.receipt?.state);
    if(terminal){pending.delete(scope);if(instance.state.content.pendingInteractions)delete instance.state.content.pendingInteractions[reference];}
    event.detail.resolve({state:terminal?'observed':'uncertain',receipt:observed.receipt});return;
   }
   if(known)throw Error('Check the previous position outcome before another move.');
   known={requestKey:crypto.randomUUID().replaceAll('-','')};pending.set(scope,known);
   const prepared=await send({operation:'prepare-interaction',references:[reference],inputs:event.detail.inputs,...known});
   if(prepared.intentId!==known.requestKey||!/^[a-f0-9]{64}$/.test(prepared.wireDigest))throw Error('Position preparation could not be verified.');
   known.wireDigest=prepared.wireDigest;
   if(prepared.state!=='prepared')throw Error('Outcome unavailable. Check status.');
   const result=await send({operation:'invoke-interaction',references:[reference],...known});
   if(result.intentId!==known.requestKey||result.wireDigest!==known.wireDigest||result.state!=='complete')throw Error('Outcome unavailable. Check status.');
   pending.delete(scope);event.detail.resolve(result);
   if(result.value?.phema&&['parameters','snapshot'].includes(result.value.source_mode))document.dispatchEvent(new CustomEvent('phemacast:open-result',{bubbles:true,detail:{phema:result.value.phema,sourceMode:result.value.source_mode}}));
  }catch(error){if(!known?.wireDigest)pending.delete(scope);error.uncertain=Boolean(known?.wireDigest);event.detail.reject(error);}
 };
 document.addEventListener('phemacast:graph-position',listener);return()=>document.removeEventListener('phemacast:graph-position',listener);
}
