import {ui as uiHTML,label} from './i18n.js';
import {declaredTimezone} from './declared-timezone.js';
/** The selected zone controls presentation and wall-clock inputs, never source encoding. */
let preference='system',signature='';
export function validDisplayTimezone(value){if(['system','market'].includes(value))return true;if(typeof value!=='string'||value.length>100)return false;try{new Intl.DateTimeFormat('en-GB',{timeZone:value});return true;}catch{return false;}}
export const systemTimezone=()=>Intl.DateTimeFormat().resolvedOptions().timeZone||'UTC';
export function resolveTimezone(context={}){
 if(preference==='market'){const zone=declaredTimezone(context);return {zone:zone||systemTimezone(),mode:zone?'market':'system-fallback'};}
 return {zone:preference==='system'?systemTimezone():preference,mode:preference==='system'?'system':'specified'};
}
export function applyDisplayTimezone(value){
 preference=validDisplayTimezone(value)?value:'system';const next=preference+'|'+systemTimezone();
 if(next!==signature){signature=next;if(typeof document!=='undefined')document.dispatchEvent(new CustomEvent('terminal:timezone'));}
}
export const displayTimezone=context=>resolveTimezone(context).zone;
export function timezoneSource(mode){return {system:'System',market:'Market',specified:'Specified','system-fallback':'System (market unavailable)'}[mode];}
function date(value){if(value==null||value==='')return null;const d=new Date(typeof value==='number'?(Math.abs(value)<1e12?value*1000:value):value);return Number.isNaN(d.getTime())?null:d;}
export function displayTime(value,context){const d=date(value);return d?new Intl.DateTimeFormat('en-GB',{timeZone:displayTimezone(context),hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'}).format(d):'—';}
export function displayDateTime(value,context){
 if(typeof value==='string'&&/^\d{4}-\d{2}-\d{2}$/.test(value))return value;
 const d=date(value);if(!d)return '—';
 const parts=new Intl.DateTimeFormat('en-GB',{timeZone:displayTimezone(context),year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(d),get=type=>parts.find(p=>p.type===type).value;
 return `${get('year')}-${get('month')}-${get('day')} ${displayTime(d,context)}`;
}
export const displayTimeTitle=(value,context)=>`${displayDateTime(value,context)} · ${displayTimezone(context)}`;
export function timezoneSettingsHTML(value,specified){
 const selected=validDisplayTimezone(value)?value:'system',mode=['system','market'].includes(selected)?selected:'specified';
 const zone=validDisplayTimezone(specified)&&!['system','market'].includes(specified)?specified:mode==='specified'?selected:systemTimezone();
 const zones=[...new Set(['UTC',systemTimezone(),zone,...(Intl.supportedValuesOf?.('timeZone')||['Asia/Taipei','Asia/Tokyo','America/New_York','Europe/London'])])].sort();
 return uiHTML`<h3>Time zone</h3><form data-connected-form="time-display" class="time-zone-settings"><label>Time zone mode<select name="timeZoneMode" aria-label="Time zone mode"><option value="system" ${mode==='system'?'selected':''}>System</option><option value="specified" ${mode==='specified'?'selected':''}>Specified</option><option value="market" ${mode==='market'?'selected':''}>Market</option></select></label><label data-specified-zone ${mode==='specified'?'':'hidden'}>Specified time zone<select name="specifiedTimezone" aria-label="Specified time zone" ${mode==='specified'?'':'disabled'}>${zones.map(v=>`<option value="${v}" ${v===zone?'selected':''}>${v}</option>`).join('')}</select></label><p>${label('System time zone: {0}',systemTimezone())}</p><details><summary>How to Use</summary><p>Time inputs show their zone and are converted to UTC for requests. Publisher-provided time zones are used when declared; otherwise the system zone is used and labelled. Changing the zone preserves the instant of valid date/time drafts.</p></details><button class="button" type="submit">Save time zone</button></form>`;
}
