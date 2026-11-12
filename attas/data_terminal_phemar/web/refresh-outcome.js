// A ↻ the Terminal accepted fetches in the background. When its run ends with the panel still showing
// earlier data, the page says so, as it does for a ↻ the Terminal could not accept.
const WATCH_MS=120000;

export function watchRefresh(watches,browserId,remoteId,reference,answer,now=Date.now()){
 if(Number.isInteger(answer?.run))watches.set(browserId+'/'+reference,{browserId,remoteId,reference,run:answer.run,until:now+WATCH_MS});
}

// The references whose watched run has ended without new data; ended and expired watches are dropped.
export function failedRefreshes(watches,browserId,remoteId,state,now=Date.now()){
 const failed=[];
 for(const [key,watch] of watches){
  if(watch.browserId!==browserId)continue;
  if(watch.remoteId!==remoteId||watch.until<now){watches.delete(key);continue;}
  if(!((state?.manualRuns?.[watch.reference]||0)>=watch.run))continue;
  watches.delete(key);
  const result=state.content?.results?.[watch.reference];
  if(result?.status==='stale'&&result.reason!=='manual-dependency-changed')failed.push(watch.reference);
 }
 return failed;
}
