/** Versioned generic host. Application content still comes from its publisher. */
import {AgentViewNode} from './node.mjs';
import {CompositionViewChannel} from './composition-channel.mjs';

export function mountAgentView(root,reference,context,{request,presentation,compositionContext,onPresentation,hostRelease,runtimeDigest,startTimeoutMs}){
 Object.assign(root.style,{display:'flex',flexDirection:'column',flex:'1',minHeight:'0',overflow:'hidden'});
 const target=root.querySelector('[data-agent-view-host]');
 Object.assign(target.style,{height:'100%',flex:'1',minHeight:'0'});
 const status=document.createElement('p');status.setAttribute('role','status');target.append(status);
 let closed=false;
 // When the view's Phema link can't be reconnected, offer one remount of the same reference.
 const stale=document.createElement('div'),note=document.createElement('span'),reload=document.createElement('button');
 stale.setAttribute('role','status');
 Object.assign(stale.style,{display:'none',alignItems:'center',gap:'8px',padding:'4px 8px',fontSize:'12px',borderBottom:'1px solid currentColor'});
 note.textContent='This view lost its Phema connection.';reload.type='button';reload.textContent='Reload view';
 Object.assign(reload.style,{font:'inherit',color:'inherit',background:'transparent',border:'1px solid currentColor',borderRadius:'4px',padding:'2px 8px',cursor:'pointer'});
 stale.append(note,reload);target.parentNode.insertBefore(stale,target);
 const node=new AgentViewNode(target,{request,context:presentation(root,context),hostRelease,runtimeDigest,onPresentation,startTimeoutMs,
  ...(compositionContext?{compositionChannel:value=>new CompositionViewChannel({...compositionContext(root,value),reference:value,onStale:()=>{if(!closed)stale.style.display='flex';}})}:{}),
  // Mounting replaces the target's children; bring the status back when it has something to say.
  onStatus:value=>{status.textContent=value;status.hidden=value==='Mounted';if(!status.hidden&&!status.isConnected)target.prepend(status);}});
 const mount=()=>node.reload(reference).catch(error=>{if(closed)return;target.replaceChildren(status);status.hidden=false;status.textContent=error.message;});
 reload.addEventListener('click',()=>{stale.style.display='none';mount();});
 // The container announces a presentation change (such as its theme); the open view follows without remounting.
 const follow=()=>{if(!closed)node.updateContext(presentation(root,context));};
 addEventListener('phemacast:presentation-changed',follow);
 mount();
 return ()=>{closed=true;removeEventListener('phemacast:presentation-changed',follow);stale.remove();node.dispose();};
}
