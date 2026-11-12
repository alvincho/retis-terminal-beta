import {ui as uiHTML} from './i18n.js';
/** Presentation-only market preferences; no connection or subscription changes. */
export const MARKET_DEFAULTS=Object.freeze({upColor:'#f07178',downColor:'#4ec99b',colorChanges:true,flashEnabled:true,flashColor:'#add8e6',flashDurationMs:300});
export function marketAppearance(value={}){
 const result={...MARKET_DEFAULTS};
 for(const key of ['upColor','downColor','flashColor'])if(/^#[0-9a-f]{6}$/i.test(value?.[key]))result[key]=value[key];
 for(const key of ['colorChanges','flashEnabled'])if(typeof value?.[key]==='boolean')result[key]=value[key];
 if(Number.isInteger(value?.flashDurationMs)&&value.flashDurationMs>=50&&value.flashDurationMs<=5000)result.flashDurationMs=value.flashDurationMs;
 return result;
}
let current=marketAppearance();
export function applyMarketAppearance(value){
 current=marketAppearance(value);
 const root=document.documentElement;
 root.style.setProperty('--market-up',current.upColor);root.style.setProperty('--market-down',current.downColor);
 root.dataset.marketColors=String(current.colorChanges);
 if(!current.flashEnabled)for(const animation of root.getAnimations({subtree:true}))if(animation.id==='market-value-flash')animation.cancel();
}
export function valueDirection(field,value,row={}){const direction=row?.display?.[field]?.direction;return ['up','down'].includes(direction)?direction:'';}

export function flashUsesBlackText(color){
 const channels=color.match(/^rgba?\(([^)]+)\)$/)?.[1].match(/[\d.]+/g)?.slice(0,3).map(Number);
 return !!channels&&channels.length===3&&Math.min(...channels)>=185&&Math.max(...channels)-Math.min(...channels)<=45;
}
export function captureValueState(panel){return new Map([...panel.querySelectorAll('[data-value-key]')].map(n=>{const animation=n.getAnimations().find(a=>a.id==='market-value-flash'&&a.playState==='running');return [n.dataset.valueKey,{value:n.dataset.value,time:animation?.currentTime}];}));}
export function flashChangedValues(prior,newPanel){
 if(!current.flashEnabled)return;
 for(const cell of newPanel.querySelectorAll('[data-value-key]')){
  const old=prior.get(cell.dataset.valueKey);if(!old)continue;
  const changed=cell.dataset.value!==old.value;
  if(!changed&&old.time==null)continue;
  // Only the background and white/near-white foreground change temporarily.
  // Direction colors retain their normal appearance; the animation releases
  // both properties at completion, restoring the current theme's text color.
  const frame={backgroundColor:current.flashColor};
  if(flashUsesBlackText(getComputedStyle(cell).color))frame.color='#000000';
  const animation=cell.animate([frame,frame],{duration:current.flashDurationMs});
  animation.id='market-value-flash';
  if(!changed)animation.currentTime=old.time;
 }
}
export function marketSettingsHTML(value){
 const p=marketAppearance(value);
 return uiHTML`<h3>Market colors & value updates</h3><form data-connected-form="market-appearance"><p>Applies to every Data Browser in this Terminal account.</p><label><input type="checkbox" name="colorChanges" ${p.colorChanges?'checked':''}> Color price and change values</label><div class="market-color-fields"><label>Up color<input type="color" name="upColor" aria-label="Up color" value="${p.upColor}"></label><label>Down color<input type="color" name="downColor" aria-label="Down color" value="${p.downColor}"></label></div><label><input type="checkbox" name="flashEnabled" ${p.flashEnabled?'checked':''}> Highlight changed values</label><div class="market-color-fields"><label>Update highlight color<input type="color" name="flashColor" aria-label="Update highlight color" value="${p.flashColor}"></label><label>Highlight duration (seconds)<input type="number" name="flashDuration" aria-label="Highlight duration in seconds" min="0.05" max="5" step="0.05" value="${p.flashDurationMs/1000}"></label></div><button class="button" type="submit">Save market appearance</button><button class="button subtle" type="button" data-market-defaults>Reset to red / green defaults</button><p role="status" data-market-feedback></p></form>`;
}
