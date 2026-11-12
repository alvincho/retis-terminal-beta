/** App Consultant authored labels; a host language never rewrites data or IDs. */
import {PHEMA_MESSAGES} from './phema-ui-messages.js';
import {MESSAGES} from './ui-messages.js';
import {uiBundle} from './phema-i18n.js';
export function withPhemaUI(entry){
 const keys=[entry.name,entry.design.title,...entry.design.layout.children.map(n=>n.title),...(entry.design.layout.tabbedView?.tabs||[]).map(t=>t.title),...entry.design.dataRequirements.map(r=>r.label)];
 const native={};
 const authored=Object.fromEntries(keys.filter(k=>PHEMA_MESSAGES[k]).map(k=>[k,PHEMA_MESSAGES[k]]));
 const messages={...authored,...native,...Object.fromEntries(keys.filter(k=>MESSAGES[k]).map(k=>[k,MESSAGES[k]])),...uiBundle(entry)?.messages};
 if(entry.baseLayoutId==='content'&&entry.extensions?.['phemacast:layout']){
  const next=structuredClone(entry),document=next.extensions['phemacast:layout'];document.extensions={...document.extensions,'phemacast:ui':{version:1,defaultLocale:'en',messages}};return next;
 }
 return {...entry,extensions:{...entry.extensions,'phemacast:ui':{version:1,defaultLocale:'en',messages}}};
}
