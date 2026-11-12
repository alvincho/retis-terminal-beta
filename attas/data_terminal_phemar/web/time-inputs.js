import {message,scopedTranslate,setUIText,setUIAttribute,applyUI} from './i18n.js';
import {resolveTimezone,timezoneSource} from './time-display.js';
const parts=(time,zone)=>Object.fromEntries(new Intl.DateTimeFormat('en-GB',{timeZone:zone,calendar:'iso8601',numberingSystem:'latn',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'}).formatToParts(time).filter(p=>p.type!=='literal').map(p=>[p.type,Number(p.value)]));
const epoch=p=>{const d=new Date(0);d.setUTCFullYear(p.year,p.month-1,p.day);d.setUTCHours(p.hour,p.minute,p.second,p.millisecond||0);return +d;};
function failure(key,...args){const error=new Error(key.replace(/\{(\d+)\}/g,(_,i)=>args[i]));error.uiMessage=message(key,...args);return error;}
export function formatTimeInput(value,zone,seconds=false){
 const d=value instanceof Date?value:new Date(value);if(!Number.isFinite(+d))return '';
 const p=parts(d,zone),pad=n=>String(n).padStart(2,'0');
 return `${String(p.year).padStart(4,'0')}-${pad(p.month)}-${pad(p.day)}T${pad(p.hour)}:${pad(p.minute)}${seconds?':'+pad(p.second):''}`;
}
/** Find UTC candidates and reject skipped or repeated DST wall times explicitly. */
export function timeInputUTC(value,zone){
 const match=String(value).match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2})(?:\.(\d{1,3}))?)?$/);
 if(!match)throw failure('Enter a valid date and time.');
 const [year,month,day,hour,minute,second]=match.slice(1,7).map(v=>Number(v||0)),millisecond=Number((match[7]||'').padEnd(3,'0'));
 const expected={year,month,day,hour,minute,second,millisecond},wall=epoch(expected),d=new Date(wall);
 if(year<1||year>9999||d.getUTCFullYear()!==year||d.getUTCMonth()+1!==month||d.getUTCDate()!==day||d.getUTCHours()!==hour||d.getUTCMinutes()!==minute||d.getUTCSeconds()!==second)throw failure('Enter a valid date and time.');
 const offsets=new Set();for(const hours of [-36,-24,-12,0,12,24,36]){const at=wall+hours*3600000;offsets.add(epoch(parts(at,zone))-Math.floor(at/1000)*1000);}
 const candidates=[...offsets].map(offset=>wall-offset).filter(at=>{const p=parts(at,zone);return Object.entries(expected).every(([key,value])=>key==='millisecond'||p[key]===value);});
 if(!candidates.length)throw failure('This time does not exist in {0} because the clocks change. Choose another time.',zone);
 if(candidates.length>1)throw failure('This time occurs twice in {0} because the clocks change. Choose UTC or another time.',zone);
 return new Date(candidates[0]).toISOString();
}
/** Each input owns a visible zone label and retains its instant on a zone/context change. */
export function mountTimeInputs(inputs,{context=()=>({}),onChange=()=>{}}={}){
 let resolution=resolveTimezone(context());const errors=new Map(),badges=new Map();
 for(const input of inputs){
  const badge=document.createElement('small');badge.className='time-input-zone';
  const label=input.closest('label'),caption=label?.querySelector('[data-time-caption]')||label?.firstElementChild;
  if(caption?.tagName==='SPAN'&&!caption.dataset.uiKey)caption.append(badge);else label?.insertBefore(badge,input);
  badges.set(input,badge);input.dataset.timeZone=resolution.zone;
 }
 function validate(input){
  errors.delete(input);if(input.type==='date')return;
  input.setCustomValidity('');if(!input.value||input.disabled)return;
  try{timeInputUTC(input.value,resolution.zone);}catch(error){errors.set(input,error);input.setCustomValidity(scopedTranslate(input,error.uiMessage.uiMessage,...error.uiMessage.args));}
 }
 function refresh(notify=true){
  const next=resolveTimezone(context());
  if(next.zone!==resolution.zone)for(const input of inputs)if(input.type==='datetime-local'&&input.value){
   try{input.value=formatTimeInput(timeInputUTC(input.value,resolution.zone),next.zone,input.value.length>16);}catch{/* Keep an unfinished draft for validation in the newly labelled zone. */}
  }
  resolution=next;
  for(const input of inputs){
   input.dataset.timeZone=next.zone;validate(input);const badge=badges.get(input);
   setUIText(badge,' · {0} · {1}',message(timezoneSource(next.mode)),next.zone);
   setUIAttribute(input,'title','Time zone: {0} · {1}',next.zone,message(timezoneSource(next.mode)));applyUI(input);applyUI(badge);
  }
  if(notify)onChange();
 }
 const changed=event=>{validate(event.target);onChange();};
 for(const input of inputs)input.addEventListener('input',changed);
 document.addEventListener('terminal:timezone',refresh);document.addEventListener('ui-language:changed',refresh);refresh(false);
 let connected=inputs.some(input=>input.isConnected);const observer=new MutationObserver(()=>{if(inputs.some(input=>input.isConnected))connected=true;else if(connected)dispose();});observer.observe(document.body,{childList:true,subtree:true});
 function dispose(){observer.disconnect();for(const input of inputs)input.removeEventListener('input',changed);document.removeEventListener('terminal:timezone',refresh);document.removeEventListener('ui-language:changed',refresh);}
 return {zone:()=>resolution.zone,refresh,toUTC:input=>timeInputUTC(input.value,resolution.zone),error:()=>inputs.filter(input=>!input.disabled).map(input=>errors.get(input)).find(Boolean),validate:()=>inputs.forEach(validate),dispose};
}
