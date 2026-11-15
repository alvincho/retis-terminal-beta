/** Opt-in generic Phemacast script host, with no application adapters. */
import {installAgentViewRenderer} from './agent-view-renderer-adapter.js';
import {registerRenderer} from './composition-renderers.js';
import {api,runtime,compositionViewInstance,setCompositionViewParameters} from './connected.js';
import {currentDisplayFormat} from './display-format.js';
import {resolvedLocale} from './i18n.js';
const PALETTE=['accent','background','surface','text'];
function hostColors(){const values=(document.documentElement.dataset.themePalette||'').split(' ');
 return values.length===PALETTE.length&&values.every(value=>/^#[0-9a-f]{6}$/i.test(value))?Object.fromEntries(PALETTE.map((name,index)=>['host-'+name,values[index]])):{};}
function timezone(){const selected=runtime.settings?.preferences?.displayTimezone;try{if(typeof selected==='string'){new Intl.DateTimeFormat('en',{timeZone:selected});return selected;}}catch{}return Intl.DateTimeFormat().resolvedOptions().timeZone;}
installAgentViewRenderer({register:registerRenderer,request:(path,body)=>api(path,{method:'POST',headers:{'X-Phemacast-View-Request':'1'},body}),
 compositionContext:(root,reference)=>({request:api,prefix:compositionViewInstance(root).prefix,instance:()=>compositionViewInstance(root).instance,
  parameters:values=>setCompositionViewParameters(root,values)}),
 presentation:(root,context)=>({locale:root.isConnected?resolvedLocale(root):context.locale||resolvedLocale(root),timezone:timezone(),theme:document.documentElement.dataset.theme||'dark',colors:hostColors(),density:'compact',format:currentDisplayFormat()})});
// Open agent views follow the Terminal's theme and language, and a browser's own language, without reopening; the
// generic host listens for this event and asks `presentation` again. Views get the base (dark or light) and the main
// colors, which a view may use as --host-accent, --host-background, --host-surface and --host-text (theme.js writes
// them to data-theme-palette). Applying the same value again announces nothing.
const announce=records=>{if(records.some(record=>record.oldValue!==record.target.getAttribute(record.attributeName)))dispatchEvent(new CustomEvent('phemacast:presentation-changed'));};
new MutationObserver(announce).observe(document.documentElement,{attributes:true,attributeFilter:['data-theme','data-theme-palette','lang'],attributeOldValue:true});
new MutationObserver(announce).observe(document.documentElement,{subtree:true,attributes:true,attributeFilter:['data-ui-locale'],attributeOldValue:true});
