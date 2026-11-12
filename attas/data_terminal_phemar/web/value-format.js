/** One display format for every Phema value: the viewer's global style (Terminal settings) applied to each
 * field's kind. A Phema declares a field's kind and may override decimals, sign or date style; undeclared values
 * are read by their shape. Presentation only: values, units and requests never change. */
// percent is in percentage points (1 = 1%); ratio is a fraction shown as a percentage (0.1 = 10%).
export const FORMAT_KINDS=['datetime','date','time','amount','percent','ratio','count','number','text'];
// Large numbers read abbreviated (3.81T, 3.81兆) unless the viewer chose Full.
export const DEFAULT_DISPLAY_FORMAT=Object.freeze({dateTime:'compact',clock:'24h',decimals:2,largeNumbers:'abbreviated',signed:true});
const choice=(value,allowed,fallback)=>allowed.includes(value)?value:fallback;
const digits=(value,max,fallback)=>Number.isInteger(value)&&value>=0&&value<=max?value:fallback;
/** The viewer's global style; unknown or invalid settings fall back to the defaults. */
export function displayFormat(value){
 const v=value&&typeof value==='object'?value:{},d=DEFAULT_DISPLAY_FORMAT;
 return {dateTime:choice(v.dateTime,['compact','full'],d.dateTime),clock:choice(v.clock,['24h','12h'],d.clock),
  decimals:digits(v.decimals,4,d.decimals),largeNumbers:choice(v.largeNumbers,['full','abbreviated'],d.largeNumbers),
  signed:typeof v.signed==='boolean'?v.signed:d.signed};
}
/** A field's declared format: its kind plus optional decimals, sign and date style. */
export function fieldFormat(value){
 if(typeof value==='string')value={kind:value};
 if(!value||typeof value!=='object'||!FORMAT_KINDS.includes(value.kind))return null;
 const result={kind:value.kind};
 if(Number.isInteger(value.decimals)&&value.decimals>=0&&value.decimals<=8)result.decimals=value.decimals;
 if(typeof value.signed==='boolean')result.signed=value.signed;
 if(['compact','full'].includes(value.dateTime))result.dateTime=value.dateTime;
 return result;
}
const DATE_TIME=/^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}/,DATE=/^\d{4}-\d{2}-\d{2}$/;
export function inferredKind(value){
 if(value instanceof Date)return 'datetime';
 if(typeof value==='number')return Number.isInteger(value)?'count':'number';
 if(typeof value==='string')return DATE.test(value)?'date':DATE_TIME.test(value)?'datetime':'text';
 return 'text';
}
/** A formatter for one viewer: (value, declared field format) → text. */
export function createFormatter({format,locale='en',timeZone}={}){
 const style=displayFormat(format),cache=new Map(),zone=validZone(timeZone);
 const intl=(type,options)=>{const key=type+JSON.stringify(options);if(!cache.has(key))cache.set(key,type==='n'?new Intl.NumberFormat(locale,options):new Intl.DateTimeFormat(locale,options));return cache.get(key);};
 const parts=(d,options)=>Object.fromEntries(intl('d',options).formatToParts(d).filter(p=>p.type!=='literal').map(p=>[p.type,p.value]));
 const wall=(d,tz)=>parts(d,{timeZone:tz,calendar:'iso8601',numberingSystem:'latn',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'});
 const clock=(seconds,tz)=>({timeZone:tz,hour:'2-digit',minute:'2-digit',...(seconds?{second:'2-digit'}:{}),...(style.clock==='12h'?{hour12:true}:{hourCycle:'h23'})});
 function dateText(d,tz,full,p){
  if(full)return `${p.year}-${p.month}-${p.day}`;
  // Compact dates always carry the year, so dates from different panels read alike.
  return intl('d',{timeZone:tz,year:'numeric',month:'numeric',day:'numeric'}).format(d);
 }
 function dateTime(value,kind,full){
  // A calendar date reads as written: a daily bar stamped at New York midnight is that day everywhere.
  const calendar=typeof value==='string'&&(DATE.test(value)||kind==='date'&&DATE_TIME.test(value));
  const d=calendar?new Date(value.slice(0,10)+'T00:00:00Z'):new Date(typeof value==='number'?(Math.abs(value)<1e12?value*1000:value):value);
  if(Number.isNaN(d.getTime()))return String(value);
  const tz=calendar?'UTC':zone,p=wall(d,tz),midnight=p.hour==='00'&&p.minute==='00'&&p.second==='00';
  if(kind==='time')return intl('d',clock(full,tz)).format(d);
  const day=dateText(d,tz,full,p);
  if(kind==='date'||calendar||(!full&&midnight))return day;
  return day+' '+intl('d',clock(full,tz)).format(d);
 }
 // Rounded first, so a value that rounds to zero reads 0 (never -0 or +0) and the sign follows what is shown.
 function number(value,minimum,maximum,field={}){
  const scale=10**maximum,rounded=Math.round(value*scale)/scale||0;
  const text=style.largeNumbers==='abbreviated'&&Math.abs(rounded)>=1e6?intl('n',{notation:'compact',maximumFractionDigits:2}).format(rounded)
   :intl('n',{minimumFractionDigits:minimum,maximumFractionDigits:maximum}).format(rounded);
  return field.signed&&style.signed&&rounded>0?'+'+text:text;
 }
 return function formatValue(value,declared){
  if(value==null||value==='')return '—';
  const field=fieldFormat(declared)||{},kind=field.kind||inferredKind(value);
  if(value instanceof Date)value=value.getTime();
  if(['datetime','date','time'].includes(kind))return typeof value==='string'||typeof value==='number'?dateTime(value,kind,(field.dateTime||style.dateTime)==='full'):String(value);
  if(kind==='text'||typeof value!=='number')return typeof value==='object'?JSON.stringify(value):String(value);
  if(!Number.isFinite(value))return '—';
  if(kind==='count')return number(value,0,field.decimals??6,field);
  if(kind==='percent'||kind==='ratio'){const decimals=field.decimals??2;return number(kind==='ratio'?value*100:value,Math.min(1,decimals),decimals,field)+'%';}
  const size=Math.abs(value);
  // A measured value below 1 keeps its leading digits (0.01235), not 0.01.
  if(kind==='number'&&size>0&&size<1&&field.decimals==null)return number(value,2,Math.min(10,Math.ceil(-Math.log10(size))+3),field);
  const decimals=field.decimals??style.decimals;
  return number(value,decimals,decimals,field);
 };
}
function validZone(value){
 if(typeof value==='string'&&value.length<=100){try{new Intl.DateTimeFormat('en',{timeZone:value});return value;}catch{}}
 return Intl.DateTimeFormat().resolvedOptions().timeZone||'UTC';
}
