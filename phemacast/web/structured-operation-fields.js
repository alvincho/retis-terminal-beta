/** Generic bounded structured controls. No JSON editors or application rules. */
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const forbidden=key=>/password|secret|token|api.?key|credential|__proto__|constructor|prototype/i.test(key);
const pathName=path=>JSON.stringify(path);
function choices(schema){
 if(Array.isArray(schema.enum))return schema.enum.map(value=>({value,title:String(value)}));
 if(Array.isArray(schema.oneOf)&&schema.oneOf.every(x=>Object.hasOwn(x,'const')))return schema.oneOf.map(x=>({value:x.const,title:x.title??String(x.const)}));
 return null;
}
export function structuredFields(schema,{text=esc}={}){
 let count=0;
 function field(value,path,required,depth){
  if(depth>4||++count>100||path.some(forbidden))return '';
  const name=esc(pathName(path)),label=text(value.title||path.at(-1)),type=value.type||'string';
  const attrs=`name="${name}" aria-label="${esc(value.title||path.at(-1))}" data-structured-field data-value-type="${esc(type)}" ${required?'required':''}`;
  if(Object.hasOwn(value,'const'))return `<input ${attrs} type="hidden" value="${esc(JSON.stringify(value.const))}" data-constant="true">`;
  if(type==='object'){
   const children=Object.entries(value.properties||{}).slice(0,30).map(([key,item])=>field(item,[...path,key],(value.required||[]).includes(key),depth+1)).join('');
   return children?`<fieldset class="phema-structured-group"><legend>${label}</legend><div class="phema-form-grid">${children}</div></fieldset>`:'';
  }
  const options=choices(type==='array'?value.items||{}:value);
  if(options){
   const defaults=type==='array'?(Array.isArray(value.default)?value.default:[]):[value.default];
   return `<label>${label}<select ${attrs} ${type==='array'?'multiple data-item-type="'+esc(value.items?.type||'string')+'"':''}>${!required&&type!=='array'?'<option value=""></option>':''}${options.slice(0,500).map(({value:item,title})=>`<option value="${esc(JSON.stringify(item))}" ${defaults.includes(item)?'selected':''}>${esc(title)}</option>`).join('')}</select></label>`;
  }
  if(type==='boolean')return `<label>${label}<input ${attrs} type="checkbox" ${value.default===true?'checked':''}></label>`;
  if(type==='array'){
   if(!['string','integer','number'].includes(value.items?.type||'string'))return `<p>${text('This structured input is not supported by this client.')}</p>`;
   return `<label>${label}<textarea ${attrs} data-lines="true" data-item-type="${esc(value.items?.type||'string')}" rows="2" maxlength="12000">${esc((value.default||[]).join('\n'))}</textarea><small>${text('One value per line')}</small></label>`;
  }
  const bounds=['minLength','maxLength','minimum','maximum'].map(key=>value[key]===undefined?'':`${{minLength:'minlength',maxLength:'maxlength',minimum:'min',maximum:'max'}[key]}="${esc(value[key])}"`).join(' ');
  if(type==='string'&&(value.maxLength||0)>500)return `<label>${label}<textarea ${attrs} ${bounds} rows="3">${esc(value.default??'')}</textarea></label>`;
  return `<label>${label}<input ${attrs} type="${['integer','number'].includes(type)?'number':'text'}" step="${type==='integer'?'1':'any'}" ${bounds} value="${esc(value.default??'')}"></label>`;
 }
 return Object.entries(schema?.properties||{}).slice(0,30).map(([key,value])=>field(value,[key],(schema.required||[]).includes(key),0)).join('');
}
export function readStructuredFields(form){
 const result={};
 function assign(path,value){
  if(!Array.isArray(path)||!path.length||path.length>5||path.some(x=>typeof x!=='string'||forbidden(x)))throw new Error('Invalid structured field path.');
  let target=result;for(const key of path.slice(0,-1))target=target[key]??=Object.create(null);
  target[path.at(-1)]=value;
 }
 for(const field of form.querySelectorAll('[data-structured-field]')){
  if(field.disabled)continue;
  let value;
  if(field.dataset.constant==='true')value=JSON.parse(field.value);
  else if(field.type==='checkbox')value=field.checked;
  else if(field.multiple)value=[...field.selectedOptions].map(option=>JSON.parse(option.value));
  else if(field.tagName==='SELECT'){
   if(field.value==='')continue;value=JSON.parse(field.value);
  }else if(field.dataset.lines==='true'){
   value=field.value.split(/\r?\n/).map(x=>x.trim()).filter(Boolean).map(x=>field.dataset.itemType==='string'?x:Number(x));
  }else{
   if(field.value==='')continue;
   value=['integer','number'].includes(field.dataset.valueType)?Number(field.value):field.value;
  }
  if(typeof value==='number'&&!Number.isFinite(value)||Array.isArray(value)&&value.some(x=>typeof x==='number'&&!Number.isFinite(x)))throw new Error('Enter a finite number.');
  assign(JSON.parse(field.name),value);
 }
 return result;
}
