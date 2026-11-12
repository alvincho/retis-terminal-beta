/** Discardable UI projection; durable values belong to the server workspace. */
export function normalizeContentOverrides(value){
 const result={};if(!value||typeof value!=='object'||Array.isArray(value))return result;
 for(const [path,policy] of Object.entries(value).slice(0,256)){
  if(!/^[A-Za-z0-9_.:-]+(?:\/[A-Za-z0-9_.:-]+)*$/.test(path)||path.length>1200||!policy||typeof policy!=='object')continue;
  const clean={};
  if(['open','change','manual','interval','stream'].includes(policy.refresh?.trigger)){clean.refresh={trigger:policy.refresh.trigger};if(clean.refresh.trigger==='interval'){if(!Number.isInteger(policy.refresh.seconds)||policy.refresh.seconds<60||policy.refresh.seconds>86400)continue;clean.refresh.seconds=policy.refresh.seconds;}}
  for(const [key,min,max] of [['maxAgeSeconds',0,31536000],['timeoutSeconds',1,120]])if(Number.isFinite(policy[key])&&policy[key]>=min&&policy[key]<=max)clean[key]=policy[key];
  if(['retain','unavailable'].includes(policy.onFailure))clean.onFailure=policy.onFailure;
  // The data source the viewer chose for a data panel (an agent identity).
  if(typeof policy.sourceAgentId==='string'&&/^[A-Za-z0-9_.:-]{1,200}$/.test(policy.sourceAgentId))clean.sourceAgentId=policy.sourceAgentId;
  if(Object.keys(clean).length)result[path]=clean;
 }
 return result;
}
