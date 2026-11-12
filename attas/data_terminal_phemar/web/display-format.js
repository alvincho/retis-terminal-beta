/** Terminal settings → Display format: one style for every Phema's dates and numbers, in the Terminal's time zone.
 * Generic presentation only; a Phema declares each field's kind (value-format.js). */
import {ui as uiHTML} from './i18n.js';
import {displayFormat,createFormatter} from './value-format.js';
import {displayTimezone} from './time-display.js';
let current=displayFormat();
export function applyDisplayFormat(value){current=displayFormat(value);}
export const currentDisplayFormat=()=>({...current});
/** (value, field format) → text, for the viewer's language. */
export const valueFormatter=locale=>createFormatter({format:current,locale,timeZone:displayTimezone()});
const selected=(value,current)=>value===current?'selected':'';
export function displayFormatSettingsHTML(value){
 const f=displayFormat(value);
 return uiHTML`<h3>Display format</h3><form data-connected-form="display-format" class="display-format-settings"><div class="market-color-fields"><label>Date and time<select name="dateTime"><option value="compact" ${selected('compact',f.dateTime)}>Compact</option><option value="full" ${selected('full',f.dateTime)}>Full</option></select></label><label>Clock<select name="clock"><option value="24h" ${selected('24h',f.clock)}>24-hour</option><option value="12h" ${selected('12h',f.clock)}>12-hour</option></select></label><label>Decimals<select name="decimals">${[0,1,2,3,4].map(n=>`<option value="${n}" ${selected(n,f.decimals)}>${n}</option>`).join('')}</select></label><label>Large numbers<select name="largeNumbers"><option value="full" ${selected('full',f.largeNumbers)}>Full</option><option value="abbreviated" ${selected('abbreviated',f.largeNumbers)}>Abbreviated</option></select></label></div><label><input type="checkbox" name="signed" ${f.signed?'checked':''}> Show + on gains</label><details><summary>How to Use</summary><p>Applies to every Phema in this Terminal: tables, details and agent views, in your time zone. Each field keeps its kind (date, amount, percent, count); a Phema can set a field’s decimals or date style.</p></details><button class="button" type="submit">Save display format</button></form>`;
}
export const displayFormatValues=values=>({dateTime:values.dateTime,clock:values.clock,decimals:Number(values.decimals),largeNumbers:values.largeNumbers,signed:values.signed==='on'});
