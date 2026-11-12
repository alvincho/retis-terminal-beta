"""Read current published layouts from their independent App Consultants."""

# OBSOLETE BUSINESS IMPLEMENTATION — TO BE REMOVED AFTER OWNER CUTOVER.
# Owner: app_consultant. Do not extend this legacy Terminal implementation.
# See BUSINESS_LOGIC_OBSOLETE.json for replacements and retirement gates.
import asyncio
import httpx
from fastapi import HTTPException
from .contracts import layout_operation
from .phema_transfer import from_entry, to_entry

async def check_layout_updates(network,client,user,requests):
    if not isinstance(requests,list) or len(requests)>384 or any(not isinstance(r,dict) or not isinstance(r.get('layoutId'),str) or len(r['layoutId'])>100 or any(k in r and (not isinstance(r[k],str) or len(r[k])>120) for k in ('agentId','publicationId')) for r in requests):
        raise HTTPException(422,'Provide at most 384 saved layout references.')
    agents=await network.consultants(user['token'])
    async def read(agent):
        agent_id=agent['meta']['data_terminal']['id']
        try:
            await network.require_trusted(agent, user['token'])
            response=await network.agent_request(agent,user['token'],'GET','/api/layouts',timeout=8)
            response.raise_for_status()
            data=response.json()
            builtins=[{'entry':entry,'layoutRevision':1} for entry in data.get('entries',[]) if entry.get('id','').startswith('builtin-')]
            return {'agentId':agent_id,'status':'available','records':[*data.get('published',[]),*builtins]}
        except httpx.HTTPStatusError as exc:
            return {'agentId':agent_id,'status':'unavailable','records':[],
                    'error':'App Consultant connection returned HTTP '+str(exc.response.status_code)+'.'}
        except Exception:
            return {'agentId':agent_id,'status':'unavailable','records':[],
                    'error':'App Consultant connection could not be completed.'}
    catalogs=await asyncio.gather(*(read(a) for a in agents))
    results=[];validated={}
    for index,ref in enumerate(requests):
        eligible=[c for c in catalogs if not ref.get('agentId') or c['agentId']==ref['agentId']]
        matches=[(c,r) for c in eligible for r in c['records'] if r.get('entry',{}).get('id')==ref['layoutId'] and (not ref.get('publicationId') or r.get('publicationId')==ref['publicationId'])]
        if len(matches)!=1:
            status='ambiguous' if len(matches)>1 else 'unavailable' if not eligible or any(c['status']=='unavailable' for c in eligible) else 'not-published'
            results.append({'index':index,'status':status});continue
        catalog,record=matches[0]
        key=(catalog['agentId'],record.get('publicationId'),record['entry']['id'])
        try:
            if key not in validated:
                entry=(await layout_operation('validate',entry=record['entry']))['entry']
                validated[key]=to_entry(from_entry(entry,secure=True))
            results.append({'index':index,'status':'available','entry':validated[key],'agentId':catalog['agentId'],'publicationId':record.get('publicationId'),'revision':record['layoutRevision']})
        except Exception:results.append({'index':index,'status':'invalid'})
    return {'results':results,'warnings':[c['error'] for c in catalogs if c.get('error')]}
