/** Financial application adapter; the shared grammar has no product semantics. */
import {LAYOUT_GRAMMAR} from './layout-contract.js';
const MEDIA=['text','image','audio','video','scene','interactive'];
// A panel's per-field display formats travel in this framework extension, which hosts without it ignore.
const FIELD_FORMATS='phemacast:fieldFormats';
const SURFACE_FIELDS=['id','title','position','area','tabId','requirementId','supplementalRequirementIds','fieldMappings','referenceId','componentId','media','fallback'];
const REQUIREMENT_FIELDS=['id','label','fields','mode','essential','schema','arguments','currency','timezone','frequency','units','maxAgeSeconds','refreshSeconds','refreshMode','limit','dependsOn','modelRef','modelContract','optionalFields','join','allowLossy'];
const stable=v=>v&&typeof v==='object'?Array.isArray(v)?v.map(stable):Object.fromEntries(Object.keys(v).sort().map(k=>[k,stable(v[k])])):v;
const equal=(a,b)=>JSON.stringify(stable(a))===JSON.stringify(stable(b));
function renderer(node){
 if(node.template==='custom')return {...structuredClone(node.capability),required:!node.fallback};
 return {id:(node.type==='phemar'||node.template==='pulse-chat')?'phemacast:pulse-chat':node.template==='phema'?'phemacast:nesting':(MEDIA.includes(node.template)?'phemacast:media.':'attas:display.')+node.template,version:'1.0.0'};
}
function parameterSchema(params){
 const convert=value=>{value=structuredClone(value);if(value.type==='ticker')value.type='string';delete value.notation;if(value.items)value.items=convert(value.items);return value;};
 return Object.fromEntries(Object.entries(params).map(([key,value])=>[key,convert(value)]));
}
export function entryToDocument(entry){
 if(entry.baseLayoutId==='content')return contentDocument(entry);
 const design=structuredClone(entry.design),meta=structuredClone(Object.fromEntries(Object.entries(entry).filter(([key])=>!['id','name','description','design','composition'].includes(key))));
 Object.assign(meta,{schemaVersion:design.schemaVersion,params:structuredClone(design.params)});
 design.schemaVersion=LAYOUT_GRAMMAR;design.params=parameterSchema(design.params);
 for(const node of design.layout.children){
  const cap=renderer(node),formats=node.fieldFormats,extras={};delete node.fieldFormats;for(const key of Object.keys(node))if(!SURFACE_FIELDS.includes(key)){extras[key]=node[key];delete node[key];}
  node.renderer=cap;node.extensions={'attas:surface':extras,...(formats?{[FIELD_FORMATS]:formats}:{})};
 }
 for(const req of design.dataRequirements){const extras={};for(const key of Object.keys(req))if(!REQUIREMENT_FIELDS.includes(key)){extras[key]=req[key];delete req[key];}if(Object.keys(extras).length)req.extensions={'attas:requirement':extras};}
 return {id:entry.id,name:entry.name,description:entry.description,design,extensions:{'attas:layout':meta,...(entry.extensions?.['phemacast:ui']?{'phemacast:ui':structuredClone(entry.extensions['phemacast:ui'])}:{})}};
}
export function documentToEntry(original){
 const document=structuredClone(original),meta=document.extensions?.['attas:layout'];
 if(!meta||typeof meta!=='object')return contentEntry(document);
 if(document.design.schemaVersion!==LAYOUT_GRAMMAR)throw new Error('Unsupported shared layout version.');
 const design=document.design;
 if(!equal(parameterSchema(meta.params),design.params))throw new Error('Layout parameter hints conflict with the generic contract.');
 design.params=meta.params;design.schemaVersion=meta.schemaVersion;delete meta.params;delete meta.schemaVersion;
 for(const node of design.layout.children){
  const declared=node.renderer,extras=node.extensions?.['attas:surface']||{},formats=node.extensions?.[FIELD_FORMATS];delete node.renderer;delete node.extensions;if(formats)node.fieldFormats=formats;
  if(Object.keys(extras).some(k=>SURFACE_FIELDS.includes(k))||(!equal(renderer({...node,...extras}),declared)&&!(extras.type==='phemar'&&equal(declared,{id:'attas:persona-discussion',version:'1.0.0'}))))throw new Error('Application surface hints conflict with the generic renderer.');
  Object.assign(node,extras);
 }
 for(const req of design.dataRequirements){const extras=req.extensions?.['attas:requirement']||{};delete req.extensions;if(Object.keys(extras).some(k=>REQUIREMENT_FIELDS.includes(k)))throw new Error('Application requirement hints cannot override shared fields.');Object.assign(req,extras);}
 if(Object.keys(meta).some(k=>!['baseLayoutId','command','extensions'].includes(k)))throw new Error('Unsupported application layout hints.');
 if(document.extensions?.['phemacast:ui'])meta.extensions={...meta.extensions,'phemacast:ui':document.extensions['phemacast:ui']};
 return {id:document.id,name:document.name,description:document.description,design,...meta};
}

function contentEntry(document){
 const design=structuredClone(document.design);design.schemaVersion=typeof design.layout.columns==='number'?'dbl/0.4':'dbl/0.1';
 for(const node of design.layout.children){const cap=node.renderer,formats=node.extensions?.[FIELD_FORMATS];delete node.renderer;delete node.extensions;if(formats)node.fieldFormats=formats;const kind=cap.id.replace(/^phemacast:media\./,'');Object.assign(node,cap.id==='phemacast:nesting'?{type:'template',template:'phema'}:cap.id==='phemacast:pulse-chat'?{type:'template',template:'pulse-chat'}:MEDIA.includes(kind)?{type:'template',template:kind}:{type:'template',template:'custom',capability:cap});}
 for(const req of design.dataRequirements)delete req.extensions;
 return {id:document.id,name:document.name,description:document.description,baseLayoutId:'content',command:'CONTENT',design,extensions:{'phemacast:layout':structuredClone(document)}};
}
function contentDocument(entry){
 const original=structuredClone(entry.extensions?.['phemacast:layout']||{}),document={...original,id:entry.id,name:entry.name,description:entry.description,design:structuredClone(entry.design)},design=document.design;
 design.schemaVersion=LAYOUT_GRAMMAR;
 for(const node of design.layout.children){const old=original.design?.layout?.children?.find(n=>n.id===node.id)||{},derived=renderer(node),cap=old.renderer?.id===derived.id?old.renderer:derived,formats=node.fieldFormats;for(const key of Object.keys(node))if(!SURFACE_FIELDS.includes(key))delete node[key];node.renderer=cap;if(old.extensions)node.extensions=old.extensions;
  if(formats)node.extensions={...node.extensions,[FIELD_FORMATS]:formats};else if(node.extensions)delete node.extensions[FIELD_FORMATS];}
 for(const req of design.dataRequirements){const old=original.design?.dataRequirements?.find(r=>r.id===req.id);if(old?.extensions)req.extensions=old.extensions;}
 return document;
}
