/** Explicit, bounded local text selection. No file paths or browser handles cross. */
export async function readTextFile(file){
 if(!file)return null;
 if(file.size>100000)throw Error('Choose a text file up to 100 KB.');
 const text=await file.text();
 if(text.includes('\0')||new TextEncoder().encode(text).length>100000)throw Error('Choose a UTF-8 text file up to 100 KB.');
 return {name:String(file.name).replace(/[\u0000-\u001f]/g,'').slice(0,160),text};
}
export function pickTextFile(root){
 if(!navigator.userActivation?.isActive)throw Error('Select a file from an explicit user action.');
 return new Promise((resolve,reject)=>{
  const input=document.createElement('input');input.type='file';input.accept='text/*,application/json,.csv,.geojson';input.hidden=true;root.append(input);
  const finish=(value,error)=>{input.remove();error?reject(error):resolve(value);};
  input.addEventListener('cancel',()=>finish(null),{once:true});
  input.onchange=()=>readTextFile(input.files?.[0]).then(value=>finish(value),error=>finish(null,error));
  input.click();
 });
}
