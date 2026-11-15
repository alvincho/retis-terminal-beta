// Whether this tab's Terminal still answers. A request that cannot reach the Terminal at all means its process
// stopped: the tab says so, and asks /health every few seconds. Once the Terminal answers again, `restarted()`
// decides whether it is a new process (this tab's panels and sign-in belonged to the old one, so it offers a
// reload) or the same one after a short gap (the tab carries on).
import {t} from './i18n.js';

const PROBE_MS=5000;
let state='up',probe=0,restarted=async()=>true;
const listeners=new Set();

/** 'up', 'stopped' or 'restarted'. */
export function terminalPresence(){return state;}
export function onTerminalPresence(listener){listeners.add(listener);return ()=>listeners.delete(listener);}
/** How the tab tells a restarted Terminal from the same one coming back (connected.js asks for one of its panels). */
export function decideRestart(check){restarted=check;}

function set(next){
 if(state===next)return;
 state=next;
 for(const listener of listeners){try{listener(state);}catch{}}
 notice();
}

/** A fetch that failed before any answer: the browser's own error ("Failed to fetch"), never shown as it is. */
export function unreachable(error){return error instanceof TypeError||error?.name==='TypeError';}

/** The error a request reports when the Terminal did not answer; it also marks the Terminal stopped. */
export function terminalStopped(cause){
 if(state==='up')set('stopped');
 if(state==='stopped')watch();
 const error=new Error(t('This Terminal is not responding. It may have stopped.'));
 error.status=0;error.terminalUnreachable=true;error.cause=cause;
 return error;
}

function watch(){
 if(probe||typeof fetch!=='function')return;
 probe=setInterval(async()=>{
  try{
   const response=await fetch('/health',{credentials:'same-origin',cache:'no-store'});if(!response.ok)return;
   clearInterval(probe);probe=0;
   let restart;
   // A check that cannot reach the Terminal either keeps it stopped (that request started a new probe).
   try{restart=await restarted();}catch(error){if(error?.terminalUnreachable)return;restart=true;}
   set(restart?'restarted':'up');
  }catch{}
 },PROBE_MS);
}

/** The sentence the tab shows while its Terminal is stopped, naming how this Terminal is started. */
export function stoppedText(launcherApp){
 return launcherApp?t('This Terminal has stopped. Open it again from the {0} icon\'s menu, then reload this tab.',launcherApp)
  :t('This Terminal has stopped. Start it again with "python3 terminal.py up", then reload this tab.');
}

let launcher='';
/** The app that starts this Terminal ('' for the kit), for the stopped notice. */
export function presenceLauncher(app){launcher=app||'';}

// One notice at the top of the page while the Terminal is stopped or after it restarted.
function notice(){
 if(typeof document==='undefined')return;
 let node=document.querySelector('[data-terminal-presence]');
 if(state==='up'){node?.remove();return;}
 if(!node){
  node=document.createElement('div');node.setAttribute('role','alert');node.className='terminal-presence-notice';node.dataset.terminalPresence='';
  document.body.prepend(node);
 }
 node.dataset.terminalPresence=state;
 const text=document.createElement('span');
 text.textContent=state==='stopped'?stoppedText(launcher):t('This Terminal restarted. Reload this tab to continue.');
 node.replaceChildren(text);
 if(state==='restarted'){
  const reload=document.createElement('button');reload.type='button';reload.textContent=t('Reload this tab');
  reload.onclick=()=>location.reload();node.append(reload);
 }
}
