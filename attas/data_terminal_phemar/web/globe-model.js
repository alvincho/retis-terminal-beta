/** WGS84 data-only Globe View input. No transport, scripts, or inferred locations. */
export const GLOBE_LIMITS=Object.freeze({layers:12,features:1000,coordinates:20000,bytes:160000,fileBytes:2000000});
const object=v=>v!==null&&typeof v==='object'&&!Array.isArray(v);
const finite=v=>typeof v==='number'&&Number.isFinite(v);
const clone=v=>JSON.parse(JSON.stringify(v));
const position=v=>Array.isArray(v)&&v.length>=2&&v.length<=3&&v.every(finite)&&Math.abs(v[0])<=180&&Math.abs(v[1])<=90&&(v.length===2||Math.abs(v[2])<=10000000);
export function featureCollection(input){
 if(input?.crs)throw new Error('Reproject data to WGS84 longitude / latitude before importing.');
 let features=input?.type==='FeatureCollection'?input.features:input?.type==='Feature'?[input]:input?.type&&(input.coordinates||input.type==='GeometryCollection')?[{type:'Feature',properties:{},geometry:input}]:null;
 if(!Array.isArray(features)||features.length>GLOBE_LIMITS.features)throw new Error('Use a GeoJSON FeatureCollection with at most 1,000 features.');
 let count=0;
 function point(v){if(!position(v))throw new Error('Coordinates must be finite WGS84 [longitude, latitude, optional height].');if(++count>GLOBE_LIMITS.coordinates)throw new Error('Simplify geometry to at most 20,000 coordinates.');}
 function sequence(v,min){if(!Array.isArray(v)||v.length<min)throw new Error('Geometry has too few coordinates.');v.forEach(point);}
 function ring(v){sequence(v,4);if(JSON.stringify(v[0])!==JSON.stringify(v.at(-1)))throw new Error('Polygon rings must be closed.');}
 function geometry(g,depth=0){
  if(g===null)return;
  if(!object(g)||depth>4)throw new Error('Invalid GeoJSON geometry.');
  const c=g.coordinates;
  switch(g.type){
   case 'Point':point(c);break;
   case 'MultiPoint':sequence(c,1);break;
   case 'LineString':sequence(c,2);break;
   case 'MultiLineString':if(!Array.isArray(c)||!c.length)throw new Error('Invalid lines.');c.forEach(v=>sequence(v,2));break;
   case 'Polygon':if(!Array.isArray(c)||!c.length)throw new Error('Invalid polygon.');c.forEach(ring);break;
   case 'MultiPolygon':if(!Array.isArray(c)||!c.length)throw new Error('Invalid polygons.');c.forEach(p=>{if(!Array.isArray(p)||!p.length)throw new Error('Invalid polygon.');p.forEach(ring);});break;
   case 'GeometryCollection':if(!Array.isArray(g.geometries)||g.geometries.length>1000)throw new Error('Invalid geometry collection.');g.geometries.forEach(v=>geometry(v,depth+1));break;
   default:throw new Error('Unsupported GeoJSON geometry type.');
  }
 }
 const result={type:'FeatureCollection',features:features.map((f,i)=>{
  if(!object(f)||f.type!=='Feature'||f.properties!=null&&!object(f.properties))throw new Error('Invalid GeoJSON feature.');geometry(f.geometry);
  // Source properties remain plain JSON. Rendering always uses textContent.
  return {type:'Feature',id:typeof f.id==='string'||finite(f.id)?f.id:String(i+1),properties:clone(f.properties||{}),geometry:clone(f.geometry)};
 })};
 if(JSON.stringify(result).length>GLOBE_LIMITS.bytes)throw new Error('Simplify or split this dataset; saved Globe data is limited to 160 KB.');
 return result;
}
function csvRows(text){
 const rows=[];let row=[],cell='',quoted=false;
 for(let i=0;i<text.length;i++){
  const c=text[i];if(c==='"'){if(quoted&&text[i+1]==='"'){cell+='"';i++;}else quoted=!quoted;}
  else if(c===','&&!quoted){row.push(cell);cell='';}
  else if((c==='\n'||c==='\r')&&!quoted){if(c==='\r'&&text[i+1]==='\n')i++;row.push(cell);if(row.some(v=>v.trim()))rows.push(row);row=[];cell='';}
  else cell+=c;
 }
 if(quoted)throw new Error('CSV contains an unclosed quoted field.');row.push(cell);if(row.some(v=>v.trim()))rows.push(row);
 return rows;
}
export function rowsToGeoJSON(rows){
 if(!Array.isArray(rows)||rows.length>GLOBE_LIMITS.features)throw new Error('Use at most 1,000 coordinate rows.');
 function pick(row,names){const key=Object.keys(row).find(k=>names.includes(k.toLowerCase().trim()));return key===undefined?undefined:row[key];}
 const number=v=>typeof v==='number'?v:typeof v==='string'&&v.trim()?Number(v):NaN;
 return featureCollection({type:'FeatureCollection',features:rows.map((row,i)=>{
  if(!object(row))throw new Error('Coordinate rows must be objects.');
  const lon=number(pick(row,['longitude','lon','lng'])),lat=number(pick(row,['latitude','lat']));
  if(!finite(lon)||!finite(lat))throw new Error('Every row needs latitude/lat and longitude/lon/lng. No location is inferred.');
  const elevation=pick(row,['altitude','height','elevation']);const coordinates=[lon,lat];if(elevation!==undefined&&elevation!==null&&elevation!=='')coordinates.push(number(elevation));
  return {type:'Feature',id:row.id??String(i+1),properties:row,geometry:{type:'Point',coordinates}};
 })});
}
export function importGlobeText(text,name){
 if(new TextEncoder().encode(text).length>GLOBE_LIMITS.fileBytes)throw new Error('Choose a GeoJSON or CSV file smaller than 2 MB.');
 if(/\.csv$/i.test(name)){
  const [header,...values]=csvRows(text.replace(/^\uFEFF/,''));if(!header||!values.length)throw new Error('CSV needs column names and at least one data row.');
  if(new Set(header.map(h=>h.trim().toLowerCase())).size!==header.length)throw new Error('CSV column names must be unique.');
  return rowsToGeoJSON(values.map(cells=>{if(cells.length!==header.length)throw new Error('CSV row length does not match its header.');return Object.fromEntries(header.map((h,i)=>[h.trim(),cells[i]]));}));
 }
 let input;try{input=JSON.parse(text);}catch{throw new Error('This file is not valid JSON.');}
 return Array.isArray(input)?rowsToGeoJSON(input):featureCollection(input);
}
export function normalizeGlobe(input={}){
 if(!object(input))throw new Error('Globe View needs a data object.');
 const center=input.center||[120,23,22000000];
 if(!Array.isArray(center)||center.length!==3||!center.every(finite)||Math.abs(center[0])>180||Math.abs(center[1])>90||center[2]<100||center[2]>100000000)throw new Error('Center needs [longitude, latitude, camera height in meters], with height 100…100,000,000.');
 if(!['satellite','streets','natural-earth'].includes(input.basemap||'satellite'))throw new Error('Unsupported basemap.');
 const layers=input.layers||[];if(!Array.isArray(layers)||layers.length>GLOBE_LIMITS.layers)throw new Error('Globe View supports at most 12 saved layers.');
 const ids=new Set();
 const model={center:[...center],basemap:input.basemap||'satellite',layers:layers.map((l,i)=>{
  if(!object(l)||typeof l.name!=='string'||!l.name.trim()||l.name.length>80)throw new Error('Each layer needs a name of at most 80 characters.');
  const id=l.id||'layer-'+(i+1);if(typeof id!=='string'||!/^[A-Za-z0-9_-]{1,80}$/.test(id)||ids.has(id))throw new Error('Layer IDs must be unique.');ids.add(id);
  if(l.color!==undefined&&!/^#[a-f0-9]{6}$/i.test(l.color))throw new Error('Layer color must be a hex color.');
  if(l.opacity!==undefined&&(!finite(l.opacity)||l.opacity<0||l.opacity>1))throw new Error('Layer opacity must be between 0 and 1.');
  return {id,name:l.name,color:l.color||'#5bd9c4',opacity:l.opacity??.85,visible:l.visible!==false,geojson:featureCollection(l.geojson)};
 })};
 if(input.sourceStyle!==undefined){
  const style=input.sourceStyle;if(!object(style)||Object.keys(style).some(k=>!['visible','opacity'].includes(k))||style.visible!==undefined&&typeof style.visible!=='boolean'||style.opacity!==undefined&&(!finite(style.opacity)||style.opacity<0||style.opacity>1))throw new Error('Invalid connected layer visibility or opacity.');
  model.sourceStyle={visible:style.visible!==false,opacity:style.opacity??.9};
 }
 if(new TextEncoder().encode(JSON.stringify(model)).length>GLOBE_LIMITS.bytes)throw new Error('Saved Globe layers exceed 160 KB. Simplify or split the data.');
 return model;
}
export function sourceLayer(data,style={}){
 if(data===undefined||data===null||Array.isArray(data)&&!data.length)return null;
 const rows=Array.isArray(data)?data:data.rows||data.items;
 return {id:'source-data',name:'Connected data',color:'#f5b85c',opacity:style.opacity??.9,visible:style.visible!==false,geojson:rows?rowsToGeoJSON(rows):object(data)&&!data.type?rowsToGeoJSON([data]):featureCollection(data),readonly:true};
}
