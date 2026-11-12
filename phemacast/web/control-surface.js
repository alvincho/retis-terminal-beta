/** Trusted primitives for owner-authored data. No owner script, HTML or URL runs. */
import {structuredForm,structuredContent} from '../core/structured-forms.js';

const element=(tag,text)=>{const node=document.createElement(tag);if(text!==undefined)node.textContent=String(text);return node;};
const number=value=>Number.isFinite(value)?new Intl.NumberFormat('en-US',{minimumFractionDigits:2,maximumFractionDigits:2}).format(value):'—';
const readable=value=>String(value??'').replace(/\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b/gi,'Saved reference');

export function mountControlSurface(root,surface,{invoke,onChange=()=>{}}){
 if(surface?.schema!=='phemacast:control-surface/1'||!Array.isArray(surface.nodes)||surface.nodes.length>100||typeof invoke!=='function')throw Error('A bounded data surface and verified host channel are required.');
 let disposed=false;const cleanups=[],dialogs=[],results=element('section');const status=element('p');status.setAttribute('role','status');
 async function call(binding,input,button){
  if(disposed||button?.disabled)return;
  if(!binding||typeof binding.reference!=='string'||binding.reference.length>200||binding.automatic===true)throw Error('Explicit closed surface binding required.');
  if(button)button.disabled=true;status.textContent='Working…';
  let uncertain=false;
  try{const reply=await invoke(binding.reference,{...structuredClone(binding.parameters||{}),...input});if(disposed)return;status.textContent=reply?.message||'Complete';if(reply?.surface){const dispose=mountControlSurface(results,reply.surface,{invoke,onChange});cleanups.push(dispose);}else results.replaceChildren(structuredContent(reply));onChange(reply);}
  catch(error){uncertain=error?.dispatchOccurred!==false;if(!disposed)status.textContent=error.message;}
  finally{if(button&&!disposed)button.disabled=Boolean(binding.disabled)||uncertain;}
 }
 function render(node,depth=0){
  if(depth>12||!node||typeof node!=='object')throw Error('Surface nesting exceeds its limit.');
  const box=element('section');box.className='control-surface-node';
  if(node.title)box.append(element('h4',readable(node.title)));
  if(node.kind==='text'){const text=element('p',readable(node.text));if(node.tone==='warning'||node.tone==='alert')text.className='control-surface-alert';box.append(text);}
  else if(node.kind==='table'){
   if(!Array.isArray(node.columns)||!Array.isArray(node.rows)||node.columns.length>50||node.rows.length>10000)throw Error('Invalid bounded table.');
   const table=element('table'),head=element('tr'),body=element('tbody');table.setAttribute('aria-label',node.title||'Values');
   for(const column of node.columns)head.append(element('th',readable(column.label||column.key)));table.append(head,body);
   for(const row of node.rows){const line=element('tr');for(const column of node.columns){const value=row[column.key],cell=element('td',column.format==='number'?number(value):readable(typeof value==='object'?value?.text:value));if(row._disabled)cell.className='control-surface-muted';else if((row._alerts||[]).includes(column.key))cell.className='control-surface-alert';if(row._tooltips?.[column.key])cell.title=readable(row._tooltips[column.key]);line.append(cell);}body.append(line);}box.append(table);
  }else if(node.kind==='form'){
   const form=element('form'),fields=structuredForm(node.inputSchema,node.initial||{}, {disabled:Boolean(node.disabled)}),submit=element('button',node.submitLabel||'Apply');submit.type='submit';submit.disabled=Boolean(node.disabled);
   form.append(fields.element,submit);form.onsubmit=event=>{event.preventDefault();let input;try{input=fields.value();}catch(error){status.textContent=error.message;return;}call(node.binding,input,submit);};box.append(form);
  }else if(node.kind==='button'){
   const button=element('button',node.label||node.title||'Apply');button.type='button';button.disabled=Boolean(node.disabled);button.onclick=()=>call(node.binding,{},button);box.append(button);
  }else if(node.kind==='row'){
   box.className+=' control-surface-row';for(const child of node.children||[])box.append(render(child,depth+1));
  }else if(node.kind==='dialog'){
   const open=element('button',node.label||node.title||'Settings'),dialog=element('dialog'),close=element('button','×');open.type=close.type='button';close.setAttribute('aria-label','Close '+(node.title||'dialog'));dialog.append(close);for(const child of node.children||[])dialog.append(render(child,depth+1));open.onclick=()=>dialog.showModal();close.onclick=()=>dialog.close();box.append(open,dialog);dialogs.push(dialog);
  }else if(node.kind==='tabs'){
   const buttons=element('div'),content=element('div'),panels=[];buttons.setAttribute('role','tablist');buttons.className='control-surface-tabs';
   for(const tab of node.tabs||[]){const button=element('button',readable(tab.label)),panel=element('section');button.type='button';button.setAttribute('role','tab');panel.setAttribute('role','tabpanel');for(const child of tab.children||[])panel.append(render(child,depth+1));buttons.append(button);content.append(panel);panels.push({button,panel,key:tab.key});button.onclick=()=>select(tab.key);}
   function select(key){for(const item of panels){const selected=item.key===key;item.button.setAttribute('aria-selected',String(selected));item.panel.hidden=!selected;}}
   select(node.selected||panels[0]?.key);box.append(buttons,content);
  }else if(node.kind==='chart')box.append(renderPlot(node));
  else throw Error('Unsupported neutral surface primitive.');
  return box;
 }
 function renderPlot(model){
  if(!Array.isArray(model.rows)||model.rows.length>10000||!Array.isArray(model.series)||model.series.length>24)throw Error('Bounded chart rows and series required.');
  const figure=element('figure'),svg=document.createElementNS('http://www.w3.org/2000/svg','svg'),hint=element('p'),choices=element('div');svg.setAttribute('viewBox','0 0 900 360');svg.setAttribute('role','img');svg.setAttribute('aria-label',model.title||'Observation chart');svg.setAttribute('tabindex','0');
  const selected=new Set(model.series.filter(row=>row.selected!==false).map(row=>row.key));let point=-1;
  for(const series of model.series){const label=element('label'),input=element('input');input.type='checkbox';input.checked=selected.has(series.key);input.onchange=()=>{input.checked?selected.add(series.key):selected.delete(series.key);draw();};label.append(input,element('span',readable(series.label||series.key)));choices.append(label);}
  const append=(tag,attrs={},text)=>{const node=document.createElementNS('http://www.w3.org/2000/svg',tag);for(const [key,value] of Object.entries(attrs))node.setAttribute(key,String(value));if(text!==undefined)node.textContent=String(text);svg.append(node);return node;};
  const rows=model.rows,left=70,right=830,top=25,bottom=305;
  const timeline=rows.map((row,index)=>model.scale==='time'?Date.parse(row[model.x]):index),min=Math.min(...timeline),max=Math.max(...timeline),x=index=>left+(timeline[index]-min)/(max-min||1)*(right-left);
  function draw(){svg.replaceChildren();let axis=0;for(const series of model.series)if(selected.has(series.key)){
   const values=rows.map(row=>row[series.key]).filter(Number.isFinite);if(!values.length)continue;
   let low=Math.min(...values),high=Math.max(...values);const pad=(high-low||Math.abs(low)*.02||1)*.06;low-=pad;high+=pad;
   const y=value=>bottom-(value-low)/(high-low)*(bottom-top),colors=['#446fa8','#ae5959','#6c8b58','#967348'],color=colors[axis%colors.length];
   for(let index=0;index<5;index++){const value=low+(high-low)*index/4;append('text',{x:axis?right+4:left-6,y:y(value)+4,'text-anchor':axis?'start':'end','font-size':10,fill:color},number(value));}
   append('text',{x:axis?right:left,y:top-8,'font-size':10,fill:color},readable(series.label||series.key));
   let path='',segment=false;rows.forEach((row,index)=>{const value=row[series.key];if(!Number.isFinite(value)){segment=false;return;}path+=(segment?' L':' M')+x(index)+' '+y(value);segment=true;});append('path',{d:path,fill:'none',stroke:color,'stroke-width':1.6});
   if(point>=0&&Number.isFinite(rows[point]?.[series.key]))append('line',{x1:left,x2:right,y1:y(rows[point][series.key]),y2:y(rows[point][series.key]),stroke:color,'stroke-dasharray':'3 4'});axis++;
  }
  for(let index=0;index<Math.min(6,rows.length);index++){const at=Math.round(index*(rows.length-1)/Math.max(1,Math.min(6,rows.length)-1));append('text',{x:x(at),y:bottom+22,'text-anchor':'middle','font-size':10},String(rows[at][model.x]||'').replace('T',' ').slice(0,19));}
  if(point>=0){append('line',{x1:x(point),x2:x(point),y1:top,y2:bottom,stroke:'#657982','stroke-dasharray':'4 4'});hint.textContent=String(rows[point][model.x])+' · '+model.series.filter(series=>selected.has(series.key)).map(series=>readable(series.label||series.key)+': '+number(rows[point][series.key])).join(' · ');}
  }
  svg.onpointermove=event=>{if(!rows.length)return;const bounds=svg.getBoundingClientRect(),at=(event.clientX-bounds.left)/bounds.width*900;point=timeline.reduce((best,_,index)=>Math.abs(x(index)-at)<Math.abs(x(best)-at)?index:best,0);draw();};svg.onpointerleave=()=>{point=-1;hint.textContent='';draw();};svg.onkeydown=event=>{if(!['ArrowLeft','ArrowRight'].includes(event.key))return;event.preventDefault();point=Math.max(0,Math.min(rows.length-1,point+(event.key==='ArrowRight'?1:-1)));draw();};
  figure.append(choices,svg,hint);draw();return figure;
 }
 root.replaceChildren(status,...surface.nodes.map(node=>render(node)),results);
 return ()=>{disposed=true;for(const close of cleanups)close();for(const dialog of dialogs)if(dialog.open)dialog.close();root.replaceChildren();};
}
