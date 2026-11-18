/** Trusted data-only form primitives. Publisher data never becomes HTML, code,
 * URLs or a raw JSON editor. Business validation stays in the owning agent. */
export function structuredForm(schema,initial={},options={}) {
 const root=document.createElement('div');root.className='structured-form';
 const full=schema;
 const readable=value=>String(value??'—').replace(/\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b/gi,'Saved reference');
 const resolve=value=>{if(!value?.$ref)return value;const path=value.$ref;if(!path.startsWith('#/'))throw Error('Only local form schema references are supported');const target=path.slice(2).split('/').reduce((v,k)=>v?.[k.replaceAll('~1','/').replaceAll('~0','~')],full);if(!target)throw Error('Form schema reference is unavailable');return {...target,...Object.fromEntries(Object.entries(value).filter(([key])=>key!=='$ref'))};};
 function matches(raw,value,depth=0){
  if(value===undefined||depth>12)return false;
  const s=resolve(raw)||{};
  if(Object.hasOwn(s,'const'))return JSON.stringify(s.const)===JSON.stringify(value);
  if(s.enum)return s.enum.some(item=>JSON.stringify(item)===JSON.stringify(value));
  if(s.oneOf||s.anyOf)return (s.oneOf||s.anyOf).some(item=>matches(item,value,depth+1));
  if(s.type==='null')return value===null;
  if(s.type==='array')return Array.isArray(value);
  if(s.type==='object'||s.properties)return value!==null&&typeof value==='object'&&!Array.isArray(value)&&Object.entries(s.properties||{}).every(([key,property])=>value[key]===undefined?!(s.required||[]).includes(key):matches(property,value[key],depth+1));
  if(s.type==='integer')return Number.isInteger(value);
  if(s.type==='number')return typeof value==='number'&&Number.isFinite(value);
  if(Array.isArray(s.type))return s.type.some(type=>matches({...s,type},value,depth+1));
  return !s.type||typeof value===s.type;
 }
 function field(raw,value,label,depth=0){
  if(depth>12)throw Error("Form nesting exceeds the supported depth");
  const s=resolve(raw)||{},box=document.createElement('fieldset');box.className='structured-field';
  const heading=document.createElement('legend');heading.textContent=readable(s.title||label||'Value');box.append(heading);
  value=value===undefined?s.default:value;
  if(Object.hasOwn(s,'const'))return {node:document.createDocumentFragment(),value:()=>structuredClone(s.const)};
  if(Array.isArray(s.type))return field({...s,type:undefined,oneOf:s.type.map(type=>({...s,type,oneOf:undefined,anyOf:undefined}))},value,label,depth+1);
  if(s.oneOf||s.anyOf){
   const variants=s.oneOf||s.anyOf,select=document.createElement('select'),body=document.createElement('div');let active;
   variants.forEach((v,i)=>{const option=document.createElement('option');option.value=i;option.textContent=readable(v.title||v.type||'Option '+(i+1));select.append(option);});
   const selected=variants.findIndex(v=>matches(v,value));if(selected>=0)select.value=String(selected);else if(value!==undefined&&(typeof value!=='object'||value===null||Object.keys(value).length)){const option=document.createElement('option');option.value='unavailable';option.textContent='Saved selection unavailable';select.prepend(option);select.value=option.value;}select.disabled=Boolean(options.disabled||s.readOnly);
   function update(){if(select.value==='unavailable'){body.textContent='Choose an available option before submitting.';active={value:()=>{throw Error('Saved selection unavailable');}};return;}active=field(variants[Number(select.value)],value,label,depth+1);body.replaceChildren(active.node);}
   select.onchange=()=>{value=undefined;update();};box.append(select,body);update();return {node:box,value:()=>active.value()};
  }
  if(s.enum){
   const input=document.createElement('select');for(const [index,item] of s.enum.entries()){const option=document.createElement('option');option.value=String(index);option.textContent=readable(item);input.append(option);}const selected=s.enum.findIndex(item=>JSON.stringify(item)===JSON.stringify(value));if(selected>=0)input.value=String(selected);else if(value!==undefined){const option=document.createElement('option');option.value='unavailable';option.textContent='Saved selection unavailable';input.prepend(option);input.value=option.value;}input.disabled=Boolean(options.disabled||s.readOnly);box.append(input);
   return {node:box,value:()=>{if(input.value==='unavailable')throw Error('Saved selection unavailable');return structuredClone(s.enum[Number(input.value)]);}};
  }
  if(s.type==='object'||s.properties){
   const children=[],required=new Set(s.required||[]);value=value&&typeof value==='object'?value:{};
   for(const [key,property] of Object.entries(s.properties||{})){
    const include=document.createElement('input'),body=document.createElement('div');let child;include.type='checkbox';include.checked=required.has(key)||value[key]!==undefined||property.default!==undefined||Object.hasOwn(property,'const');include.disabled=Boolean(options.disabled||s.readOnly||required.has(key)||Object.hasOwn(property,'const'));
    function update(){if(include.checked&&!child)child=field({...property,readOnly:property.readOnly||s.readOnly},value[key],property.title||key.replaceAll('_',' '),depth+1);body.replaceChildren(...(include.checked?[child.node]:[]));}include.onchange=update;update();
    const toggle=document.createElement('label');toggle.append(include,document.createTextNode(readable('Include '+(property.title||key.replaceAll('_',' ')))));if(!required.has(key)&&!Object.hasOwn(property,'const'))box.append(toggle);box.append(body);children.push({key,value:()=>child.value(),include});
   }
   const extras=Object.fromEntries(Object.entries(value).filter(([key])=>!Object.hasOwn(s.properties||{},key)));
   const dynamic=typeof s.additionalProperties==='object'&&s.additionalProperties!==null?s.additionalProperties:null;
   const entries=[];
   if(dynamic){
    const rows=document.createElement('div'),add=document.createElement('button');add.type='button';add.textContent='Add field';
    const locked=Boolean(options.disabled||s.readOnly);
    function update(){add.disabled=locked||children.length+entries.length>=(s.maxProperties??80);}
    function append(key='',item){
     const row=document.createElement('div'),name=document.createElement('input'),remove=document.createElement('button');row.className='structured-map-row';name.type='text';name.value=key;name.setAttribute('aria-label','Field name');name.disabled=locked;name.maxLength=Math.min(s.propertyNames?.maxLength??200,200);
     const child=field({...dynamic,readOnly:dynamic.readOnly||s.readOnly},item,'Value',depth+1),entry={name,child,row};
     remove.type='button';remove.textContent='Remove';remove.disabled=locked;remove.onclick=()=>{entries.splice(entries.indexOf(entry),1);row.remove();update();};row.append(name,child.node,remove);entries.push(entry);rows.append(row);update();
    }
    for(const [key,item] of Object.entries(extras))append(key,item);add.onclick=()=>{if(!add.disabled)append();};update();box.append(rows,add);
   }else{
    if(Object.keys(extras).length&&s.additionalProperties===false)throw Error('Saved inputs contain undeclared fields');
    if(Object.keys(extras).length)box.append(structuredContent(extras));
   }
   return {node:box,value:()=>{
    const pairs=children.filter(c=>c.include.checked).map(c=>[c.key,c.value()]);const keys=new Set(pairs.map(([key])=>key));
    if(dynamic){for(const entry of entries){const key=entry.name.value;if(!key.trim()||keys.has(key))throw Error('Use unique, nonempty field names');keys.add(key);pairs.push([key,entry.child.value()]);}}
    else pairs.push(...Object.entries(structuredClone(extras)));
    return Object.fromEntries(pairs);
   }};
  }
  if(s.type==='array'){
   const rows=document.createElement('div'),add=document.createElement('button'),children=[];add.type='button';add.textContent='Add item';
   function update(){add.disabled=Boolean(options.disabled||s.readOnly||(s.maxItems!==undefined&&children.length>=s.maxItems));}
   function append(item){const wrapper=document.createElement('div'),child=field({...s.items,readOnly:s.items?.readOnly||s.readOnly},item,'Item',depth+1),remove=document.createElement('button');remove.type='button';remove.textContent='Remove';remove.disabled=Boolean(options.disabled||s.readOnly);const row={child,wrapper};remove.onclick=()=>{children.splice(children.indexOf(row),1);wrapper.remove();update();};wrapper.append(child.node,remove);children.push(row);rows.append(wrapper);update();}
   for(const item of Array.isArray(value)?value:[])append(item);add.onclick=()=>{if(!add.disabled)append(undefined);};update();box.append(rows,add);return {node:box,value:()=>children.map(c=>c.child.value())};
  }
  if(s.type==='null')return {node:document.createDocumentFragment(),value:()=>null};
  if(!s.type&&value!==null&&typeof value==='object'){box.append(structuredContent(value));return {node:box,value:()=>structuredClone(value)};}
  const input=document.createElement(s.maxLength>500?'textarea':'input');
  if(s.type==='boolean'){input.type='checkbox';input.checked=value===true;}
  else {if(input.tagName==='INPUT')input.type=s.type==='number'||s.type==='integer'?'number':'text';if(input.type==='number')input.step=s.type==='integer'?'1':'any';input.value=value??'';}
  input.setAttribute('aria-label',s.title||label||'Value');if(s.readOnly)input.disabled=true;if(options.disabled)input.disabled=true;box.append(input);
  return {node:box,value:()=>{if(s.type==='boolean')return input.checked;if(s.type==='number'||s.type==='integer'){if(!input.value.trim())throw Error('Enter '+readable(s.title||label||'a number'));const number=Number(input.value);if(!Number.isFinite(number)||(s.type==='integer'&&!Number.isInteger(number)))throw Error('Enter a valid '+s.type);return number;}return input.value;}};
 }
 const form=field(schema,initial,'Inputs');root.append(form.node);return {element:root,value:form.value};
}

export function structuredContent(value,{maxItems=80,depth=0}={}) {
 if(depth>12)throw Error('Content nesting exceeds the supported depth');
 const root=document.createElement('div');
 if(value===null||typeof value!=='object'){root.textContent=String(value??'—').replace(/\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b/gi,'Saved reference');return root;}
 if(Array.isArray(value)){const list=document.createElement('ul');for(const item of value.slice(0,maxItems)){const row=document.createElement('li');row.append(structuredContent(item,{maxItems,depth:depth+1}));list.append(row);}root.append(list);return root;}
 const list=document.createElement('dl');for(const [key,item] of Object.entries(value).slice(0,maxItems)){const term=document.createElement('dt'),content=document.createElement('dd');term.textContent=key.replaceAll('_',' ').replace(/\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b/gi,'Saved reference');content.append(structuredContent(item,{maxItems,depth:depth+1}));list.append(term,content);}root.append(list);return root;
}
