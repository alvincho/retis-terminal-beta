import {t,label,setUIText} from './i18n.js';
import {displayTime} from './time-display.js';
import {failureText} from './failure-messages.js';
export function layoutFetchStatus(state,requirements){
 if(state.visibleRequirementIds)requirements=requirements.filter(r=>state.visibleRequirementIds.includes(r.id));
 if(state.status==='empty')return {text:t('No layout yet'),key:'No layout yet',args:[],detail:t('Open a layout with + beside OPEN BROWSERS, or with Change layout in this window.'),busy:false};
 // The Terminal can't get new data (Plaza unreachable): the panels keep what they had, and say so.
 if(state.transport==='Reconnecting')return {text:t('Can’t update · showing earlier data'),key:'Can’t update · showing earlier data',args:[],detail:t('The Terminal can’t get new data right now. The panels show what they had and update again once the connection is back.'),busy:true};
 if(state.layoutConflict)return {text:t('Layout update needs review'),key:'Layout update needs review',args:[],detail:state.layoutConflict.requirements.join(', '),busy:false};
 // Plaza did not answer the trust check, or does not list an agent yet: not a decision, so say it is retrying rather than ask for approval.
 if(state.security?.trustUnconfirmed&&Object.values(state.content?.results||{}).some(r=>r?.status==='blocked'))return {text:t('Can’t confirm trust, retrying…'),key:'Can’t confirm trust, retrying…',args:[],detail:t('Plaza hasn’t confirmed this layout’s agents yet. It reloads by itself in a few seconds.'),busy:true};
 // Panels blocked by permission wait for the viewer's approval, not for data.
 if(Object.values(state.content?.results||{}).some(r=>r?.status==='blocked'))return {text:t('Approval required'),key:'Approval required',args:[],detail:t('Approve this layout to load its panels.'),busy:false};
 if(state.sourceMode==='snapshot')return {text:t('Snapshot'),key:'Snapshot',args:[],detail:state.content?.snapshotId||'',busy:false};
 // A form waiting for the viewer's input has finished loading; only content still on its way is pending.
 const waitsForInput=r=>r.status==='pending'&&r.reason==='interaction-required';
 const content=state.content?.results||{},pending=Object.entries(content).filter(([,r])=>r.status!=='ready'&&!waitsForInput(r)&&!(r.status==='pending'&&r.reason==='manual'&&state.content?.status==='ready'));
 // Panels whose source failed are unavailable, not on their way: the window says so, as the panels do.
 if(pending.length){const busy=pending.some(([,r])=>r.status==='resolving'),failed=pending.filter(([,r])=>['unavailable','failed'].includes(r.status)).length;const key=busy?'Resolving content':failed?'{0} of {1} panels unavailable':'Content pending',args=!busy&&failed?[failed,Object.keys(content).length]:[];return {text:t(key,...args),key,args,detail:pending.map(([id,r])=>id+': '+r.status+(r.reason?' · '+failureText(r.reason):'')).join('\n'),busy};}
 const results=state.results||{};
 const waiting=requirements.filter(r=>!results[r.id]||results[r.id].status==='loading'||results[r.id].refreshStatus==='loading');
 const failed=requirements.filter(r=>results[r.id]?.status==='unavailable'||results[r.id]?.refreshStatus==='unavailable');
 const latest=Math.max(0,...requirements.map(r=>{const value=results[r.id]?.observedAt;return typeof value==='number'?value:Number.isFinite(Date.parse(value))?Date.parse(value)/1000:0;}));
 // Forms waiting for input keep the content 'partial'; when everything else has loaded, the window is ready.
 const loaded=Object.values(content).length>0&&Object.values(content).every(r=>r.status==='ready'||waitsForInput(r));
 const status=state.status==='partial'&&loaded?'ready':state.status;
 const key=state.error?null:waiting.length?'Fetching {0}/{1} datasets':failed.length?'{0} datasets unavailable':latest?'Updated {0}':({ready:'Ready',loading:'Loading',completed:'Complete',partial:'Partial',waiting:'Waiting',unavailable:'Unavailable'})[status]||status||'Waiting for data';
 const args=waiting.length?[waiting.length,requirements.length]:failed.length?[failed.length]:latest?[displayTime(latest)]:[];
 const text=key?t(key,...args):state.error;

 const detail=requirements.map(r=>{const v=results[r.id];return `${r.label}: ${v?.refreshStatus==='loading'?'fetching':v?.status||'waiting'}${v?.source?' · '+v.source:''}${v?.error?' · '+v.error:''}`;}).join('\n');
 return {text,key,args,detail,busy:waiting.length>0};
}

export function statusHTML(value){return value.key?label(value.key,...value.args):String(value.text||'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
export function updateStatus(node,value){node.innerHTML=statusHTML(value);node.title=value.detail;node.dataset.busy=String(value.busy);}
