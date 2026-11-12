/** Publishers own semantic mappings; only structural binding safety is checked. */
export const DISPLAY_ROLES={};
export function fieldSchema(schema,path){let current=schema?.items||schema;for(const part of path.split('.'))current=current?.properties?.[part];return current||{};}
export function mappingErrors(node){const mapping=node.fieldMappings;if(mapping===undefined)return [];if(!mapping||typeof mapping!=='object'||Array.isArray(mapping)||Object.keys(mapping).length>100)return ['Invalid display binding.'];return Object.entries(mapping).every(([key,path])=>key.length<=200&&typeof path==='string'&&path.length<=200&&!path.split('.').some(part=>['__proto__','constructor','prototype'].includes(part)))?[]:['Invalid display binding.'];}
export function initialMapping(){return {};}
export function displayRows(rows){return rows;}
