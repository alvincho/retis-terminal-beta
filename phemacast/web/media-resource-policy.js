/** Only reviewed origins may receive automatic, credentialless media requests. */
export function permittedResource(value,policy){
 try{const url=new URL(value);if(!['https:','http:'].includes(url.protocol)||url.username||url.password||!policy?.approved)return null;
  if(url.protocol==='http:'&&!policy.localOrigins?.includes(url.origin))return null;
  return [...policy.mediaOrigins||[],...policy.localOrigins||[]].includes(url.origin)?url.href:null;
 }catch{return null;}
}
export async function boundedAsset(url,{maxBytes=20_000_000,signal,authenticatedLocal=false}={}){
 const timeout=AbortSignal.timeout(15000);
 const local=authenticatedLocal&&new URL(url,location.href).origin===location.origin;
 const response=await fetch(url,{credentials:local?'same-origin':'omit',referrerPolicy:'no-referrer',redirect:'error',signal:signal?AbortSignal.any([signal,timeout]):timeout});
 if(!response.ok||Number(response.headers.get('content-length'))>maxBytes)throw new Error('Media unavailable or exceeds size limit');
 const reader=response.body.getReader(),parts=[];let count=0;
 try{while(true){const {done,value}=await reader.read();if(done)break;count+=value.byteLength;if(count>maxBytes)throw new Error('Media exceeds size limit');parts.push(value);}}
 catch(error){await reader.cancel();throw error;}
 return new Blob(parts,{type:response.headers.get('content-type')||'application/octet-stream'});
}
