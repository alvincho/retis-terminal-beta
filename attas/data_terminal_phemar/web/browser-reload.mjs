/** Reload one Phema, never the Terminal or a sibling Browser. */
const pending=new WeakMap();
const generationSchemas=new Set(['research.commentary.v1','research.video-commentary.v1']);

export function reloadableRequirements(state,browser){
 const visible=state.visibleRequirementIds&&new Set(state.visibleRequirementIds);
 return (state.entry?.design?.dataRequirements||[]).filter(r=>
  (!visible||visible.has(r.id))&&!r.dependsOn&&!generationSchemas.has(r.schema)&&
  r.schema!=='portfolio.holdings.v1'&&r.mode!=='interactive'&&
  (browser.refreshOverrides?.[r.id]?.refreshMode||r.refreshMode)!=='manual');
}

function reloadableReferences(phema,overrides={},path=''){
 const node=phema?.composition;if(!node)return [];
 const rules=node.extensions?.['phemacast:permissions']?.references||[];
 const refs=(node.references||[]).flatMap(ref=>{
  const id=[path,ref.id].filter(Boolean).join('/'),rule=rules.find(r=>r.id===ref.id);
  const trigger=overrides[id]?.refresh?.trigger||ref.refresh?.trigger;
  return rule?.effect==='read'&&rule.classification==='public'&&trigger!=='manual'&&!ref.interaction?[id]:[];
 });
 return refs.concat((node.components||[]).flatMap(c=>reloadableReferences(c.phema,overrides,[path,c.id].filter(Boolean).join('/'))));
}

export function reloadBrowserPhema(browser,options){
 if(pending.has(browser))return pending.get(browser);
 const task=reload(browser,options).finally(()=>pending.delete(browser));
 pending.set(browser,task);return task;
}

async function reload(browser,{request,readLayout=async()=>({updated:0}),entry,accept,isCurrent=()=>true}){
 if(browser.editMode)throw Error('Finish editing before reloading this Phema.');
 const snapshot=browser.sourceMode==='snapshot';
 const keys=['customLayout','design','layoutId','ticker','tickers','consultantId','layoutPublication','fieldBindings','refreshOverrides','instrumentOverrides'];
 const previous=Object.fromEntries(keys.map(k=>[k,browser[k]]));
 const update=snapshot?{updated:0}:await readLayout(browser);
 if(!isCurrent()||browser.editMode)return false;
 if(browser.layoutConflict)throw Error('Review the layout update before reloading this Phema.');
 const adopted=browser.customLayout;let state;
 try{state=update.updated?await request('',{method:'PUT',body:{entry:entry(browser),
   parameterValues:browser.parameterValues||{},instrumentOverrides:browser.instrumentOverrides||{},
   layoutTabs:browser.layoutTabs||{},fieldBindings:browser.fieldBindings||{},
   refreshOverrides:browser.refreshOverrides||{},contentOverrides:browser.contentOverrides||{}}}):await request('');}
 catch(error){if(update.updated&&isCurrent()&&browser.customLayout===adopted)Object.assign(browser,previous);throw error;}
 if(!isCurrent())return false;
 accept(state);
 // Snapshot mode reopens the saved content. It never changes source mode or
 // issues live calls. Manual generation and private holdings stay untouched.
 if(snapshot||state.sourceMode==='snapshot')return {snapshot:true};
 for(const r of reloadableRequirements(state,browser)){
  if(!isCurrent()||browser.editMode)return false;
  state=await request('',{method:'PUT',body:{refreshRequirement:r.id}});
  if(!isCurrent())return false;
  accept(state);
 }
 const references=reloadableReferences(state.phema||browser.customLayout,browser.contentOverrides);
 if(references.length&&isCurrent())await request('/content',{method:'POST',body:{references}});
 if(!isCurrent())return false;
 state=await request('');if(!isCurrent())return false;
 accept(state);
 return {snapshot:false,layoutUnavailable:!!update.unavailable};
}
