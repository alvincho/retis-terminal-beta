import {uiBundle} from './phema-i18n.js';
import {ui as uiHTML,label,message,attribute} from './i18n.js';
import {panelHTML,esc,browserState} from './connected.js';
import {layoutStyle,templateStyle,layoutScope} from './layout-presentation.js';
export function renderBrowserPanels(b,design=b.design,path=''){
 const layout=design.layout,view=layout.tabbedView;
 if(!view)return layout.children.map(p=>renderPanel(p,b,layout,path)).join('');
 const tabKey=b.id+(path?'::'+path:''),active=view.tabs.find(t=>t.id===b.layoutTabs?.[path||'root'])||view.tabs[0],root=layoutScope(layout),scope=layoutScope(layout,active.id),prefix='layout-tab-'+tabKey;
 return root.children.filter(n=>n.id!==view.id).map(p=>renderPanel(p,b,root,path)).join('')+uiHTML`<section data-panel-id="${esc(view.id)}" class="layout-tabs" style="${esc(templateStyle(view,root))}"><nav role="tablist" ${attribute('aria-label','{0} views',message(design.title))}>${view.tabs.map(t=>uiHTML`<button type="button" role="tab" id="${esc(prefix+'-'+t.id)}" data-layout-tab="${esc(t.id)}" data-tab-browser="${esc(tabKey)}" aria-selected="${t.id===active.id}" tabindex="${t.id===active.id?0:-1}" aria-controls="${esc(prefix+'-content')}">${label(t.title)}</button>`).join('')}</nav><div data-tab-content role="tabpanel" id="${esc(prefix+'-content')}" aria-labelledby="${esc(prefix+'-'+active.id)}" class="layout-tab-content" style="${esc(layoutStyle(scope))}">${scope.children.length?scope.children.map(p=>renderPanel(p,b,scope,path)).join(''):uiHTML`<p data-panel-id="empty" class="panel-note">No templates configured for this tab. Add them in App Consultant.</p>`}</div></section>`;
}
function renderPanel(p,b,layout=b.design.layout,path='') {
  if(p.template==='phema'){
    const childPath=[path,p.componentId].filter(Boolean).join('/'),child=browserState(b).nestedLayouts?.[childPath];
    return uiHTML`<section data-panel-id="${esc(p.id)}" class="template-panel nested-phema" style="${esc(templateStyle(p,layout))}"><div class="panel-heading"><h3>${label(p.title)}</h3><span class="muted">Phema</span></div><div class="nested-phema-layout" data-phema-ui="${esc(JSON.stringify(uiBundle(child)||{}))}" style="${esc(child?layoutStyle(child.design.layout):'')}">${child?renderBrowserPanels(b,child.design,childPath):uiHTML`<p class="panel-note">Loading</p>`}</div></section>`;
  }
  return panelHTML({...p,type:p.type==='phemar'?'discussion':p.template},b,templateStyle(p,layout));
}

export function installLayoutTabs(onChange,findBrowser){
document.addEventListener('click',event=>{const button=event.target.closest('[data-layout-tab]');if(!button)return;const [id,path]=button.dataset.tabBrowser.split('::'),browser=findBrowser?.(id);if(!browser)return;browser.layoutTabs={...browser.layoutTabs,[path||'root']:button.dataset.layoutTab};onChange();});
document.addEventListener('keydown',event=>{const button=event.target.closest('[data-layout-tab]');if(!button||!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;event.preventDefault();const buttons=[...button.parentElement.querySelectorAll('[data-layout-tab]')],index=buttons.indexOf(button),next=buttons[event.key==='Home'?0:event.key==='End'?buttons.length-1:(index+(event.key==='ArrowRight'?1:-1)+buttons.length)%buttons.length];next.click();document.getElementById(next.id)?.focus();});

}
