"""Authenticated media reads from a registered Report Phemar only."""

# OBSOLETE BUSINESS IMPLEMENTATION — TO BE REMOVED AFTER OWNER CUTOVER.
# Owner: app_report. Do not extend this legacy Terminal implementation.
# See BUSINESS_LOGIC_OBSOLETE.json for replacements and retirement gates.
from urllib.parse import urlsplit, quote
from fastapi import HTTPException
from .contracts import identifier

MAX_MEDIA_BYTES=20*1024*1024
ALLOWED={'application/pdf','text/plain','text/html','image/png','image/jpeg','image/gif','image/webp','audio/mpeg','audio/wav','audio/ogg','video/mp4','video/webm'}

async def read_media(network,user,agent_id,report_id,media_id):
    for value in (agent_id,report_id,media_id):identifier(value)
    agent=await network.agent(agent_id,user['token']);await network.require_trusted(agent, user['token'])
    capability=next((c for c in agent['meta'].get('phemar_capabilities',[]) if c.get('id')=='report-generation'),None)
    if not capability:raise HTTPException(403,'Publisher is not a registered Report Phemar.')
    endpoint=urlsplit(capability.get('feature_endpoint') or '/api/report-phemar/feature')
    if endpoint.scheme or endpoint.netloc or endpoint.query or endpoint.fragment or not endpoint.path.startswith('/api/') or not endpoint.path.endswith('/feature') or any(x in endpoint.path for x in ('..','%','\\')):raise HTTPException(403,'Invalid report capability path.')
    path=endpoint.path.removesuffix('/feature')+'/media/'+quote(report_id,safe='')+'/'+quote(media_id,safe='')
    async with network.client.stream('GET',agent['address']+path,headers=await network.call_headers(agent,user['token'],'GET',path),follow_redirects=False,timeout=40) as response:
        if response.status_code!=200:raise HTTPException(response.status_code if response.status_code in (401,403,404) else 502,'Report media is unavailable.')
        mime=response.headers.get('content-type','').split(';')[0].lower()
        if mime not in ALLOWED:raise HTTPException(415,'This media format is not supported by the Terminal viewer.')
        content=bytearray()
        async for chunk in response.aiter_bytes():
            content.extend(chunk)
            if len(content)>MAX_MEDIA_BYTES:raise HTTPException(413,'Report media exceeds the 20 MB viewer limit. Open the original report instead.')
    return bytes(content),mime
