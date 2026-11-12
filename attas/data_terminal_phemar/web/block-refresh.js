import {ui as uiHTML,t,message} from './i18n.js';
export function normalizeRefreshOverrides(value){
 if(!value||typeof value!=='object'||Array.isArray(value))return {};
 return Object.fromEntries(Object.entries(value).filter(([id,p])=>/^[a-zA-Z0-9_-]{1,80}$/.test(id)&&p&&['automatic','manual','interval'].includes(p.refreshMode)&&(p.refreshMode!=='interval'||Number.isInteger(p.refreshSeconds)&&p.refreshSeconds>=60&&p.refreshSeconds<=86400)).slice(0,128).map(([id,p])=>[id,{refreshMode:p.refreshMode,...(p.refreshMode==='interval'?{refreshSeconds:p.refreshSeconds}:{})}]));
}
export function refreshPolicy(requirement,overrides={}){return {...requirement,...overrides[requirement?.id]};}
export function refreshLabel(r){return r.refreshMode==='manual'?t('Manual'):r.refreshMode==='interval'||r.refreshSeconds?t('Every {0} min',r.refreshSeconds/60):t(r.mode==='stream'?'Live / automatic':'Automatic');}
export function refreshFields(r,prefix=''){
 return uiHTML`<label>Update frequency<select name="${prefix}refreshMode" aria-label="Update frequency"><option value="automatic" ${!r.refreshMode||r.refreshMode==='automatic'?'selected':''}>Automatic (delivery / input updates)</option><option value="interval" ${r.refreshMode==='interval'?'selected':''}>Timed interval</option><option value="manual" ${r.refreshMode==='manual'?'selected':''}>Manual only</option></select></label><label>Interval (seconds)<input name="${prefix}refreshSeconds" aria-label="Update interval in seconds" type="number" min="60" max="86400" step="1" value="${r.refreshSeconds||600}" ${r.refreshMode==='interval'?'':'disabled'}></label>`;
}

export function refreshMessage(r){return r.refreshMode==='manual'?message('Manual'):r.refreshMode==='interval'||r.refreshSeconds?message('Every {0} min',r.refreshSeconds/60):message(r.mode==='stream'?'Live / automatic':'Automatic');}
export function refreshTooltip(requirement,nextTime){const action=message(requirement.dependsOn?'Generate analysis · {0}':'Refresh data · {0}',refreshMessage(requirement));return nextTime?message('{0} · Next: {1}',action,nextTime):action;}
