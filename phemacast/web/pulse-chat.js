/** Shared Pulse chat presentation. Hosts supply discovery, translation and execution. */
export const PULSE_CHAT_CAPABILITY={id:'phemacast:pulse-chat',version:'1.0.0'};
export const secretField=key=>/password|secret|(?:^|_)token(?:$|_)|api.?key|credential|connection.?id|^__proto__$|^constructor$|^prototype$/i.test(key);
const escape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export function messageField(schema={},config={}){
 const properties=schema.properties||{};
 if(config.messageField)return properties[config.messageField]?config.messageField:null;
 return ['incoming_text','message','query','prompt','text','question'].find(k=>properties[k]&&(!properties[k].type||properties[k].type==='string'))||(properties.messages?.type==='array'?'messages':null);
}
export function targetKey(agentId,pulseId){return JSON.stringify([agentId||'',pulseId||'']);}
export function selectTarget(catalog,child,saved={}){
 const config=child.media||{},pinned=child.type==='phemar'?child.pulseId:null;
 let candidates=catalog.pulses.filter(p=>p.executable!==false);
 if(pinned)candidates=candidates.filter(p=>p.id===pinned);
 if(config.allowTargetSelection===false)candidates=candidates.filter(p=>p.publisherId===config.target?.agentId&&p.provenance?.endpointId===config.target?.pulse);
 const available=p=>p.activity!=='inactive'&&!['attachment-required','missing','unavailable'].includes(p.credentialStatus)&&p.access!=='Attachment required';
 const preferred=candidates.find(p=>p.id===child.pulseId)||candidates.find(p=>p.publisherId===config.target?.agentId&&p.provenance?.endpointId===config.target?.pulse);
 const publishers=catalog.publishers.filter(p=>candidates.some(c=>c.publisherId===p.id));
 const agentId=publishers.some(p=>p.id===saved.agentId)?saved.agentId:preferred?.publisherId||candidates.find(available)?.publisherId||publishers[0]?.id||'';
 const pulses=candidates.filter(p=>p.publisherId===agentId);
 const pulse=pulses.find(p=>p.id===saved.pulseId)||pulses.find(p=>p.id===preferred?.id)||pulses.find(available)||pulses[0];
 return {publishers,pulses,agentId,pulse,available:!!pulse&&available(pulse),fixed:!!pinned||config.allowTargetSelection===false};
}
export function argumentFields(schema={},config={}){
 const message=messageField(schema,config),properties=schema.properties||{};
 return Object.entries(properties).filter(([key])=>key!==message&&key!==config.historyField&&key!==config.contextField&&!secretField(key));
}
export function parseArguments(fields,raw){
 const args=raw===undefined?Object.create(null):JSON.parse(raw||'{}');
 if(!args||typeof args!=='object'||Array.isArray(args))throw new Error('Pulse arguments must be an object.');
 for(const field of fields){if(field.value==='')continue;if(secretField(field.key))throw new Error('Configure credentials through the host connection settings.');
  args[field.key]=field.type==='string'?field.value:JSON.parse(field.value);
 }
 return args;
}
export function validateChatConfig(config={}){
 if(!config||typeof config!=='object'||Array.isArray(config)||Object.keys(config).some(k=>!['target','allowTargetSelection','arguments','messageField','historyField','contextField'].includes(k)))throw new Error('Invalid Pulse chat configuration.');
 if(config.allowTargetSelection!==undefined&&typeof config.allowTargetSelection!=='boolean')throw new Error('Invalid Pulse chat target selection.');
 if(config.allowTargetSelection===false&&!config.target)throw new Error('A fixed Pulse chat requires a registered Pulser and Pulse.');
 if(config.target&&(!config.target.agentId||!config.target.pulse||Object.keys(config.target).some(k=>!['agentId','pulse'].includes(k))))throw new Error('Choose a registered Pulser and Pulse.');
 return config;
}
export function chatForm({catalog,child,saved={},arguments:defaults={},draft='',browserId,snapshot=false,label=escape}){
 const original=child.media||{},target=selectTarget(catalog,child,saved),pulse=target.pulse;
 const targetMatches=!original.target||(original.target.agentId===target.agentId&&original.target.pulse===pulse?.provenance?.endpointId);
 const config=targetMatches?original:Object.fromEntries(Object.entries(original).filter(([k])=>!['arguments','messageField','historyField','contextField'].includes(k)));
 const schema=pulse?.inputSchema||{},properties=schema.properties||{},message=messageField(schema,config);
 const active=saved.targets?.[targetKey(target.agentId,pulse?.id)]||(saved.agentId===target.agentId&&(!saved.pulseId||saved.pulseId===pulse?.id)?saved:{});
 const same=child.pulseId?child.pulseId===pulse?.id:targetMatches;
 const values={...(same?defaults:{}),...(config.arguments||{})};
 const fields=argumentFields(schema,config);
 const raw=!schema.properties||['oneOf','anyOf','allOf'].some(k=>schema[k]);
 const option=(v,name,selected)=>`<option value="${escape(v)}" ${selected?'selected':''}>${escape(name)}</option>`;
 const field=([key,definition])=>{
  const type=typeof definition.type==='string'?definition.type:'json',value=active.arguments?.[key]??(typeof (values[key]??definition.default)==='object'?JSON.stringify(values[key]??definition.default):values[key]??definition.default??'');
  const attrs=`data-argument="${escape(key)}" data-argument-type="${escape(type)}" ${schema.required?.includes(key)?'required':''}`;
  let input;
  if(definition.enum||type==='boolean'){const entries=definition.enum||(type==='boolean'?[true,false]:[]);input=`<select ${attrs}><option value=""></option>${entries.map(v=>option(type==='string'?v:JSON.stringify(v),String(v),String(value)===(type==='string'?String(v):JSON.stringify(v)))).join('')}</select>`;}
  else if(['object','array','json'].includes(type))input=`<textarea ${attrs} rows="2" spellcheck="false">${escape(value)}</textarea>`;
  else input=`<input ${attrs} type="${['number','integer'].includes(type)?'number':'text'}" ${type==='integer'?'step="1"':'step="any"'} value="${escape(value)}" ${definition.minimum!==undefined?`min="${escape(definition.minimum)}"`:''} ${definition.maximum!==undefined?`max="${escape(definition.maximum)}"`:''}>`;
  return `<label title="${escape(definition.description||'')}">${escape(definition.title||key)}${schema.required?.includes(key)?' *':''}${input}</label>`;
 };
 return `<form data-conversation="${escape(browserId)}"><input name="nodeId" type="hidden" value="${escape(child.id)}"><div class="pulse-chat-targets"><label>${label('Pulser')}<select name="agentId" ${target.fixed?'disabled':''}>${target.publishers.map(p=>option(p.id,p.name,p.id===target.agentId)).join('')}</select></label><label>${label('Pulse')}<select name="pulseId" ${target.fixed?'disabled':''}>${target.pulses.map(p=>option(p.id,p.name,p.id===pulse?.id)).join('')}</select></label></div><p class="panel-note">${pulse?escape(pulse.description||''):label('No connected Pulses available.')}${pulse?.access?`${pulse.description?' · ':''}${label(pulse.access)}`:''}</p><div class="pulse-chat-arguments">${raw?`<label>${label('Arguments (JSON)')}<textarea data-raw-arguments rows="3" spellcheck="false">${escape(active.rawArguments||JSON.stringify(values))}</textarea></label>`:fields.map(field).join('')}</div><label ${message?'':'hidden'}>${label('Message')}<textarea name="text" rows="2" maxlength="2000" ${schema.required?.includes(message)?'required':''}>${escape(active.text??draft)}</textarea></label>${snapshot?`<p class="panel-note">${label('Snapshot mode is read-only.')}</p>`:''}<div class="pulse-chat-actions"><button class="button" data-chat-submit ${!target.available||snapshot?'disabled':''}>${label(message?'Send':'Run Pulse')}</button><span class="pulse-chat-feedback" role="status"></span></div></form>`;
}
