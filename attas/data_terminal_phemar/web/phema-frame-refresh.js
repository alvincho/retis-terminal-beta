/** Refresh one selected Phema while preserving its Terminal and sibling views. */
export async function refreshPhemaFrame(root,browser,{read,render,mounted=()=>{}}){
 const selector='[data-browser="'+CSS.escape(browser.id)+'"]';
 const find=()=>root.matches?.(selector)?root:root.querySelector(selector);
 const frame=find();
 if(!frame)return false;
 if(browser.editMode)throw Error('Finish editing before reloading this Phema.');
 const result=await read(browser);
 // A close or replacement during the read must not refresh a different view.
 if(result===false||!frame.isConnected||find()!==frame||browser.editMode||browser.layoutConflict)return false;
 const content=frame.querySelector('.browser-layout');if(!content)return false;
 const template=frame.ownerDocument.createElement('template');template.innerHTML=render(browser);
 content.replaceChildren(template.content);
 await mounted(browser,frame);return true;
}
