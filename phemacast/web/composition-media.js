import {mountCompactComposition} from './compact-composition.js';
import {permittedResource,boundedAsset} from './media-resource-policy.js';
import {customHTML} from './composition-renderers.js';
import {interactionResultHTML} from './interaction-result.js';
import {structuredFields} from './structured-operation-fields.js';
/** Generic, declarative media renderers. No markup, scripts or plugin code from Phema. */
const escape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export const MEDIA_TYPES=['text','image','audio','video','scene','interactive'];
export function safeMediaURL(value){try{const u=new URL(value);return u.protocol==='https:'&&!u.username&&!u.password?u.href:null;}catch{return null;}}
export function mediaHTML(kind,value,{fallback='Media unavailable',label=x=>x,uiLabel=null,uiAttribute=null,resolveURL=x=>x,resourcePolicy=null,authenticatedLocal=false}={}){
 const text=uiLabel||((key)=>escape(label(key))),attr=uiAttribute||((name,key)=>`${name}="${escape(label(key))}"`);
 if(!value||typeof value!=='object')return `<p class="panel-note">${text(fallback)}</p>`;
 if(kind==='text')return `<div class="composition-text">${escape(value.text??value.title??'')}${safeMediaURL(value.url)?`<p><a href="${escape(safeMediaURL(value.url))}" target="_blank" rel="noopener noreferrer">${value.title?escape(value.title):text('Open source')}</a></p>`:''}</div>`;
 if(kind==='interactive')return interactiveHTML(value,text,label);
 if(kind==='scene'&&value.asset&&!permittedResource(value.asset.url,resourcePolicy))return `<p class="panel-note">${text('Phema permission review required')}</p>`;
 if(kind==='scene')return `<div class="composition-scene" data-scene="${escape(JSON.stringify(value))}"><canvas ${value.description?`aria-label="${escape(value.description)}"`:attr('aria-label','3D scene')}></canvas><button type="button" data-scene-play ${attr('title','Play / pause')}>▶</button><input data-scene-time type="range" min="0" max="${Number(value.timeline?.duration)||60}" step="0.01" value="0" ${attr('aria-label','Timeline')}><span data-scene-status>${escape(value.description||'')}</span></div>`;
 const url=permittedResource(value.url,resourcePolicy);
 if(!url)return `<p class="panel-note">${value.transcript?escape(value.transcript):text(fallback)}</p>`;
 if(kind==='image')return `<img class="composition-image" data-asset-url="${escape(url)}" crossorigin="anonymous" alt="${escape(value.alt||'')}" loading="lazy" referrerpolicy="no-referrer">`;
 let content='';
 const youtube=kind==='video'&&permittedResource('https://www.youtube-nocookie.com/',resourcePolicy)&&new URL(url).hostname.match(/^(?:www\.)?(?:youtube\.com|youtu\.be)$/);
 if(youtube){const u=new URL(url),parts=u.pathname.split('/').filter(Boolean),id=u.hostname==='youtu.be'?parts[0]:u.searchParams.get('v')||(['shorts','embed','live'].includes(parts[0])?parts[1]:null);content=/^[\w-]{11}$/.test(id||'')?`<iframe src="https://www.youtube-nocookie.com/embed/${id}" ${value.title?`title="${escape(value.title)}"`:attr('title','Video')} sandbox="allow-scripts allow-same-origin allow-presentation" allow="fullscreen" loading="lazy" referrerpolicy="no-referrer"></iframe>`:'';}
 else if(kind==='audio'||kind==='video')content=`<${kind} controls preload="metadata" data-playback="${escape(JSON.stringify(value.playback||{}))}" data-asset-url="${escape(resolveURL(url))}" data-authenticated-local="${authenticatedLocal?'true':'false'}" crossorigin="anonymous" ${value.poster&&permittedResource(value.poster,resourcePolicy)?`data-poster-url="${escape(resolveURL(value.poster))}"`:''}>${(Array.isArray(value.captions)?value.captions:[]).filter(t=>permittedResource(t.url,resourcePolicy)).map(t=>`<track kind="captions" data-authenticated-local="${authenticatedLocal?'true':'false'}" data-asset-url="${escape(resolveURL(t.url))}" srclang="${escape(t.language)}" label="${escape(t.label||t.language)}">`).join('')}</${kind}>`;
 return `<div class="composition-media">${content}<a href="${escape(resolveURL(url))}" target="_blank" rel="noopener noreferrer">${value.title?escape(value.title):text('Open media')}</a>${value.transcript?`<details><summary>${text('Transcript')}</summary><p>${escape(value.transcript)}</p></details>`:''}</div>`;
}
export function mountScenes(root=document){
 for(const element of root.querySelectorAll('.composition-scene:not([data-mounted])')){
  element.dataset.mounted='true';
  if(sceneAsset(element)){continue;}
  const canvas=element.querySelector('canvas'),ctx=canvas.getContext('2d');let scene;
  try{scene=JSON.parse(element.dataset.scene);}catch{continue;}
  let angle=.45,playing=false,last=0,elapsed=0,frame=null,drag=null;
  const vector=(v,fallback)=>Array.isArray(v)&&v.length===3&&v.every(n=>Number.isFinite(n)&&Math.abs(n)<=10000)?v:fallback;
  const objects=Array.isArray(scene?.objects)?scene.objects.filter(o=>o&&typeof o==='object').slice(0,100):[];
  function draw(now){
   if(!element.isConnected)return;
   if(playing)elapsed+=Math.min((now-last)/1000,.1);last=now;
   const width=Math.max(1,element.clientWidth),height=Math.max(100,element.clientHeight-28);canvas.width=width*devicePixelRatio;canvas.height=height*devicePixelRatio;canvas.style.width=width+'px';canvas.style.height=height+'px';ctx.scale(devicePixelRatio,devicePixelRatio);ctx.clearRect(0,0,width,height);
   const scale=Math.min(width,height)/8;
   const project=([x,y,z])=>[width/2+(x*Math.cos(angle)-z*Math.sin(angle))*scale,height/2+(-y+(x*Math.sin(angle)+z*Math.cos(angle))*.35)*scale];
   for(const obj of objects){
    const animated=timelineObject(obj,scene.timeline,elapsed);
    const p=vector(animated.position,[0,0,0]),s=vector(animated.size,[1,1,1]),r=vector(animated.rotation,[0,0,0]);
    const spin=Number.isFinite(obj.spin)&&Math.abs(obj.spin)<=10?obj.spin:0;
    const transform=([x,y,z])=>{const [rx,ry,rz]=[r[0],r[1]+spin*elapsed,r[2]];[y,z]=[y*Math.cos(rx)-z*Math.sin(rx),y*Math.sin(rx)+z*Math.cos(rx)];[x,z]=[x*Math.cos(ry)-z*Math.sin(ry),x*Math.sin(ry)+z*Math.cos(ry)];[x,y]=[x*Math.cos(rz)-y*Math.sin(rz),x*Math.sin(rz)+y*Math.cos(rz)];return project([p[0]+x,p[1]+y,p[2]+z]);};
    ctx.strokeStyle=/^#[0-9a-f]{6}$/i.test(obj.color||'')?obj.color:'#86bccc';ctx.lineWidth=1.3;
    if(obj.shape==='sphere'){for(const plane of [[0,1],[1,2],[0,2]]){ctx.beginPath();for(let i=0;i<=32;i++){const v=[0,0,0];v[plane[0]]=Math.cos(i/32*2*Math.PI)*s[plane[0]]/2;v[plane[1]]=Math.sin(i/32*2*Math.PI)*s[plane[1]]/2;const point=transform(v);i?ctx.lineTo(...point):ctx.moveTo(...point);}ctx.stroke();}continue;}
    if(obj.shape==='mesh'){const points=(obj.vertices||[]).slice(0,10000).map(v=>transform(v.map((x,i)=>x*s[i])));for(const face of (obj.faces||[]).slice(0,20000)){if(face.some(i=>!points[i]))continue;ctx.beginPath();face.forEach((index,i)=>i?ctx.lineTo(...points[index]):ctx.moveTo(...points[index]));ctx.closePath();ctx.stroke();}continue;}
    if(obj.shape!=='box')continue;
    const vertices=Array.from({length:8},(_,i)=>transform([((i&1)?1:-1)*s[0]/2,((i&2)?1:-1)*s[1]/2,((i&4)?1:-1)*s[2]/2]));
    for(let i=0;i<8;i++)for(const bit of [1,2,4])if(!(i&bit)){ctx.beginPath();ctx.moveTo(...vertices[i]);ctx.lineTo(...vertices[i|bit]);ctx.stroke();}
   }
   const slider=element.querySelector('[data-scene-time]');if(slider&&!slider.matches(':active'))slider.value=scene.timeline?.loop?elapsed%scene.timeline.duration:elapsed;
   if(playing&&!frame)frame=requestAnimationFrame(time=>{frame=null;draw(time);});
  }
  element.querySelector('[data-scene-play]').onclick=()=>{playing=!playing;last=performance.now();draw(last);};
  const slider=element.querySelector('[data-scene-time]');if(slider)slider.oninput=()=>{elapsed=Number(slider.value);draw(performance.now());};
  canvas.onpointerdown=e=>{drag=e.clientX;canvas.setPointerCapture(e.pointerId);};canvas.onpointermove=e=>{if(drag!==null){angle+=(e.clientX-drag)/100;drag=e.clientX;draw(performance.now());}};canvas.onpointerup=()=>{drag=null;};
  const observer=new ResizeObserver(()=>{if(element.isConnected)draw(performance.now());else observer.disconnect();});observer.observe(element);draw(performance.now());
 }
}

function interactiveHTML(value,text,plainText){
 const fields=structuredFields(value.inputSchema,{text});
 return `<form data-phema-interaction><fieldset ${value.disabled?'disabled':''}><div class="phema-form-grid">${fields}</div><button type="submit">${text(value.submitLabel||'Run interaction')}</button><p role="status"></p>${resultHTML(value.result,text,plainText)}</fieldset></form>`;
}
// A result that hands over a resolved Phema keeps its open button when the panel redraws.
function resultHTML(result,text,plainText){
 const navigation=!result||typeof result!=='object'?[]:Array.isArray(result.navigation)?result.navigation:result.phema?[{label:'Open Phema',phema:result.phema,source_mode:result.source_mode}]:[];
 const items=navigation.slice(0,4).filter(item=>item?.phema&&['parameters','snapshot'].includes(item.source_mode));
 if(items.length)return `<div class="composition-interaction-result">${text('Operation completed.')} ${items.map(item=>`<button type="button" data-phema-result="${escape(JSON.stringify({phema:item.phema,sourceMode:item.source_mode}))}">${text(item.label||'Open Phema')}</button>`).join('')}</div>`;
 // Owner tables and key values render as on acknowledgement, not as raw JSON.
 return `<div class="composition-interaction-result">${interactionResultHTML(result,{escape,render:customHTML,text,plainText})}</div>`;
}
export function timelineObject(object,timeline,elapsed){
 const result={...object};if(!timeline)return result;
 const time=timeline.loop?elapsed%timeline.duration:Math.min(elapsed,timeline.duration);
 for(const track of timeline.tracks||[]){if(track.target!==object.id)continue;const frames=track.keyframes;if(!frames?.length)continue;let left=frames[0],right=frames.at(-1);for(const frame of frames){if(frame.time<=time)left=frame;if(frame.time>=time){right=frame;break;}}const ratio=left===right?0:Math.max(0,Math.min(1,(time-left.time)/(right.time-left.time)));result[track.property]=left.value.map((v,i)=>v+(right.value[i]-v)*ratio);}
 return result;
}
function sceneAsset(element){let scene;try{scene=JSON.parse(element.dataset.scene);}catch{return false;}if(!scene.asset)return false;import('./composition-scene-assets.js').then(m=>m.mountAssetScene(element,scene)).catch(()=>{element.querySelector('[data-scene-status]').textContent='Model renderer unavailable';});return true;}
export function mountMedia(root){
 mountCompactComposition(root);
 // The host opens the handed-over Phema; mount authorization still runs on its server.
 for(const button of root.querySelectorAll('[data-phema-result]:not([data-result-mounted])')){
  button.dataset.resultMounted='true';
  button.onclick=()=>{let detail;try{detail=JSON.parse(button.dataset.phemaResult);}catch{return;}
   button.dispatchEvent(new CustomEvent('phemacast:open-result',{bubbles:true,detail}));};
 }
 for(const media of root.querySelectorAll('[data-asset-url]:not([data-asset-loading])')){
  media.dataset.assetLoading='true';const control=new AbortController(),blobs=[];
  const observer=new MutationObserver(()=>{if(!media.isConnected){control.abort();blobs.forEach(URL.revokeObjectURL);observer.disconnect();}});observer.observe(document.body,{childList:true,subtree:true});
  const limit=media.tagName==='TRACK'?250000:media.tagName==='IMG'?20000000:50000000;
  boundedAsset(media.dataset.assetUrl,{maxBytes:limit,signal:control.signal,authenticatedLocal:media.dataset.authenticatedLocal==='true'}).then(blob=>{if(!media.isConnected)return;const url=URL.createObjectURL(blob);blobs.push(url);media.src=url;
   media.addEventListener(media.tagName==='IMG'?'load':'loadedmetadata',()=>{if((media.naturalWidth||media.videoWidth||0)*(media.naturalHeight||media.videoHeight||0)>16000000||media.duration>3600){media.removeAttribute('src');media.dataset.assetError='Media decoding limit exceeded';}},{once:true});
  }).catch(()=>{media.dataset.assetError='Media unavailable';});
  if(media.dataset.posterUrl)boundedAsset(media.dataset.posterUrl,{maxBytes:20000000,signal:control.signal,authenticatedLocal:media.dataset.authenticatedLocal==='true'}).then(blob=>{const url=URL.createObjectURL(blob);blobs.push(url);media.poster=url;}).catch(()=>{});
 }
for(const media of root.querySelectorAll('audio[data-playback]:not([data-mounted]),video[data-playback]:not([data-mounted])')){media.dataset.mounted='true';let p;try{p=JSON.parse(media.dataset.playback);}catch{p={};}media.muted=p.muted===true;media.playbackRate=p.rate||1;const start=Number(p.start)||0;const init=()=>{if(start<media.duration)media.currentTime=start;};media.addEventListener('loadedmetadata',init,{once:true});if(media.readyState>=1)init();media.addEventListener('timeupdate',()=>{const end=Math.min(Number(p.end)||media.duration,media.duration);if(media.currentTime>=end){if(p.loop){media.currentTime=start;media.play().catch(()=>{});}else media.pause();}});}}
