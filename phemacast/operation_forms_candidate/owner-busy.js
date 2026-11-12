/** Generic client candidate; installation is separate from owner adoption. */
import {createOwnerBusy as ownerBusy} from '../agent_view/owner_busy.js';
export function createOwnerBusy(root,options={}){
 if(!document.querySelector('link[data-operation-feedback-style]')){
  const link=document.createElement('link');link.rel='stylesheet';link.href=new URL('./owner-busy.css',import.meta.url).href;link.dataset.operationFeedbackStyle='true';document.head.append(link);
 }
 return ownerBusy(root,options);
}
