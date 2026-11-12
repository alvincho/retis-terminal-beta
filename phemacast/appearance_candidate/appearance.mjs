/** Data-only presentation tokens. No URLs, scripts, authority or application logic. */
export const TONES=['navy','teal','blue','amber','rose','slate'];
export function appearanceClass(extensions){
 const v=extensions?.['phemacast:appearance'];
 if(!v||v.version!==1||!TONES.includes(v.tone))return '';
 const variants=['plain','key-grid','rows'];
 return 'phema-color-panel tone-'+v.tone+' presentation-'+(variants.includes(v.variant)?v.variant:'plain');
}
export function rowTone(value){return TONES.includes(value)?'tone-'+value:'';}

export function appearanceFor(panel,entry){
 return panel.extensions || entry?.extensions?.['phemacast:layout']?.design?.layout?.children?.find(n=>n.id===panel.id)?.extensions;
}
