/** Closed DOM surface. Every return is JSON; browser objects never enter the VM. */
const tags=new Set('template main section header footer article aside nav div span p strong em small b i label input select option optgroup button details summary figure figcaption table thead tbody tfoot tr th td dl dt dd ul ol li h1 h2 h3 h4 form fieldset legend textarea pre code svg g path circle line polyline polygon rect text tspan defs marker title desc'.split(' '));
const readable=new Set('textContent innerHTML id className lang title value type checked selected disabled hidden open required multiple tabIndex min max step name placeholder pattern maxLength rows cols clientWidth clientHeight offsetWidth offsetHeight scrollHeight scrollWidth scrollLeft scrollTop tagName nodeName isConnected selectionStart selectionEnd'.split(' '));
const writable=new Set('textContent id className lang title value type checked selected disabled hidden open required multiple tabIndex min max step name placeholder pattern maxLength rows cols scrollLeft scrollTop'.split(' '));
export function safeCSS(css){
 if(typeof css!=='string'||css.length>120000||/[\\\u0000]|@import|@namespace|url\s*\(|image-set\s*\(|expression\s*\(|-moz-binding/i.test(css))throw Error('Network-capable CSS is unavailable in a confined view');return css;
}
const NODE_LIMIT=30000,IDLE_MS=5000;
export class DrawingSurface {
 constructor(root,dispatch,now=()=>performance.now()){this.root=root;this.dispatch=dispatch;this.now=now;this.nodes=new Map([[1,root]]);this.next=1;this.ids=new WeakMap([[root,1]]);this.used=new Map([[1,now()]]);this.listeners=[];this.dormant=new Map();}
 id(node){if(!node)return null;if(this.ids.has(node)){const id=this.ids.get(node);this.used.set(id,this.now());return id;}
  if(this.nodes.size>=NODE_LIMIT)this.sweep();if(this.nodes.size>=NODE_LIMIT)throw Error('Drawing surface node limit exceeded');
  const id=++this.next;this.nodes.set(id,node);this.ids.set(node,id);this.used.set(id,this.now());return id;}
 node(id){const node=this.nodes.get(id);if(!node)throw Error('Node is outside this drawing surface');this.used.set(id,this.now());return node;}
 // A view that redraws makes new nodes and drops the old ones; kept forever, their ids reach the node limit and a
 // long-open view stops drawing. At the limit, forget nodes that are off the page and untouched
 // for a while, with any handlers resting on them; a node just made and not yet placed stays.
 sweep(){
  const gone=new Set(),now=this.now();
  for(const [id,node] of this.nodes)if(id!==1&&!node.isConnected&&now-(this.used.get(id)??0)>IDLE_MS){gone.add(node);this.nodes.delete(id);this.ids.delete(node);this.used.delete(id);this.dormant.delete(node);}
  if(gone.size)this.listeners=this.listeners.filter(l=>{if(!gone.has(l.node))return true;l.node.removeEventListener(l.type,l.callback);return false;});
 }
 // An element off the page cannot receive events, so its handlers rest here instead of counting toward the view's limit,
 // and come back when the view puts the element back on the page. A long-open view that redraws no longer runs out.
 sleep(){
  const live=[];
  for(const item of this.listeners){
   if(item.node.isConnected){live.push(item);continue;}
   item.node.removeEventListener(item.type,item.callback);
   const resting=this.dormant.get(item.node);if(resting)resting.push(item);else this.dormant.set(item.node,[item]);
  }
  this.listeners=live;
 }
 // Only what was just inserted can come back onto the page, so only it is searched; a moved fragment is already empty.
 inserted(parent,items){
  if(!this.dormant.size||!parent?.isConnected)return;
  const wake=node=>{const resting=this.dormant.get(node);if(!resting)return;this.dormant.delete(node);for(const item of resting){node.addEventListener(item.type,item.callback);this.listeners.push(item);}};
  if(items.some(item=>item.nodeType===11)){for(const node of [...this.dormant.keys()])if(node.isConnected)wake(node);return;}
  for(const item of items){if(item.nodeType!==1)continue;wake(item);if(item.firstElementChild)for(const inner of item.querySelectorAll('*'))if(this.dormant.has(inner))wake(inner);}
 }
 attr(node,name,value){
  if(typeof name!=='string'||!/^(?:[A-Za-z][A-Za-z0-9:_.-]*)$/.test(name)||/^on|^src|^href$|^action$|^formaction$|^ping$|^xmlns$|^xlink:href$|^is$|^integrity$|^nonce$|^content$|^http-equiv$/i.test(name))throw Error('Active or network attributes are unavailable');
  if(name==='style')value=safeCSS(String(value));
  if(/\\|url\s*\(/i.test(String(value))&&!/^url\(#[A-Za-z][A-Za-z0-9_-]{0,200}\)$/.test(String(value)))throw Error('Network-capable drawing attributes are unavailable');
  if(String(value).length>120000)throw Error('Drawing attribute exceeds its limit');
  node.setAttribute(name,String(value));
 }
 markup(node,text){
  if(typeof text!=='string'||text.length>300000)throw Error('Markup exceeds its limit');
  // Template parsing is inert. Validate the complete fragment before insertion.
  const template=document.createElement('template'),svg=node.namespaceURI==='http://www.w3.org/2000/svg';
  template.innerHTML=svg?'<svg>'+text+'</svg>':text;
  for(const child of template.content.querySelectorAll('*')){
   if(!tags.has(child.localName))throw Error('Active or network element is unavailable');
   for(const attr of [...child.attributes])this.attr(child,attr.name,attr.value);
  }
  (node.localName==='template'?node.content:node).replaceChildren(...(svg?template.content.firstElementChild.childNodes:template.content.childNodes));
 }
 call(method,args){
  if(!Array.isArray(args)||args.length>10000||JSON.stringify(args).length>300000)throw Error('Drawing request exceeds its limit');
  const [id,...rest]=args;let node;
  if(method==='create'){if(!tags.has(id))throw Error('Element is unavailable');return this.id(document.createElement(id));}
  if(method==='createNS'){if(id!=='http://www.w3.org/2000/svg'||!tags.has(rest[0]))throw Error('Namespace element is unavailable');return this.id(document.createElementNS(id,rest[0]));}
  if(method==='text')return this.id(document.createTextNode(String(id)));
  node=this.node(id);
  const selected=selector=>{if(typeof selector!=='string'||selector.length>1000)throw Error('Invalid selector');return selector;};
  const child=value=>value&&typeof value==='object'&&Object.keys(value).length===1&&Number.isInteger(value.node)?this.node(value.node):document.createTextNode(String(value??''));
  switch(method){
   case 'get':if(!readable.has(rest[0]))throw Error('Property is unavailable');return node[rest[0]]??null;
   case 'set':if(!writable.has(rest[0]))throw Error('Property is unavailable');node[rest[0]]=rest[1];return null;
   case 'html':this.markup(node,rest[0]);return null;
   case 'attr':this.attr(node,rest[0],rest[1]);return null;
   case 'getAttr':return node.getAttribute(rest[0]);
   case 'removeAttr':node.removeAttribute(rest[0]);return null;
   case 'query':return this.id(node.querySelector(selected(rest[0])));
   case 'queryAll':return [...node.querySelectorAll(selected(rest[0]))].map(n=>this.id(n));
   case 'closest':{const match=node.closest(selected(rest[0]));return match&&(match===this.root||this.root.contains(match))?this.id(match):null;}
   case 'matches':return node.matches(selected(rest[0]));
   case 'contains':return !!rest[0]&&node.contains(this.node(rest[0]));
   case 'content':if(node.localName!=='template')throw Error('Content is unavailable');return this.id(node.content);
   case 'children':return [...node.children].map(n=>this.id(n));
   case 'options':return [...(node.options||[])].map(n=>this.id(n));
   case 'parent':return node===this.root?null:this.id(node.parentNode);
   case 'append':{const items=rest.map(child);node.append(...items);this.inserted(node,items);return null;}
   case 'prepend':{const items=rest.map(child);node.prepend(...items);this.inserted(node,items);return null;}
   case 'replaceChildren':{const items=rest.map(child);node.replaceChildren(...items);this.inserted(node,items);return null;}
   case 'replaceWith':{if(node===this.root)throw Error('The mount root cannot be replaced');const parent=node.parentNode,items=rest.map(child);node.replaceWith(...items);this.inserted(parent,items);return null;}
   case 'after':{if(node===this.root)throw Error('The mount root has no external siblings');const parent=node.parentNode,items=rest.map(child);node.after(...items);this.inserted(parent,items);return null;}
   case 'before':{if(node===this.root)throw Error('The mount root has no external siblings');const parent=node.parentNode,items=rest.map(child);node.before(...items);this.inserted(parent,items);return null;}
   case 'remove':if(node===this.root)throw Error('The mount root cannot be removed');node.remove();return null;
   case 'style':{const [key,value]=rest;if(!/^[-A-Za-z][A-Za-z0-9-]{0,80}$/.test(key))throw Error('Invalid style property');node.style[key]=safeCSS(String(value));return null;}
   case 'class':{const [op,...names]=rest;if(!['add','remove','toggle','contains'].includes(op)||names.some(v=>typeof v!=='string'&&typeof v!=='boolean'))throw Error('Invalid class action');return node.classList[op](...names)??null;}
   case 'rect':{const r=node.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height,top:r.top,right:r.right,bottom:r.bottom,left:r.left};}
   case 'matrix':{const m=node.getScreenCTM?.();return m?{a:m.a,b:m.b,c:m.c,d:m.d,e:m.e,f:m.f}:null;}
   case 'focus':node.focus?.({preventScroll:true});return null;
   case 'listen':{const [type,handler]=rest;if(typeof type!=='string'||!/^(?:click|change|input|submit|toggle|keydown|keyup|pointerdown|pointermove|pointerleave|pointerup|focus|blur)$/.test(type)||typeof handler!=='string')throw Error('Event is unavailable');
    if(this.listeners.length>1000)this.sleep();
    if(this.listeners.length>1000)throw Error('Event is unavailable');
    const callback=event=>{if(type==='submit'||type==='keydown'&&['ArrowLeft','ArrowRight','Home','End','Escape'].includes(event.key))event.preventDefault();this.dispatch(handler,{type,key:event.key||'',clientX:event.clientX||0,clientY:event.clientY||0,button:event.button||0,target:this.id(event.target),currentTarget:id,relatedTarget:event.relatedTarget&&this.root.contains(event.relatedTarget)?this.id(event.relatedTarget):null});};
    node.addEventListener(type,callback);this.listeners.push({node,type,callback,handler});return null;}
   case 'unlisten':{const [type,handler]=rest,same=l=>l.node===node&&l.type===type&&l.handler===handler;for(const item of this.listeners.filter(same))node.removeEventListener(type,item.callback);this.listeners=this.listeners.filter(l=>!same(l));
    const resting=this.dormant.get(node)?.filter(l=>!same(l));if(resting?.length)this.dormant.set(node,resting);else this.dormant.delete(node);return null;}
   default:throw Error('Undeclared drawing operation');
  }
 }
 dispose(){for(const l of this.listeners)l.node.removeEventListener(l.type,l.callback);this.listeners=[];this.dormant.clear();this.nodes.clear();this.used.clear();this.root.replaceChildren();}
}
