import {ui as uiHTML} from './i18n.js';
import {connect,runtime,settingsHTML,esc} from './connected.js';
const main=document.querySelector('#settings-surface');
function render(){main.innerHTML=uiHTML`<h1>${esc(runtime.agent?.name||'Agent settings')}</h1>${runtime.error?uiHTML`<p role="alert">${esc(runtime.error)}</p>`:''}${settingsHTML()}`;}
await connect();const theme=runtime.settings.preferences?.theme||'dark';document.documentElement.dataset.themePreference=theme;document.documentElement.dataset.theme=theme==='system'?(matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light'):theme;
render();document.addEventListener('connected:settings',render);
