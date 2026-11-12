/** Non-secret window identity; actual login stays in a scoped HttpOnly cookie. */
const WINDOW_KEY='phemacast.terminal-window.v1';
const valid=value=>/^[a-f0-9]{32}$/.test(value||'');
let activeWindow='';
const fresh=()=>crypto.randomUUID().replaceAll('-','');
export function terminalContext(page = globalThis.location) {
  const params = new URLSearchParams(page?.search || '');
  if(params.get('consultantSession')==='own')return '';
  return params.get('terminalOrigin') || params.get('returnOrigin') || '';
}

export function terminalWindow(page=globalThis.location){
  const value=new URLSearchParams(page?.search||'').get('terminalWindow');
  return valid(value)?value:page===globalThis.location?activeWindow:'';
}

async function initializeWindow(){
  if(typeof document==='undefined')return;
  const companion=/\/(?:app-consultant|producer-browser|strategy-consultant)\.html$/.test(location.pathname)
    ||document.body?.classList.contains('agent-page')||!!document.querySelector('[data-consultant-login]');
  const requested=terminalWindow();
  if(companion){activeWindow=requested;return;}
  let saved;try{saved=sessionStorage.getItem(WINDOW_KEY);}catch{}
  const own=requested||(valid(saved)?saved:''),newWindow=new URLSearchParams(location.search).has('new-window');
  activeWindow=own||fresh();
  const terminal=/\/(?:index\.html)?$/.test(location.pathname);
  if(terminal&&navigator.locks){
    // Duplicating a tab copies sessionStorage. A document-held lock makes the
    // duplicate independent without changing the original window's identity.
    async function claim(id){
      return new Promise(resolve=>{
        navigator.locks.request('phemacast-terminal-window:'+id,{ifAvailable:true},lock=>{
          resolve(!!lock);
          if(lock)return new Promise(release=>window.addEventListener('pagehide',release,{once:true}));
        }).catch(()=>resolve(false));
      });
    }
    if(!own||!await claim(own)){
      // A new tab, or a duplicate whose window is open elsewhere, takes up the most recently used window this browser
      // is signed in to that no open tab holds. "New Terminal window" and a link to a named window start their own.
      let resumed='';
      if(!requested&&!newWindow)for(const id of await idleWindows())if(await claim(id)){resumed=id;break;}
      activeWindow=resumed||fresh();
      if(!resumed)await claim(activeWindow);
    }
  }else if(terminal&&typeof BroadcastChannel!=='undefined'){
    const channel=new BroadcastChannel('phemacast-terminal-windows');let collision=false;
    channel.onmessage=event=>{
      if(event.data?.id!==activeWindow)return;
      if(event.data.type==='probe')channel.postMessage({id:activeWindow,type:'occupied'});
      else if(event.data.type==='occupied')collision=true;
    };
    channel.postMessage({id:activeWindow,type:'probe'});
    await new Promise(resolve=>setTimeout(resolve,100));
    if(collision)activeWindow=fresh();
    window.addEventListener('pagehide',()=>channel.close(),{once:true});
  }
  try{sessionStorage.setItem(WINDOW_KEY,activeWindow);}catch{}
  if((requested&&requested!==activeWindow)||newWindow){const url=new URL(location.href);url.searchParams.delete('terminalWindow');url.searchParams.delete('new-window');history.replaceState({},'',url);}
}
async function idleWindows(){
  try{const response=await fetch('/api/auth/resumable-windows',{credentials:'same-origin',cache:'no-store'});
    return response.ok?((await response.json()).windows||[]).filter(valid):[];}
  catch{return [];}
}
await initializeWindow();

// `terminal.py up` prints this Terminal's address with a secret for this launch in the fragment, which never
// reaches a server or its log. Exchanging it gives this browser the HttpOnly cookie that sign-in requires.
async function claimLocalAccess(){
  if(typeof location==='undefined'||typeof fetch==='undefined')return;
  const match=/(?:^#|&)local-access=([A-Za-z0-9_-]{32,128})(?:&|$)/.exec(location.hash||'');
  if(!match)return false;
  history.replaceState(history.state,'',location.pathname+location.search);
  try{await fetch('/api/local-access',{method:'POST',credentials:'same-origin',cache:'no-store',
    headers:{'Content-Type':'application/json'},body:JSON.stringify({token:match[1]})});}catch{}
  return true;
}
await claimLocalAccess();
// The address pasted into a tab that already shows this Terminal only changes the fragment; claim, then reload.
if(typeof addEventListener!=='undefined')addEventListener('hashchange',async()=>{if(await claimLocalAccess())location.reload();});

export function terminalHeaders(headers = {}, page = globalThis.location) {
  const result = new Headers(headers), origin = terminalContext(page), window=terminalWindow(page);
  if (origin) result.set('X-Terminal-Origin', origin);
  if (window) result.set('X-Terminal-Window',window);
  return result;
}

export function terminalURL(path, page = globalThis.location) {
  const url = new URL(path, page.href), origin = terminalContext(page), window=terminalWindow(page);
  if (url.origin === page.origin){
    if(origin)url.searchParams.set('terminalOrigin', origin);
    if(window)url.searchParams.set('terminalWindow',window);
  }
  return url.href;
}

// Native images/media/navigation cannot attach fetch headers. Carry only the
// window selector, never credentials, and never decorate external content URLs.
export function prepareTerminalLinks(root){
  const nodes=[...(root?.matches?.('[href],[src]')?[root]:[]),...(root?.querySelectorAll?.('[href],[src]')||[])];
  for(const node of nodes){
    if(node.hasAttribute('data-new-terminal'))continue;
    for(const attr of ['href','src']){
      const value=node.getAttribute(attr);if(!value||value.startsWith('#'))continue;
      let url;try{url=new URL(value,location.href);}catch{continue;}
      if(url.origin!==location.origin)continue;
      if(!/\/api\//.test(url.pathname)&&!/\/(?:account|settings|producer-browser|strategy-consultant)\.html$/.test(url.pathname))continue;
      const next=terminalURL(url.href);if(next!==url.href)node.setAttribute(attr,next);
    }
  }
}
if(typeof document!=='undefined'){
  prepareTerminalLinks(document);
  new MutationObserver(records=>{for(const record of records){if(record.type==='attributes')prepareTerminalLinks(record.target);else for(const node of record.addedNodes)if(node.nodeType===1)prepareTerminalLinks(node);}})
    .observe(document.documentElement,{subtree:true,childList:true,attributes:true,attributeFilter:['href','src']});
}
