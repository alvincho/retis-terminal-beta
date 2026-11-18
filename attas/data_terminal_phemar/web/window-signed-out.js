// Why this tab's window was signed out, kept across the reload that follows so the signed-out panel can say so
// instead of looking like a window that was never signed in. It is this tab's own discardable UI state: cleared once
// the window is signed in again or signs out on purpose, and forgotten after a day.
const KEY='terminal-window-signed-out';
const DAY=86400000;
// The Terminal's answer when the window signed out in another tab while a request was on its way.
const ELSEWHERE='This window signed out. Sign in again.';
export function noteSignedOut(detail){
  try{sessionStorage.setItem(KEY,JSON.stringify({elsewhere:detail===ELSEWHERE,at:Date.now()}));}catch{}
}
export function signedOutNote(){
  try{const note=JSON.parse(sessionStorage.getItem(KEY)||'null');return note&&Date.now()-note.at<DAY?note:null;}catch{return null;}
}
export function clearSignedOut(){try{sessionStorage.removeItem(KEY);}catch{}}
