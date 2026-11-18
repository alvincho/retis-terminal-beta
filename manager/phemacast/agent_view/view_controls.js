/** Optional portable controls built only from the confined drawing primitives.
 * These helpers are bundled by publishers; they expose no browser objects,
 * credentials, network, storage, URLs, frame navigation or container authority.
 */
export function installViewControls(){
 if(typeof window!=='undefined')return;
 if(typeof structuredClone!=='function')globalThis.structuredClone=value=>JSON.parse(JSON.stringify(value));
 const create=document.createElement.bind(document),root=document.documentElement;
 const prototype=Object.getPrototypeOf(root);
 if(typeof root.dispatchEvent!=='function')prototype.dispatchEvent=event=>document.dispatchEvent(event);
 if(typeof root.insertBefore!=='function')prototype.insertBefore=function(child,before){before?before.before(child):this.append(child);return child;};
 if(!('lastElementChild' in root))Object.defineProperty(prototype,'lastElementChild',{get(){return this.children.at(-1)||null;}});
 if(!('lastChild' in root))Object.defineProperty(prototype,'lastChild',{get(){return this.lastElementChild;}});
 const closest=prototype.closest;
 prototype.closest=function(selector){
  const attached=closest.call(this,selector);if(attached)return attached;
  for(let node=this;node;node=node.parentNode){if(node.matches(selector))return node;if(node===root)break;}
  return null;
 };
 function valid(node){
  if(node.disabled)return true;
  const value=String(node.value??'');if(node.__validationError||node.required&&!value.trim())return false;
  if(node.pattern&&value&&!new RegExp('^(?:'+node.pattern+')$').test(value))return false;
  if(Number(node.minLength)>0&&value.length<Number(node.minLength)||typeof node.maxLength==='number'&&node.maxLength>=0&&value.length>node.maxLength)return false;
  if(['number','range'].includes(node.type)&&value){const number=Number(value);
   if(!Number.isFinite(number)||node.min!==''&&number<Number(node.min)||node.max!==''&&number>Number(node.max))return false;
   if(node.step&&node.step!=='any'&&Number(node.step)>0){const steps=(number-Number(node.min||0))/Number(node.step);if(Math.abs(steps-Math.round(steps))>1e-8)return false;}
  }
  return true;
 }
 function dialog(){
  const node=create('section'),listeners=new Map([['close',new Map()],['cancel',new Map()]]);let opened=false;
  node.setAttribute('role','dialog');node.setAttribute('aria-modal','true');node.hidden=true;
  node.showModal=()=>{opened=true;node.open=true;node.hidden=false;node.setAttribute('open','');
   Object.assign(node.style,{position:'fixed',inset:'5%',maxHeight:'85vh',overflow:'auto',zIndex:'1000',boxShadow:'0 0 0 100vmax #0007'});};
  node.close=()=>{if(!opened)return;opened=false;node.open=false;node.hidden=true;node.removeAttribute('open');
   for(const [fn,once] of [...listeners.get('close')]){fn();if(once)listeners.get('close').delete(fn);}node.onclose?.();};
  const add=node.addEventListener.bind(node),remove=node.removeEventListener.bind(node);
  node.addEventListener=(type,fn,options={})=>listeners.has(type)?listeners.get(type).set(fn,options.once===true):add(type,fn,options);
  node.removeEventListener=(type,fn,...options)=>listeners.has(type)?listeners.get(type).delete(fn):remove(type,fn,...options);
  add('keydown',event=>{if(event.key==='Escape'){let cancelled=false;const value={preventDefault(){cancelled=true;}};
   for(const fn of listeners.get('cancel').keys())fn(value);node.oncancel?.(value);if(!cancelled)node.close();}});
  return node;
 }
 document.createElement=tag=>{
  tag=String(tag).toLowerCase();const node=tag==='dialog'?dialog():create(tag);
  const data=node.dataset,cache=new Map();
  node.dataset=new Proxy({}, {
   get:(_,key)=>cache.has(key)?cache.get(key):data[key],
   set:(_,key,value)=>{cache.set(key,String(value));data[key]=value;return true;},
   deleteProperty:(_,key)=>{cache.delete(key);delete data[key];return true;}
  });
  Object.defineProperty(node,'firstChild',{get:()=>node.firstElementChild});
  if(['table','thead','tbody','tfoot'].includes(tag))Object.defineProperty(node,'rows',{get:()=>node.querySelectorAll('tr')});
  if(tag==='tr')Object.defineProperty(node,'cells',{get:()=>node.querySelectorAll('th,td')});
  if(['input','select','textarea'].includes(tag)){
   node.setCustomValidity=text=>{node.__validationError=String(text);};node.checkValidity=()=>valid(node);node.reportValidity=node.checkValidity;
   Object.defineProperty(node,'validity',{get:()=>({valid:valid(node)})});
   Object.defineProperty(node,'validationMessage',{get:()=>node.__validationError||'Enter a valid value.'});
   if(tag==='select')Object.defineProperty(node,'selectedOptions',{get:()=>node.options.filter(option=>option.selected)});
  }
  if(tag==='form'){node.checkValidity=()=>[...node.querySelectorAll('input,select,textarea')].every(valid);node.requestSubmit=()=>{if(node.checkValidity())node.__onsubmit?.({target:node,currentTarget:node,preventDefault(){},stopPropagation(){}});};}
  node.click=()=>node.__onclick?.({target:node,currentTarget:node,preventDefault(){},stopPropagation(){}});
  for(const type of ['keydown','keyup','pointermove','pointerleave','pointerdown','pointerup','focus','blur']){
   Object.defineProperty(node,'on'+type,{set(fn){if(node['__handler'+type])node.removeEventListener(type,node['__handler'+type]);node['__handler'+type]=fn;if(typeof fn==='function')node.addEventListener(type,fn);}});
  }
  return node;
 };
 globalThis.window=Object.freeze({addEventListener:document.addEventListener.bind(document),removeEventListener:document.removeEventListener.bind(document)});
 const intervals=new Map();let counter=0;
 globalThis.setInterval=(fn,ms)=>{const id=++counter;const tick=()=>{if(!intervals.has(id))return;fn();if(intervals.has(id))intervals.set(id,setTimeout(tick,ms));};intervals.set(id,setTimeout(tick,ms));return id;};
 globalThis.clearInterval=id=>{clearTimeout(intervals.get(id));intervals.delete(id);};
 globalThis.MutationObserver=class{
  constructor(callback){this.callback=callback;this.nodes=[];this.timer=null;}
  observe(node,options){this.nodes.push({node,options,last:null});if(!this.timer)this.timer=setInterval(()=>{
   const changed=[];for(const item of this.nodes){const value=JSON.stringify([item.node.isConnected,
    ...(item.options.attributeFilter||[]).map(key=>item.node.getAttribute(key))]);if(item.last!==null&&value!==item.last)changed.push({target:item.node});item.last=value;}
   if(changed.length)this.callback(changed,this);
  },500);}
  disconnect(){clearInterval(this.timer);this.timer=null;this.nodes=[];}
 };
}
