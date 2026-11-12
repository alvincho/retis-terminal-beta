/** Pinned glTF 2 renderer. Assets never supply scripts or authentication. */
export async function mountAssetScene(element,scene){
 const [{GLTFLoader},THREE]=await Promise.all([import('./composition-vendor/three/GLTFLoader.js'),import('./composition-vendor/three/three.module.js')]);
 if(!element.isConnected)return;
 const manager=new THREE.LoadingManager();manager.setURLModifier(value=>{if(value.startsWith('blob:')||/^data:(application|image)\//.test(value))return value;const u=new URL(value,scene.asset.url);if(u.protocol!=='https:'||u.username||u.password)throw new Error('Model resource requires HTTPS');return u.href;});
 const control=new AbortController();let removed=false,cleanup=()=>{};
 const observer=new MutationObserver(()=>{if(!element.isConnected){removed=true;control.abort();observer.disconnect();cleanup();}});observer.observe(document.body,{childList:true,subtree:true});
 try{
  const response=await fetch(scene.asset.url,{credentials:'omit',referrerPolicy:'no-referrer',redirect:'error',signal:AbortSignal.any([control.signal,AbortSignal.timeout(15000)])});if(!response.ok)throw new Error('Model is unavailable');
  if(Number(response.headers.get('content-length'))>20000000)throw new Error('Model exceeds 20 MB');
  const reader=response.body.getReader(),parts=[];let total=0;
  while(true){const {done,value}=await reader.read();if(done)break;total+=value.byteLength;if(total>20000000){await reader.cancel();throw new Error('Model exceeds 20 MB');}parts.push(value);}
  const bytes=new Uint8Array(total);let offset=0;for(const part of parts){bytes.set(part,offset);offset+=part.length;}
  if(scene.asset.sha256){const hash=[...new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))].map(v=>v.toString(16).padStart(2,'0')).join('');if(hash!==scene.asset.sha256)throw new Error('Model integrity mismatch');}
  const jsonBytes=scene.asset.format==='gltf'?bytes:bytes.slice(20,20+new DataView(bytes.buffer).getUint32(12,true));
  const manifest=JSON.parse(new TextDecoder().decode(jsonBytes).replace(/\0+$/,''));
  for(const key of ['nodes','meshes','accessors','animations'])if((manifest[key]||[]).length>2000)throw new Error('Model complexity exceeds limits');
  if((manifest.images||[]).length>64||(manifest.accessors||[]).reduce((n,a)=>n+(Number(a.count)||0),0)>2000000)throw new Error('Model geometry exceeds limits');
  if([...(manifest.buffers||[]),...(manifest.images||[])].some(r=>r.uri&&!/^data:(application|image)\//.test(r.uri)))throw new Error('Use a self-contained glTF or GLB asset.');
  const model=await new GLTFLoader(manager).parseAsync(scene.asset.format==='gltf'?new TextDecoder().decode(bytes):bytes.buffer,new URL('.',scene.asset.url).href);
  model.scene.traverse(node=>{for(const material of Array.isArray(node.material)?node.material:[node.material])if(material)for(const value of Object.values(material))if(value?.isTexture&&value.image?.width*value.image?.height>16000000)throw new Error('Model texture exceeds limits');});
  if(removed)return;
  const canvas=element.querySelector('canvas'),renderer=new THREE.WebGLRenderer({canvas,alpha:true,antialias:true,preserveDrawingBuffer:true}),world=new THREE.Scene(),camera=new THREE.PerspectiveCamera(45,1,.01,1000);
  world.add(new THREE.HemisphereLight(0xffffff,0x334455,3));const light=new THREE.DirectionalLight(0xffffff,3);light.position.set(3,5,4);world.add(light);world.add(model.scene);
  const bounds=new THREE.Box3().setFromObject(model.scene),center=bounds.getCenter(new THREE.Vector3()),size=Math.max(...bounds.getSize(new THREE.Vector3()).toArray(),.1);model.scene.position.sub(center);
  let angle=.4,playing=false,drag=null,last=performance.now(),elapsed=0,frame=0;function invalidate(){if(!frame&&element.isConnected)frame=requestAnimationFrame(draw);}const mixer=new THREE.AnimationMixer(model.scene);const clips=model.animations.filter(c=>!scene.asset.animation||c.name===scene.asset.animation);clips.forEach(c=>mixer.clipAction(c).play());
  const slider=element.querySelector('[data-scene-time]'),duration=Math.max(...clips.map(c=>c.duration),1);if(slider){slider.max=duration;slider.oninput=()=>{elapsed=Number(slider.value);mixer.setTime(elapsed);invalidate();};}
  const button=element.querySelector('[data-scene-play]');button.onclick=()=>{playing=!playing;button.setAttribute('aria-pressed',String(playing));last=performance.now();invalidate();};
  canvas.onpointerdown=e=>{drag=e.clientX;canvas.setPointerCapture(e.pointerId);};canvas.onpointermove=e=>{if(drag!==null){angle+=(e.clientX-drag)/100;drag=e.clientX;invalidate();}};canvas.onpointerup=()=>drag=null;
  const resize=new ResizeObserver(()=>{renderer.setPixelRatio(Math.min(devicePixelRatio,2));renderer.setSize(Math.max(1,element.clientWidth),Math.max(100,element.clientHeight-32),false);camera.aspect=canvas.width/canvas.height;camera.updateProjectionMatrix();invalidate();});resize.observe(element);
  element.dataset.assetLoaded='true';element.querySelector('[data-scene-status]').textContent=scene.description||scene.asset.version;
  cleanup=()=>{cancelAnimationFrame(frame);resize.disconnect();observer.disconnect();renderer.dispose();mixer.stopAllAction();mixer.uncacheRoot(model.scene);world.traverse(o=>{o.geometry?.dispose();const materials=Array.isArray(o.material)?o.material:[o.material];for(const m of materials)if(m){for(const v of Object.values(m))if(v?.isTexture)v.dispose();m.dispose();}});};
  function draw(now){frame=0;if(!element.isConnected){cleanup();return;}if(playing){const delta=Math.min((now-last)/1000,.1);elapsed=(elapsed+delta)%duration;mixer.setTime(elapsed);if(slider)slider.value=elapsed;}last=now;camera.position.set(Math.sin(angle)*size*2,size*.7,Math.cos(angle)*size*2);camera.lookAt(0,0,0);renderer.render(world,camera);if(playing)invalidate();}
  invalidate();

 }catch(error){if(!removed){element.dataset.assetError='true';element.querySelector('[data-scene-status]').textContent=error.message;}}finally{if(removed)observer.disconnect();}
}
