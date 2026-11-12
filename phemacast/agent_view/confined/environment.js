/* Evaluated inside QuickJS only. All host crossings are bounded JSON strings. */
(()=>{
 const crossing=(method,args=[])=>JSON.parse(__surface(method,JSON.stringify(args))),nodes=new Map(),handlers=new Map();let next=0;
 const plainNode=value=>value instanceof Element?{node:value.__id}:value;
 const wrap=id=>id==null?null:(nodes.get(id)||new Element(id));
 const matrix=value=>value==null?null:{...value,inverse(){const {a,b,c,d,e,f}=this,k=a*d-b*c;return matrix({a:d/k,b:-b/k,c:-c/k,d:a/k,e:(c*f-d*e)/k,f:(b*e-a*f)/k});}};
 class Element{
  constructor(id){this.__id=id;nodes.set(id,this);this.dataset=new Proxy({},{get:(_,key)=>crossing('getAttr',[id,'data-'+String(key).replace(/[A-Z]/g,c=>'-'+c.toLowerCase())]),set:(_,key,value)=>{crossing('attr',[id,'data-'+String(key).replace(/[A-Z]/g,c=>'-'+c.toLowerCase()),value]);return true;},deleteProperty:(_,key)=>{crossing('removeAttr',[id,'data-'+String(key).replace(/[A-Z]/g,c=>'-'+c.toLowerCase())]);return true;}});
   this.style=new Proxy({},{set:(_,key,value)=>{crossing('style',[id,key,value]);return true;},get:(_,key)=>key==='setProperty'?((name,value)=>crossing('attr',[id,'style',name+':'+value])):undefined});
   this.classList={add:(...names)=>crossing('class',[id,'add',...names]),remove:(...names)=>crossing('class',[id,'remove',...names]),toggle:(...names)=>crossing('class',[id,'toggle',...names]),contains:name=>crossing('class',[id,'contains',name])};
  }
  setAttribute(name,value){crossing('attr',[this.__id,name,value]);}getAttribute(name){return crossing('getAttr',[this.__id,name]);}hasAttribute(name){return this.getAttribute(name)!==null;}removeAttribute(name){crossing('removeAttr',[this.__id,name]);}
  querySelector(selector){return wrap(crossing('query',[this.__id,selector]));}querySelectorAll(selector){return crossing('queryAll',[this.__id,selector]).map(wrap);}closest(selector){return wrap(crossing('closest',[this.__id,selector]));}matches(selector){return crossing('matches',[this.__id,selector]);}
  contains(other){return !!other&&crossing('contains',[this.__id,other.__id]);}
  append(...items){crossing('append',[this.__id,...items.map(plainNode)]);}prepend(...items){crossing('prepend',[this.__id,...items.map(plainNode)]);}replaceChildren(...items){crossing('replaceChildren',[this.__id,...items.map(plainNode)]);}replaceWith(...items){crossing('replaceWith',[this.__id,...items.map(plainNode)]);}after(...items){crossing('after',[this.__id,...items.map(plainNode)]);}before(...items){crossing('before',[this.__id,...items.map(plainNode)]);}remove(){crossing('remove',[this.__id]);}
  get content(){return wrap(crossing('content',[this.__id]));}get firstElementChild(){return this.children[0]||null;}get nodeType(){return 1;}get children(){return crossing('children',[this.__id]).map(wrap);}get options(){return crossing('options',[this.__id]).map(wrap);}get parentNode(){return wrap(crossing('parent',[this.__id]));}get parentElement(){return this.parentNode;}
  get innerHTML(){return crossing('get',[this.__id,'innerHTML']);}set innerHTML(value){crossing('html',[this.__id,value]);}
  getBoundingClientRect(){return crossing('rect',[this.__id]);}getScreenCTM(){return matrix(crossing('matrix',[this.__id]));}createSVGPoint(){return {x:0,y:0,matrixTransform(m){return {x:m.a*this.x+m.c*this.y+m.e,y:m.b*this.x+m.d*this.y+m.f};}};}
  focus(){crossing('focus',[this.__id]);}
  addEventListener(type,fn){const id='event-'+(++next);handlers.set(id,{fn,node:this,type});crossing('listen',[this.__id,type,id]);}
  removeEventListener(type,fn){for(const [id,h]of handlers)if(h.node===this&&h.type===type&&h.fn===fn){crossing('unlisten',[this.__id,type,id]);handlers.delete(id);}}
 }
 for(const key of 'textContent id className lang title value type checked selected disabled hidden open required multiple tabIndex min max step name placeholder pattern maxLength rows cols scrollLeft scrollTop'.split(' '))Object.defineProperty(Element.prototype,key,{get(){return crossing('get',[this.__id,key]);},set(value){crossing('set',[this.__id,key,value]);}});
 for(const key of 'clientWidth clientHeight offsetWidth offsetHeight scrollHeight scrollWidth tagName nodeName isConnected selectionStart selectionEnd'.split(' '))Object.defineProperty(Element.prototype,key,{get(){return crossing('get',[this.__id,key]);}});
 for(const type of ['click','change','input','submit','toggle'])Object.defineProperty(Element.prototype,'on'+type,{set(fn){if(this['__on'+type])this.removeEventListener(type,this['__on'+type]);this['__on'+type]=fn;if(typeof fn==='function')this.addEventListener(type,fn);}});
 const root=wrap(1),documentHandlers=new Map();
 globalThis.document={createElement:tag=>wrap(crossing('create',[String(tag).toLowerCase()])),createElementNS:(ns,tag)=>wrap(crossing('createNS',[ns,tag])),createTextNode:text=>wrap(crossing('text',[text])),querySelector:s=>root.querySelector(s),querySelectorAll:s=>root.querySelectorAll(s),body:root,documentElement:root,visibilityState:'visible',addEventListener(type,fn){const listeners=documentHandlers.get(type)||new Set();listeners.add(fn);documentHandlers.set(type,listeners);},removeEventListener(type,fn){documentHandlers.get(type)?.delete(fn);},dispatchEvent(event){for(const fn of documentHandlers.get(event.type)||[])fn(event);return true;}};
 globalThis.CustomEvent=class{constructor(type,{detail}={}){this.type=type;this.detail=detail;}};
 globalThis.Option=function(text,value){const node=document.createElement('option');node.textContent=text;node.value=value;return node;};
 globalThis.HTMLElement=globalThis.Element=Element;
 globalThis.__deliverEvent=(id,event)=>{const handler=handlers.get(id);if(handler)handler.fn({...event,target:wrap(event.target),currentTarget:wrap(event.currentTarget),relatedTarget:wrap(event.relatedTarget),preventDefault(){},stopPropagation(){}});};
 globalThis.Intl={NumberFormat:function(locale,options){return {format:value=>__format('number',JSON.stringify([locale,options,value])),resolvedOptions:()=>JSON.parse(__format('numberOptions',JSON.stringify([locale,options])))};},DateTimeFormat:function(locale,options){return {format:value=>__format('date',JSON.stringify([locale,options,Number(value??Date.now())])),formatToParts:value=>JSON.parse(__format('parts',JSON.stringify([locale,options,Number(value)]))),resolvedOptions:()=>JSON.parse(__format('dateOptions',JSON.stringify([locale,options])))};},supportedValuesOf:key=>JSON.parse(__format('supported',JSON.stringify([key])))};
 globalThis.crypto=Object.freeze({randomUUID:()=>__random()});
 globalThis.console=Object.freeze({log(){},warn(){},error(){},info(){}});
 const timers=new Map();globalThis.setTimeout=(fn,ms=0)=>{const id='timer-'+(++next);timers.set(id,fn);__timer(id,Math.min(30000,Math.max(0,Number(ms))));return id;};globalThis.clearTimeout=id=>{timers.delete(id);};globalThis.__deliverTimer=id=>{const fn=timers.get(id);timers.delete(id);fn?.();};
 // A presentation change (such as the container's theme) updates context and tells the view on document.
 let context=JSON.parse(__context());
 globalThis.__deliverContext=value=>{context={...context,...value};document.dispatchEvent(new CustomEvent('phemacast:context',{detail:context}));};
 globalThis.phemacastAgentView=Object.freeze({get context(){return context;},requestKey:()=>__random(),invoke:value=>__operation('invoke',JSON.stringify(value)),pickImage:()=>__operation('pick-image','{}'),pickTextFile:()=>__operation('pick-text-file','{}'),setMode:value=>__operation('set-mode',JSON.stringify(value)),status:value=>__operation('status',JSON.stringify(value)),setPresentation:value=>__operation('presentation',JSON.stringify(value)),
  parameters:()=>__operation('parameters','{}'),setParameters:value=>__operation('set-parameters',JSON.stringify(value)),
  saveSnapshot:value=>__operation('save-snapshot',JSON.stringify(value)),snapshots:()=>__operation('snapshots','{}'),readSnapshot:id=>__operation('read-snapshot',JSON.stringify({id})),
  schedule:value=>__operation('schedule',JSON.stringify(value)),runs:()=>__operation('runs','{}'),runNow:value=>__operation('run-now',JSON.stringify(value)),stopRun:value=>__operation('stop-run',JSON.stringify(value)),
  kept:Object.freeze({read:key=>__operation('kept-read',JSON.stringify({key})),write:(key,value)=>__operation('kept-write',JSON.stringify({key,value})),drop:key=>__operation('kept-drop',JSON.stringify({key}))})});
})();
