// Plaza points this account spent: the latest few in settings, the whole history in a
// dialog that filters and saves to CSV. Agents show by name, never by ID.
import {ui as uiHTML,label,setUIText,t} from './i18n.js';
import {displayDateTime,displayTimezone} from './time-display.js';
import {csvCell} from './transactions-model.js';
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

const STATES={settled:'Charged',refunded:'Refunded',uncertain:'Needs checking',progress:'In progress'};
export const stateGroup=row=>['settled','refunded','uncertain'].includes(row.state)?row.state:'progress';
const agentLabel=(row,agentName)=>agentName(row.agentId)||row.agentName||'';

// Plaza's ledger entries that pay for an execution. Transfers, top-ups and earnings are not spending.
const CHARGES=new Set(['remote_practice_charge','report_generation_charge','report_quote_charge','scope_payment_charge','report_escrow_reserve','report_escrow_capture','report_escrow_refund']);
const seconds=value=>typeof value==='number'?value:(Date.parse(value)||0)/1000;

/** Every execution this account paid for: Plaza's ledger, which also records what agents such as the
 * Strategy Producer charge, with this Terminal's own record in place of the same execution (it can be reconciled). */
export function spendingRows(terminalRows=[],ledger=null){
 const own=new Set(terminalRows.map(row=>row.id)),rows=[...terminalRows];
 for(const run of ledger?.executions||[]){
  const entries=run.entries||[];
  if(!(run.net<0)||!entries.some(entry=>CHARGES.has(entry.action))||entries.some(entry=>own.has(entry.executionId)))continue;
  const paid=entries.find(entry=>entry.agent_id||entry.agent_name)||entries[0]||{};
  rows.push({id:'plaza:'+run.id,updatedAt:seconds(run.createdAt),agentId:paid.agent_id||'',agentName:paid.agent_name||'',pulse:paid.pulse||'',
   state:run.status==='No settlement in returned history'?'progress':'settled',points:-run.net,canReconcile:false,
   recipient:(run.payees||[])[0]?.provider_username||'',explanation:'Charge recorded on Plaza.'});
 }
 return rows.sort((a,b)=>seconds(b.updatedAt)-seconds(a.updatedAt));
}

export function pointsRowsHTML(rows,agentName){
 return uiHTML`<table class="points-table"><thead><tr><th>Date / time</th><th>Agent</th><th>Operation</th><th>State</th><th class="numeric">Points</th></tr></thead><tbody>${rows.map(row=>`<tr><td class="nowrap">${esc(displayDateTime(row.updatedAt))}</td><td>${agentLabel(row,agentName)?esc(agentLabel(row,agentName)):label('Unlisted agent')}</td><td>${esc(row.pulse||'—')}</td><td title="${esc(row.explanation)}">${label(STATES[stateGroup(row)])}${row.canReconcile?uiHTML` <button type="button" data-connected-action="reconcile" data-id="${esc(row.id)}">Reconcile reservation</button>`:''}</td><td class="numeric">${esc(row.points)}</td></tr>`).join('')}</tbody></table>`;
}

export function filterPoints(rows,{query='',state='',from='',to=''}={},agentName=()=>''){
 const needle=query.trim().toLowerCase();
 return rows.filter(row=>{
  const day=displayDateTime(row.updatedAt).slice(0,10);
  return (!state||stateGroup(row)===state)&&(!from||day>=from)&&(!to||day<=to)
   &&(!needle||[agentLabel(row,agentName),row.agentId,row.pulse].join(' ').toLowerCase().includes(needle));
 });
}

export function pointsCSV(rows,agentName=()=>''){
 const zone=displayTimezone();
 const lines=[['Date/time','Time zone','Agent','Agent ID','Operation','State','Points','Recipient','Note','Execution ID'].map(csvCell).join(',')];
 for(const row of rows)lines.push([displayDateTime(row.updatedAt),zone,agentLabel(row,agentName),row.agentId,row.pulse,row.state,row.points,row.recipient,row.explanation,row.id].map(csvCell).join(','));
 return '﻿'+lines.join('\r\n');
}

export function openPointsHistory({load,agentName}){
 const dialog=document.createElement('dialog');dialog.className='points-history';
 dialog.innerHTML=uiHTML`<header class="points-history-header"><div><h2>Points spending history</h2><small data-points-count></small></div><button type="button" data-points-close aria-label="Close">×</button></header><form class="points-history-filters" data-points-filters><label>Search<input type="search" name="query" placeholder="Agent or operation"></label><label>State<select name="state"><option value="">All states</option></select></label><label>From<input type="date" name="from"></label><label>To<input type="date" name="to"></label><button type="reset" class="button">Clear</button><button type="button" class="button" data-points-csv disabled>Save as CSV</button></form><div class="points-history-body"><div data-points-table></div><p data-points-status role="status"></p></div>`;
 document.body.append(dialog);dialog.showModal();
 const form=dialog.querySelector('[data-points-filters]');let rows=[];
 const selected=()=>filterPoints(rows,Object.fromEntries(new FormData(form)),agentName);
 function render(){
  const shown=selected(),points=shown.reduce((total,row)=>total+(Number(row.points)||0),0);
  setUIText(dialog.querySelector('[data-points-count]'),'{0} of {1} executions · {2} points',shown.length,rows.length,points);
  dialog.querySelector('[data-points-table]').innerHTML=shown.length?pointsRowsHTML(shown,agentName):'';
  dialog.querySelector('[data-points-status]').innerHTML=!rows.length?label('No points spent yet.'):!shown.length?label('No executions match these filters.'):'';
  dialog.querySelector('[data-points-csv]').disabled=!shown.length;
 }
 async function refresh(){
  try{
   rows=(await load()).rows||[];
   const states=form.elements.state,current=states.value,present=new Set(rows.map(stateGroup));
   states.innerHTML=uiHTML`<option value="">All states</option>`+Object.keys(STATES).filter(key=>present.has(key)).map(key=>`<option value="${key}">${esc(t(STATES[key]))}</option>`).join('');
   states.value=present.has(current)?current:'';
   render();
  }catch(error){dialog.querySelector('[data-points-status]').textContent=error.message;}
 }
 // A reconciled execution changes state; show it without reopening.
 document.addEventListener('connected:settings',refresh);
 dialog.addEventListener('close',()=>{document.removeEventListener('connected:settings',refresh);dialog.remove();},{once:true});
 form.addEventListener('submit',event=>event.preventDefault());
 form.addEventListener('input',render);
 form.addEventListener('reset',()=>setTimeout(render,0));
 dialog.addEventListener('click',event=>{
  if(event.target.closest('[data-points-close]'))dialog.close();
  if(!event.target.closest('[data-points-csv]'))return;
  const url=URL.createObjectURL(new Blob([pointsCSV(selected(),agentName)],{type:'text/csv;charset=utf-8'})),link=document.createElement('a');
  link.href=url;link.download='points-spending-'+new Date().toISOString().slice(0,10)+'.csv';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
 });
 refresh();
 return dialog;
}
