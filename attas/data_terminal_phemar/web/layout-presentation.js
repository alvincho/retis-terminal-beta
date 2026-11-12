import {label,attribute} from './i18n.js';
export const TEMPLATE_LABELS = { 'pulse-chat':'Pulse chat', phema:'Nested Phema',interactive:'Interactive Phemar',custom:'Custom renderer',text:'Text',image:'Image',audio:'Audio',video:'Video',scene:'3D scene', tabs:'Tabbed view', chart:'Price chart', watchlist:'Quote table', news:'News feed', stats:'Key metrics', financials:'Financial summary', reports:'Report list', comparison:'Comparison table', allocation:'Allocation chart', holdings:'Holdings table', event:'Event summary', discussion:'Pulse chat' };

/** Shared presentation for legacy area grids and composed DBL grids. */
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

export {layoutScope,layoutRows,usesFreePlacement,canvasStyle,layoutStyle,templateStyle} from './layout-geometry.js';
import {layoutScope,layoutRows,usesFreePlacement,layoutStyle,templateStyle} from './layout-geometry.js';

export function renderDesignMap(design, {activeTabId} = {}) {
  const original = design.layout, tabs = original.tabbedView?.tabs || [];
  const active = tabs.find(tab => tab.id === activeTabId) || tabs[0];
  const layout = tabs.length ? layoutScope(original) : original;
  const cell = node => {
    if(node.type === 'tabs' && active) {
      const scope = layoutScope(original, active.id);
      return `<section class="design-map-tabs" style="${esc(templateStyle(node, layout))};--preview-rows:${layoutRows(scope)}"><div class="design-map-tab-list" role="tablist" ${attribute('aria-label','Layout sections')}>${tabs.map(tab => `<button type="button" role="tab" data-action="preview-tab" data-preview-tab="${esc(tab.id)}" aria-selected="${tab.id === active.id}" aria-controls="preview-panel-${esc(original.id)}">${label(tab.title)}</button>`).join('')}</div><div class="design-map-tab-panel" id="preview-panel-${esc(original.id)}" role="tabpanel" ${attribute('aria-label',active.title)}>${renderDesignMap({...design, layout:scope})}</div></section>`;
    }
    return `<div class="design-map-cell" data-preview-node="${esc(node.id)}" style="${esc(templateStyle(node, layout))}"><strong>${label(node.title)}</strong><small>${label(node.type === 'phemar' ? 'Pulse chat' : TEMPLATE_LABELS[node.template] || node.template)}</small></div>`;
  };
  return `<div class="design-map ${layout.columns === 12 ? 'composed-design-map' : ''} ${usesFreePlacement(layout) ? 'free-placement' : ''}" style="${esc(layoutStyle(layout, true))}" aria-label="Layout preview">${layout.children.map(cell).join('')}</div>`;
}
