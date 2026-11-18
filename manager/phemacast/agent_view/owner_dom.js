// Portable view controls using only the existing confined drawing primitives.
export function cloneViewValue(value){return JSON.parse(JSON.stringify(value));}
export function viewFields(form){return new Proxy({}, {get:(_,name)=>[...form.querySelectorAll('[name]')].find(node=>node.name===String(name))});}
export function viewFormEntries(form){
 const values=[];
 for(const node of form.querySelectorAll('input,select,textarea')){
  if(!node.name||node.disabled||['submit','button','reset','file'].includes(node.type))continue;
  if(['checkbox','radio'].includes(node.type)&&!node.checked)continue;
  if(node.tagName.toLowerCase()==='select'&&node.multiple){for(const option of node.options)if(option.selected)values.push([node.name,option.value]);}
  else values.push([node.name,node.value]);
 }
 return values;
}
export function appendViewMarkup(node,markup){const template=document.createElement('template');template.innerHTML=markup;node.append(template.content);}
export function createViewDialog(parent,markup,className){
 const node=document.createElement('section'),callbacks=new Set();let closed=false;
 node.className=className;node.setAttribute('role','dialog');node.setAttribute('aria-modal','true');node.innerHTML=markup;
 node.pmOnClose=callback=>callbacks.add(callback);
 node.showModal=()=>{closed=false;node.setAttribute('open','');node.hidden=false;};
 node.close=()=>{if(closed)return;closed=true;node.hidden=true;node.removeAttribute('open');for(const fn of [...callbacks])fn();callbacks.clear();if(typeof node.onclose==='function')node.onclose();};
 parent.append(node);node.showModal();return node;
}
