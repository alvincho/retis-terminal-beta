/** Content-sized layouts for declarative forms and displays. No application rules. */
const styles = `
.browser-layout.phema-content-sized{grid-template-rows:none!important;grid-auto-rows:auto!important;align-content:start;gap:0!important;background:var(--surface);}
.browser-layout.phema-content-sized>.composition-content{min-height:0!important;height:auto;overflow:visible;padding:8px 10px;border:0;box-shadow:none;}
.browser-layout.phema-content-sized[data-boundaries="true"]>.composition-content{outline:1px solid var(--line);outline-offset:-1px;}
.phema-content-sized .panel-heading{margin-bottom:6px;min-height:22px;gap:6px;}
.phema-content-sized .panel-heading>.muted{display:none;}
.phema-boundaries{margin-left:auto!important;font-size:10px!important;white-space:nowrap;display:flex;align-items:center;gap:4px;cursor:pointer;}
.phema-boundaries input{width:12px;height:12px;margin:0;}
.composition-content form[data-phema-interaction]{padding:0;}
.composition-content form[data-phema-interaction] .phema-form-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(170px,100%),1fr));gap:8px 12px;align-items:start;}
.composition-content form[data-phema-interaction] .phema-form-grid>label{display:flex;flex-direction:column;align-items:stretch;gap:4px;min-width:0;font-size:11px;line-height:1.35;}
.composition-content form[data-phema-interaction] :is(input:not([type=checkbox]),select,textarea){box-sizing:border-box;width:100%;min-width:0;min-height:29px;padding:5px 7px;font:inherit;border:1px solid var(--line);border-radius:4px;background:var(--surface);color:var(--text);}
.composition-content form[data-phema-interaction] .phema-form-grid>label:has(input[type=checkbox]){flex-direction:row;align-items:center;justify-content:flex-start;gap:6px;min-height:29px;}
.composition-content form[data-phema-interaction] input[type=checkbox]{width:14px;height:14px;flex:none;margin:0;order:-1;}
.composition-content form[data-phema-interaction] .phema-check-row{grid-column:1/-1;display:flex;flex-wrap:wrap;gap:6px 16px;}
.composition-content form[data-phema-interaction] .phema-check-row>label{display:flex;align-items:center;gap:5px;white-space:nowrap;}
.composition-content form[data-phema-interaction] button[type=submit]{margin-top:8px;padding:6px 12px;background:var(--accent-bg,var(--raised));color:var(--accent);border:1px solid var(--line);border-radius:4px;font-size:11px;}
.composition-content form[data-phema-interaction] [role=status]:empty,.composition-content .composition-interaction-result:empty{display:none;}
.composition-content .composition-interaction-result{margin:6px 0 0;}
.phema-content-sized .composition-custom-table{max-height:320px;}
.phema-content-sized .composition-custom-table table{width:100%;border-collapse:collapse;font-size:12px;}
.phema-content-sized .composition-custom-table :is(th,td){text-align:left;padding:5px 8px;border-bottom:1px solid var(--line-soft,var(--line));white-space:nowrap;}
.phema-content-sized .composition-custom-table tbody tr:last-child td{border-bottom:0;}
@container(max-width:600px){.browser-layout.phema-content-sized{display:flex!important;flex-direction:column;}.browser-layout.phema-content-sized>.composition-content{flex:none;}}
`;
export function mountCompactComposition(root){
 const doc=root.ownerDocument||root;
 if(!doc.getElementById('phemacast-compact-composition')){const style=doc.createElement('style');style.id='phemacast-compact-composition';style.textContent=styles;doc.head.append(style);}
 for(const grid of root.querySelectorAll('form[data-phema-interaction] .phema-form-grid')){
  // Consecutive switches form a compact row without changing their names or values.
  let run=[];
  const flush=()=>{if(run.length>1){const group=doc.createElement('div');group.className='phema-check-row';run[0].before(group);group.append(...run);}run=[];};
  for(const child of [...grid.children]){if(child.tagName==='LABEL'&&child.querySelector('input[type=checkbox]'))run.push(child);else flush();}flush();
 }
 for(const layout of root.querySelectorAll('.browser-layout:not(.free-placement)')){
  const panels=[...layout.children];
  // Charts, embeds, nested layouts and manually positioned canvases retain their sizing.
  if(!panels.length||!layout.querySelector('form[data-phema-interaction]')||!panels.every(p=>p.classList.contains('composition-content')&&!p.querySelector('canvas,iframe,img,video,audio')&&p.matches('.panel-interactive,.panel-custom,.panel-text')))continue;
  layout.classList.add('phema-content-sized');
  if(layout.querySelector('.phema-boundaries'))continue;
  const target=panels.filter(p=>p.querySelector('form[data-phema-interaction]')).at(-1);
  const heading=target?.querySelector('.panel-heading');if(!heading)continue;
  const label=doc.createElement('label'),toggle=doc.createElement('input');label.className='phema-boundaries';toggle.type='checkbox';toggle.checked=layout.dataset.boundaries==='true';
  toggle.onchange=()=>{layout.dataset.boundaries=String(toggle.checked);};
  label.append(toggle,doc.createTextNode('Panel boundaries'));heading.append(label);
 }
}
