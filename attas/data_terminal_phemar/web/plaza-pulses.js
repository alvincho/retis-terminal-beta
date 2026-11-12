/** Authorized Plaza publication metadata, refreshed without retrieving data. */
const freeze = (value) => {
  Object.values(value).forEach((item) => { if (item && typeof item === 'object') freeze(item); });
  return Object.freeze(value);
};

export let PLAZA_PUBLISHERS = [];

const pulse = (id, name, description, publisherId, category, schema, fields, mode, templates) => ({
  id, name, description, publisherId, category, schema, fields, mode, templates,
});

export let PLAZA_PULSES = [];
export let CATALOG_STATUS = {warnings:[], loadedAt:null, error:''};
export function setPulseCatalog(catalog){PLAZA_PULSES=catalog.pulses||[];PLAZA_PUBLISHERS=catalog.publishers||[];CATALOG_STATUS={warnings:catalog.warnings||[],loadedAt:Date.now(),error:''};}
export function setCatalogError(error){CATALOG_STATUS={...CATALOG_STATUS,error:String(error)};}
export function pulseStatus(pulse) {
  return [pulse.activity || 'Activity unknown', pulse.executable === false ? 'Catalog only' : pulse.executable === true ? 'Executable' : 'Execution unknown', pulse.access || 'Access unchecked'].join(' · ');
}

export function filterPulses({ query = '', publisherType = '', publisherId = '', category = '', mode = '', activity = '', executable = '' } = {}) {
  const terms = typeof query === 'string' ? query.toLowerCase().trim().split(/\s+/).filter(Boolean) : [];
  return PLAZA_PULSES.filter((entry) => {
    const publisher = PLAZA_PUBLISHERS.find((item) => item.id === entry.publisherId) || {name:"Unavailable publisher"};
    if (publisherType && publisher.type !== publisherType) return false;
    if (publisherId && publisher.id !== publisherId) return false;
    if (category && entry.category !== category) return false;
    if (mode && entry.mode !== mode) return false;
    if (activity && entry.activity !== activity) return false;
    if (executable && entry.executable !== (executable === 'true')) return false;
    const haystack = [entry.name, entry.description, entry.category, entry.schema, publisher.name, ...entry.fields, ...entry.templates].join(' ').toLowerCase();
    return terms.every((term) => haystack.includes(term));
  });
}

export {TEMPLATE_GEOMETRY_EPSILON,validTemplatePosition,templatePositionsOverlap} from './layout-geometry.js';
