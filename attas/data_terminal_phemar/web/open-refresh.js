/** A Phema opened from New Data Browser fetches the panels that wait for ↻, once, as pressing each ↻ would. */
// Never forms, generation, operations that declare effects, or calls allowed to spend points.
// The Terminal still checks the Phema's approval and data permissions on the request.
const PERMISSIONS='phemacast:permissions',EFFECTS='phemacast:operation-effects';

function compositionOf(value){
 if(value?.composition)return value.composition;
 const nodes=value?.sections?.flatMap(s=>s.content||[]).filter(n=>n?.type==='phemacast:composition')||[];
 return nodes.length===1?nodes[0]:null;
}

// A read a ↻ may load on the viewer's behalf: no form, no declared effect, no points.
function loadable(phema,results,path,wanted){
 const node=compositionOf(phema);if(!node)return [];
 const rules=node.extensions?.[PERMISSIONS]?.references||[];
 const own=(node.references||[]).flatMap(ref=>{
  const id=[path,ref.id].filter(Boolean).join('/'),rule=rules.find(r=>r.id===ref.id),result=results?.[id];
  const effects=Object.values(ref.extensions?.[EFFECTS]||{});
  return ref.kind==='pulse'&&!ref.interaction&&rule&&!(rule.maxPoints>0)&&!effects.some(Boolean)&&wanted(result)?[id]:[];
 });
 return own.concat((node.components||[]).flatMap(c=>loadable(c.phema,results,[path,c.id].filter(Boolean).join('/'),wanted)));
}

// Opening a Phema is the request for its data: panels showing a result kept from earlier fetch it anew too.
export function openingReferences(phema,results,path=''){
 return loadable(phema,results,path,result=>result?.status==='pending'&&result.reason==='manual'||result?.status==='ready'&&result.kept===true);
}

/** After the viewer approves or revokes in ♧, the same reads load again: approved ones show data, revoked ones stop. */
export function permissionReferences(phema,results){
 return loadable(phema,results,'',result=>!!result&&!['pending','resolving'].includes(result.status)||result?.reason==='manual');
}

/** The references to load now, or null while the opened Browser's first content is still settling. */
export function openingLoad(state,layout){
 const content=state?.content;if(!content?.results)return null;
 // A Phema still awaiting permission review loads once it is approved.
 if(state.security&&state.security.approved!==true)return null;
 // Panels still blocked by permission, such as private data not yet allowed, load
 // together once approved, rather than leaving them for ↻.
 if(Object.values(content.results).some(result=>result?.status==='blocked'))return null;
 const references=(state.sourceMode||'parameters')==='parameters'?openingReferences(state.phema||layout,content.results):[];
 return references.length||content.status!=='resolving'?references:null;
}
