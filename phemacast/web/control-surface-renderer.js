/** Explicit registration of the neutral data surface; no application code. */
import {mountControlSurface} from './control-surface.js';

export function registerControlSurface(registerRenderer){
 registerRenderer({id:'phemacast:control-surface',version:'1.0.0',features:['structured-forms','tabs','dialogs','tables','series-charts'],
  render:()=>'<div class="control-surface"></div>',
  mount(host,value){
   const surface=value?.surface||value;
   return mountControlSurface(host.querySelector('.control-surface'),surface,{invoke:(reference,inputs)=>new Promise((resolve,reject)=>{
    host.dispatchEvent(new CustomEvent('phemacast:surface-action',{bubbles:true,detail:{reference,inputs,resolve,reject}}));
   })});
  }});
}
