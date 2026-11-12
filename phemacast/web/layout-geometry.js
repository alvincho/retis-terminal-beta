/** Shared grid, tab and fractional placement geometry. */
export function layoutScope(layout,tabId){
 return {...layout,tabbedView:undefined,children:tabId?layout.children.filter(n=>n.tabId===tabId):[...layout.children.filter(n=>!n.tabId),...(layout.tabbedView?[{...layout.tabbedView,type:'tabs',title:layout.tabbedView.tabs.map(t=>t.title).join(' / ')}]:[])]};
}

export function layoutRows(layout) {
  if(layout.tabbedView)layout=layoutScope(layout);
  return layout.columns === 12
    ? Math.max(1, Math.ceil(Math.max(1, ...layout.children.map(node => node.position.row + node.position.rowSpan - 1)) - 1e-9))
    : layout.areas.length;
}

const fractionalPosition = position => position && ['column', 'row', 'columnSpan', 'rowSpan']
  .some(key => !Number.isInteger(position[key]));
const cssNumber = value => Number(Number(value).toFixed(8));
const placementOrder = (node, layout) => [...(layout?.children || []).filter(item => item.id !== node.id), node]
  .sort((a, b) => a.position.row - b.position.row || a.position.column - b.position.column)
  .indexOf(node);

/** Existing fractions stay in place even when grid snapping is switched on. */
export function usesFreePlacement(layout) {
  if(layout?.tabbedView)layout=layoutScope(layout);
  return layout?.columns === 12 && (layout.snapToGrid === false
    || layout.children.some(node => fractionalPosition(node.position)));
}

function freeContainerStyle(rows, rowHeight) {
  const count = Math.max(1, Math.ceil(rows - 1e-9));
  return `--placement-rows:${count};--placement-row-height:${rowHeight}px;--placement-content-height:calc((var(--placement-row-height) + var(--placement-gap,0px)) * ${count} - var(--placement-gap,0px));position:relative;display:block;height:calc(var(--placement-content-height) + var(--placement-padding,0px) * 2 + var(--placement-border,0px) * 2)`;
}

/** Editor rows use the same 100px unit for grid and fractional placement. */
export function canvasStyle(layout, rows = layoutRows(layout)) {
  const count = Math.max(1, Math.ceil(rows - 1e-9));
  return usesFreePlacement(layout)
    ? freeContainerStyle(count, 100)
    : `grid-template-rows:repeat(${count},100px)`;
}

export function layoutStyle(layout, preview = false) {
  if (layout.columns === 12) {
    if (usesFreePlacement(layout)) return `--layout-columns:repeat(12,minmax(0,1fr));grid-template-areas:none;${freeContainerStyle(layoutRows(layout), preview ? 64 : 100)}`;
    return `--layout-columns:repeat(12,minmax(0,1fr));grid-template-columns:repeat(12,minmax(0,1fr));grid-template-areas:none;grid-template-rows:repeat(${layoutRows(layout)},minmax(${preview ? '64px' : '100px'},1fr))`;
  }
  return `--layout-columns:${layout.columns};grid-template-columns:${layout.columns};grid-template-areas:${layout.areas.map(row => '"' + row.join(' ') + '"').join(' ')}`;
}

export function templateStyle(node, layout) {
  if (node.position) {
    const p = node.position;
    if (usesFreePlacement(layout) || fractionalPosition(p)) {
      const columnStep = '(100% - var(--placement-padding,0px) * 2 + var(--placement-gap,0px)) / 12';
      const rowStep = '(var(--placement-row-height,100px) + var(--placement-gap,0px))';
      return `position:absolute;grid-column:auto;grid-row:auto;left:calc(var(--placement-padding,0px) + (${columnStep}) * ${cssNumber(p.column - 1)});top:calc(var(--placement-padding,0px) + ${rowStep} * ${cssNumber(p.row - 1)});width:calc((${columnStep}) * ${cssNumber(p.columnSpan)} - var(--placement-gap,0px));height:calc(${rowStep} * ${cssNumber(p.rowSpan)} - var(--placement-gap,0px));order:${placementOrder(node, layout)}`;
    }
    return `grid-column:${p.column}/span ${p.columnSpan};grid-row:${p.row}/span ${p.rowSpan};order:${p.row*12+p.column}`;
  }
  return `grid-area:${node.area}`;
}

/** Geometry checks shared by the composition editor and layout validators. */
export const TEMPLATE_GEOMETRY_EPSILON = 1e-7;

export function validTemplatePosition(position) {
  return position !== null && typeof position === 'object' && !Array.isArray(position)
    && Object.keys(position).every((key) => ['column', 'row', 'columnSpan', 'rowSpan'].includes(key))
    && ['column', 'row', 'columnSpan', 'rowSpan'].every((key) => typeof position[key] === 'number' && Number.isFinite(position[key]) && position[key] >= 1)
    && position.column + position.columnSpan <= 13 + TEMPLATE_GEOMETRY_EPSILON
    && position.row + position.rowSpan <= 25 + TEMPLATE_GEOMETRY_EPSILON && position.rowSpan <= 6 + TEMPLATE_GEOMETRY_EPSILON;
}

export function templatePositionsOverlap(a, b) {
  return a.column < b.column + b.columnSpan - TEMPLATE_GEOMETRY_EPSILON
    && b.column < a.column + a.columnSpan - TEMPLATE_GEOMETRY_EPSILON
    && a.row < b.row + b.rowSpan - TEMPLATE_GEOMETRY_EPSILON
    && b.row < a.row + a.rowSpan - TEMPLATE_GEOMETRY_EPSILON;
}
