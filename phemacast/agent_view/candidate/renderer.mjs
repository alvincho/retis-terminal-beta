/** Versioned generic host. Application content still comes from its publisher. */
import {AgentViewNode} from './node.mjs';
import {CompositionViewChannel} from './composition-channel.mjs';
// The host's own words, in the viewer's language (walk of 2026-11-18: a Chinese Terminal showed them in English).
const WORDS={lost:{en:'This view lost its Phema connection.','zh-Hant':'此畫面與 Phema 的連線已中斷。','zh-Hans':'此画面与 Phema 的连接已中断。',
  ja:'このビューは Phema との接続を失いました。',es:'Esta vista perdió su conexión con el Phema.',fr:'Cette vue a perdu sa connexion au Phema.'},
 reload:{en:'Reload view','zh-Hant':'重新載入畫面','zh-Hans':'重新载入画面',ja:'ビューを再読み込み',es:'Recargar vista',fr:'Recharger la vue'},
 // The channel's reasons a view could not start, shown under the banner or in place of the view.
 'The selected Phema has changed or closed. Reload this view.':{'zh-Hant':'所選的 Phema 已變更或關閉。請重新載入此畫面。','zh-Hans':'所选的 Phema 已更改或关闭。请重新载入此画面。'},
 'The Phema did not finish initializing. Reload this view.':{'zh-Hant':'Phema 尚未完成初始化。請重新載入此畫面。','zh-Hans':'Phema 尚未完成初始化。请重新载入此画面。'},
 'The Phema could not initialize.':{'zh-Hant':'Phema 無法初始化。','zh-Hans':'Phema 无法初始化。'}};
const say=(word,locale)=>{const code=String(locale||'en'),words=Object.hasOwn(WORDS,word)?WORDS[word]:null;if(!words)return word;
 return words[/^zh[-_](hant|tw|hk|mo)/i.test(code)?'zh-Hant':/^zh/i.test(code)?'zh-Hans':code.slice(0,2).toLowerCase()]||words.en||word;};

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
 const label=()=>{const locale=presentation(root,context).locale;note.textContent=say('lost',locale);reload.textContent=say('reload',locale);};
 label();reload.type='button';
 Object.assign(reload.style,{font:'inherit',color:'inherit',background:'transparent',border:'1px solid currentColor',borderRadius:'4px',padding:'2px 8px',cursor:'pointer'});
 stale.append(note,reload);target.parentNode.insertBefore(stale,target);
 const node=new AgentViewNode(target,{request,context:presentation(root,context),hostRelease,runtimeDigest,onPresentation,startTimeoutMs,
  ...(compositionContext?{compositionChannel:value=>new CompositionViewChannel({...compositionContext(root,value),reference:value,onStale:()=>{if(!closed)stale.style.display='flex';}})}:{}),
  // Mounting replaces the target's children; bring the status back when it has something to say.
  onStatus:value=>{status.textContent=value;status.hidden=value==='Mounted';if(!status.hidden&&!status.isConnected)target.prepend(status);}});
 const mount=()=>node.reload(reference).catch(error=>{if(closed)return;target.replaceChildren(status);status.hidden=false;status.textContent=say(error.message,presentation(root,context).locale);});
 reload.addEventListener('click',()=>{stale.style.display='none';mount();});
 // The container announces a presentation change (such as its theme); the open view follows without remounting.
 const follow=()=>{if(!closed){node.updateContext(presentation(root,context));label();}};
 addEventListener('phemacast:presentation-changed',follow);
 mount();
 return ()=>{closed=true;removeEventListener('phemacast:presentation-changed',follow);stale.remove();node.dispose();};
}
