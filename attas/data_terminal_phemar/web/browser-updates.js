// Short requests leave HTTP/1 connection slots available across Terminal tabs.
// The worker still owns execution and durable state; polling only reads it.
export function watchBrowserInstance(request,path,onUpdate,onError=()=>{},
  {intervalMs=2000,hiddenIntervalMs=15000,timeoutMs=15000,initial=null}={}) {
  let closed=false,timer,controller,last=initial&&signature(initial),failed=false,reading=false,wakePending=false;
  let baseline=initial;
  function signature(value){return JSON.stringify([value.sequence,value.generation,value.status,value.error,value.discoveryWarning]);}
  function older(value){return baseline&&value.generation!=null&&baseline.generation!=null&&(value.generation<baseline.generation||value.generation===baseline.generation&&value.sequence<baseline.sequence);}
  function accept(value){baseline=value;last=signature(value);}
  async function read(){
    if(closed||reading)return;
    clearTimeout(timer);reading=true;
    controller=new AbortController();const timeout=setTimeout(()=>controller.abort(),timeoutMs);
    try{
      const value=await request(path,{signal:controller.signal});
      if(closed)return;
      if(older(value))return;
      const next=signature(value);
      if(next!==last||failed){accept(value);failed=false;onUpdate(value);}
    }catch(error){if(!closed){failed=true;await onError(error);}}
    finally{
      clearTimeout(timeout);controller=null;reading=false;
      if(!closed){if(wakePending){wakePending=false;queueMicrotask(read);}else timer=setTimeout(read,globalThis.document?.hidden?hiddenIntervalMs:intervalMs);}
    }
  }
  function visible(){if(globalThis.document?.hidden||closed)return;clearTimeout(timer);if(reading)wakePending=true;else read();}
  globalThis.document?.addEventListener?.('visibilitychange',visible);
  read();
  return {accept,close(){closed=true;clearTimeout(timer);controller?.abort();globalThis.document?.removeEventListener?.('visibilitychange',visible);}};
}
