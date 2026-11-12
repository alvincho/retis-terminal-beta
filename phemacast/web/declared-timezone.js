/** Render explicit publisher context. Never infer a venue or instrument. */
export function declaredTimezone(context={}){
 for(const value of [context.timeZone,context.timezone,context.display?.timeZone]){
  if(typeof value!=='string'||value.length>100)continue;
  try{new Intl.DateTimeFormat('en',{timeZone:value});return value;}catch{}
 }
 return null;
}
