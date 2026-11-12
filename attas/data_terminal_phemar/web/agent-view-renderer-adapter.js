/** Stable container bootstrap; fresh approved host selection for each mount. */
import {loadApprovedHost} from './agent-view-host-loader.js';
import {agentViewPresentation} from './agent-view-presentation.js';
export function installAgentViewRenderer({register,request,presentation,compositionContext}){
 register({id:'phemacast:agent-view',version:'1.0.0',render:()=>'<section data-agent-view-host style="height:100%;min-height:0"></section>',mount(root,reference,context){
  let closed=false,dispose=null;const caption=agentViewPresentation(root);
  const status=document.createElement('p');status.setAttribute('role','status');const locale=presentation(root,context).locale;status.textContent=({'zh-Hant':'載入中…','zh-TW':'載入中…','zh-Hans':'加载中…','zh-CN':'加载中…',ja:'読み込み中…'}[locale]||'Loading…');root.querySelector('[data-agent-view-host]').append(status);
  loadApprovedHost(request).then(({reference:host,module})=>{
   if(closed||!root.isConnected)return;
   dispose=module.mountAgentView(root,reference,context,{request,presentation,compositionContext,hostRelease:host.release,runtimeDigest:host.runtime.digest,onPresentation:caption.update});
  }).catch(error=>{if(closed||!root.isConnected)return;status.textContent=error.message;status.hidden=false;root.querySelector('[data-agent-view-host]').replaceChildren(status);});
  return ()=>{closed=true;caption.dispose();dispose?.();};
 }});
}
