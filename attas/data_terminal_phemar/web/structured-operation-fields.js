/** Generic bounded structured controls. No JSON editors or application rules. */
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const forbidden=key=>/password|secret|token|api.?key|credential|__proto__|constructor|prototype/i.test(key);
const pathName=path=>JSON.stringify(path);
// An image input: a JSON-Schema string with contentEncoding base64 and an image contentMediaType. The browser shrinks
// the chosen picture to IMAGE_EDGE pixels on its longest side and re-encodes it as that media type, within maxLength.
const IMAGE_TYPES=['image/png','image/jpeg','image/webp'],IMAGE_EDGE=1568,MAX_FILE_BYTES=10*1024*1024,MAX_PIXELS=40_000_000,IMAGE_LENGTH=900_000;
const imageInput=schema=>schema.type==='string'&&schema.contentEncoding==='base64'&&IMAGE_TYPES.includes(schema.contentMediaType);
function choices(schema){
 if(Array.isArray(schema.enum))return schema.enum.map(value=>({value,title:String(value)}));
 if(Array.isArray(schema.oneOf)&&schema.oneOf.every(x=>Object.hasOwn(x,'const')))return schema.oneOf.map(x=>({value:x.const,title:x.title??String(x.const)}));
 return null;
}
export function structuredFields(schema,{text=esc,attribute=(name,key)=>`${name}="${esc(key)}"`}={}){
 let count=0;
 function field(value,path,required,depth){
  if(depth>4||++count>100||path.some(forbidden))return '';
  const name=esc(pathName(path)),label=text(value.title||path.at(-1)),type=value.type||'string';
  const attrs=`name="${name}" ${attribute('aria-label',value.title||path.at(-1))} data-structured-field data-value-type="${esc(type)}" ${required?'required':''}`;
  if(imageInput(value)){
   // The picture itself never enters the page's saved form draft: only the hidden field carries it, and drafts skip hidden fields.
   const limit=Math.min(Number.isInteger(value.maxLength)?value.maxLength:IMAGE_LENGTH,IMAGE_LENGTH);
   return `<label class="phema-image-field">${label}<input type="file" accept="${IMAGE_TYPES.join(',')}" data-structured-image data-media-type="${esc(value.contentMediaType)}" data-max-length="${limit}" ${attribute('aria-label',value.title||path.at(-1))} ${required?'required':''}>`
    +`<input type="hidden" name="${name}" data-structured-field data-value-type="string" data-image-value><small data-image-note hidden></small>`
    +`<button type="button" data-image-clear hidden>${text('Remove image')}</button>`
    +`<small data-image-message="busy" hidden>${text('Preparing the image…')}</small>`
    +`<small data-image-message="type" role="alert" hidden>${text('Choose a PNG, JPEG or WebP image up to 10 MB and 40 megapixels.')}</small>`
    +`<small data-image-message="large" role="alert" hidden>${text('This image is still too large after shrinking. Choose a simpler one.')}</small></label>`;
  }
  if(Object.hasOwn(value,'const'))return `<input ${attrs} type="hidden" value="${esc(JSON.stringify(value.const))}" data-constant="true">`;
  if(type==='object'){
   const children=Object.entries(value.properties||{}).slice(0,30).map(([key,item])=>field(item,[...path,key],(value.required||[]).includes(key),depth+1)).join('');
   return children?`<fieldset class="phema-structured-group"><legend>${label}</legend><div class="phema-form-grid">${children}</div></fieldset>`:'';
  }
  const options=choices(type==='array'?value.items||{}:value);
  if(options){
   const defaults=type==='array'?(Array.isArray(value.default)?value.default:[]):[value.default];
   return `<label>${label}<select ${attrs} ${type==='array'?'multiple data-item-type="'+esc(value.items?.type||'string')+'"':''}>${!required&&type!=='array'?'<option value=""></option>':''}${options.slice(0,500).map(({value:item,title})=>`<option value="${esc(JSON.stringify(item))}" ${attribute('label',title)} ${defaults.includes(item)?'selected':''}>${esc(title)}</option>`).join('')}</select></label>`;
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

async function base64(blob){
 const bytes=new Uint8Array(await blob.arrayBuffer());let text='';
 for(let i=0;i<bytes.length;i+=0x8000)text+=String.fromCharCode(...bytes.subarray(i,i+0x8000));
 return btoa(text);
}
async function shrink(image,type,limit){
 for(const edge of [IMAGE_EDGE,1280,1024,768]){
  const scale=Math.min(1,edge/Math.max(image.width,image.height)),width=Math.max(1,Math.round(image.width*scale)),height=Math.max(1,Math.round(image.height*scale));
  const canvas=document.createElement('canvas');canvas.width=width;canvas.height=height;const context=canvas.getContext('2d');
  if(type==='image/jpeg'){context.fillStyle='#ffffff';context.fillRect(0,0,width,height);}  // JPEG has no transparency
  context.drawImage(image,0,0,width,height);
  for(const quality of type==='image/png'?[undefined]:[0.85,0.7,0.55]){
   const blob=await new Promise(resolve=>canvas.toBlob(resolve,type,quality));
   if(!blob||blob.type!==type)return null;
   const data=await base64(blob);
   if(data.length<=limit)return {data,width,height};
  }
 }
 return null;
}
async function pickImage(input){
 const holder=input.closest('.phema-image-field');if(!holder)return;
 const value=holder.querySelector('[data-image-value]'),note=holder.querySelector('[data-image-note]'),clear=holder.querySelector('[data-image-clear]');
 const say=kind=>holder.querySelectorAll('[data-image-message]').forEach(node=>{node.hidden=node.dataset.imageMessage!==kind;});
 value.value='';note.hidden=true;clear.hidden=true;say(null);input.setCustomValidity('');
 const file=input.files?.[0];if(!file)return;
 // Until the picture is ready the form does not submit, so a design never leaves without the image it shows.
 say('busy');input.setCustomValidity(holder.querySelector('[data-image-message="busy"]').textContent||'Preparing the image…');
 let image=null;
 try{
  if(!IMAGE_TYPES.includes(file.type)||file.size>MAX_FILE_BYTES)throw new Error('type');
  image=await createImageBitmap(file);
  if(image.width*image.height>MAX_PIXELS)throw new Error('type');
  const encoded=await shrink(image,input.dataset.mediaType,Number(input.dataset.maxLength)||IMAGE_LENGTH);
  if(!encoded)throw new Error('large');
  value.value=encoded.data;note.textContent=`${file.name} · ${encoded.width}×${encoded.height}`;note.hidden=false;clear.hidden=false;say(null);
 }catch(error){input.value='';say(error?.message==='large'?'large':'type');}
 finally{image?.close?.();input.setCustomValidity('');}
}
if(typeof document!=='undefined'){
 document.addEventListener('change',event=>{if(event.target?.matches?.('input[type="file"][data-structured-image]'))pickImage(event.target);});
 document.addEventListener('click',event=>{
  const button=event.target?.closest?.('[data-image-clear]');if(!button)return;
  const holder=button.closest('.phema-image-field'),input=holder?.querySelector('[data-structured-image]');if(!input)return;
  input.value='';pickImage(input);
 });
}
