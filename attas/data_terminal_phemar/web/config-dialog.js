import {ui as uiHTML} from './i18n.js';
import {refreshFields} from './block-refresh.js';
import {api} from './connected.js';
import { PLAZA_PULSES, PLAZA_PUBLISHERS, filterPulses, pulseStatus, CATALOG_STATUS } from './plaza-pulses.js';
import { TEMPLATE_LABELS } from './layout-presentation.js';
import { validateLayoutEntry, normalizeLayoutEntry, bindLayoutEntry } from './layout-library.js';
import { canPlaceTemplate, replaceTemplatePulse, toComposableEntry } from './layout-composition.js';
import {DISPLAY_ROLES,initialMapping} from './display-mapping.js';

const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const gear = uiHTML`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m9.5 3-.7 2.4-2 .9-2.3-.6-2.5 4.2 1.7 1.8v2.2L2 15.7l2.5 4.2 2.3-.6 2 .9.7 2.4h5l.7-2.4 2-.9 2.3.6 2.5-4.2-1.7-1.8v-2.2L22 9.9l-2.5-4.2-2.3.6-2-.9L14.5 3Z" transform="translate(0 -1) scale(1 .95)"/><circle cx="12" cy="12" r="3"/></svg>`;
const displayType = node => node.type === 'phemar' ? 'pulse-chat' : node.template;

export function configButton(kind, label, { nodeId = '', requirementId = '', focus = '' } = {}) {
  return uiHTML`<button type="button" class="config-icon-button" data-config-kind="${kind}" data-node-id="${esc(nodeId)}" data-requirement-id="${esc(requirementId)}" data-config-focus="${esc(focus)}" aria-label="${esc(label)}" title="${esc(label)}" aria-haspopup="dialog">${gear}</button>`;
}

export function renderLayoutSettings(entry) {
  return uiHTML`<div class="config-summary-rows layout-settings-summary"><div class="config-summary-row"><div><small>Layout</small><strong>${esc(entry.name)}</strong></div>${configButton('layout','Configure layout',{focus:'layoutName'})}</div><div class="config-summary-row"><div><small>Default command</small><strong class="mono">${esc(entry.command)}</strong></div>${configButton('layout','Configure default command',{focus:'command'})}</div><div class="config-summary-row"><div><small>Description</small><span>${esc(entry.description || 'No description')}</span></div>${configButton('layout','Configure description',{focus:'description'})}</div></div>`;
}

export function renderRequirementSettings(entry) {
  return uiHTML`<section class="requirement-settings" aria-label="Layout data requirements"><div class="config-summary-heading"><h3>Data requirements <small>${entry.design.dataRequirements.length}</small></h3>${configButton('requirements','Configure data requirements')}</div><div class="config-summary-rows">${entry.design.dataRequirements.map(r => uiHTML`<div class="config-summary-row"><div><strong>${esc(r.label)}</strong><small>${r.mode === 'stream' ? 'Stream capable' : 'Snapshot'} · ${r.essential ? 'Required' : 'Optional'}${r.schema ? ` · ${esc(r.schema)}` : ''}</small></div>${configButton('requirements',`Configure dataset ${r.label}`,{requirementId:r.id})}</div>`).join('')}</div></section>`;
}

/** Modal-local drafts keep cancelled or invalid edits away from the canvas. */
export class ConfigDialog {
  constructor({ getDraft, onChange }) {
    this.getDraft = getDraft;
    this.onChange = onChange;
    this.state = null;
    this.dialog = document.createElement('dialog');
    this.dialog.id = 'config-dialog';
    this.dialog.className = 'config-dialog';
    document.body.append(this.dialog);
    document.addEventListener('click', event => {
      const button = event.target.closest('[data-config-kind]');
      if (button && !button.disabled && this.getDraft()) this.open(button);
    });
    this.dialog.addEventListener('cancel', event => { event.preventDefault(); this.close(); });
    this.dialog.addEventListener('click', event => {
      if (event.target === this.dialog) {
        const rect = this.dialog.getBoundingClientRect();
        if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) this.close();
      }
      const action = event.target.closest('[data-config-action]')?.dataset.configAction;
      if (action === 'close') this.close();
      if (action === 'refresh-pulses') document.dispatchEvent(new CustomEvent('catalog:refresh'));
      if (action === 'bind-pulse') {
        const pulse = PLAZA_PULSES.find(p => p.id === event.target.closest('[data-pulse-id]')?.dataset.pulseId);
        if (pulse) {
          const type = displayType(this.node());
          const next = replaceTemplatePulse(this.state.draft, this.state.nodeId, pulse.id, pulse.templates.includes(type)?type:pulse.templates[0]);
          if (next === this.state.draft) this.showErrors(['This pulse could not be bound. Check its contract and the layout requirements.']);
          else {this.state.draft=next;this.state.requirementId=this.node().requirementId;this.render();}
        }
      }
      const tab = event.target.closest('[data-config-tab]');
      if (tab) this.changeTab(tab.dataset.configTab);
    });
    this.dialog.addEventListener('keydown', event => {
      const tab = event.target.closest('[role="tab"]');
      if (!tab || !['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
      event.preventDefault();
      const tabs = [...this.dialog.querySelectorAll('[role="tab"]')];
      const index = tabs.indexOf(tab), next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : (index + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
      this.changeTab(tabs[next].dataset.configTab);
    });
    this.dialog.addEventListener('input', event => this.update(event.target));
    this.dialog.addEventListener('change', event => {
      if (!this.state || !this.dialog.contains(event.target)) return;
      if (event.target.id === 'config-dataset') {
        this.state.requirementId = event.target.value;
        this.render();
        this.dialog.querySelector('#config-dataset')?.focus();
      } else this.update(event.target);
    });
    this.dialog.addEventListener('submit', event => { event.preventDefault(); this.apply(); });
    document.addEventListener('catalog:updated',()=>{if(this.state?.tab==='data'&&!this.state.pending)this.render();});
  }

  open(button) {
    if (this.dialog.open) return;
    const draft = toComposableEntry(this.getDraft()), kind = button.dataset.configKind;
    const node = draft.design.layout.children.find(n => n.id === button.dataset.nodeId);
    if(node?.template!=='pulse-chat'&&(node?.referenceId||node?.media||node?.componentId)){import('./phema-editor.js').then(({openPhemaEditor})=>openPhemaEditor(draft,next=>this.onChange(next),node.id));return;}
    if (kind === 'template' && !node) return;
    this.state = { kind, nodeId:node?.id, pulseFilters:{query:'',publisherType:'',activity:''}, requirementId:button.dataset.requirementId || node?.requirementId || (kind!=='template'?draft.design.dataRequirements[0]?.id:undefined),
      tab:kind === 'requirements' ? 'data' : kind, draft, base:this.getDraft(), trigger:button, triggerData:{...button.dataset}, errors:[] };
    this.render();
    this.dialog.showModal();
    const focus = button.dataset.configFocus;
    const field = focus ? this.dialog.querySelector(`[name="${focus}"]`) : this.dialog.querySelector('input:not([readonly]),textarea,select:not([disabled])');
    field?.focus();
  }

  close() {
    if (!this.state) return;
    const {trigger,triggerData} = this.state;
    this.dialog.close();
    this.state = null;
    const replacement = [...document.querySelectorAll('[data-config-kind]')].find(button => ['configKind','nodeId','requirementId','configFocus'].every(key => button.dataset[key] === triggerData[key]));
    (trigger.isConnected ? trigger : replacement)?.focus({preventScroll:true});
  }

  node() { return this.state?.draft.design.layout.children.find(n => n.id === this.state.nodeId); }
  requirements() {
    const node = this.node(), all = this.state.draft.design.dataRequirements;
    return this.state.kind === 'template' ? all.filter(r => [node?.requirementId,...(node?.supplementalRequirementIds||[])].includes(r.id)) : all;
  }
  requirement() { return this.requirements().find(r => r.id === this.state.requirementId); }
  pulseBound(requirement) { return this.state.draft.design.layout.children.some(n => n.pulseId && n.requirementId === requirement?.id); }

  changeTab(tab) {
    if(this.state.pending)return;
    this.state.tab = tab;
    this.render();
    this.dialog.querySelector(`[data-config-tab="${tab}"]`)?.focus();
  }

  render() {
    const s = this.state, node = this.node();
    const title = s.kind === 'layout' ? 'Layout settings' : s.kind === 'template' ? 'Template settings' : 'Data requirements';
    const tabs = s.kind === 'layout' ? [['layout','Layout'],['data','Data']] : s.kind === 'template' ? [['template','Template'],['data','Data']] : [['data','Data']];
    this.dialog.setAttribute('aria-label',title);
    this.dialog.innerHTML = uiHTML`<form id="config-settings-form" novalidate><header class="config-dialog-header"><div class="config-dialog-title"><span class="config-dialog-symbol">${gear}</span><div><h2>${title}</h2><p>${esc(node?.title || s.draft.name)}</p></div></div><button class="config-icon-button" type="button" data-config-action="close" aria-label="Close settings" title="Close settings">×</button></header><nav class="config-dialog-tabs" role="tablist" aria-label="Settings sections">${tabs.map(([id,label]) => uiHTML`<button type="button" role="tab" id="config-tab-${id}" data-config-tab="${id}" aria-controls="config-panel" aria-selected="${s.tab===id}" tabindex="${s.tab===id?0:-1}">${label}</button>`).join('')}</nav><div class="config-dialog-body" id="config-panel" role="tabpanel" aria-labelledby="config-tab-${s.tab}" tabindex="0">${s.tab === 'layout' ? this.renderLayout() : s.tab === 'template' ? this.renderTemplate() : this.renderData()}<div class="config-errors" role="alert" tabindex="-1" ${s.errors.length ? '' : 'hidden'}>${s.errors.map(message => uiHTML`<p>${esc(message)}</p>`).join('')}</div></div><footer class="config-dialog-footer"><span>Apply to this draft. Save layout to keep changes.</span><div><button type="button" class="button subtle" data-config-action="close">Cancel</button><button type="submit" class="button primary">Apply changes</button></div></footer></form>`;
  }

  renderLayout() {
    const d = this.state.draft;
    return uiHTML`<section class="config-section"><h3>Layout identity</h3><p>Set the name and default context used when this layout opens.</p><div class="config-grid"><label>Layout name<input name="layoutName" aria-label="Layout name" value="${esc(d.name)}" maxlength="80" required/></label><label>Default command<input name="command" aria-label="Default command" value="${esc(d.command)}" maxlength="499" required/></label>${d.design.params.industry?uiHTML`<label>Industry<input name="industry" maxlength="80" value="${esc(d.design.params.industry.default)}"></label>`:''}<label class="config-field-wide">Description<textarea name="description" aria-label="Layout description" rows="3" maxlength="240">${esc(d.description)}</textarea></label></div></section>${d.design.params.research?uiHTML`<section class="config-section"><label>Research defaults (JSON)<textarea name="researchDefaults" rows="8">${esc(JSON.stringify(d.design.params.research.default,null,2))}</textarea></label></section>`:''}<section class="config-section"><h3>Tabbed view</h3><label class="config-checkbox"><input type="checkbox" data-config-tabs="enabled" ${d.design.layout.tabbedView?'checked':''}/> Enable tabbed view</label>${d.design.layout.tabbedView?uiHTML`<div class="config-grid"><label class="config-field-wide">Tab names, one per line<textarea data-config-tabs="names" aria-label="Tab names" rows="5">${esc(d.design.layout.tabbedView.tabs.map(t=>t.title).join('\n'))}</textarea></label>${[['column','Column',12],['row','Row',24],['columnSpan','Width',12],['rowSpan','Height',6]].map(([key,label,max])=>uiHTML`<label>Tabbed view ${label}<input type="number" data-config-tabs-position="${key}" aria-label="Tabbed view ${label}" value="${d.design.layout.tabbedView.position[key]}" min="1" max="${max}" step="any"/></label>`).join('')}</div><p>Assign templates to a tab in Template settings. Each tab has its own grid. The main canvas controls the table and tabbed view size.</p>`:''}</section>`;
  }

  renderTemplate() {
    const node = this.node(), pulse = PLAZA_PULSES.find(p => p.id === node.pulseId), type = displayType(node);
    const choices = pulse&&pulse.mode!=='interactive'?[...new Set([...pulse.templates,...Object.keys(DISPLAY_ROLES)])]:pulse?.templates || [type], pos = node.position;
    return uiHTML`<section class="config-section"><h3>Display</h3><p>${pulse ? `Choose a compatible display for ${esc(pulse.name)}.` : 'This preset uses its original display contract.'}</p><div class="config-grid"><label>Title<input data-config-node="title" aria-label="Template title" value="${esc(node.title)}" maxlength="80" required/></label><label>Display template<select data-config-node="display" aria-label="Template display">${choices.map(t => uiHTML`<option value="${t}" ${type===t?'selected':''}>${TEMPLATE_LABELS[t]}</option>`).join('')}</select></label></div>${type==='pulse-chat'?uiHTML`<label class="config-checkbox"><input type="checkbox" data-config-chat-target ${node.media?.allowTargetSelection!==false?'checked':''}> Allow target selection in Browser</label>`:''}</section>${this.requirements().filter(r=>type!=='pulse-chat').map(r=>uiHTML`<section class="config-section"><h3>${esc(r.label)} · Updates</h3><div class="config-grid" data-refresh-requirement="${esc(r.id)}">${refreshFields(r)}</div><p>Manual waits for Refresh or Generate in Data Browser. Timed intervals update independently. Templates sharing a dataset share its schedule.</p></section>`).join('')}${this.renderMapping()}${this.state.draft.design.layout.tabbedView?uiHTML`<section class="config-section"><label>Place in<select data-config-node="tabId" aria-label="Template tab"><option value="">Main layout</option>${this.state.draft.design.layout.tabbedView.tabs.map(t=>uiHTML`<option value="${esc(t.id)}" ${node.tabId===t.id?'selected':''}>${esc(t.title)}</option>`).join('')}</select></label></section>`:''}<section class="config-section"><h3>Position & size</h3><p>Positions start at 1. Fractional values allow precise placement. The tile must fit an empty part of the 12-column canvas.</p><div class="config-grid config-position-grid">${[['column','Column',12],['row','Row',24],['columnSpan','Width',12],['rowSpan','Height',6]].map(([key,label,max]) => uiHTML`<label>${label}<input type="number" data-config-position="${key}" data-template-position="${key}" aria-label="${label}" value="${pos[key]}" min="1" max="${max}" step="any" required/></label>`).join('')}</div></section>`;
  }

  renderMapping() {
    const node=this.node(),r=this.state.draft.design.dataRequirements.find(r=>r.id===node?.requirementId),roles=DISPLAY_ROLES[node?.template];
    if(!r||!roles)return '';
    const mapping=node.fieldMappings||initialMapping(node.template,r.fields);
    return uiHTML`<section class="config-section"><h3>Display fields</h3><p>Choose the field for each display slot. Values keep their source units.</p><div class="config-grid">${[...roles.required,...roles.optional].map(role=>uiHTML`<label>${esc(role)}${roles.required.includes(role)?' *':''}<select data-config-mapping="${esc(role)}" aria-label="Display field ${esc(role)}"><option value="">Not displayed</option>${r.fields.map(field=>uiHTML`<option value="${esc(field)}" ${mapping[role]===field?'selected':''}>${esc(field)}</option>`).join('')}</select></label>`).join('')}</div></section>`;
  }

  renderData() {
    const requirements = this.requirements(), r = this.requirement() || requirements[0];
    if (!r) return this.renderPulsePicker() + uiHTML`<section class="config-section"><h3>No data requirements</h3><p>Select a pulse above to bind this template’s data or interaction contract.</p></section>`;
    this.state.requirementId = r.id;
    const fixed = false, node = this.state.draft.design.layout.children.find(n => n.requirementId===r.id && n.pulseId);
    const pulse = PLAZA_PULSES.find(p => p.id === node?.pulseId), publisher = PLAZA_PUBLISHERS.find(p => p.id === pulse?.publisherId);
    return this.renderPulsePicker() + uiHTML`<section class="config-section"><h3>Data contract</h3><div class="model-contract-summary"><code>${esc(r.modelRef?r.modelRef.id+' @ '+r.modelRef.version:'Legacy schema')}</code></div><p>${fixed ? 'The pulse is a schema reference. Choose the required fields here; Data Browser maps each field to a source.' : 'Describe the shared data this layout needs. Data Browser chooses the sources.'}</p>${requirements.length > 1 ? uiHTML`<label class="config-dataset-select">Dataset<select id="config-dataset" aria-label="Dataset">${requirements.map(item => uiHTML`<option value="${esc(item.id)}" ${item.id===r.id?'selected':''}>${esc(item.label)}</option>`).join('')}</select></label>` : ''}${pulse ? uiHTML`<div class="config-pulse-context"><strong>${esc(pulse.name)}</strong><span>${esc(publisher?.name||'Unavailable publisher')}</span><code>${esc(pulse.schema)}</code></div>` : ''}<div class="config-grid"><label class="config-field-wide">Dataset name<input data-config-requirement="label" data-config-requirement-id="${esc(r.id)}" aria-label="Dataset name" value="${esc(r.label)}" maxlength="120" required/></label><label class="config-field-wide">Schema<input data-config-requirement="schema" data-config-requirement-id="${esc(r.id)}" aria-label="Dataset schema" value="${esc(r.schema||'')}" maxlength="160"></label><label class="config-field-wide">Fields, separated by commas<input data-config-requirement="fields" data-config-requirement-id="${esc(r.id)}" aria-label="Dataset fields" value="${esc(r.fields.join(', '))}" maxlength="1200"  required/></label><label>Update mode<select data-config-requirement="mode" data-config-requirement-id="${esc(r.id)}" aria-label="Dataset update mode" ${fixed?'disabled':''}><option value="snapshot" ${r.mode==='snapshot'?'selected':''}>Snapshot</option><option value="stream" ${r.mode==='stream'?'selected':''}>Stream capable</option><option value="interactive" ${r.mode==='interactive'?'selected':''}>User interaction</option></select></label><label class="config-checkbox"><input type="checkbox" data-config-requirement="essential" data-config-requirement-id="${esc(r.id)}" aria-label="Required dataset" ${r.essential?'checked':''}/> Required dataset</label>${['currency','units','frequency','timezone'].map(key=>uiHTML`<label>${esc(key)}<input aria-label="Dataset ${key}" data-config-requirement="${key}" data-config-requirement-id="${esc(r.id)}" value="${esc(r[key]||'')}" maxlength="80" placeholder="No additional constraint"></label>`).join('')}<label>Maximum source age (seconds)<input type="number" min="1" max="31536000" aria-label="Dataset maximum age" data-config-requirement="maxAgeSeconds" data-config-requirement-id="${esc(r.id)}" value="${r.maxAgeSeconds||''}"></label>${['refreshSeconds','limit'].map(key=>uiHTML`<label>${key==='refreshSeconds'?'Refresh interval (seconds)':'Maximum items'}<input type="number" min="${key==='refreshSeconds'?60:1}" max="${key==='refreshSeconds'?86400:100}" data-config-requirement="${key}" data-config-requirement-id="${esc(r.id)}" aria-label="Dataset ${key}" value="${r[key]||''}"></label>`).join('')}<label class="config-checkbox"><input type="checkbox" data-config-requirement="editableInBrowser" data-config-requirement-id="${esc(r.id)}" aria-label="Allow ticker editing in Browser" ${r.editableInBrowser?'checked':''}/> Allow ticker editing in Browser</label><label>Instrument binding<select data-config-requirement="binding" data-config-requirement-id="${esc(r.id)}"><option value="">Follow Browser command</option><option value="fixed" ${r.binding==='fixed'?'selected':''}>Fixed listing</option></select></label><label>Instruments<input data-config-requirement="instruments" data-config-requirement-id="${esc(r.id)}" value="${esc((r.instruments||[]).join(', '))}"></label>${r.dependsOn?uiHTML`<p class="config-field-wide">Commentary uses ${esc(r.pulseName)} after dataset ${esc(r.dependsOn)} refreshes. Terminal selects the Persona.</p>`:''}${r.parameterBindings?uiHTML`<label class="config-field-wide">Parameter bindings (JSON)<textarea data-config-requirement="parameterBindings" data-config-requirement-id="${esc(r.id)}" rows="4">${esc(JSON.stringify(r.parameterBindings,null,2))}</textarea></label>`:''}${this.renderArguments(r,pulse)}${r.modelRef?uiHTML`<label class="config-field-wide">Join policy (JSON)<textarea data-config-requirement="join" data-config-requirement-id="${esc(r.id)}" rows="3">${esc(JSON.stringify(r.join||{keys:[]},null,2))}</textarea></label>`:''}<label class="config-field-wide">Request arguments (JSON)<textarea data-config-requirement="arguments" data-config-requirement-id="${esc(r.id)}" aria-label="Dataset arguments" rows="4">${esc(JSON.stringify(r.arguments||{},null,2))}</textarea><small>Optional semantic inputs, such as dates or frequency. Credentials and transport addresses are not allowed.</small></label></div></section>`;
  }

  renderPulsePicker() {
    if (this.state.kind !== 'template') return '';
    const f=this.state.pulseFilters;
    return uiHTML`<section class="config-section"><h3>Plaza pulses <button type="button" class="text-button" data-config-action="refresh-pulses">Refresh catalog</button></h3><p>Select or replace this template’s pulse. The selected publication describes the contract; Data Browser chooses the execution source.</p><div class="config-grid"><label class="config-field-wide">Search pulses<input aria-label="Search template pulses" data-config-pulse-filter="query" value="${esc(f.query)}" placeholder="Name, field or publisher"/></label><label>Publisher type<select aria-label="Template pulse publisher type" data-config-pulse-filter="publisherType"><option value="">All Pulsers</option>${['data-source','persona','pulser'].map(t=>uiHTML`<option value="${t}" ${f.publisherType===t?'selected':''}>${t==='persona'?'Personas':t==='pulser'?'Pulsers':'Data sources'}</option>`).join('')}</select></label><label>Activity<select aria-label="Template pulse activity" data-config-pulse-filter="activity"><option value="">All activity states</option>${['active','inactive','unknown'].map(t=>uiHTML`<option value="${t}" ${f.activity===t?'selected':''}>${t}</option>`).join('')}</select></label></div><div id="config-pulse-results" class="config-pulse-results">${this.renderPulseResults()}</div></section>`;
  }

  renderPulseResults() {
    const results=filterPulses(this.state.pulseFilters);
    const warnings=[CATALOG_STATUS.error,...CATALOG_STATUS.warnings].filter(Boolean);
    return warnings.map(w=>uiHTML`<p role="status">${esc(w)}</p>`).join('')+uiHTML`<small>${results.length} matching pulses${results.length>100?' · showing first 100; narrow your search':''}</small>`+results.slice(0,100).map(p=>uiHTML`<button type="button" class="config-pulse-choice" data-config-action="bind-pulse" data-pulse-id="${esc(p.id)}" aria-pressed="${this.node()?.pulseId===p.id}"><strong>${esc(p.name)}</strong><span>${esc(PLAZA_PUBLISHERS.find(a=>a.id===p.publisherId)?.name||'Unavailable publisher')}</span><small>${esc(pulseStatus(p))} · ${esc(p.mode)}</small></button>`).join('')+(results.length?'':uiHTML`<p>No matching pulses. Clear filters or refresh the catalog.</p>`);
  }

  renderArguments(requirement,pulse) {
    if(!pulse)return '';
    const schema=pulse.inputSchema||{}, args=requirement.arguments||{};
    return Object.entries(schema.properties||{}).filter(([key])=>!/^(api_?key|token|password|secret|connection_?id|url|address|endpoint)$/i.test(key)).map(([key,s])=>{
      const label=(s.title||key)+(schema.required?.includes(key)?' *':''),v=args[key]??s.default;
      const attributes=`data-config-argument="${esc(key)}" data-argument-type="${esc(s.type||'string')}" aria-label="Pulse input ${esc(key)}"`;
      const field=s.enum?uiHTML`<select ${attributes}><option value="">Use default / context</option>${s.enum.map(e=>uiHTML`<option value="${esc(JSON.stringify(e))}" ${v===e?'selected':''}>${esc(e)}</option>`).join('')}</select>`:s.type==='boolean'?uiHTML`<select ${attributes}><option value="">Use default</option><option value="true" ${v===true?'selected':''}>True</option><option value="false" ${v===false?'selected':''}>False</option></select>`:['object','array'].includes(s.type)?uiHTML`<textarea ${attributes} rows="3">${esc(v===undefined?'':JSON.stringify(v,null,2))}</textarea>`:uiHTML`<input ${attributes} type="${['number','integer'].includes(s.type)?'number':s.format==='date'?'date':'text'}" ${s.type==='integer'?'step="1"':s.type==='number'?'step="any"':''} value="${esc(v??'')}" placeholder="${['symbol','ticker'].includes(key)?esc(requirement.instruments?.[0]||'Browser context'):'Optional unless required'}"/>`;
      return uiHTML`<label class="config-field-wide">${esc(label)}${field}${s.description?uiHTML`<small>${esc(s.description)}</small>`:''}</label>`;
    }).join('');
  }

  update(target) {
    if (!this.state || !this.dialog.contains(target)) return;
    if (target.dataset.configPulseFilter) {
      if (this.state.pulseFilters[target.dataset.configPulseFilter] === target.value) return;
      this.state.pulseFilters[target.dataset.configPulseFilter]=target.value;
      this.dialog.querySelector('#config-pulse-results').innerHTML=this.renderPulseResults();
      return;
    }
    const d = this.state.draft;
    if(target.name==='researchDefaults'){try{d.design.params.research.default=JSON.parse(target.value);}catch{d.design.params.research.default=target.value;}return;}
    if(target.name==='industry'&&d.design.params.industry){d.design.params.industry.default=target.value;return;}
    const schedule=target.closest('[data-refresh-requirement]');
    if(schedule){const r=d.design.dataRequirements.find(r=>r.id===schedule.dataset.refreshRequirement);const mode=schedule.querySelector('select').value;const input=schedule.querySelector('input');r.refreshMode=mode;input.disabled=mode!=='interval';if(mode==='interval')r.refreshSeconds=Number(input.value);else delete r.refreshSeconds;return;}
    if(target.dataset.configArgument){
      const r=this.requirement();if(!r)return;
      r.arguments=typeof r.arguments==='object'&&!Array.isArray(r.arguments)&&r.arguments?r.arguments:{};
      const key=target.dataset.configArgument,type=target.dataset.argumentType;
      if(target.value==='')delete r.arguments[key];
      else {try{r.arguments[key]=target.tagName==='SELECT'||['number','integer','boolean','object','array'].includes(type)?JSON.parse(target.value):target.value;}catch{r.arguments[key]=target.value;}}
      const json=this.dialog.querySelector('[data-config-requirement="arguments"]');if(json)json.value=JSON.stringify(r.arguments,null,2);
      return;
    }
    if (target.name === 'layoutName') { d.name=target.value; d.design.title=target.value; }
    if (target.name === 'description') d.description=target.value;
    if (target.name === 'command') d.command=target.value;
    if(target.dataset.configTabs==='enabled'){
      if(target.checked){d.design.schemaVersion='dbl/0.4';const row=Math.min(19,Math.max(1,...d.design.layout.children.map(n=>n.position.row+n.position.rowSpan)));d.design.layout.tabbedView={id:'tabs-'+crypto.randomUUID().slice(0,8),position:{column:1,row,columnSpan:12,rowSpan:4},tabs:[{id:'tab-1',title:'New tab'}]};}
      else {delete d.design.layout.tabbedView;d.design.layout.children.forEach(n=>delete n.tabId);}
      this.render();return;
    }
    if(target.dataset.configTabs==='names'){const old=d.design.layout.tabbedView.tabs;d.design.layout.tabbedView.tabs=target.value.split('\n').filter(t=>t.trim()).map((title,i)=>({id:old[i]?.id||'tab-'+(i+1),title:title.trim()}));}
    if(target.dataset.configTabsPosition)d.design.layout.tabbedView.position[target.dataset.configTabsPosition]=Number(target.value);
    const node = this.node();
    if(node&&target.hasAttribute('data-config-chat-target')){node.media??={};node.media.allowTargetSelection=target.checked;}
    if(node&&target.dataset.configNode==='tabId'){if(target.value)node.tabId=target.value;else delete node.tabId;}
    if (node && target.dataset.configNode === 'title') node.title=target.value;
    if (node && target.dataset.configNode === 'display') {
      const pulse = PLAZA_PULSES.find(p => p.id===node.pulseId), type=target.value;
      if (!(pulse&&pulse.mode!=='interactive'?[...new Set([...pulse.templates,...Object.keys(DISPLAY_ROLES)])]:pulse?.templates || [displayType(node)]).includes(type)) return;
      delete node.template; delete node.phemarType; delete node.instancePolicy; delete node.inputs;
      if (type==='discussion') Object.assign(node,{type:'template',template:'pulse-chat',media:{allowTargetSelection:true,...(pulse?{target:{agentId:pulse.publisherId,pulse:pulse.provenance.endpointId}}:{})}});
      else Object.assign(node,{type:'template',template:type,fieldMappings:initialMapping(type,this.requirement()?.fields)});
      const requirement=d.design.dataRequirements.find(r=>r.id===node.requirementId);
      if(['pulse-chat','discussion'].includes(type)){delete node.fieldMappings;node.media={allowTargetSelection:true,...(pulse?{target:{agentId:pulse.publisherId,pulse:pulse.provenance.endpointId}}:{})};if(requirement){requirement.mode='interactive';requirement.refreshMode='manual';}}else if(pulse&&requirement){requirement.mode=pulse.mode;delete node.media;}
      this.render();return;
    }
    if(node&&target.dataset.configMapping){node.fieldMappings??=initialMapping(node.template,this.requirement()?.fields);if(target.value)node.fieldMappings[target.dataset.configMapping]=target.value;else delete node.fieldMappings[target.dataset.configMapping];}
    if (node && target.dataset.configPosition) node.position[target.dataset.configPosition]=Number(target.value);
    const r = d.design.dataRequirements.find(item=>item.id===target.dataset.configRequirementId), field = target.dataset.configRequirement;
    if (r && ['label','schema','fields','mode','essential','arguments','parameterBindings','join','currency','units','frequency','timezone','maxAgeSeconds','refreshSeconds','limit','binding','instruments','editableInBrowser'].includes(field)) {
      
      if(['currency','units','frequency','timezone','maxAgeSeconds','refreshSeconds','limit','binding'].includes(field)){if(target.value==='')delete r[field];else r[field]=['maxAgeSeconds','refreshSeconds','limit'].includes(field)?Number(target.value):target.value;return;}
      if(field==='arguments'||field==='parameterBindings'||field==='join'){try{r[field]=JSON.parse(target.value);}catch{r[field]=target.value;}return;}
      if(field==='schema')for(const node of d.design.layout.children.filter(n=>n.requirementId===r.id))delete node.pulseId;
      r[field]=['essential','editableInBrowser'].includes(field)?target.checked:['fields','instruments'].includes(field)?target.value.split(',').map(v=>v.trim()).filter(Boolean):target.value;
    }
  }

  async apply() {
    const s = this.state;
    if (!s) return;
    if (this.getDraft() !== s.base) { this.showErrors(['The layout changed while this dialog was open. Close settings and reopen it to edit the current draft.']); return; }
    const candidate = s.draft, node = this.node(), errors=[];
    if (!candidate.name.trim()) errors.push('Enter a layout name.');
    if (!candidate.command.trim()) errors.push('Enter a default command.');
    if (node && !node.title.trim()) errors.push('Enter a template title.');
    if (node && !canPlaceTemplate(candidate,node.id,node.position)) errors.push('Choose a position and size that fit an empty part of the grid.');
    const validation = validateLayoutEntry(candidate);
    if (errors.length || !validation.valid) { this.showErrors(errors.length ? errors : validation.errors); return; }
    if(this.state.pending)return;
    const checks=candidate.design.dataRequirements.filter(r=>r.id===node?.requirementId||JSON.stringify(r)!==JSON.stringify(s.base.design.dataRequirements.find(old=>old.id===r.id))).flatMap(requirement=>{
      const bindings=candidate.design.layout.children.filter(n=>n.pulseId&&n.requirementId===requirement.id);
      return (bindings.length?bindings:[{}]).map(pulseNode=>({pulseNode,requirement}));
    });
    if(checks.length){
      this.state.pending=true;this.dialog.querySelector('#config-panel').inert=true;
      const submit=this.dialog.querySelector('[type="submit"]');submit.disabled=true;submit.textContent='Checking inputs…';
      try {for(const {pulseNode,requirement} of checks)await api('/api/validate-pulse-inputs',{method:'POST',body:{pulseId:pulseNode.pulseId,display:pulseNode.template==='pulse-chat'?'pulse-chat':undefined,arguments:requirement.arguments||{},instruments:requirement.instruments||[],constraints:Object.fromEntries(['currency','units','frequency','timezone','maxAgeSeconds'].filter(k=>requirement[k]!==undefined).map(k=>[k,requirement[k]]))}});}
      catch(error){if(this.state===s){s.pending=false;this.dialog.querySelector('#config-panel').inert=false;submit.disabled=false;submit.textContent='Apply changes';this.showErrors([error.message]);}return;}
      if(this.state!==s)return;
      s.pending=false;
    }
    const clean = normalizeLayoutEntry(candidate);
    if (clean.command !== s.base.command) clean.design=bindLayoutEntry(clean,clean.command).dbl;
    this.dialog.close();
    this.onChange(clean);
    this.close();
  }

  showErrors(errors) {
    this.state.errors=errors;
    const region = this.dialog.querySelector('.config-errors');
    region.hidden=false;
    region.innerHTML=errors.map(message=>uiHTML`<p>${esc(message)}</p>`).join('');
    region.focus();
  }
}
