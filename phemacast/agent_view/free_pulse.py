"""Generic exact-reference relay for free, effect-free supplied-input analytics.

Host discovery and scoped transport are injected. No application schemas,
calculation implementations, source selection or local fallback live here.
"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urlsplit
from fastapi import HTTPException, Request
from jsonschema import Draft202012Validator, FormatChecker
import httpx
from jsonschema.exceptions import SchemaError


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def declared(agent):
    output=[]
    for pulse in agent.get('meta',{}).get('supported_pulses',[]):
        policy=pulse.get('extensions',{}).get('phemacast:operation',{})
        if (pulse.get('pulse_class')!='analytic' or type(pulse.get('cost')) not in (int,float) or pulse['cost']!=0 or policy.get('read_only') is not True
            or policy.get('effects')!=[] or policy.get('onward_authority')!=[]):continue
        pointer=policy.get('input_sha256_pointer')
        if not isinstance(pointer,str) or not pointer.startswith('/'):continue
        if not all(pulse.get(k) for k in ('id','version','input_schema','output_schema')):continue
        def local_references(value):
            if isinstance(value,dict):
                for key,child in value.items():
                    if key in ('$ref','$dynamicRef','$recursiveRef') and (not isinstance(child,str) or not child.startswith('#')):
                        raise ValueError('Operation schemas must use inline or local references.')
                    local_references(child)
            elif isinstance(value,list):
                for child in value:local_references(child)
        try:
            for schema in (pulse['input_schema'],pulse['output_schema']):
                Draft202012Validator.check_schema(schema);local_references(schema)
        except (SchemaError,ValueError):continue
        ref={'publisher_id':agent['id'],'operation_id':pulse['id'],'version':pulse['version'],
             'definition_sha256':digest(pulse)}
        output.append({'reference':ref,'pulse_name':pulse.get('pulse_name') or pulse['name'],
                       'input_schema':deepcopy(pulse['input_schema']),'output_schema':deepcopy(pulse['output_schema']),
                       'input_sha256_pointer':pointer,
                       'binding':deepcopy(pulse.get('extensions',{}).get('phemacast:phema-binding',{}))})
    return output


def mount_free_pulses(app, principal, network, *, paired_connections=None):
    paired=[]
    if paired_connections:
        paired=json.loads(Path(paired_connections).read_text())
        if not isinstance(paired,list) or len(paired)>8:raise ValueError('Configure at most eight paired operation agents.')
        for row in paired:
            url=urlsplit(row['address'])
            if url.scheme!='http' or url.hostname not in ('127.0.0.1','localhost','::1') or not url.port or url.path or url.query or url.fragment or url.username:
                raise ValueError('Paired operation agents require a configured loopback origin.')
            if not row.get('publisher_id'):raise ValueError('Pin the paired publisher identity before enabling operations.')
    async def available(user):
        try:rows=await network.trusted_agents(user['token'])
        except (HTTPException,httpx.HTTPError):rows=[]
        rows=[row for row in rows if row.get('availability')=='online']
        for connection in paired:
            try:
                async with httpx.AsyncClient(trust_env=False) as client:
                    response=await client.get(connection['address']+'/api/agent-operations/descriptor',timeout=5,follow_redirects=False)
                response.raise_for_status();descriptor=response.json()
                if descriptor['id']!=connection['publisher_id'] or descriptor['name']!=connection['name']:continue
                rows.append({**descriptor,'address':connection['address'],'_paired':connection,
                             'connection_id':connection['id'],'availability':'online'})
            except (httpx.HTTPError,ValueError,KeyError):continue
        return rows

    @app.get('/api/agent-operations')
    async def operations(request: Request):
        user=await principal(request)
        return {'publishers':[{'id':row.get('connection_id',row['id']),'name':row['name'],'operations':declared(row)}
                for row in await available(user) if declared(row)]}

    @app.post('/api/agent-operations/invoke')
    async def invoke(request: Request):
        user=await principal(request)
        if len(await request.body())>300000:raise HTTPException(413,'Operation inputs are too large.')
        try:
            body=await request.json()
            if not isinstance(body,dict) or set(body)!={'reference','parameters'}:raise ValueError('Supply an exact operation reference and typed parameters.')
            ref=body['reference']
            if not isinstance(ref,dict) or set(ref)!={'publisher_id','operation_id','version','definition_sha256'} or not all(isinstance(v,str) and v for v in ref.values()):
                raise ValueError('Supply an immutable publisher and operation reference.')
            agent=next((row for row in await available(user) if row['id']==ref['publisher_id']),None)
            if agent is None:raise HTTPException(409,'Selected agent is unavailable. Refresh its operation catalog.')
            operation=next((op for op in declared(agent) if op['reference']==ref),None)
            if operation is None:raise HTTPException(409,'Operation reference changed or its effects are not permitted. Refresh its catalog.')
            expected_input=digest(body['parameters'])
            errors=list(Draft202012Validator(operation['input_schema'],format_checker=FormatChecker()).iter_errors(body['parameters']))
            if errors:raise ValueError('Inputs do not match the selected operation schema.')
            # Scoped transport rechecks resource ACLs and binds the original
            # user and exact request. A lost response never triggers replay.
            if agent.get('_paired'):
                credential=Path(agent['_paired']['credential_file'])
                if credential.stat().st_mode & 0o077:raise HTTPException(503,'Paired credentials must remain private to the server account.')
                token=json.loads(credential.read_text())['direct_token']
                payload={'sender':network.registration.agent_id,'receiver':agent['id'],'msg_type':'get_pulse_data',
                         'caller_agent_address':{'pit_id':network.registration.agent_id,'plazas':[]},
                         'caller_direct_token':token,'caller_user_token':user['token'],
                         'content':{'pulse_name':operation['pulse_name'],'params':body['parameters']}}
                async with httpx.AsyncClient(trust_env=False) as client:
                    response=await client.post(agent['address']+'/use_practice/get_pulse_data',json=payload,
                        headers={'X-Phemacast-Operation-Reference':json.dumps(ref,separators=(',',':'))},timeout=40,follow_redirects=False)
                response.raise_for_status();result=response.json()
            else:
                result=await network.pulse(agent,operation['pulse_name'],body['parameters'],user['token'])
            for _ in range(5):
                if isinstance(result,dict) and result.get('status') in ('ok','success') and isinstance(result.get('result'),dict):result=result['result']
                else:break
            if list(Draft202012Validator(operation['output_schema'],format_checker=FormatChecker()).iter_errors(result)):
                raise HTTPException(502,'Agent response does not match its pinned operation schema.')
            bound=result
            try:
                for key in operation['input_sha256_pointer'][1:].split('/'):
                    bound=bound[key.replace('~1','/').replace('~0','~')]
            except (KeyError,TypeError,IndexError):
                raise HTTPException(502,'Agent response is missing its declared exact-input binding.')
            if bound!=expected_input:raise HTTPException(502,'Agent response refers to different operation inputs.')
            return {'reference':ref,'result':result}
        except (ValueError,TypeError,KeyError) as exc:raise HTTPException(422,str(exc)) from exc
        except httpx.HTTPStatusError as exc:
            status=exc.response.status_code
            raise HTTPException(status if status in {401,403,404,409,422} else 502,'Selected agent rejected this operation.') from exc
        except httpx.HTTPError as exc:raise HTTPException(502,'Selected agent is unreachable. No operation was replayed.') from exc
