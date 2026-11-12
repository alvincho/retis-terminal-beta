// Terminal settings → Scheduled runs. A view may ask this Terminal to call one of its
// declared references on a timer, as you, even with no view open. This list shows
// this window's runs and stops any of them; it reads nothing a run computes.
import {ui as html,label} from './i18n.js';
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let api,onChange,signedIn=()=>true,rows=[],loaded=false,pending=null,warning='',timer=null;

const time=value=>value?new Date(value*1000).toLocaleString(undefined,{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}):'—';
function every(seconds){return seconds%3600===0?label('Every {0} h',seconds/3600):label('Every {0} min',Math.round(seconds/60));}
function state(run){
 if(run.status==='running')return label('Running');
 if(run.status==='paused')return label('Paused');
 return label('Stopped');
}

function row(run){
 return html`<tr data-run="${esc(run.id)}"><td><strong>${esc(run.label)}</strong><small>${esc(run.phemaTitle)}</small></td><td>${state(run)}${run.reason?html`<small>${esc(run.reason)}</small>`:''}</td><td>${every(run.everySeconds)}</td><td>${time(run.lastAt)}</td><td>${run.status==='running'?time(run.nextAt):'—'}</td><td>${run.status==='stopped'?'':html`<button type="button" class="button" data-stop-run="${esc(run.id)}">Stop</button>`}</td></tr>`;
}

function paint(){
 for(const container of document.querySelectorAll('[data-scheduled-runs]')){
  const body=container.querySelector('[data-run-list]');
  body.innerHTML=rows.length?rows.map(row).join(''):html`<tr><td colspan="6">${label(loaded?'No scheduled runs in this Terminal window.':'Loading runs…')}</td></tr>`;
  container.querySelector('[data-run-warning]').textContent=warning;
 }
}

async function load(){
 if(pending)return pending;
 if(!signedIn()){rows=[];loaded=true;paint();return;}
 pending=(async()=>{
  try{rows=(await api('/api/scheduled-runs')).runs||[];warning='';}catch(error){warning=error.message;}
  finally{loaded=true;pending=null;paint();onChange?.();}
 })();
 return pending;
}

/** Phema IDs with a running run, for the open-browsers list. */
export function runningPhemas(){return new Set(rows.filter(run=>run.status==='running').map(run=>run.phemaId));}

export function scheduledRunsHTML(){
 queueMicrotask(()=>load());
 return html`<section data-scheduled-runs><p>Views can keep a run going in this Terminal while you are away. Each call is charged like a call you make in the view.</p><table class="scheduled-runs"><thead><tr><th>Run</th><th>Status</th><th>Schedule</th><th>Last call</th><th>Next call</th><th></th></tr></thead><tbody data-run-list></tbody></table><p data-run-warning role="status"></p></section>`;
}

export function installScheduledRuns(deps){
 ({api,onChange}=deps);if(deps.signedIn)signedIn=deps.signedIn;
 document.addEventListener('click',async event=>{
  const button=event.target.closest('[data-stop-run]');if(!button)return;
  button.disabled=true;
  try{await api('/api/scheduled-runs/'+encodeURIComponent(button.dataset.stopRun)+'/stop',{method:'POST',body:{}});await load();}
  catch(error){const feedback=button.closest('[data-scheduled-runs]')?.querySelector('[data-run-warning]');if(feedback)feedback.textContent=error.message;button.disabled=false;}
 });
 // Keep the sidebar's running marks current without a busy poll.
 if(!timer){load();timer=setInterval(()=>{if(!document.hidden)load();},60000);}
}
