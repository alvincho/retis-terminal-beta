/** This browser's key for a Terminal host (its login cookies count only with it).

Browsers send a loopback host's cookies to every port on it, so another local service can receive this
Terminal's HttpOnly login cookies. Storage is kept per origin, port included: the key lives here and goes
only in a header on this Terminal's own requests, so a cookie that reaches another service is no login
without it, and the key alone is none without the cookie. Clearing site data forgets it; signing in again
makes a new one. */
export const BROWSER_KEY_HEADER='X-Terminal-Browser-Key';
const STORAGE='phemacast.terminal-browser-key.v1';
const valid=value=>typeof value==='string'&&/^[A-Za-z0-9_-]{43,128}$/.test(value);
let memory='';

function storages(){
  const found=[];
  for(const name of ['localStorage','sessionStorage']){try{if(globalThis[name])found.push(globalThis[name]);}catch{}}
  return found;
}

function fresh(){
  const bytes=new Uint8Array(32);globalThis.crypto.getRandomValues(bytes);
  return btoa(String.fromCharCode(...bytes)).replaceAll('+','-').replaceAll('/','_').replace(/=+$/,'');
}

/** Read on every request, so tabs that start together settle on the one key the browser kept. */
export function browserKey(){
  for(const storage of storages()){
    try{const value=storage.getItem(STORAGE);if(valid(value))return memory=value;}catch{}
  }
  if(!valid(memory))memory=fresh();
  for(const storage of storages()){
    try{storage.setItem(STORAGE,memory);if(storage.getItem(STORAGE)===memory)break;}catch{}
  }
  return memory;
}
