/* Retis MAS Manager view. It runs in the Terminal's confined frame and reaches the manager on this computer
 * only through the Phema's one composition reference (bridge.invoke). The manager keeps no Terminal data. */
(()=>{
const REFERENCE='mas-manager';
const STATES={not_installed:'Not installed',installing:'Installing',stopped:'Stopped',starting:'Starting',running:'Running',
 needs_data:'Needs data',unhealthy:'Unhealthy',restarting:'Restarting'};
// Components the viewer depends on while this page is open: an update asks before restarting them.
const ASK_BEFORE_RESTART=new Set(['terminal']);
const HELP=['Each row is one Retis component on this computer: install it, update it, start or stop it, or read its log.',
 'Updates install beside the current version; Roll back returns to the previous one.',
 'Large data never comes with a component: a component that says Needs data offers its own download.',
 'A component works in your Terminal as soon as it runs: no sign-in or pairing.',
 'Remove keeps the component\'s data unless you untick Keep data.'];
const MESSAGES={
 'Refresh':'重新整理','Check for updates':'檢查更新','How to use':'使用說明','This computer':'這台電腦',
 'Component':'元件','Version':'版本','Status':'狀態','Actions':'操作',
 'Not installed':'未安裝','Installing':'安裝中','Stopped':'已停止','Starting':'啟動中','Running':'執行中','Needs data':'需要資料',
 'Unhealthy':'異常','Restarting':'重新啟動中',
 'Install':'安裝','Update':'更新','Start':'啟動','Stop':'停止','Restart':'重新啟動','Roll back':'復原','Logs':'記錄','Remove':'移除',
 'Update to {0}':'更新至 {0}','Not offered for this computer':'此電腦無可用版本',
 'Accept and install':'同意並安裝','Accept these terms first:':'請先同意以下條款：','Cancel':'取消',
 'Licences':'授權條款','and {0} more':'以及另外 {0} 個','Third-party notices':'第三方授權聲明','No notices.':'沒有授權聲明。',
 'Install downloads these from the Python Package Index (PyPI):':'安裝時會從 Python 套件索引（PyPI）下載以下程式庫：',
 'Restart now':'立即重新啟動','At next start':'下次啟動時','Restart {0} now to finish the update?':'立即重新啟動 {0} 以完成更新？',
 'Remove {0}?':'移除 {0}？','Keep data':'保留資料','Close':'關閉','Recent events':'最近事件',
 'Parts list {0} · checked {1}':'元件清單 {0} · 檢查於 {1}','No parts list yet.':'尚無元件清單。',
 'Last error: {0}':'最近錯誤：{0}','Updates at next start: {0}':'下次啟動時更新：{0}','{0} failed: {1}':'{0} 失敗：{1}',
 'The manager returned no result.':'管理員沒有回應。','Pending':'待處理',
 'Problem report':'問題回報','Report saved on this computer: {0}':'回報已存於這台電腦：{0}','Download data':'下載資料','Download {0}':'下載 {0}',
 'Stop download':'停止下載','Downloading: {0}':'下載中：{0}','Download finished':'下載完成','Download failed':'下載失敗',
 'CPU {0}% · memory {1}':'CPU {0}% · 記憶體 {1}','{0} on disk':'磁碟用量 {0}','{0} on disk ({1} data)':'磁碟用量 {0}（資料 {1}）','Data download':'資料下載','Free disk: {0}':'可用磁碟：{0}',
 '{0} keeps crashing.':'{0} 一再當機。','{0} has been unhealthy for a while.':'{0} 已異常一段時間。','Update for {0}: {1}':'{0} 有更新：{1}',
 '{0} could not download its data.':'{0} 無法下載資料。','Disk space is low: {0} free.':'磁碟空間不足：剩 {0}。',
 'Each row is one Retis component on this computer: install it, update it, start or stop it, or read its log.':'每一列是這台電腦上的一個 Retis 元件：可安裝、更新、啟動、停止或查看記錄。',
 'Updates install beside the current version; Roll back returns to the previous one.':'更新會安裝在目前版本旁；「復原」可回到上一版。',
 'Large data never comes with a component: a component that says Needs data offers its own download.':'大型資料不隨元件安裝；顯示「需要資料」的元件會提供自己的下載。',
 'A component works in your Terminal as soon as it runs: no sign-in or pairing.':'元件一執行，終端即可使用，無需登入或配對。',
 'Remove keeps the component\'s data unless you untick Keep data.':'移除時會保留元件資料，除非取消勾選「保留資料」。'};
function language(locale){return /^zh(-|_)?(hant|tw|hk|mo)?/i.test(String(locale||''))?'zh-Hant':'en';}
function translate(lang,key,...values){
 let text=lang==='zh-Hant'&&MESSAGES[key]||key;
 values.forEach((value,index)=>{text=text.split('{'+index+'}').join(String(value));});
 return text;
}
/** The buttons a row offers, in order, for its state. */
function actions(row){
 const list=[];
 const licences=(row.notices||[]).length?['licences']:[];
 if(row.state==='not_installed'){if(row.available_here&&!busy(row))list.push('install');return [...list,...licences];}
 if(row.state==='installing'||busy(row))return list;
 // A component the app bundles (the Terminal) updates with the app: run it, never remove or replace it here.
 if(row.bundled){
  if(row.state==='stopped')list.push('start');
  else list.push('stop','restart');
  list.push('logs');return list;
 }
 if(row.update&&row.available_here)list.push('update');
 if(['stopped'].includes(row.state))list.push('start');
 if(['running','starting','needs_data','unhealthy','restarting'].includes(row.state))list.push('stop','restart');
 if(row.state==='unhealthy'&&row.process&&row.process.crash_loop)list.push('start');
 if(row.previous)list.push('rollback');
 list.push('logs','remove',...licences);
 return [...new Set(list)];
}
function busy(row){return !!(row.job&&row.job.state==='running');}
/** How soon to ask again: quickly while something is changing. */
function nextRefresh(rows){
 return rows.some(row=>busy(row)||downloading(row)||['installing','starting','restarting'].includes(row.state))?3000:30000;
}
function downloading(row){return !!(row.data_job&&row.data_job.state==='running');}
/** The data sets a row may download now: those its parts list gives a download for, once it is installed. */
function downloads(row){
 if(row.state==='not_installed'||row.state==='installing'||downloading(row))return [];
 return (row.data||[]).filter(item=>Array.isArray(item.command)&&item.command.length);
}
function bytes(value){
 if(value==null)return '—';
 const units=['B','KB','MB','GB','TB'];let n=Number(value),i=0;
 while(n>=1024&&i<units.length-1){n/=1024;i++;}
 return (i?n.toFixed(n<10?1:0):String(n))+' '+units[i];
}
const ALERTS={crash_loop:'{0} keeps crashing.',unhealthy:'{0} has been unhealthy for a while.',update:'Update for {0}: {1}',
 data_failed:'{0} could not download its data.',low_disk:'Disk space is low: {0} free.'};

function mount(root,bridge){
 const context=bridge.context||{},lang=language(context.locale),t=(key,...values)=>translate(lang,key,...values);
 const el=(tag,className,text)=>{const node=document.createElement(tag);if(className)node.className=className;if(text!=null)node.textContent=text;return node;};
 const button=(label,onclick,className='')=>{const node=el('button',className,t(label));node.type='button';node.onclick=onclick;return node;};
 const when=seconds=>seconds?new Intl.DateTimeFormat(context.locale||'en',{dateStyle:'short',timeStyle:'short',timeZone:context.timezone}).format(seconds*1000):'—';
 root.className='mm';root.dataset.theme=context.theme||'light';
 const onContext=event=>{root.dataset.theme=event.detail.theme||'light';};
 document.addEventListener('phemacast:context',onContext);
 if(context.sourceMode&&context.sourceMode!=='parameters'){root.replaceChildren(el('p','mm-status','Open this Phema with parameters.'));return ()=>{};}

 const bar=el('div','mm-bar'),host=el('strong','mm-host',t('This computer')),note=el('p','mm-status'),parts=el('span','mm-parts');
 note.setAttribute('role','status');
 const help=el('details','mm-help'),helpList=el('ul');
 help.append(el('summary',null,t('How to use')),helpList);helpList.replaceChildren(...HELP.map(line=>el('li',null,t(line))));
 const report=button('Problem report',async()=>{
  note.textContent='';
  try{const saved=await call('mas_report',{});note.textContent=t('Report saved on this computer: {0}',saved.path);}
  catch(error){note.textContent=error.message;}
 });
 const space=el('span','mm-parts');
 bar.append(host,button('Refresh',()=>load()),button('Check for updates',()=>run('mas_check_updates',{})),report,parts,space,note,help);
 const alertBox=el('ul','mm-alerts');alertBox.hidden=true;
 const dialog=el('section','mm-dialog');dialog.hidden=true;
 const table=el('table','mm-table'),body=el('tbody');
 const head=el('tr');for(const label of ['Component','Version','Status','Actions'])head.append(el('th',null,t(label)));
 const thead=el('thead');thead.append(head);table.append(thead,body);
 const logs=el('section','mm-logs');logs.hidden=true;
 const events=el('details','mm-events');
 root.replaceChildren(bar,alertBox,dialog,table,logs,events);

 let disposed=false,timer=null,rows=[];
 async function call(operation,parameters){
  const reply=await bridge.invoke({compositionReference:REFERENCE,sourceMode:'parameters',
   input:{operation,parameters,request_key:crypto.randomUUID()}});
  if(reply&&reply.error)throw Error(reply.error);
  if(!reply||!reply.response)throw Error(t('The manager returned no result.'));
  if(reply.response.error)throw Error(reply.response.error);
  if(reply.response.response===undefined)return reply.response;
  return reply.response.response;
 }
 async function run(operation,parameters,label){
  note.textContent='';
  try{await call(operation,parameters);}
  catch(error){note.textContent=label?t('{0} failed: {1}',t(label),error.message):error.message;}
  await load();
 }
 function schedule(){clearTimeout(timer);if(!disposed)timer=setTimeout(load,nextRefresh(rows));}
 async function load(){
  try{
   const status=await call('mas_status',{language:lang});if(disposed)return;
   rows=status.components||[];
   host.textContent=t('This computer')+' · '+(status.host&&status.host.name||'');
   const list=status.parts_list||{};
   parts.textContent=list.sequence!=null?t('Parts list {0} · checked {1}',list.sequence,when(list.checked_at)):t('No parts list yet.');
   if(list.error)note.textContent=list.error;
   space.textContent=status.disk?t('Free disk: {0}',bytes(status.disk.free_bytes)):'';
   renderAlerts(status.alerts||[]);render(rows);renderEvents(status.events||[]);
  }catch(error){if(!disposed)note.textContent=error.message;}
  schedule();
 }
 function confirmBox(message,choices){
  dialog.replaceChildren(el('p',null,message),...choices);dialog.hidden=false;
 }
 function closeDialog(){dialog.hidden=true;dialog.replaceChildren();}
 function act(row,kind){
  const component=row.id;
  if(kind==='install'){
   const terms=(row.terms||[]).map(term=>term.id+'@'+term.version);
   if(!terms.length)return run('mas_install',{component},'Install');
   // Each term in full (a note's text in the viewer's language), then what Install downloads and its licences.
   const parts=[];
   for(const term of row.terms||[]){
    const text=term.texts&&(term.texts[lang]||term.texts.en);
    const title=el('strong',null,(term.names&&term.names[lang]||term.names&&term.names.en||term.id)+' ('+term.version+')');
    if(term.url)title.append(' ',el('code',null,term.url));
    parts.push(title);if(text)parts.push(el('p','mm-note',text));
   }
   if((row.notices||[]).length){
    const list=el('ul','mm-notices');
    // The libraries the note names come first; Licences lists every one.
    for(const item of row.notices.slice(0,5))list.append(el('li',null,item.name+' '+item.version+(item.license?' ('+item.license+')':'')));
    if(row.notices.length>5)list.append(el('li',null,t('and {0} more',row.notices.length-5)));
    parts.push(el('p',null,t('Install downloads these from the Python Package Index (PyPI):')),list,button('Licences',()=>showNotices(row)));
   }
   confirmBox(t('Accept these terms first:'),[...parts,button('Accept and install',()=>{closeDialog();run('mas_install',{component,accept_terms:terms},'Install');}),button('Cancel',closeDialog)]);
   return;
  }
  if(kind==='update'){
   const terms=(row.terms||[]).map(term=>term.id+'@'+term.version);
   if(ASK_BEFORE_RESTART.has(component)&&row.process&&row.process.running){
    confirmBox(t('Restart {0} now to finish the update?',row.name),[
     button('Restart now',()=>{closeDialog();run('mas_update',{component,restart:true,accept_terms:terms},'Update');}),
     button('At next start',()=>{closeDialog();run('mas_update',{component,restart:false,accept_terms:terms},'Update');}),
     button('Cancel',closeDialog)]);
    return;
   }
   return run('mas_update',{component,restart:true,accept_terms:terms},'Update');
  }
  if(kind==='remove'){
   const keep=el('input');keep.type='checkbox';keep.checked=true;const label=el('label');label.append(keep,' '+t('Keep data'));
   confirmBox(t('Remove {0}?',row.name),[label,button('Remove',()=>{closeDialog();run('mas_remove',{component,keep_data:keep.checked},'Remove');},'mm-danger'),button('Cancel',closeDialog)]);
   return;
  }
  if(kind==='logs')return showLogs(row);
  if(kind==='licences')return showNotices(row);
  return run('mas_'+kind,{component},{start:'Start',stop:'Stop',restart:'Restart',rollback:'Roll back'}[kind]);
 }
 let noticesShown=0;
 async function showNotices(row){
  // The notices come in parts (a view takes at most 300,000 characters in one value or one drawing request); each
  // part is added to the text as it arrives, until the last, or until another view of the panel replaces it.
  const shown=++noticesShown;
  try{
   let result=await call('mas_notices',{component:row.id});if(disposed)return;
   const text=el('pre',null,result.text||t('No notices.'));
   logs.replaceChildren(el('h3',null,t('Third-party notices')+' · '+row.name),text,button('Close',()=>{logs.hidden=true;}));logs.hidden=false;
   for(let part=1;result.next!=null&&part<200;part++){
    result=await call('mas_notices',{component:row.id,offset:result.next});
    if(disposed||shown!==noticesShown||logs.hidden)return;
    text.append(result.text);
   }
  }catch(error){note.textContent=error.message;}
 }
 async function showLogs(row){
  noticesShown++;  // the panel shows the logs now: notices still loading stop
  try{
   const result=await call('mas_logs',{component:row.id,lines:200});if(disposed)return;
   const parts=[el('h3',null,t('Logs')+' · '+row.name)];
   if((result.lines||[]).length||!(result.data_lines||[]).length)parts.push(el('pre',null,(result.lines||[]).join('\n')||'—'));
   if((result.data_lines||[]).length)parts.push(el('h4',null,t('Data download')),el('pre',null,result.data_lines.join('\n')));
   logs.replaceChildren(...parts,button('Close',()=>{logs.hidden=true;}));logs.hidden=false;
  }catch(error){note.textContent=error.message;}
 }
 const LABELS={install:'Install',update:'Update',start:'Start',stop:'Stop',restart:'Restart',rollback:'Roll back',logs:'Logs',remove:'Remove',licences:'Licences'};
 function render(list){
  body.replaceChildren(...list.map(row=>{
   const tr=el('tr');tr.dataset.state=row.state;
   const name=el('td','mm-name');name.append(el('span',null,row.name));if(row.description)name.title=row.description;
   if(!row.available_here&&row.state==='not_installed')name.append(el('small',null,t('Not offered for this computer')));
   const version=el('td',null,row.version||'—');
   if(row.update)version.append(el('small','mm-badge',t('Update to {0}',row.update)));
   if(row.pending)version.append(el('small',null,t('Updates at next start: {0}',row.pending)));
   const state=el('td','mm-state');const dot=el('span','mm-dot');dot.dataset.state=row.state;
   let text=t(STATES[row.state]||row.state);
   if(row.state==='installing'&&row.progress&&row.progress.total)text+=' '+Math.floor(row.progress.received*100/row.progress.total)+'%';
   if(busy(row))text=t(row.job.kind==='update'?'Update':'Install')+' · '+t('Pending');
   state.append(dot,el('span',null,text));
   if(row.reason)state.append(el('small',null,row.reason));
   if(row.job&&row.job.state==='failed')state.append(el('small','mm-error',t('{0} failed: {1}',t(row.job.kind==='update'?'Update':'Install'),row.job.error)));
   else if(row.last_error)state.append(el('small','mm-error',t('Last error: {0}',row.last_error)));
   if(row.resources)state.append(el('small',null,t('CPU {0}% · memory {1}',row.resources.cpu_percent,bytes(row.resources.memory_bytes))));
   if(row.disk_bytes)state.append(el('small',null,row.data_bytes?t('{0} on disk ({1} data)',bytes(row.disk_bytes),bytes(row.data_bytes)):t('{0} on disk',bytes(row.disk_bytes))));
   const job=row.data_job;
   if(job&&job.state==='running')state.append(el('small','mm-badge',t('Downloading: {0}',job.last_line||'…')));
   else if(job&&job.state==='failed')state.append(el('small','mm-error',t('Download failed')));
   else if(job&&job.state==='done')state.append(el('small',null,t('Download finished')));
   const buttons=el('td','mm-actions');
   const offered=downloads(row);
   for(const item of offered){
    const label=offered.length>1?t('Download {0}',item.names&&(item.names[lang]||item.names.en)||item.id):t('Download data');
    const node=el('button','',label);node.type='button';node.onclick=()=>run('mas_download_data',{component:row.id,data:item.id},'Download data');
    buttons.append(node);
   }
   if(downloading(row))buttons.append(button('Stop download',()=>run('mas_stop_download',{component:row.id},'Stop download')));
   for(const kind of actions(row))buttons.append(button(LABELS[kind],()=>act(row,kind),kind==='remove'?'mm-danger':''));
   tr.append(name,version,state,buttons);return tr;
  }));
 }
 function renderAlerts(list){
  const name=id=>(rows.find(row=>row.id===id)||{}).name||id;
  alertBox.replaceChildren(...list.map(alert=>{
   const item=el('li','mm-alert',t(ALERTS[alert.kind]||alert.kind,alert.component?name(alert.component):bytes(alert.free_bytes),alert.version||''));
   item.dataset.severity=alert.severity||'info';return item;
  }));
  alertBox.hidden=!list.length;
 }
 function renderEvents(list){
  const items=el('ul');
  for(const event of list.slice().reverse())items.append(el('li',null,when(event.at)+' · '+(event.component||'')+' · '+event.kind));
  events.replaceChildren(el('summary',null,t('Recent events')),items);
 }
 load();
 return ()=>{disposed=true;clearTimeout(timer);document.removeEventListener('phemacast:context',onContext);root.replaceChildren();};
}

globalThis.MasManagerView=Object.freeze({mount,actions,downloads,bytes,nextRefresh,translate,language,MESSAGES,HELP,ALERTS});
const bridge=globalThis.phemacastAgentView;
if(bridge&&typeof document!=='undefined'){
 const root=document.querySelector('#mas-manager');
 try{if(root)globalThis.disposeMasManager=mount(root,bridge);}
 catch(error){const message=document.createElement('p');message.setAttribute('role','alert');message.textContent=error.message;root.replaceChildren(message);}
}
})();
