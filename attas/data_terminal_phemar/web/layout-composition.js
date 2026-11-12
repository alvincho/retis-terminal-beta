import { normalizeLayoutEntry } from './layout-library.js';
import { PLAZA_PULSES, validTemplatePosition, templatePositionsOverlap, TEMPLATE_GEOMETRY_EPSILON } from './plaza-pulses.js';

const pulseInstruments=()=>[];
const bindPresetRequirements=entry=>entry;

/** Convert a valid saved design to a fresh 12-column DBL 0.2 editing document. */
export function toComposableEntry(entry) {
  const next = normalizeLayoutEntry(entry);
  if (!next) return next;
  if (['dbl/0.2','dbl/0.3','dbl/0.4'].includes(next.design.schemaVersion)) return bindPresetRequirements(next);
  const grid = next.design.layout;
  const ratios = grid.columns.split(' ').map((part) => parseFloat(part));
  const leftColumns = Math.max(1, Math.min(11, Math.round(12 * ratios[0] / (ratios[0] + ratios[1]))));
  const children = grid.children.map(({ area, ...node }) => {
    const cells = [];
    grid.areas.forEach((row, y) => row.forEach((name, x) => { if (name === area) cells.push([x, y]); }));
    const left = Math.min(...cells.map(([x]) => x));
    const right = Math.max(...cells.map(([x]) => x));
    const top = Math.min(...cells.map(([, y]) => y));
    const bottom = Math.max(...cells.map(([, y]) => y));
    return { ...node, position: {
      column: left === 0 ? 1 : leftColumns + 1,
      row: top + 1,
      columnSpan: left !== right ? 12 : left === 0 ? leftColumns : 12 - leftColumns,
      rowSpan: bottom - top + 1,
    } };
  });
  next.design.schemaVersion = 'dbl/0.2';
  next.design.layout = { id: grid.id, type: 'grid', columns: 12, snapToGrid: true, children };
  return normalizeLayoutEntry(bindPresetRequirements(next));
}

/** Rebinding changes only this tile, even if its original requirement is shared. */
export function replaceTemplatePulse(entry, nodeId, pulseId, template) {
  const pulse = PLAZA_PULSES.find(p => p.id === pulseId);
  const next = toComposableEntry(entry);
  const node = next?.design.layout.children.find(n => n.id === nodeId);
  if (!node || !pulse || !pulse.templates.includes(template)) return entry;
  const oldId = node.requirementId;
  const requirementId = uniqueId('pulse-data', next.design.dataRequirements);
  const portfolio = next.baseLayoutId === 'portfolio';
  next.design.dataRequirements.push({id:requirementId, label:pulse.name, schema:pulse.schema,
    fields:[...pulse.fields], mode:['pulse-chat','discussion'].includes(template)?'interactive':pulse.mode, essential:true,
    ...(next.baseLayoutId==='content'?{}:portfolio ? {portfolioId:next.design.params.portfolioId.default} : {instruments:pulseInstruments(pulse,(next.design.params.tickers?.default||[]))})});
  Object.assign(node, {pulseId, requirementId});delete node.fieldMappings;
  delete node.template; delete node.phemarType; delete node.instancePolicy; delete node.inputs;
  if (template === 'discussion') Object.assign(node, {type:'template', template:'pulse-chat', media:{target:{agentId:pulse.publisherId,pulse:pulse.provenance.endpointId},allowTargetSelection:true}});
  else Object.assign(node, {type:'template', template});
  if(template==='pulse-chat')node.media={target:{agentId:pulse.publisherId,pulse:pulse.provenance.endpointId},allowTargetSelection:true};
  if (oldId && !next.design.layout.children.some(n => n.requirementId === oldId))
    next.design.dataRequirements = next.design.dataRequirements.filter(r => r.id !== oldId);
  return normalizeLayoutEntry(next) || entry;
}

/** Accept either a DBL design or its layout; a blank canvas occupies one row. */
export function getLayoutRowCount(designOrLayout) {
  const grid = designOrLayout?.layout || designOrLayout;
  if (Array.isArray(grid?.areas)) return Math.max(1, grid.areas.length);
  return Math.max(1, Math.ceil(Math.max(1, ...(grid?.children || []).filter((node) => validTemplatePosition(node.position))
    .map((node) => node.position.row + node.position.rowSpan - 1)) - TEMPLATE_GEOMETRY_EPSILON));
}

/** Snapping is an interaction preference; switching it never rounds geometry. */
export function setGridSnapping(entry, enabled) {
  if (typeof enabled !== 'boolean') return entry;
  const next = toComposableEntry(entry);
  if (!next) return entry;
  next.design.layout.snapToGrid = enabled;
  return next;
}

export function canPlaceTemplate(entry, nodeId, position) {
  if (!validTemplatePosition(position) || !['dbl/0.2','dbl/0.3','dbl/0.4'].includes(entry?.design?.schemaVersion)) return false;
  const layout=entry.design.layout,tabId=layout.children.find(n=>n.id===nodeId)?.tabId;
  if(nodeId!==layout.tabbedView?.id&&!tabId&&layout.tabbedView&&templatePositionsOverlap(position,layout.tabbedView.position))return false;
  return layout.children.every((node) => node.id === nodeId || node.tabId!==tabId
    || (validTemplatePosition(node.position) && !templatePositionsOverlap(position, node.position)));
}

const uniqueId = (prefix, items) => {
  let index = 1;
  while (items.some((item) => item.id === `${prefix}-${index}`)) index += 1;
  return `${prefix}-${index}`;
};

export function addPulseTemplate(entry, pulseId, template) {
  const pulse = PLAZA_PULSES.find((item) => item.id === pulseId);
  if (!pulse || !pulse.templates.includes(template)) return entry;
  const next = toComposableEntry(entry);
  if (!next || next.design.layout.children.length >= 12) return entry;
  let position;
  for (let row = 1; row <= 24 && !position; row += 1) {
    for (let column = 1; column <= 7; column += 1) {
      const candidate = { column, row, columnSpan: 6, rowSpan: 1 };
      if (canPlaceTemplate(next, null, candidate)) { position = candidate; break; }
    }
  }
  if (!position) return entry;
  const nodeId = uniqueId('template', next.design.layout.children);
  const requirementId = uniqueId('pulse-data', next.design.dataRequirements);
  const portfolio = next.baseLayoutId === 'portfolio';
  next.design.dataRequirements.push({
    id: requirementId, label: pulse.name, schema: pulse.schema, fields: [...pulse.fields], mode: template==='pulse-chat'?'interactive':pulse.mode, essential: true,
    ...(next.baseLayoutId==='content'?{}:portfolio ? { portfolioId: next.design.params.portfolioId.default } : { instruments: pulseInstruments(pulse,(next.design.params.tickers?.default||[])) }),
  });
  const node = { id: nodeId, title: pulse.name, position, pulseId: pulse.id, requirementId, ...(template==='pulse-chat'?{media:{target:{agentId:pulse.publisherId,pulse:pulse.provenance.endpointId},allowTargetSelection:true}}:{}) };
  next.design.layout.children.push(template === 'discussion'
    ? { ...node, type: 'template', template: 'pulse-chat', media:{target:{agentId:pulse.publisherId,pulse:pulse.provenance.endpointId},allowTargetSelection:true} }
    : { ...node, type: 'template', template });
  return normalizeLayoutEntry(next) || entry;
}

/** Atomically apply a complete rectangle, avoiding invalid intermediate moves. */
export function setTemplatePosition(entry, nodeId, position) {
  const next = toComposableEntry(entry);
  const node = next?.design.layout.children.find((item) => item.id === nodeId) || (next?.design.layout.tabbedView?.id===nodeId?next.design.layout.tabbedView:null);
  if (!node) return entry;
  if (!canPlaceTemplate(next, nodeId, position)) return entry;
  node.position = { ...position };
  return normalizeLayoutEntry(next) || entry;
}

function reposition(entry, nodeId, values) {
  const next = toComposableEntry(entry);
  const node = next?.design.layout.children.find((item) => item.id === nodeId) || (next?.design.layout.tabbedView?.id===nodeId?next.design.layout.tabbedView:null);
  return node ? setTemplatePosition(entry, nodeId, { ...node.position, ...values }) : entry;
}

export function moveTemplate(entry, nodeId, { column, row } = {}) {
  return reposition(entry, nodeId, { column, row });
}

export function resizeTemplate(entry, nodeId, { columnSpan, rowSpan } = {}) {
  return reposition(entry, nodeId, { columnSpan, rowSpan });
}

export function removeTemplate(entry, nodeId) {
  const next = toComposableEntry(entry);
  const node = next?.design.layout.children.find((item) => item.id === nodeId) || (next?.design.layout.tabbedView?.id===nodeId?next.design.layout.tabbedView:null);
  if (!node) return entry;
  next.design.layout.children = next.design.layout.children.filter((item) => item.id !== nodeId);
  if (node.requirementId && !next.design.layout.children.some((item) => item.requirementId === node.requirementId)) {
    next.design.dataRequirements = next.design.dataRequirements.filter((item) => item.id !== node.requirementId);
  }
  return normalizeLayoutEntry(next) || entry;
}
