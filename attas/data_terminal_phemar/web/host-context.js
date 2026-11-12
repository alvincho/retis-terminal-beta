/** Provider and host origins remain distinct while embedded in Attas. */
export function providerOrigin(location) {
 const encoded=location.pathname.match(/^\/phemar-proxy\/([^/]+)(?:\/|$)/)?.[1];
 if(!encoded)return location.origin;
 try{const value=decodeURIComponent(encoded),url=new URL(value);return url.origin===value&&['http:','https:'].includes(url.protocol)?value:location.origin;}catch{return location.origin;}
}
export function hostedPath(path,location) {return (location.pathname.match(/^\/phemar-proxy\/[^/]+/)?.[0]||'')+path;}
export function consultantURL(raw,location) {
 const url=new URL(raw,location.href);
 // Local services share host-scoped HttpOnly sessions across ports. Keep the
 // browser's loopback hostname while retaining the registered provider origin
 // separately in terminalOrigin (localhost and 127.0.0.1 have different cookies).
 const loopback=host=>['localhost','127.0.0.1','[::1]'].includes(host);
 if(!location.pathname.startsWith('/phemar-proxy/')&&loopback(new URL(location.href).hostname)&&loopback(url.hostname)&&url.protocol===location.protocol)url.hostname=new URL(location.href).hostname;
 return location.pathname.startsWith('/phemar-proxy/')?new URL(location.origin+'/phemar-proxy/'+encodeURIComponent(url.origin)+url.pathname+url.search+url.hash):url;
}
