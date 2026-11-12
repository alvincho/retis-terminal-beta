import capture from './preview-capture-vendor.js';

const svgNamespace='http://www.w3.org/2000/svg';
const paintProperties=['fill','fill-opacity','stroke','stroke-opacity','stroke-width','stroke-linecap','stroke-linejoin','stroke-dasharray','stroke-dashoffset','opacity','color','font-family','font-size','font-weight','text-anchor','dominant-baseline','visibility','display'];

/** The DOM capture library copies HTML styles but omits computed SVG styles. */
export async function toJpeg(node,options){
 const url=await capture.toSvg(node,options);
 const documentCopy=new DOMParser().parseFromString(decodeURIComponent(url.slice(url.indexOf(',')+1)),'image/svg+xml');
 const foreign=documentCopy.documentElement.querySelector('foreignObject');
 const included=n=>{
  for(let p=n;p&&p!==node;p=p.parentElement)if(options.filter&&!options.filter(p))return false;
  return true;
 };
 const originals=[...node.querySelectorAll('*')].filter(n=>n.namespaceURI===svgNamespace&&included(n));
 const clones=[...foreign.querySelectorAll('*')].filter(n=>n.namespaceURI===svgNamespace);
 // Avoid associating styles with unrelated nodes if a custom SVG adds symbols.
 if(originals.length===clones.length)originals.forEach((original,i)=>{
  const computed=getComputedStyle(original);
  for(const property of paintProperties)clones[i].style.setProperty(property,computed.getPropertyValue(property));
 });
 const image=new Image();
 const ready=new Promise((resolve,reject)=>{image.onload=resolve;image.onerror=()=>reject(new Error('Preview rasterization failed.'));});
 image.src='data:image/svg+xml;charset=utf-8,'+encodeURIComponent(new XMLSerializer().serializeToString(documentCopy));
 await ready;
 const canvas=document.createElement('canvas');
 canvas.width=options.canvasWidth||options.width;canvas.height=options.canvasHeight||options.height;
 canvas.getContext('2d').drawImage(image,0,0,canvas.width,canvas.height);
 return canvas.toDataURL('image/jpeg',options.quality??.82);
}
