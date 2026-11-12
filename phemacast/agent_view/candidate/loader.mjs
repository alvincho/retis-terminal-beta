/** Stable bootstrap. Selects only a locally approved, immutable generic host. */
const HASH=/^[a-f0-9]{64}$/,modules=new Map();
export function approvedHostReference(value){
 if(!value||value.schema!=='phemacast:agent-view-host-release-ref/1'||!HASH.test(value.release)||!HASH.test(value.entrypoint?.digest)||!HASH.test(value.runtime?.digest)
  ||value.entrypoint.path!=='/api/agent-view-host-releases/'+value.release+'/host.mjs'||!Number.isInteger(value.entrypoint.bytes)||value.entrypoint.bytes<1||value.entrypoint.bytes>1000000)throw Error('Invalid approved generic host release');
 return value;
}
export async function loadApprovedHost(request){
 // Resolve on every explicit new mount; never switch an active view.
 const ref=approvedHostReference(await request('/api/agent-views/host-release',{}));
 const key=ref.release+':'+ref.entrypoint.digest;
 if(!modules.has(key))modules.set(key,(async()=>{
  const response=await fetch(ref.entrypoint.path,{credentials:'omit',redirect:'error',cache:'force-cache'});
  if(!response.ok)throw Error('Approved generic host code unavailable');
  const code=await response.text(),bytes=new TextEncoder().encode(code);
  const hash=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))).map(b=>b.toString(16).padStart(2,'0')).join('');
  if(bytes.length!==ref.entrypoint.bytes||hash!==ref.entrypoint.digest)throw Error('Approved generic host code integrity mismatch');
  // This same-origin content-addressed route verifies the immutable file on
  // every read. Its module bundles all imports; publishers cannot supply URLs.
  const module=await import(ref.entrypoint.path);
  if(typeof module.mountAgentView!=='function')throw Error('Unsupported generic host contract');
  return module;
 })().catch(error=>{modules.delete(key);throw error;}));
 return {reference:ref,module:await modules.get(key)};
}
