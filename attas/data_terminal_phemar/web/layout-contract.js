/** Generic, data-only layout contract. Renderer implementations belong to hosts. */
export const LAYOUT_GRAMMAR='phemacast:layout/1';
export const LAYOUT_CAPABILITY={id:'phemacast:layout',version:'1.0.0'};
export function layoutRequirements(document){
 const grid=document.design.layout;
 const extendedTabs=(grid.tabbedView?.tabs.length||0)>8;
 const result=[{id:'phemacast:composition',version:'1.1.0'},{...LAYOUT_CAPABILITY,...(extendedTabs?{version:'1.1.0'}:{})},{id:'phemacast:grid',version:'1.1.0',limits:{surfaces:grid.children.length}}];
 if(grid.tabbedView)result.push({id:'phemacast:tabs',version:extendedTabs?'1.2.0':'1.1.0',limits:{tabs:grid.tabbedView.tabs.length}});
 for(const node of grid.children){
  result.push(structuredClone(node.renderer));
  if(node.fallback)result.push({id:'phemacast:media.text',version:'1.1.0'});
  if(node.renderer.id==='phemacast:media.scene'){
   const media=node.media||{},features=[...(media.asset?[media.asset.format]:[]),...(media.timeline?['timeline']:[]),...(media.objects?.some(o=>o.shape==='mesh')?['mesh']:[])];
   if(features.length)result.push({id:'phemacast:media.scene',version:'1.1.0',features});
  }
 }
 const stable=v=>v&&typeof v==='object'?Array.isArray(v)?v.map(stable):Object.fromEntries(Object.keys(v).sort().map(k=>[k,stable(v[k])])):v;
 return result.filter((r,i)=>result.findIndex(v=>JSON.stringify(stable(v))===JSON.stringify(stable(r)))===i);
}
export function layoutToPhema(document,options={}){
 document=structuredClone(document);
 if(document.design?.schemaVersion!==LAYOUT_GRAMMAR)throw new Error('Unsupported shared layout version.');
 const capabilities=[...layoutRequirements(document),...[...new Set((options.references||[]).map(r=>r.kind))].sort().map(kind=>({id:'phemacast:resolve.'+kind,version:'1.0.0'})),...(options.capabilities||[])];
 return {id:document.id,phema_id:document.id,name:document.name,description:document.description,owner:'',meta:{profile:'phemacast:composition/1'},resolution_mode:'dynamic',
  input_schema:{type:'object',properties:structuredClone(document.design.params),additionalProperties:false},
  sections:[{name:'Composition',content:[{type:'phemacast:composition',version:'1.0.0',presentation:{grammar:LAYOUT_GRAMMAR,document},capabilities,references:structuredClone(options.references||[]),extensions:structuredClone(options.extensions||{}),...(options.components?.length?{components:structuredClone(options.components)}:{})}]}]};
}
