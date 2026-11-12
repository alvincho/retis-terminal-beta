import {ui as uiHTML,label,t,attribute} from './i18n.js';
import {runtime,api,scopedStorage} from './connected.js';
const STORAGE_KEY = 'data-terminal-theme.v1';
const CUSTOM_KEY = 'data-terminal-custom-theme.v1';
const choices = [
  { id:'dark', label:'Dark', description:'Low-light workspace' },
  { id:'light', label:'Light', description:'Bright, clear surfaces' },
  { id:'system', label:'System', description:'Follow your device' },
  { id:'custom', label:'Custom', description:'Your own colors' },
];
const validChoice = value => choices.some(choice => choice.id === value);
// A custom theme starts from the dark or light theme and replaces its main colors.
const BASES = {
  dark: { accent:'#e0b577', background:'#0d1117', surface:'#121820', text:'#e7ecf2' },
  light: { accent:'#87541b', background:'#edf2f5', surface:'#ffffff', text:'#24394b' },
};
const COLORS = [['accent','Accent'],['background','Background'],['surface','Panels'],['text','Text']];
const TOKENS = ['--bg','--surface','--raised','--hover','--line','--line-soft','--text','--muted','--dim','--accent','--accent-bg','--light-chrome','--light-input'];
const validCustom = value => !!value && typeof value === 'object' && Object.hasOwn(BASES,value.base)
  && COLORS.every(([key]) => /^#[0-9a-f]{6}$/i.test(value[key] || ''));
let preference = 'dark';
let custom = { base:'dark', ...BASES.dark };
let storageError = '';
let initialized = false;
let systemTheme;

function customTokens(colors) {
  const mix = (color,over,percent) => `color-mix(in srgb,${color} ${percent}%,${over})`;
  return { '--bg':colors.background, '--surface':colors.surface, '--raised':mix(colors.text,colors.surface,5), '--hover':mix(colors.text,colors.surface,9),
    '--line':mix(colors.text,colors.surface,18), '--line-soft':mix(colors.text,colors.surface,11), '--text':colors.text,
    '--muted':mix(colors.text,colors.background,68), '--dim':mix(colors.text,colors.background,52), '--accent':colors.accent,
    '--accent-bg':mix(colors.accent,colors.surface,16), '--light-chrome':mix(colors.text,colors.surface,4), '--light-input':colors.surface };
}

function syncControls() {
  document.querySelectorAll('input[name="appearance-theme"]').forEach(input => {
    input.checked = input.value === preference;
  });
  document.querySelectorAll('[data-custom-theme]').forEach(fieldset => {
    fieldset.hidden = preference !== 'custom';
    fieldset.elements.customBase.value = custom.base;
    for (const [key] of COLORS) fieldset.elements[key].value = custom[key];
  });
  document.querySelectorAll('[data-theme-option="custom"] .theme-preview').forEach(preview => {
    preview.style.cssText = `--preview-bg:${custom.background};--preview-surface:${custom.surface};--preview-accent:${custom.accent}`;
  });
  document.querySelectorAll('.theme-storage-status').forEach(status => {
    status.textContent = storageError;
    status.hidden = !storageError;
  });
}

function applyTheme() {
  const resolved = preference === 'system' ? (systemTheme.matches ? 'dark' : 'light') : preference === 'custom' ? custom.base : preference;
  const root = document.documentElement;
  for (const token of TOKENS) root.style.removeProperty(token);
  if (preference === 'custom') for (const [token,value] of Object.entries(customTokens(custom))) root.style.setProperty(token,value);
  root.dataset.theme = resolved;
  root.dataset.themePreference = preference;
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content',preference === 'custom' ? custom.background : resolved === 'dark' ? '#0d1117' : '#f3f5f7');
  syncControls();
}

function saveTheme(body) {
  if (!runtime.user) return;
  api('/api/settings').then(current => {
    const {id,revision,updatedAt,...settings} = current.preferences;
    return api('/api/settings',{method:'PUT',body:{...settings,...body}});
  }).catch(() => { storageError = 'Theme applies locally; shared settings unavailable.'; syncControls(); });
}

function saveCustom() {
  try { scopedStorage().setItem(CUSTOM_KEY,JSON.stringify(custom)); } catch {}
  saveTheme({ theme:'custom', customTheme:custom });
}

export function applyAccountTheme(){
  preference=validChoice(runtime.settings?.preferences?.theme)?runtime.settings.preferences.theme:'dark';
  if(validCustom(runtime.settings?.preferences?.customTheme))custom={...runtime.settings.preferences.customTheme};
  storageError='';applyTheme();
}

/** Appearance updates the document only; open editors retain their drafts and focus. */
export function initializeTheme() {
  if (initialized) return;
  initialized = true;
  systemTheme = window.matchMedia('(prefers-color-scheme: dark)');
  try {
    const saved = scopedStorage().getItem(STORAGE_KEY);
    preference = validChoice(runtime.settings?.preferences?.theme) ? runtime.settings.preferences.theme : validChoice(saved) ? saved : 'dark';
    const savedCustom = runtime.settings?.preferences?.customTheme || JSON.parse(scopedStorage().getItem(CUSTOM_KEY) || 'null');
    if (validCustom(savedCustom)) custom = { ...savedCustom };
  } catch {
    storageError = 'Theme storage is unavailable. Changes apply to this tab only.';
  }
  applyTheme();
  window.addEventListener('focus',async()=>{if(!runtime.user)return;try{const settings=await api('/api/settings');const next=settings.preferences.theme;if(validCustom(settings.preferences.customTheme))custom={...settings.preferences.customTheme};if(validChoice(next)){preference=next;applyTheme();}}catch{}});
  systemTheme.addEventListener('change',() => { if (preference === 'system') applyTheme(); });
  window.addEventListener('storage',event => {
    // Window-scoped sessionStorage does not synchronize other Terminal windows.
    if (event.storageArea!==sessionStorage || !event.key?.endsWith(STORAGE_KEY)) return;
    preference = validChoice(event.newValue) ? event.newValue : 'dark';
    storageError = '';
    applyTheme();
  });
  document.addEventListener('change',event => {
    if (!(event.target instanceof HTMLInputElement) || event.target.name !== 'appearance-theme' || !validChoice(event.target.value)) return;
    preference = event.target.value;
    try {
      scopedStorage().setItem(STORAGE_KEY,preference);
      saveTheme(preference === 'custom' ? { theme:preference, customTheme:custom } : { theme:preference });
      storageError = '';
    } catch {
      storageError = 'Theme changed for this tab, but could not be saved on this device.';
    }
    applyTheme();
  });
  // Colors preview while the picker is open and save when it closes.
  document.addEventListener('input',event => {
    const fieldset = event.target.closest?.('[data-custom-theme]');
    if (!fieldset || !COLORS.some(([key]) => key === event.target.name) || !/^#[0-9a-f]{6}$/i.test(event.target.value)) return;
    custom = { ...custom, [event.target.name]:event.target.value };
    applyTheme();
  });
  document.addEventListener('change',event => {
    const fieldset = event.target.closest?.('[data-custom-theme]');
    if (!fieldset) return;
    if (event.target.name === 'customBase' && Object.hasOwn(BASES,event.target.value)) custom = { base:event.target.value, ...BASES[event.target.value] };
    else if (!COLORS.some(([key]) => key === event.target.name)) return;
    applyTheme(); saveCustom();
  });
  document.addEventListener('click',event => {
    const trigger = event.target instanceof Element && event.target.closest('[data-theme-open]');
    if (trigger) openAppearanceSettings(trigger);
    if (event.target instanceof Element && event.target.closest('[data-custom-theme-reset]')) {
      custom = { base:custom.base, ...BASES[custom.base] };
      applyTheme(); saveCustom();
    }
  });
}

function customThemeHTML(readOnly) {
  const disabled = readOnly ? 'disabled' : '';
  return uiHTML`<fieldset class="custom-theme" data-custom-theme ${preference === 'custom' ? '' : 'hidden'} ${disabled}><label>Base<select name="customBase"><option value="dark" ${custom.base === 'dark' ? 'selected' : ''}>Dark</option><option value="light" ${custom.base === 'light' ? 'selected' : ''}>Light</option></select></label>${COLORS.map(([key,name]) => uiHTML`<label>${label(name)}<input type="color" name="${key}" value="${custom[key]}"></label>`).join('')}<button class="button subtle" type="button" data-custom-theme-reset>Reset colors</button></fieldset>`;
}

export function renderThemeSettings() {
  const readOnly=runtime.kind==='app-consultant'&&!runtime.permissions?.canConfigure;
  return uiHTML`<section class="theme-setting"><div><h3>Theme</h3><p>Applies to this Terminal window and its Data Browsers and App Consultants.</p></div><div class="theme-options" role="radiogroup" aria-label="Theme">${choices.map(choice => uiHTML`<label class="theme-option" data-theme-option="${choice.id}"><input type="radio" name="appearance-theme" value="${choice.id}" ${readOnly?'disabled':''} ${attribute("aria-label",choice.label)} ${preference === choice.id ? 'checked' : ''}/><span class="theme-preview" aria-hidden="true" ${choice.id === 'custom' ? `style="--preview-bg:${custom.background};--preview-surface:${custom.surface};--preview-accent:${custom.accent}"` : ''}><i></i><i></i><i></i></span><strong>${label(choice.label)}</strong><small>${label(choice.description)}</small></label>`).join('')}</div>${customThemeHTML(readOnly)}${readOnly?uiHTML`<p>Only the owner can change App Consultant settings.</p>`:''}<p class="theme-storage-status" role="status" ${storageError ? '' : 'hidden'}>${storageError}</p></section>`;
}

export function openAppearanceSettings(trigger) {
  if (document.querySelector('#appearance-dialog[open]')) return;
  const dialog = document.createElement('dialog');
  dialog.id = 'appearance-dialog';
  dialog.className = 'modal appearance-dialog';
  dialog.setAttribute('aria-label','App Consultant settings');
  dialog.innerHTML = uiHTML`<div class="modal-top"><span>App Consultant settings</span><button class="icon-button" type="button" data-theme-close aria-label="Close settings">×</button></div><div class="settings-body"><h2>Appearance</h2>${renderThemeSettings()}<p class="theme-instant-note">Changes apply immediately and save automatically.</p><button class="button subtle" type="button" data-theme-close>Done</button></div>`;
  document.body.append(dialog);
  const close = () => dialog.close();
  dialog.addEventListener('close',() => {
    dialog.remove();
    const target = trigger?.isConnected ? trigger : document.querySelector('[data-theme-open]');
    target?.focus({preventScroll:true});
  },{once:true});
  dialog.addEventListener('click',event => {
    if (event.target.closest('[data-theme-close]')) close();
    if (event.target === dialog) {
      const box = dialog.getBoundingClientRect();
      if (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom) close();
    }
  });
  dialog.showModal();
  if(runtime.kind==='app-consultant'&&runtime.permissions?.canConfigure){
    import('./consultant-access.js').then(module=>module.mountTrustedAccounts(dialog)).catch(()=>{});
  }
  dialog.querySelector('input:checked')?.focus();
}
