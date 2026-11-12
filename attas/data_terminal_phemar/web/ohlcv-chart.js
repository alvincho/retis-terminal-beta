import {ui as uiHTML} from './i18n.js';
import {displayDateTime} from './time-display.js';
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const numeric=value=>typeof value==='number'&&Number.isFinite(value);
const fmt=value=>numeric(value)?value.toLocaleString(undefined,{maximumFractionDigits:4}):'—';
export function candleBars(rows){
 return rows.filter(r=>r.timestamp&&['open','high','low','close'].every(k=>numeric(r[k]))&&r.low<=Math.min(r.open,r.close)&&r.high>=Math.max(r.open,r.close)).sort((a,b)=>String(a.timestamp).localeCompare(String(b.timestamp)));
}
export function ohlcvChart(rows,ticker){
 const bars=candleBars(rows),missing=rows.length-bars.length;
 if(!bars.length)return uiHTML`<p class="panel-note">OHLCV unavailable: this source has not supplied complete open, high, low and close observations.</p>`;
 const lo=Math.min(...bars.map(r=>r.low)),hi=Math.max(...bars.map(r=>r.high)),span=hi-lo||Math.max(1,Math.abs(hi)*.01);
 const y=v=>15+(hi-v)/span*133,step=574/bars.length,width=Math.max(.5,Math.min(12,step*.65));
 const volumeMax=Math.max(1,...bars.map(r=>numeric(r.volume)&&r.volume>=0?r.volume:0));
 const last=bars.at(-1),latest=`${displayDateTime(last.timestamp)} · O ${fmt(last.open)} H ${fmt(last.high)} L ${fmt(last.low)} C ${fmt(last.close)} V ${fmt(last.volume)}`;
 const shapes=bars.map((r,i)=>{const x=8+step*(i+.5),up=r.close>=r.open,top=Math.min(y(r.open),y(r.close)),height=Math.max(1,Math.abs(y(r.open)-y(r.close))),vol=numeric(r.volume)&&r.volume>=0?r.volume/volumeMax*40:null;const label=`${ticker} ${displayDateTime(r.timestamp)}: open ${fmt(r.open)}, high ${fmt(r.high)}, low ${fmt(r.low)}, close ${fmt(r.close)}, volume ${fmt(r.volume)}`;return uiHTML`<g class="candle ${up?'candle-up':'candle-down'}" tabindex="0" role="img" aria-label="${esc(label)}"><title>${esc(label)}</title><line x1="${x}" y1="${y(r.high)}" x2="${x}" y2="${y(r.low)}"/><rect x="${x-width/2}" y="${top}" width="${width}" height="${height}"/>${vol===null?'':uiHTML`<rect class="volume-bar" x="${x-width/2}" y="${204-vol}" width="${width}" height="${vol}"/>`}</g>`;}).join('');
 return uiHTML`<div class="ohlcv-summary" data-value-key="${esc('ohlcv:'+ticker)}" data-value="${esc(latest)}" title="${esc(latest)}">${esc(latest)}</div><svg class="price-chart ohlcv-chart" viewBox="0 0 640 224" preserveAspectRatio="none" role="group" aria-label="${esc(ticker)} OHLCV candlestick and volume chart">${[hi,(hi+lo)/2,lo].map(v=>uiHTML`<line class="ohlcv-grid" x1="4" y1="${y(v)}" x2="586" y2="${y(v)}"/><text class="ohlcv-axis" x="591" y="${y(v)+3}">${esc(fmt(v))}</text>`).join('')}<line class="ohlcv-grid" x1="4" y1="158" x2="586" y2="158"/><text class="ohlcv-axis" x="591" y="178">Volume</text>${shapes}<text class="ohlcv-axis" x="4" y="220">${esc(displayDateTime(bars[0].timestamp))}</text><text class="ohlcv-axis" text-anchor="end" x="582" y="220">${esc(displayDateTime(last.timestamp))}</text></svg>${missing?uiHTML`<small class="panel-note">${missing} incomplete OHLC observations omitted.</small>`:''}`;
}
