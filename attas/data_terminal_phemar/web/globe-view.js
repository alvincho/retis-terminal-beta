import {normalizeGlobe,sourceLayer,importGlobeText,GLOBE_LIMITS} from './globe-model.js';
let engine;
function stylesheet(href){if(!document.querySelector(`link[data-globe-sheet="${href}"]`)){const link=document.createElement('link');link.rel='stylesheet';link.href=href;link.dataset.globeSheet=href;document.head.append(link);}}
function cesium(){
 if(engine)return engine;
 const base=new URL('./composition-vendor/cesium/',location.href).href;globalThis.CESIUM_BASE_URL=base;
 stylesheet(base+'Widgets/widgets.css');
 engine=new Promise((resolve,reject)=>{const script=document.createElement('script');script.src=base+'Cesium.js';script.onload=()=>globalThis.Cesium?resolve(globalThis.Cesium):reject(new Error('Globe engine did not initialize.'));script.onerror=()=>{engine=null;script.remove();reject(new Error('Globe engine is unavailable. Reload and try again.'));};document.head.append(script);});
 return engine;
}
export function globeHTML(value,{escape}){
 normalizeGlobe(value);stylesheet(new URL('./globe-view.css',location.href).href);
 return `<div class="globe-view"><div class="globe-toolbar"><button type="button" data-globe-action="layers" aria-expanded="true">Layers</button><label><span>Map</span><select data-globe-basemap aria-label="Globe basemap"><option value="satellite">Satellite</option><option value="streets">Streets</option><option value="natural-earth">Natural Earth · offline</option></select></label><form data-globe-go><input name="coordinates" placeholder="Latitude, longitude" aria-label="Go to latitude, longitude" autocomplete="off"><button type="submit">Go</button></form><button type="button" class="globe-import" data-globe-action="import">Import data</button><input type="file" accept=".geojson,.json,.csv" data-globe-file hidden><button type="button" data-globe-action="save">Save view</button><button type="button" data-globe-action="help" aria-label="How to Use Globe View">?</button></div><div class="globe-body"><div class="globe-canvas" aria-label="Interactive 3D Earth" tabindex="0"></div><div class="globe-loading" role="status">Loading globe…</div><aside class="globe-aside" aria-label="Geospatial layers"><header><strong>Data layers</strong><span data-globe-count>0</span></header><div data-globe-layers></div></aside><nav class="globe-nav" aria-label="Globe navigation"><button type="button" data-globe-action="north" aria-label="Face north" title="Face north">N</button><button type="button" data-globe-action="zoom-in" aria-label="Zoom in" title="Zoom in">+</button><button type="button" data-globe-action="zoom-out" aria-label="Zoom out" title="Zoom out">−</button><button type="button" data-globe-action="home" aria-label="Reset globe" title="Reset globe">◎</button><button type="button" data-globe-action="fit" aria-label="Fit visible data" title="Fit visible data">⌖</button></nav><section class="globe-details" aria-label="Selected feature" hidden><header><strong data-feature-title></strong><button type="button" data-globe-action="close-feature" aria-label="Close feature">×</button></header><dl></dl></section></div><footer class="globe-footer"><span class="globe-message" role="status">${escape(value.layers?.length?'Parameter layers':'No data layers · import GeoJSON or CSV')}</span><span class="globe-position">WGS84 · ellipsoid surface</span></footer></div>`;
}
export function mountGlobe(host,input,{escape}){
 const root=host.querySelector('.globe-view'),status=root.querySelector('.globe-message'),loading=root.querySelector('.globe-loading'),canvas=root.querySelector('.globe-canvas');
 let model=normalizeGlobe(input),alive=true,viewer=null,C=null,saving=false,generation=0,baseGeneration=0,sources=new Map(),resize,disposers=[];
 const editable=!!root.closest('[data-content-interaction]');root.querySelector('[data-globe-basemap]').value=model.basemap;
 function message(text,error=false){status.textContent=text;status.classList.toggle('globe-error',error);}
 const cameraCenter=()=>viewer?[C.Math.toDegrees(viewer.camera.positionCartographic.longitude),C.Math.toDegrees(viewer.camera.positionCartographic.latitude),Math.max(100,viewer.camera.positionCartographic.height)]:model.center;
 async function save(next){
  if(!editable)throw new Error('Open Globe View in a Data Browser to save layers.');
  if(saving)throw new Error('A Globe change is being saved. Try again shortly.');
  next=normalizeGlobe({...next,center:cameraCenter()});saving=true;
  try{await new Promise((resolve,reject)=>{root.dispatchEvent(new CustomEvent('browser-renderer-data',{bubbles:true,detail:{value:next,resolve,reject}}));});model=next;message('Saved with this Browser Phema.');}finally{saving=false;}
 }
 function featureInfo(entity){
  const details=root.querySelector('.globe-details');if(!entity){details.hidden=true;return;}
  root.querySelector('[data-feature-title]').textContent=entity.name||String(entity.id);const dl=details.querySelector('dl');dl.replaceChildren();
  const properties=entity.properties?.getValue(viewer.clock.currentTime)||{};
  for(const [key,value] of Object.entries(properties).slice(0,40)){const dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=key;dd.textContent=typeof value==='object'?JSON.stringify(value):String(value??'—');dl.append(dt,dd);}
  if(entity.position){const p=C.Cartographic.fromCartesian(entity.position.getValue(viewer.clock.currentTime));for(const [key,v] of [['Latitude',C.Math.toDegrees(p.latitude).toFixed(6)],['Longitude',C.Math.toDegrees(p.longitude).toFixed(6)]]){const dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=key;dd.textContent=v;dl.append(dt,dd);}}
  details.hidden=false;
 }
 async function basemap(name){
  if(!viewer)return;const token=++baseGeneration;
  try{
   let provider;
   if(name==='natural-earth')provider=new C.UrlTemplateImageryProvider({url:C.buildModuleUrl('Assets/Textures/NaturalEarthII')+'/{z}/{x}/{reverseY}.jpg',tilingScheme:new C.GeographicTilingScheme(),maximumLevel:5,credit:'Natural Earth'});
   else if(name==='satellite')provider=await C.ArcGisMapServerImageryProvider.fromUrl('https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer',{enablePickFeatures:false});
   else provider=new C.OpenStreetMapImageryProvider({url:'https://tile.openstreetmap.org/',maximumLevel:19,credit:'© OpenStreetMap contributors'});
   if(!alive||token!==baseGeneration)return;
   // Keep offline Natural Earth underneath remote imagery, including tile failures.
   while(viewer.imageryLayers.length>1)viewer.imageryLayers.remove(viewer.imageryLayers.get(1),true);
   if(name!=='natural-earth')viewer.imageryLayers.addImageryProvider(provider);
   provider.errorEvent?.addEventListener(()=>message('Map tiles unavailable · offline Natural Earth remains visible.',true));viewer.scene.requestRender();
  }catch(error){if(alive&&token===baseGeneration)message('Satellite map unavailable · using offline Natural Earth.',true);}
 }
 function layersUI(layers){
  root.querySelector('[data-globe-count]').textContent=String(layers.length);
  root.querySelector('[data-globe-layers]').innerHTML=layers.length?layers.map(l=>`<div class="globe-layer" data-globe-layer="${escape(l.id)}"><label class="globe-layer-name"><input type="checkbox" data-layer-visible ${l.visible?'checked':''}><strong>${escape(l.name)}</strong></label><small>${l.geojson.features.length} features · ${l.readonly?'Connected source':'Saved parameters'}</small><div class="globe-layer-controls"><input type="range" data-layer-opacity min="0" max="1" step="0.05" value="${l.opacity}" aria-label="${escape(l.name)} opacity"><button type="button" data-layer-fit aria-label="Fit ${escape(l.name)}">Fit</button>${!l.readonly?`<button type="button" data-layer-remove aria-label="Remove ${escape(l.name)}">×</button>`:''}</div></div>`).join(''):'<div class="globe-empty">Import GeoJSON or CSV with latitude and longitude columns.<br>Points, lines and polygons use WGS84 coordinates.</div>';
 }
 async function drawLayers(){
  const token=++generation;let layers=[...model.layers];try{const source=sourceLayer(input.data,model.sourceStyle);if(source)layers.push(source);}catch(error){message('Connected data: '+error.message,true);}
  layersUI(layers);if(!viewer)return;
  for(const s of sources.values())viewer.dataSources.remove(s,true);sources.clear();
  for(const layer of layers){
   try{const color=C.Color.fromCssColorString(layer.color);const source=await C.GeoJsonDataSource.load(layer.geojson,{clampToGround:false,stroke:color.withAlpha(layer.opacity),fill:color.withAlpha(layer.opacity*.4),strokeWidth:2,markerColor:color,markerSize:28});
    if(!alive||token!==generation){source.entities.removeAll();return;}
    source.show=layer.visible;
    for(const e of source.entities.values){if(e.billboard){e.billboard=undefined;e.point=new C.PointGraphics({pixelSize:9,color:color.withAlpha(layer.opacity),outlineColor:C.Color.WHITE,outlineWidth:1.5,disableDepthTestDistance:0});}}
    await viewer.dataSources.add(source);if(!alive||token!==generation){if(!viewer.isDestroyed())viewer.dataSources.remove(source,true);return;}sources.set(layer.id,source);
   }catch(error){if(alive)message('Layer '+layer.name+': '+error.message,true);}
  }
  if(alive)viewer.scene.requestRender();
 }
 function fit(source){if(!viewer)return;const entities=source?source.entities.values:[...sources.values()].filter(s=>s.show).flatMap(s=>s.entities.values);if(!entities.length){message('No visible features to fit.');return;}if(entities.length===1&&entities[0].position){const p=C.Cartographic.fromCartesian(entities[0].position.getValue(viewer.clock.currentTime));viewer.camera.flyTo({destination:C.Cartesian3.fromRadians(p.longitude,p.latitude,200000),orientation:{heading:0,pitch:-Math.PI/2,roll:0},duration:.8});return;}viewer.flyTo(entities,{duration:.8,offset:new C.HeadingPitchRange(0,-Math.PI/2,0)});}
 const onClick=async event=>{
  try{
   const action=event.target.closest('[data-globe-action]')?.dataset.globeAction;
   if(action==='layers'){const aside=root.querySelector('.globe-aside');aside.hidden=!aside.hidden;event.target.closest('button').setAttribute('aria-expanded',String(!aside.hidden));}
   if(action==='import')root.querySelector('[data-globe-file]').click();
   if(action==='help'){const dialog=document.createElement('dialog');dialog.className='globe-help';dialog.innerHTML='<button type="button">Close</button><strong>Globe View</strong><p>Drag to rotate. Scroll or pinch to zoom. Right-drag or Ctrl-drag to tilt. Select a feature to inspect its source properties.</p><p>Import GeoJSON (WGS84) or CSV with latitude/lat and longitude/lon/lng columns. Optional height/altitude is in meters. No coordinates are inferred from names.</p><p>Imports, visibility and opacity are saved with this Browser’s parameter Phema. Save view retains the current camera. Use Phema Edit → Save Phema to keep a named copy in App Consultant or on this device.</p><p>Limits: 12 saved layers, 1,000 features per layer, 160 KB saved data. Satellite imagery needs internet. Natural Earth works offline. Elevation uses an ellipsoid; terrain and Google imagery are not configured.</p>';root.append(dialog);dialog.querySelector('button').onclick=()=>dialog.close();dialog.onclose=()=>dialog.remove();dialog.showModal();}
   if(action==='save')await save(model);
   if(action==='close-feature')featureInfo(null);
   if(!viewer)return;
   if(action==='home')viewer.camera.flyTo({destination:C.Cartesian3.fromDegrees(...model.center),orientation:{heading:0,pitch:-Math.PI/2,roll:0},duration:.8});
   if(action==='north')viewer.camera.flyTo({destination:viewer.camera.positionWC.clone(),orientation:{heading:0,pitch:viewer.camera.pitch,roll:0},duration:.5});
   if(action==='zoom-in'||action==='zoom-out'){viewer.camera[action==='zoom-in'?'zoomIn':'zoomOut'](Math.max(30,viewer.camera.positionCartographic.height*.4));viewer.scene.requestRender();}
   if(action==='fit')fit();
   const item=event.target.closest('[data-globe-layer]');if(item){const id=item.dataset.globeLayer;if(event.target.closest('[data-layer-fit]'))fit(sources.get(id));if(event.target.closest('[data-layer-remove]'))await save({...model,layers:model.layers.filter(l=>l.id!==id)});}
  }catch(error){message(error.message,true);}
 };
 const onChange=async event=>{
  try{
   if(event.target.matches('[data-globe-file]')){const file=event.target.files[0];if(!file)return;if(file.size>GLOBE_LIMITS.fileBytes)throw new Error('Choose a file smaller than 2 MB.');message('Importing '+file.name+'…');const geojson=importGlobeText(await file.text(),file.name);if(!alive)return;await save({...model,layers:[...model.layers,{id:'layer-'+crypto.randomUUID(),name:file.name.slice(0,80),geojson,color:'#5bd9c4',opacity:.85,visible:true}]});}
   if(event.target.matches('[data-globe-basemap]')){const name=event.target.value;await save({...model,basemap:name});}
   const item=event.target.closest('[data-globe-layer]');if(item&&event.target.matches('[data-layer-visible],[data-layer-opacity]')){const key=event.target.matches('[data-layer-visible]')?'visible':'opacity',value=key==='visible'?event.target.checked:Number(event.target.value),id=item.dataset.globeLayer;
    if(id==='source-data'){await save({...model,sourceStyle:{...model.sourceStyle,[key]:value}});return;}
    await save({...model,layers:model.layers.map(l=>l.id===id?{...l,[key]:value}:l)});
   }
  }catch(error){message(error.message,true);root.querySelector('[data-globe-file]').value='';root.querySelector('[data-globe-basemap]').value=model.basemap;layersUI([...model.layers]);}
 };
 const onGo=event=>{event.preventDefault();try{const parts=event.target.elements.coordinates.value.split(',').map(v=>v.trim());if(parts.length!==2||parts.some(v=>!v))throw new Error('Enter latitude, longitude.');const [lat,lon]=parts.map(Number);if(!Number.isFinite(lat)||!Number.isFinite(lon)||Math.abs(lat)>90||Math.abs(lon)>180)throw new Error('Latitude must be −90…90; longitude −180…180.');if(viewer)viewer.camera.flyTo({destination:C.Cartesian3.fromDegrees(lon,lat,400000),orientation:{heading:0,pitch:-Math.PI/2,roll:0},duration:.9});}catch(error){message(error.message,true);}};
 root.addEventListener('click',onClick);root.addEventListener('change',onChange);root.querySelector('[data-globe-go]').addEventListener('submit',onGo);
 if(!editable){root.querySelector('[data-globe-action="import"]').disabled=true;root.querySelector('[data-globe-action="save"]').disabled=true;}
 layersUI(model.layers);
 cesium().then(async value=>{
  if(!alive||!root.isConnected)return;C=value;C.Ion.defaultAccessToken='';
  try{
   const natural=new C.UrlTemplateImageryProvider({url:C.buildModuleUrl('Assets/Textures/NaturalEarthII')+'/{z}/{x}/{reverseY}.jpg',tilingScheme:new C.GeographicTilingScheme(),maximumLevel:5,credit:'Natural Earth'});
   viewer=new C.Viewer(canvas,{baseLayer:new C.ImageryLayer(natural),baseLayerPicker:false,geocoder:false,homeButton:false,sceneModePicker:false,navigationHelpButton:false,animation:false,timeline:false,fullscreenButton:false,infoBox:false,selectionIndicator:true,requestRenderMode:true,maximumRenderTimeChange:Infinity,skyBox:false,skyAtmosphere:new C.SkyAtmosphere(),contextOptions:{webgl:{preserveDrawingBuffer:true}}});
   viewer.scene.backgroundColor=C.Color.fromCssColorString('#080f1b');viewer.resolutionScale=Math.min(devicePixelRatio||1,1.5);viewer.scene.globe.depthTestAgainstTerrain=false;viewer.scene.screenSpaceCameraController.maximumZoomDistance=80000000;
   viewer.camera.setView({destination:C.Cartesian3.fromDegrees(...model.center),orientation:{heading:0,pitch:-Math.PI/2,roll:0}});
   disposers.push(viewer.selectedEntityChanged.addEventListener(featureInfo));
   disposers.push(viewer.camera.changed.addEventListener(()=>{const p=cameraCenter();root.querySelector('.globe-position').textContent=p[1].toFixed(3)+'°, '+p[0].toFixed(3)+'° · '+Math.round(p[2]/1000).toLocaleString()+' km';}));
   viewer.camera.percentageChanged=.01;
   resize=new ResizeObserver(()=>{if(alive&&!viewer.isDestroyed()){viewer.resize();viewer.scene.requestRender();}});resize.observe(canvas);
   let frames=0;const ready=viewer.scene.postRender.addEventListener(()=>{
    frames++;if(frames>=2&&viewer.scene.globe.tilesLoaded){root.dataset.globeReady='true';loading.remove();ready();}
    else if(frames<2)viewer.scene.requestRender();
   });disposers.push(ready);
   await Promise.all([basemap(model.basemap),drawLayers()]);viewer.scene.requestRender();
  }catch(error){if(alive){loading.textContent='3D globe unavailable. This browser needs WebGL 2.';message(error.message,true);root.dataset.globeError='true';}}
 }).catch(error=>{if(alive){loading.textContent=error.message;message(error.message,true);}});
 return ()=>{alive=false;generation++;baseGeneration++;root.removeEventListener('click',onClick);root.removeEventListener('change',onChange);resize?.disconnect();disposers.forEach(fn=>fn());if(viewer&&!viewer.isDestroyed())viewer.destroy();};
}
