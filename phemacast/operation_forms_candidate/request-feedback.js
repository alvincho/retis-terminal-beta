/** Generic asynchronous request feedback; input, authority and results are opaque. */
import {createOwnerBusy} from './owner-busy.js';
export function withRequestFeedback(request,{root=()=>document.body,label,locale=()=>document.documentElement.lang||'en'}={}){
 let busy;
 const wrapped=function(...args){
  if(!busy)busy=createOwnerBusy(typeof root==='function'?root():root,{label,locale});
  return busy.run(()=>request.apply(this,args));
 };
 wrapped.dispose=()=>busy?.dispose();
 return wrapped;
}
