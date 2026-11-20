/** Render the same owner result after acknowledgement or mounted-state refresh. */
import {navigationHTML} from './operation-result-navigation.js';
export function interactionResultHTML(value,{escape,render,text=x=>x,plainText=text}){
 if(value==null)return '';
 const type=value?.values&&!Array.isArray(value.values)?'phemacast:display.key-values':
  Array.isArray(value?.columns)&&Array.isArray(value?.rows)?'phemacast:display.table':null;
 // The owner wrote the reply for this form, so its words are shown through the Phema's UI catalog, as the form's own labels are.
 if(type){const html=render({id:type,version:'1.0.0'},value,{authored:true});if(html!==null)return html;}
 const message=typeof value==='string'?value:value?.text||value?.comment||value?.content||plainText('Operation completed.');
 return `<p>${escape(typeof message==='string'?message:plainText('Operation completed.'))}</p>${navigationHTML(value,escape)}`;
}
