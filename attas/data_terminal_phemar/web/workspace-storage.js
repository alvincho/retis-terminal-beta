import {secretField} from './pulse-chat.js';
import {normalizeContentOverrides} from './content-policies.js';
import {validLocale} from './i18n.js';
import {normalizeFieldBindings} from './field-bindings.js';
import {normalizeRefreshOverrides,refreshPolicy,refreshLabel,refreshFields} from './block-refresh.js';
import {normalizeInstrumentOverrides} from './browser-instruments.js';
/** Local workspace persistence. This module has no browser or rendering dependency. */
import { normalizeLayoutEntry } from './layout-library.js';
export const STORAGE_KEY = 'data-terminal-ui.v2';
export const LEGACY_STORAGE_KEY = 'data-terminal-ui.v1';

const MAX_WORKSPACES = 32;
const MAX_BROWSERS = 12;
const MAX_GEOMETRY = 1_000_000;
const RANGES = new Set(['1D', '1W', '1M', '3M', '1Y']);
const isRecord = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);
const finite = (value) => typeof value === 'number' && Number.isFinite(value);
const clamp = (value, min, max) => Math.min(Math.max(value, min), max);
const cleanText = (value, limit) => value.replace(/[\u0000-\u001f\u007f]/g, ' ').trim().slice(0, limit);

function normalizeFrame(frame) {
  if (!isRecord(frame) || !['x', 'y', 'width', 'height'].every((key) => finite(frame[key]))) return null;
  return {
    x: clamp(frame.x, 0, MAX_GEOMETRY),
    y: clamp(frame.y, 0, MAX_GEOMETRY),
    width: clamp(frame.width, 1, MAX_GEOMETRY),
    height: clamp(frame.height, 1, MAX_GEOMETRY),
  };
}

function normalizeTimestamp(value) {
  const time = typeof value === 'string' && /^\d{4}-\d{2}-\d{2}T/.test(value) ? Date.parse(value) : value;
  return finite(time) ? Math.floor(clamp(time, 0, 8_640_000_000_000_000)) : 0;
}

export function normalizeMessages(messages) {
  return (Array.isArray(messages)?messages:[]).slice(-40).filter(m=>isRecord(m)&&['user','assistant'].includes(m.role)&&typeof m.nodeId==='string').map(m=>({role:m.role,nodeId:cleanText(m.nodeId,80),text:(typeof m.text==='string'?m.text:JSON.stringify(m.text??'')).slice(0,8000),...Object.fromEntries(['agentId','pulseName','pulseId'].filter(k=>typeof m[k]==='string').map(k=>[k,cleanText(m[k],200)]))}));
}
export function normalizeDiscussionDrafts(drafts) {
  return Object.fromEntries(Object.entries(isRecord(drafts)?drafts:{}).filter(([k,v])=>/^[a-zA-Z0-9_-]{1,80}$/.test(k)&&typeof v==='string').slice(0,12).map(([k,v])=>[k,v.slice(0,2000)]));
}
export function normalizeInteractionDrafts(drafts) {
 const safe=value=>{
  const result={agentId:typeof value.agentId==='string'?cleanText(value.agentId,200):'',...(typeof value.pulseId==='string'?{pulseId:cleanText(value.pulseId,200)}:{}),arguments:Object.fromEntries(Object.entries(isRecord(value.arguments)?value.arguments:{}).filter(([k,v])=>k.length<=100&&!secretField(k)&&typeof v==='string').slice(0,40).map(([k,v])=>[k,v.slice(0,2000)]))};
  if(typeof value.text==='string')result.text=value.text.slice(0,2000);
  if(typeof value.rawArguments==='string'){
   // A raw draft is temporary UI state; never persist credential-shaped JSON.
   try{const parsed=JSON.parse(value.rawArguments);const contains=v=>isRecord(v)?Object.entries(v).some(([k,x])=>secretField(k)||contains(x)):Array.isArray(v)&&v.some(contains);if(!contains(parsed))result.rawArguments=value.rawArguments.slice(0,20000);}catch{}
  }
  return result;
 };
 return Object.fromEntries(Object.entries(isRecord(drafts)?drafts:{}).filter(([key,value])=>/^[a-zA-Z0-9_-]{1,80}$/.test(key)&&isRecord(value)).slice(0,64).map(([key,value])=>[key,{...safe(value),...(isRecord(value.targets)?{targets:Object.fromEntries(Object.entries(value.targets).filter(([k,v])=>k.length<=420&&isRecord(v)).slice(-16).map(([k,v])=>[k,safe(v)]))}:{})}]));
}

export function normalizeResearchState(value) {
 if(!isRecord(value))return {};
 // Parameters a view keeps itself (e.g. strategy definitions) nest deeper and use
 // namespaced keys; the whole value stays bounded per Browser. Secret keys are never kept.
 const safe=(v,depth=0)=>depth<16&&(v===null||typeof v==='boolean'||typeof v==='number'&&Number.isFinite(v)||typeof v==='string'&&v.length<=20000||Array.isArray(v)&&v.length<=500&&v.every(x=>safe(x,depth+1))||isRecord(v)&&Object.keys(v).length<=200&&Object.entries(v).every(([k,x])=>/^[A-Za-z$][A-Za-z0-9_.:$-]{0,80}$/.test(k)&&!secretField(k)&&safe(x,depth+1)));
 return safe(value)&&JSON.stringify(value).length<=512000?structuredClone(value):{};
}

function normalizeWorkspace(workspace, index, usedIds) {
  let id = typeof workspace.id === 'string' && /^[a-zA-Z0-9_-]{1,80}$/.test(workspace.id)
    ? workspace.id : `workspace-${index + 1}`;
  if (usedIds.has(id)) {
    let suffix = 1;
    const base = `workspace-${index + 1}`;
    id = base;
    while (usedIds.has(id)) id = `${base}-${suffix++}`;
  }
  usedIds.add(id);

  const browsers = [];
  let activeIndex = -1;
  workspace.browsers.slice(0, MAX_BROWSERS).forEach((browser, sourceIndex) => {
    if (!isRecord(browser) || typeof browser.command !== 'string') return;
    if (Number.isInteger(workspace.activeIndex) && workspace.activeIndex === sourceIndex) activeIndex = browsers.length;
    const customLayout = browser.customLayout ? normalizeLayoutEntry(browser.customLayout) : null;
    browsers.push({
      command: cleanText(browser.command, 499),
      ...(typeof browser.previewId==='string'&&/^[a-zA-Z0-9_-]{1,80}$/.test(browser.previewId)?{previewId:browser.previewId}:{}),
      ...(typeof browser.terminalDocumentId==='string'&&/^[a-zA-Z0-9_-]{1,80}$/.test(browser.terminalDocumentId)?{terminalDocumentId:browser.terminalDocumentId}:{}),
      sourceMode:browser.sourceMode==='snapshot'?'snapshot':'parameters',
      ...(typeof browser.contentSnapshotId==='string'&&/^[a-f0-9]{64}$/.test(browser.contentSnapshotId)?{contentSnapshotId:browser.contentSnapshotId}:{}),
      uiLanguage:validLocale(browser.uiLanguage)?browser.uiLanguage:null,
      fieldBindings: normalizeFieldBindings(browser.fieldBindings),
      contentOverrides: normalizeContentOverrides(browser.contentOverrides),
      refreshOverrides: normalizeRefreshOverrides(browser.refreshOverrides),
      instrumentOverrides: normalizeInstrumentOverrides(browser.instrumentOverrides),
      parameterValues: normalizeResearchState(browser.parameterValues),
      researchView: normalizeResearchState(browser.researchView),
      range: RANGES.has(browser.range) ? browser.range : '1M',
      messages: normalizeMessages(browser.messages),
      discussionDrafts: normalizeDiscussionDrafts(browser.discussionDrafts),
      interactionDrafts: normalizeInteractionDrafts(browser.interactionDrafts),
      commandDraft:typeof browser.commandDraft==='string'?browser.commandDraft.slice(0,499):browser.command,
      commandOpen:browser.commandOpen===true,
      ...(isRecord(browser.layoutTabs)?{layoutTabs:Object.fromEntries(Object.entries(browser.layoutTabs).filter(([k,v])=>/^[a-zA-Z0-9_/-]{1,256}$/.test(k)&&typeof v==='string'&&/^[a-zA-Z0-9_-]{1,80}$/.test(v)).slice(0,64))}:{}),
      frame: normalizeFrame(browser.frame),
      z: finite(browser.z) ? Math.floor(clamp(browser.z, 0, MAX_GEOMETRY)) : browsers.length + 1,
      ...(customLayout ? {customLayout} : browser.customLayout ? {unsupportedLayout:browser.customLayout} : browser.unsupportedLayout ? {unsupportedLayout:browser.unsupportedLayout} : {}),
      ...(typeof browser.consultantId==='string'&&/^[a-zA-Z0-9_-]{1,100}$/.test(browser.consultantId)?{consultantId:browser.consultantId}:{}),
      ...(isRecord(browser.layoutPublication)&&typeof browser.layoutPublication.publicationId==='string'&&/^[a-zA-Z0-9_-]{1,120}$/.test(browser.layoutPublication.publicationId)&&Number.isInteger(browser.layoutPublication.revision)&&browser.layoutPublication.revision>0?{layoutPublication:{publicationId:browser.layoutPublication.publicationId,revision:browser.layoutPublication.revision}}:{}),
    });
  });
  if (activeIndex < 0 && browsers.length > 0) activeIndex = 0;

  return {
    id,
    name: typeof workspace.name === 'string' && cleanText(workspace.name, 60)
      ? cleanText(workspace.name, 60) : `Workspace ${index + 1}`,
    updatedAt: normalizeTimestamp(workspace.updatedAt),
    browsers,
    activeIndex,
    focus: browsers.length > 0 && workspace.focus === true,
  };
}

export function normalizeDocument(input) {
  if (!isRecord(input) || input.version !== 2 || !Array.isArray(input.workspaces)) {
    throw new Error('Invalid workspace document.');
  }
  const usedIds = new Set();
  const workspaces = input.workspaces.slice(0, MAX_WORKSPACES)
    .filter((workspace) => isRecord(workspace) && Array.isArray(workspace.browsers))
    .map((workspace, index) => normalizeWorkspace(workspace, index, usedIds));
  if (workspaces.length === 0) throw new Error('A workspace document must contain at least one workspace.');
  return {
    version: 2,
    activeWorkspaceId: workspaces.some((workspace) => workspace.id === input.activeWorkspaceId)
      ? input.activeWorkspaceId : workspaces[0].id,
    workspaces,
    settings: {
      density: input.settings?.density === 'compact' ? 'compact' : 'comfortable',
      sidebarCollapsed: input.settings?.sidebarCollapsed === true,
    },
  };
}

function parseDocument(raw) {
  if (typeof raw !== 'string') throw new Error('Invalid stored workspace value.');
  return normalizeDocument(JSON.parse(raw));
}

/**
 * Read v2 first. An absent v2 may migrate a v1 snapshot in memory; this function
 * never writes or removes keys. Present but corrupt v2 blocks legacy fallback.
 */
export function readWorkspaceStore(storage) {
  let current;
  try {
    current = storage.getItem(STORAGE_KEY);
  } catch {
    return { data: null, error: 'Workspace storage is unavailable on this device.', migrated: false };
  }
  if (current != null) {
    try {
      return { data: parseDocument(current), error: null, migrated: false };
    } catch {
      return { data: null, error: 'Saved workspace data is invalid. It was left unchanged.', migrated: false };
    }
  }

  let legacy;
  try {
    legacy = storage.getItem(LEGACY_STORAGE_KEY);
  } catch {
    return { data: null, error: 'Workspace storage is unavailable on this device.', migrated: false };
  }
  if (legacy == null) return { data: null, error: null, migrated: false };
  try {
    const previous = JSON.parse(legacy);
    if (!isRecord(previous) || !Array.isArray(previous.browsers)) throw new Error('Invalid legacy workspace.');
    const data = normalizeDocument({
      version: 2,
      activeWorkspaceId: 'workspace-1',
      workspaces: [{
        id: 'workspace-1', name: 'Research desk', updatedAt: previous.updatedAt,
        browsers: previous.browsers, activeIndex: previous.activeIndex, focus: previous.focus,
      }],
      settings: { density: previous.density, sidebarCollapsed: previous.sidebarCollapsed },
    });
    return { data, error: null, migrated: true };
  } catch {
    return { data: null, error: 'The previous workspace could not be read. It was left unchanged.', migrated: false };
  }
}

/** Write only the normalized, whitelisted document; preserve corrupt existing v2. */
export function writeWorkspaceStore(storage, data, options = {}) {
  let normalized;
  try {
    normalized = normalizeDocument(data);
  } catch {
    return { ok: false, error: 'Workspace changes are invalid and could not be saved.' };
  }

  let current;
  try {
    current = storage.getItem(STORAGE_KEY);
  } catch {
    return { ok: false, error: 'Workspace storage is unavailable on this device.' };
  }
  if ('expectedRaw' in options && current !== options.expectedRaw) {
    return {ok:false,conflict:true,error:'Another Terminal tab saved these workspaces. Export your changes before reloading the latest saved version.'};
  }
  if (current != null) {
    try {
      parseDocument(current);
    } catch {
      return { ok: false, error: 'Saved workspace data is invalid. It was left unchanged.' };
    }
  }

  try {
    const raw=JSON.stringify(normalized);
    storage.setItem(STORAGE_KEY, raw);
    return { ok: true, error: null, raw };
  } catch {
    return { ok: false, error: 'Workspaces could not be saved on this device. Storage may be full or unavailable.' };
  }
}
