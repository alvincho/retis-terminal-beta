/** Opt-in generic Phemacast script host, with no application adapters. */
import {installAgentViewRenderer} from './agent-view-renderer-adapter.js';
import {registerRenderer} from './composition-renderers.js';
import {api,runtime,compositionViewInstance,setCompositionViewParameters} from './connected.js';
import {currentDisplayFormat} from './display-format.js';
function timezone(){const selected=runtime.settings?.preferences?.displayTimezone;try{if(typeof selected==='string'){new Intl.DateTimeFormat('en',{timeZone:selected});return selected;}}catch{}return Intl.DateTimeFormat().resolvedOptions().timeZone;}
installAgentViewRenderer({register:registerRenderer,request:(path,body)=>api(path,{method:'POST',headers:{'X-Phemacast-View-Request':'1'},body}),
 compositionContext:(root,reference)=>({request:api,prefix:compositionViewInstance(root).prefix,instance:()=>compositionViewInstance(root).instance,
  parameters:values=>setCompositionViewParameters(root,values)}),
 presentation:(root,context)=>({locale:context.locale||root.closest('[data-ui-locale]')?.dataset.uiLocale||document.documentElement.lang||'en',timezone:timezone(),theme:document.documentElement.dataset.theme||'dark',density:'compact',format:currentDisplayFormat()})});
// Open agent views follow the Terminal's theme without reopening; the generic host listens for this event.
new MutationObserver(records=>{if(records.some(record=>record.oldValue!==document.documentElement.dataset.theme))dispatchEvent(new CustomEvent('phemacast:presentation-changed'));})
 .observe(document.documentElement,{attributes:true,attributeFilter:['data-theme'],attributeOldValue:true});
