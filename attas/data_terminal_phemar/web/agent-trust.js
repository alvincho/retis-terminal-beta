// Which agents this account trusts now, and the connections it made, as Plaza decides and records them:
// the latest few in settings, the whole history in a dialog that filters and saves to CSV.
// Agents and accounts show by name, never by ID; the Terminal keeps none of it.
import {ui as uiHTML,label,setUIText,t,attribute} from './i18n.js';
import {displayDateTime,displayTimezone} from './time-display.js';
import {csvCell} from './transactions-model.js';
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

const REASONS={own:'Your own agent','plaza-owner':'Approved by the Plaza owner','trusted-account':'From an account you trust'};
const BASES={registered_owner:'Your own agent',same_owner:'Same owner',owner_approved_agent:'Approved by the Plaza owner',
 viewer_trusted_account:'From an account you trust',published_practice:'Published practice'};
const OUTCOMES={used:'Used',issued:'Not used',revoked:'Revoked'};
let api=null;

export function agentTrustHTML(){
 queueMicrotask(()=>load());
 return uiHTML`<section class="agent-trust" data-agent-trust><h3>Trusted agents</h3><p data-agent-trust-status role="status">Loading…</p></section>`;
}

export function trustedHTML(overview){
 const agents=overview.agents||[],accounts=overview.accounts||[];
 const rows=agents.map(a=>`<tr><td>${esc(a.name)}${a.current?uiHTML`<small class="current-terminal">(this Terminal)</small>`:''}</td><td${a.ownerUnnamed?' class="owner-unnamed" '+attribute('title','This account hasn’t published a name on Plaza.'):''}>${esc(a.owner)||'—'}</td><td class="nowrap">${label(REASONS[a.reason]||REASONS['plaza-owner'])}</td><td class="nowrap">${label(a.online?'Online':'Offline')}</td></tr>`).join('');
 const trusted=accounts.map(a=>uiHTML`<li>${esc(a.name)} · ${label('{0} agents',a.agentCount)}</li>`).join('');
 return uiHTML`<h3>Trusted agents</h3>${agents.length?uiHTML`<div class="agent-trust-scroll"><table class="agent-trust-table"><thead><tr><th>Agent</th><th>Owner</th><th>Trusted because</th><th>Status</th></tr></thead><tbody>${rows}</tbody></table></div>`:uiHTML`<p>Plaza trusts no agents for this account yet.</p>`}
  <h4>Accounts you trust</h4>${accounts.length?`<ul class="agent-trust-accounts">${trusted}</ul>`:uiHTML`<p>You trust no other accounts.</p>`}
  <p><a href="${esc(overview.trustPage)}" target="_blank" rel="noopener">${label('Manage trusted accounts on Plaza')}</a></p>`;
}

export function connectionRowsHTML(rows){
 return uiHTML`<div class="agent-trust-scroll"><table class="agent-trust-table"><thead><tr><th>Date / time</th><th>Agent</th><th>Operation</th><th>Allowed because</th><th>Outcome</th></tr></thead><tbody>${rows.map(row=>`<tr><td class="nowrap">${esc(displayDateTime(row.issuedAt))}</td><td>${esc(row.agent)}${row.onward?uiHTML` <small>(onward)</small>`:''}</td><td>${esc(row.operation||'—')}</td><td class="nowrap">${label(BASES[row.basis]||'—')}</td><td class="nowrap">${label(OUTCOMES[row.outcome]||OUTCOMES.issued)}</td></tr>`).join('')}</tbody></table></div>`;
}

export function filterConnections(rows,{query='',outcome='',from='',to=''}={}){
 const needle=query.trim().toLowerCase();
 return rows.filter(row=>{
  const day=displayDateTime(row.issuedAt).slice(0,10);
  return (!outcome||row.outcome===outcome)&&(!from||day>=from)&&(!to||day<=to)
   &&(!needle||[row.agent,row.operation,row.caller].join(' ').toLowerCase().includes(needle));
 });
}

export function connectionsCSV(rows){
 const zone=displayTimezone();
 const lines=[['Date/time','Time zone','Agent','Operation','Called from','Allowed because','Outcome','Onward call'].map(csvCell).join(',')];
 for(const row of rows)lines.push([displayDateTime(row.issuedAt),zone,row.agent,row.operation,row.caller,row.basis,row.outcome,row.onward?'yes':'no'].map(csvCell).join(','));
 return '﻿'+lines.join('\r\n');
}

async function load(){
 const nodes=[...document.querySelectorAll('[data-agent-trust]:not([data-loading])')];
 if(!nodes.length||!api)return;
 nodes.forEach(node=>node.dataset.loading='1');
 try{
  const [overview,recent]=await Promise.all([api('/api/agent-trust/overview'),api('/api/agent-trust/history?limit=10')]);
  const html=trustedHTML(overview)+uiHTML`<div class="points-recent-head"><h3>Connection history</h3><button type="button" class="button" data-agent-trust-action="history">Open history</button></div>`
   +(recent.entries.length?connectionRowsHTML(recent.entries):uiHTML`<p>No connections yet.</p>`)
   +uiHTML`<small>${label('Plaza keeps {0} days of connections.',recent.keptDays||90)}</small>`;
  nodes.forEach(node=>{node.innerHTML=html;});
 }catch(error){nodes.forEach(node=>{delete node.dataset.loading;const status=node.querySelector('[data-agent-trust-status]');if(status)status.textContent=error.message;});}
}

export function openConnectionHistory(request=api){
 const dialog=document.createElement('dialog');dialog.className='points-history agent-trust-history';
 dialog.innerHTML=uiHTML`<header class="points-history-header"><div><h2>Connection history</h2><small data-connections-count></small></div><button type="button" data-connections-close aria-label="Close">×</button></header><form class="points-history-filters" data-connections-filters><label>Search<input type="search" name="query" placeholder="Agent or operation"></label><label>Outcome<select name="outcome"><option value="">All outcomes</option>${Object.entries(OUTCOMES).map(([key,text])=>`<option value="${key}">${esc(t(text))}</option>`).join('')}</select></label><label>From<input type="date" name="from"></label><label>To<input type="date" name="to"></label><button type="reset" class="button">Clear</button><button type="button" class="button" data-connections-csv disabled>Save as CSV</button></form><div class="points-history-body"><div data-connections-table></div><p data-connections-status role="status"></p><button type="button" class="button" data-connections-more hidden>Load older</button></div>`;
 document.body.append(dialog);dialog.showModal();
 const form=dialog.querySelector('[data-connections-filters]'),more=dialog.querySelector('[data-connections-more]');let rows=[],next=null;
 const selected=()=>filterConnections(rows,Object.fromEntries(new FormData(form)));
 function render(){
  const shown=selected();
  setUIText(dialog.querySelector('[data-connections-count]'),'{0} of {1} connections',shown.length,rows.length);
  dialog.querySelector('[data-connections-table]').innerHTML=shown.length?connectionRowsHTML(shown):'';
  dialog.querySelector('[data-connections-status]').innerHTML=!rows.length?label('No connections yet.'):!shown.length?label('No connections match these filters.'):'';
  dialog.querySelector('[data-connections-csv]').disabled=!shown.length;
  more.hidden=!next;
 }
 async function page(){
  try{
   const value=await request('/api/agent-trust/history?limit=200'+(next?'&before='+encodeURIComponent(next):''));
   rows=rows.concat(value.entries||[]);next=value.next||null;render();
  }catch(error){dialog.querySelector('[data-connections-status]').textContent=error.message;}
 }
 dialog.addEventListener('close',()=>dialog.remove(),{once:true});
 form.addEventListener('submit',event=>event.preventDefault());
 form.addEventListener('input',render);
 form.addEventListener('reset',()=>setTimeout(render,0));
 dialog.addEventListener('click',event=>{
  if(event.target.closest('[data-connections-close]'))dialog.close();
  if(event.target.closest('[data-connections-more]'))page();
  if(!event.target.closest('[data-connections-csv]'))return;
  const url=URL.createObjectURL(new Blob([connectionsCSV(selected())],{type:'text/csv;charset=utf-8'})),link=document.createElement('a');
  link.href=url;link.download='connection-history-'+new Date().toISOString().slice(0,10)+'.csv';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
 });
 page();
 return dialog;
}

export function installAgentTrust(deps){
 api=deps.api;
 document.addEventListener('click',event=>{
  if(event.target.closest('[data-agent-trust-action="history"]'))openConnectionHistory();
 });
}
