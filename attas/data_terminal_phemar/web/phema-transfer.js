import {installedRenderers} from './composition-renderers.js';
import {LAYOUT_GRAMMAR,LAYOUT_CAPABILITY,layoutToPhema,layoutRequirements} from './layout-contract.js';
import {entryToDocument,documentToEntry} from './layout-document.js';
/** Portable authored Phema. Runtime credentials, sources and results stay outside. */
export const PHEMA_PROFILE='phemacast:composition/1';
const mediaTypes=['text','image','audio','video','scene','interactive'];
const referenceTypes=['pulse','api','channel','phemar-snapshot','phemar-generate','asset'];
let installedAdvertisement=null;
export function setBrowserAdvertisement(value){if(value?.schema==='capability-advertisement/1'&&Array.isArray(value.capabilities))installedAdvertisement=structuredClone(value);}
export function browserCapabilities(){if(installedAdvertisement)return structuredClone(installedAdvertisement);return {schema:'capability-advertisement/1',capabilities:[...installedRenderers(),
 {id:'phemacast:composition',version:'1.1.0'},
 {id:'phemacast:permissions',version:'1.0.0'},
 {...LAYOUT_CAPABILITY,version:'1.1.0'},
 {id:'phemacast:nesting',version:'1.1.0',limits:{depth:8,components:64}},
 {id:'phemacast:grid',version:'1.1.0',limits:{surfaces:12,columns:12}},
 {id:'phemacast:tabs',version:'1.2.0',limits:{tabs:12}},
 {id:'phemacast:pulse-chat',version:'1.0.0'},...mediaTypes.map(name=>({id:'phemacast:media.'+name,version:'1.1.0',...(name==='scene'?{features:['mesh','timeline','gltf','glb']}:{})})),...referenceTypes.map(name=>({id:'phemacast:resolve.'+name,version:'1.1.0'}))]};}
export function entryToPhema(entry){
 const options=structuredClone(entry.composition||{});options.capabilities=(options.capabilities||[]).filter(c=>!(['attas:dbl','attas:persona-discussion'].includes(c.id)&&['1.0.0','1.1.0'].includes(c.version)&&!c.limits&&(c.id==='attas:persona-discussion'?!c.features?.length:!c.features?.some(f=>!['dbl/0.1','dbl/0.2','dbl/0.3','dbl/0.4'].includes(f)))));
 return layoutToPhema(entryToDocument(entry),options);
}
// Retained solely for exact reads of immutable older publications.
function legacyPhema(entry){
 const document=structuredClone(entry),grid=document.design.layout,options=document.composition||{};delete document.composition;
 const capabilities=[{id:'phemacast:composition',version:'1.0.0'},{id:'phemacast:grid',version:'1.0.0',limits:{surfaces:grid.children.length}},
 {id:'attas:dbl',version:'1.0.0',features:[document.design.schemaVersion]}];
 if(grid.tabbedView)capabilities.push({id:'phemacast:tabs',version:'1.0.0',limits:{tabs:grid.tabbedView.tabs.length}});
 for(const id of [...new Set(grid.children.map(n=>n.type==='phemar'?'attas:persona-discussion':(n.template==='phema'?'phemacast:nesting':n.template==='custom'?'phemacast:media.text':(mediaTypes.includes(n.template)?'phemacast:media.':'attas:display.')+n.template)))].sort())capabilities.push({id,version:'1.0.0'});
 for(const node of grid.children)if(node.template==='scene'){const features=[...(node.media?.asset?[node.media.asset.format]:[]),...(node.media?.timeline?['timeline']:[]),...(node.media?.objects?.some(o=>o.shape==='mesh')?['mesh']:[])];if(features.length)capabilities.push({id:'phemacast:media.scene',version:'1.1.0',features});}
 for(const node of grid.children)if(node.template==='custom')capabilities.push({...node.capability,required:!node.fallback});
 for(const kind of [...new Set((options.references||[]).map(r=>r.kind))].sort())capabilities.push({id:'phemacast:resolve.'+kind,version:'1.0.0'});
 capabilities.push(...(options.capabilities||[]));
 return {id:document.id,phema_id:document.id,name:document.name,description:document.description,owner:'',meta:{profile:PHEMA_PROFILE},resolution_mode:'dynamic',
 input_schema:{type:'object',properties:Object.fromEntries(Object.entries(document.design.params).map(([key,val])=>[key,{...(val.type==='array'?{type:'array',items:{type:'string'}}:{type:'string'}),default:val.default}])),additionalProperties:false},
 sections:[{name:'Composition',content:[{type:'phemacast:composition',version:'1.0.0',presentation:{grammar:'attas:dbl/1',document},capabilities,references:options.references||[],extensions:options.extensions||{},...(options.components?.length?{components:options.components}:{})}]}]};
}
export function phemaToEntry(phema){
 if(phema?.meta?.profile!==PHEMA_PROFILE)throw new Error('Unsupported Phema profile.');
 const nodes=phema.sections?.flatMap(s=>s.content||[]).filter(n=>n?.type==='phemacast:composition');
 if(nodes?.length!==1||nodes[0].version!=='1.0.0'||!['attas:dbl/1',LAYOUT_GRAMMAR].includes(nodes[0].presentation?.grammar))throw new Error('Unsupported Phema composition.');
 const generic=nodes[0].presentation.grammar===LAYOUT_GRAMMAR;
 const entry=generic?documentToEntry(nodes[0].presentation.document):structuredClone(nodes[0].presentation.document);
 if(entry.id!==phema.id)throw new Error('Phema identity mismatch.');
 if(nodes[0].references?.length||Object.keys(nodes[0].extensions||{}).length)entry.composition={references:structuredClone(nodes[0].references||[]),extensions:structuredClone(nodes[0].extensions||{})};
 if(nodes[0].components?.length)entry.composition={references:[],extensions:{},...entry.composition,components:structuredClone(nodes[0].components)};
 const base=generic?[...layoutRequirements(nodes[0].presentation.document),...[...new Set((nodes[0].references||[]).map(r=>r.kind))].sort().map(kind=>({id:'phemacast:resolve.'+kind,version:'1.0.0'}))]:legacyPhema(entry).sections[0].content[0].capabilities;
 const stable=v=>v&&typeof v==='object'?Array.isArray(v)?v.map(stable):Object.fromEntries(Object.keys(v).sort().map(k=>[k,stable(v[k])])):v;
 const extra=(nodes[0].capabilities||[]).filter(cap=>!base.some(v=>JSON.stringify(stable(v))===JSON.stringify(stable(cap))));
 if(extra.length)entry.composition={...entry.composition,capabilities:structuredClone(extra)};
 return entry;
}
export function hydratePhemaPayload(value){
 if(Array.isArray(value))return value.map(hydratePhemaPayload);
 if(!value||typeof value!=='object')return value;
 const result={...value};
 // Runtime entries contain resolved parameters and namespaced child bindings.
 // Keep that projection; the authored Phema is retained separately for editing.
 if(result.phema&&!result.entry)result.entry=phemaToEntry(result.phema);
 for(const key of ['proposals','records','published','consultants','results'])if(Array.isArray(result[key]))result[key]=result[key].map(hydratePhemaPayload);
 return result;
}
