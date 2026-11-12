/** Visible operation feedback bundled by the owner; no host or network access. */
const labels={en:'Loading…','zh-Hant':'載入中…','zh-TW':'載入中…','zh-Hans':'加载中…','zh-CN':'加载中…',ja:'読み込み中…',es:'Cargando…',fr:'Chargement…'};

export function createOwnerBusy(root,{locale='en',label}={}){
 let count=0,indicator=null,closed=false,previousBusy=root.getAttribute('aria-busy');
 const controls=new Map();
 const text=()=>typeof label==='function'?label():labels[typeof locale==='function'?locale():locale]||labels.en;
 function restore(control,record){
  for(const [key,value] of Object.entries(record.attributes))value===null?control.removeAttribute(key):control.setAttribute(key,value);
  control.classList.remove('phemacast-operation-pending');
 }
 function paint(){
  if(closed)return;
  if(!count){indicator?.remove();indicator=null;previousBusy===null?root.removeAttribute('aria-busy'):root.setAttribute('aria-busy',previousBusy);return;}
  if(!indicator?.isConnected){
   indicator=document.createElement('div');indicator.className='phemacast-operation-loading';indicator.setAttribute('role','status');indicator.setAttribute('aria-live','polite');
   const spinner=document.createElement('span');spinner.className='phemacast-operation-spinner';spinner.setAttribute('aria-hidden','true');
   const message=document.createElement('span');message.textContent=text();indicator.append(spinner,message);root.prepend(indicator);
  }
  root.setAttribute('aria-busy','true');
 }
 function run(task,{control}={}){
  if(typeof task!=='function')throw Error('An operation callback is required.');
  if(closed)return Promise.reject(Error('This operation view is closed.'));
  if(control&&controls.has(control))return controls.get(control).promise;
  if(!count)previousBusy=root.getAttribute('aria-busy');
  const record=control?{attributes:{'aria-busy':control.getAttribute('aria-busy'),'aria-disabled':control.getAttribute('aria-disabled')}}:null;
  if(record){control.setAttribute('aria-busy','true');control.setAttribute('aria-disabled','true');control.classList.add('phemacast-operation-pending');}
  count++;paint();
  // Feedback is painted before invoking the callback. Synchronous failures and
  // rejected promises share the same cleanup; no operation is retried here.
  const promise=Promise.resolve().then(task).finally(()=>{
   count--;
   if(record&&controls.get(control)===record){controls.delete(control);restore(control,record);}
   paint();
  });
  if(record){record.promise=promise;controls.set(control,record);}
  return promise;
 }
 return {
  run,
  bind(control,action){return (...args)=>run(()=>action.apply(control,args),{control});},
  dispose(){if(closed)return;closed=true;indicator?.remove();indicator=null;for(const [control,record] of controls)restore(control,record);controls.clear();previousBusy===null?root.removeAttribute('aria-busy'):root.setAttribute('aria-busy',previousBusy);},
  get pending(){return count;}
 };
}
