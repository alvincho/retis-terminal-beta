"""Install the Browser reload control into a private captured UI release only."""
import json

MESSAGES={
    'Reload Phema':{'zh-Hant':'重新載入 Phema','zh-Hans':'重新加载 Phema','ja':'Phema を再読み込み','es':'Recargar Phema','fr':'Recharger le Phema'},
    'Finish editing before reloading this Phema.':{'zh-Hant':'請先完成編輯，再重新載入此 Phema。','zh-Hans':'请先完成编辑，再重新加载此 Phema。','ja':'編集を完了してから Phema を再読み込みしてください。','es':'Termina de editar antes de recargar este Phema.','fr':'Terminez la modification avant de recharger ce Phema.'},
    'Review the layout update before reloading this Phema.':{'zh-Hant':'請先檢查版面更新，再重新載入此 Phema。','zh-Hans':'请先检查布局更新，再重新加载此 Phema。','ja':'レイアウトの更新を確認してから再読み込みしてください。','es':'Revisa la actualización del diseño antes de recargar este Phema.','fr':'Vérifiez la mise à jour de la disposition avant de recharger ce Phema.'},
}

CONNECTED='''
export async function reloadBrowserPhema(b,{readLayout}={}) {
 if(!runtime.user)return {guest:true};
 return reloadPhemaOnly(b,{readLayout,request:(suffix='',options={})=>requestBrowserInstance(b,suffix,options),
  isCurrent:()=>latest.includes(b),entry:()=>({...b.customLayout,design:browserDesign(b.design,b.instrumentOverrides)}),
  accept:remote=>{const node=nodes.get(b.id);if(!node)return;node.state=remote;node.events?.accept(remote);
   const design=browserDesign(b.design,b.instrumentOverrides);node.designSignature=JSON.stringify(design);
   node.signature=JSON.stringify([design,b.customLayout?.composition,b.instrumentOverrides,b.parameterValues,b.layoutTabs]);
   node.range=b.range;rendering();}});
}
'''

MAIN='''  if(action==='reload-phema'&&b){
   el.disabled=true;el.setAttribute('aria-busy','true');
   try{await refreshPhemaFrame(document,b,{read:target=>reloadBrowserPhema(target,{readLayout:()=>refreshSavedLayouts([target],api)}),render:renderBrowserPanels,
    mounted:async(target,node)=>{node.dataset.phemaUi=JSON.stringify(uiBundle(target.customLayout)||{});
     const title=node.querySelector('.browser-title-line strong');if(title)title.innerHTML=label(target.design.title);
     const nav=document.querySelector('.browser-nav-item[data-id="'+CSS.escape(target.id)+'"] strong');if(nav)nav.innerHTML=label(target.design.title);
     const tab=document.querySelector('.window-tabs [data-id="'+CSS.escape(target.id)+'"]');if(tab)for(const text of tab.childNodes)if(text.nodeType===3&&text.textContent.trim())text.textContent=target.design.title;
     const layout=node.querySelector('.browser-layout');layout.style.cssText=layoutStyle(target.design.layout);
     layout.className='browser-layout layout-'+target.layoutId+(target.design.layout.columns===12?' composed-browser-layout':'')+(usesFreePlacement(target.design.layout)?' free-placement':'');
     mountPhemaEdit(node,target);applyUI(node);await persist();}});}
   catch(error){toast(error.message);}finally{el.disabled=false;el.removeAttribute('aria-busy');}return;
  }
'''

def install_browser_reload(directory):
    module=directory/'browser-reload.mjs'
    if not module.exists():return
    (directory/'browser-reload.js').write_bytes(module.read_bytes())
    connected=directory/'connected.js'
    if connected.exists():
        code=connected.read_text()
        if 'export async function reloadBrowserPhema(' not in code:
            connected.write_text("import {reloadBrowserPhema as reloadPhemaOnly} from './browser-reload.js';\n"+code+CONNECTED)
    messages=directory/'ui-messages.js'
    if messages.exists():messages.write_text(messages.read_text()+'\nObject.assign(MESSAGES,'+json.dumps(MESSAGES,ensure_ascii=False)+');\n')
    app=directory/'app.js'
    if app.exists():
        code=app.read_text();hook="  if(action==='browser-language')"
        if hook in code and "if(action==='reload-phema'&&b)" not in code:
            code="import {refreshPhemaFrame} from './phema-frame-refresh.js';\nimport {reloadBrowserPhema} from './connected.js';\n"+code
            code=code.replace(hook,MAIN+hook,1)
            toolbar='<button class="icon-button" data-action="change-layout"'
            code=code.replace(toolbar,'<button class="icon-button" data-action="reload-phema" data-id="${b.id}" aria-label="Reload Phema" title="Reload Phema">${icon(\'refresh\')}</button>'+toolbar,1)
            app.write_text(code)
    surface=directory/'browser-surface.js'
    if surface.exists():
        code=surface.read_text()
        if "document.querySelector('#reload-phema').onclick" in code:return
        code="import {refreshPhemaFrame} from './phema-frame-refresh.js';\nimport {reloadBrowserPhema} from './connected.js';\n"+code
        code=code.replace('<button class="button" id="toggle-edit"','<button class="button icon-button" id="reload-phema" aria-label="Reload Phema" title="Reload Phema">↻</button><button class="button" id="toggle-edit"',1)
        hook=" document.querySelector('#browser-language').onclick="
        code=code.replace(hook,""" root.dataset.browser=browser.id;
 document.querySelector('#reload-phema').onclick=async event=>{const button=event.currentTarget;button.disabled=true;
  try{await refreshPhemaFrame(root,browser,{read:b=>reloadBrowserPhema(b,{readLayout:()=>refreshSavedLayouts([b],api)}),render:renderBrowserPanels,mounted:()=>render(browser)});}
  catch(error){setUIText(root.querySelector('.layout-fetch-status'),error.message);}finally{button.disabled=false;}};
"""+hook,1)
        surface.write_text(code)
