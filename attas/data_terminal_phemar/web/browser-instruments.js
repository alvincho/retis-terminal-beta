import {normalizeNamedLists} from './input-state.js';
/** Preserve legacy opaque values; application publishers own validation. */
export const normalizeInstrumentOverrides=normalizeNamedLists;
export function browserDesign(design){return structuredClone(design);}
