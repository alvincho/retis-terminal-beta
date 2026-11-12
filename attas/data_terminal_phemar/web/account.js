import {displayDateTime,displayTimezone} from './time-display.js';
import {mountTimeInputs} from './time-inputs.js';
import {ui as uiHTML,label,setUIText} from './i18n.js';
import {api,esc,runtime,connect} from './connected.js';
import {filterLedger,ledgerCSV} from './transactions-model.js';
await connect();
const $=id=>document.getElementById(id),pageSize=50;
let data=null,page=0,loading=false;
const number=n=>Number(n).toLocaleString(),signed=n=>(n>0?'+':'')+number(n);
const date=value=>value?displayDateTime(value):'—';
const amount=n=>uiHTML`<span class="ledger-amount ${n<0?'debit':n>0?'credit':''}">${signed(n)}</span>`;
const payees=rows=>[...new Set(rows.map(p=>p.provider_username||p.provider_user_id))].join(', ')||'—';
function filters(){return {query:$('ledger-search').value,action:$('ledger-action').value,from:$('ledger-from').value,to:$('ledger-to').value,timeZone:displayTimezone()};}
function selectedRows(){return filterLedger(data?.rows||[],filters());}
function entryTable(rows){return uiHTML`<table class="ledger-table"><thead><tr><th>Date / time</th><th>Entry</th><th>Agent / registered recipient</th><th class="numeric">Change</th><th class="numeric">Balance</th><th>Reference & details</th></tr></thead><tbody>${rows.map(r=>uiHTML`<tr><td class="nowrap">${esc(date(r.created_at))}</td><td>${esc(r.label)}<small>${esc(r.source)}</small></td><td>${esc(r.agent_name||r.label)}<small>${esc(r.payees.length?payees(r.payees):r.counterparty||'—')}</small></td><td class="numeric">${amount(r.delta)}</td><td class="numeric">${number(r.credit_points)}<small>from ${number(r.previous_credit_points)}</small></td><td><details><summary>${esc(r.request_id||r.id)}</summary><p>${esc(r.reason)}</p><dl><dt>Transaction</dt><dd>${esc(r.id)}</dd><dt>Escrow</dt><dd>${esc(r.escrowId||'—')}</dd><dt>Pulse</dt><dd>${esc(r.pulse||r.practice_id||'—')}</dd>${r.payees.map(p=>uiHTML`<dt>Registered payee</dt><dd>${esc(p.provider_username||p.provider_user_id)} · ${p.sameAccount?'Your signed-in account':'Different Plaza account'}<small>${esc(p.provider_user_id)}</small></dd>`).join('')}</dl></details></td></tr>`).join('')}</tbody></table>`;}
function renderRows(){
 if(!data)return;
 const rows=selectedRows(),grouped=$('ledger-view').value==='executions',ids=new Set(rows.map(r=>r.id));
 // A matching execution shows its full ledger so its net is never a filtered partial sum.
 const items=grouped?data.executions.filter(g=>g.entries.some(r=>ids.has(r.id))):rows;
 const pages=Math.max(1,Math.ceil(items.length/pageSize));page=Math.min(page,pages-1);const shown=items.slice(page*pageSize,(page+1)*pageSize);
 setUIText($('ledger-count'),grouped?'{0} executions · {1} matching ledger entries':'{0} entries · {1} matching ledger entries',items.length,rows.length);
 $('ledger-table').innerHTML=!shown.length?uiHTML`<p class="ledger-empty">No transactions match these filters.</p>`:grouped?uiHTML`<table class="ledger-table"><thead><tr><th>Date / time</th><th>Agent / registered recipient</th><th>Recorded status</th><th class="numeric">Net change</th><th>Ledger entries</th></tr></thead><tbody>${shown.map(g=>uiHTML`<tr><td class="nowrap">${esc(date(g.createdAt))}</td><td>${esc([...new Set(g.payees.map(p=>p.agent_name).filter(Boolean))].join(', ')||g.entries[0].agent_name||g.entries[0].label)}<small>${esc(g.payees.length?payees(g.payees):g.entries[0].counterparty||'—')}</small></td><td>${esc(g.status)}</td><td class="numeric">${amount(g.net)}</td><td><details><summary>${g.entries.length} entries · ${esc(g.id)}</summary>${entryTable(g.entries)}</details></td></tr>`).join('')}</tbody></table>`:entryTable(shown);
 setUIText($('ledger-page'),'Page {0} of {1}',page+1,pages);$('ledger-previous').disabled=page===0;$('ledger-next').disabled=page===pages-1;$('ledger-export').disabled=!rows.length;
}
function renderSummary(){
 const s=data.summary,a=data.account;
 $('account-name').textContent=a.name;$('account-balance').textContent=number(a.balance);
 $('profile').innerHTML=uiHTML`<h2>Plaza identity</h2><dl class="profile-facts"><dt>Username</dt><dd>${esc(a.username)}</dd><dt>Account ID</dt><dd>${esc(a.id)}</dd><dt>Plaza</dt><dd>${esc(a.plaza)}</dd><dt>Available points</dt><dd>${number(a.balance)}</dd></dl><p>All Data Browsers in this Terminal window use this Plaza account. Other Terminal windows can use different accounts.</p><p>Provider payments go to the agent’s registered Plaza owner. An agent running on your machine can still be registered to a different account.</p><a href="#transactions">View transaction history →</a><p><a href="./settings.html">Manage login and Terminal settings</a></p>`;
 $('ledger-summary').innerHTML=uiHTML`<div class="ledger-equation"><div><span>Opening balance</span><strong>${number(s.opening)}</strong></div><b>+</b><div><span>Credits</span><strong>${number(s.credits)}</strong></div><b>−</b><div><span>Debits</span><strong>${number(s.debits)}</strong></div><b>=</b><div><span>Closing balance</span><strong>${number(s.closing)}</strong></div></div><p class="ledger-note">${label('{0} entries returned by Plaza · {1} — {2}.',s.count,date(s.from),date(s.to))} ${label('Time zone: {0}',displayTimezone())}. ${label('Totals cover this returned period.')}</p>${!s.balanced?uiHTML`<p class="ledger-warning">The returned entries do not reconcile to the recorded closing balance. History may be incomplete.</p>`:''}${s.balanceChanged?uiHTML`<p class="ledger-warning">The current balance differs from the latest ledger entry. Refresh to check for newer entries.</p>`:''}${s.truncated?uiHTML`<p class="ledger-warning">${label('Only the latest {0} entries are shown. This is not the full account history.',s.limit)}</p>`:''}<details class="ledger-breakdown"><summary>Balance changes by entry type</summary><table class="ledger-table"><thead><tr><th>Entry type</th><th class="numeric">Count</th><th class="numeric">Net points</th></tr></thead><tbody>${data.actions.map(r=>uiHTML`<tr><td>${esc(r.label)}</td><td class="numeric">${r.count}</td><td class="numeric">${amount(r.net)}</td></tr>`).join('')}</tbody></table></details>`;
 $('providers').innerHTML=uiHTML`<p class="ledger-note">These are the recipients recorded at payment time. Provider payments exclude temporary reservations. Different owner accounts do not credit your signed-in account.</p><table class="ledger-table"><thead><tr><th>Agent</th><th>Registered recipient</th><th>Account relationship</th><th class="numeric">Payments</th><th class="numeric">Points paid</th></tr></thead><tbody>${data.providers.map(p=>uiHTML`<tr><td>${esc(p.agent)}</td><td>${esc(p.payee)}</td><td>${label(p.sameAccount===true?'Your account':p.sameAccount===false?'Different Plaza account':'Not recorded')}</td><td class="numeric">${p.calls}</td><td class="numeric">${number(p.points)}</td></tr>`).join('')||uiHTML`<tr><td colspan="5">No provider payments in this period.</td></tr>`}</tbody></table>`;
 const selected=$('ledger-action').value;$('ledger-action').innerHTML=uiHTML`<option value="">All types</option>`+data.actions.map(a=>uiHTML`<option value="${esc(a.action)}">${esc(a.label)} (${a.count})</option>`).join('');$('ledger-action').value=selected;
}
async function load(){
 if(loading)return;loading=true;$('ledger-refresh').disabled=true;setUIText($('account-status'),'Loading your Plaza ledger…');
 try{data=await api('/api/account/transactions');runtime.user=data.account;renderSummary();renderRows();setUIText($('account-status'),'Updated {0} · Read-only Plaza ledger',date(data.retrievedAt*1000));}
 catch(error){if(error.status===401||error.status===403){data=null;$('ledger-table').innerHTML='';$('ledger-summary').innerHTML='';$('providers').innerHTML='';$('profile').innerHTML='';$('account-balance').textContent='—';$('ledger-export').disabled=true;}$('account-status').innerHTML=uiHTML`<span class="ledger-warning">${esc(error.message)}</span> <a href="./settings.html">Account settings</a>${data?' · '+label('Previously loaded history is still displayed.'):''}`;}
 finally{loading=false;$('ledger-refresh').disabled=false;}
}
function route(){const profile=location.hash==='#profile';$('profile').hidden=!profile;$('transactions').hidden=profile;for(const id of ['profile','transactions'])$(id+'-link').setAttribute('aria-current',(id==='profile')===profile?'page':'false');}
const ledgerTime=mountTimeInputs([$('ledger-from'),$('ledger-to')],{onChange:()=>{if(data){renderSummary();renderRows();}}});
window.addEventListener('pagehide',()=>ledgerTime.dispose(),{once:true});
$('ledger-filters').addEventListener('submit',e=>e.preventDefault());
$('ledger-filters').addEventListener('input',()=>{page=0;renderRows();});
$('ledger-filters').addEventListener('reset',()=>{setTimeout(()=>{page=0;renderRows();},0);});
$('ledger-previous').onclick=()=>{page--;renderRows();};$('ledger-next').onclick=()=>{page++;renderRows();};$('ledger-refresh').onclick=load;
$('ledger-export').onclick=()=>{const url=URL.createObjectURL(new Blob([ledgerCSV(selectedRows())],{type:'text/csv;charset=utf-8'})),link=document.createElement('a');link.href=url;link.download='plaza-transactions-'+new Date().toISOString().slice(0,10)+'.csv';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
window.addEventListener('hashchange',route);route();
api('/api/settings').then(result=>{const theme=result.preferences?.theme||'dark';document.documentElement.dataset.theme=theme==='system'?(matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light'):theme;}).catch(()=>{});
await load();
