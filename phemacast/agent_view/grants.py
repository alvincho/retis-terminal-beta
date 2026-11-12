"""Host-only adapter to Prompits' existing verified operation-grant endpoint."""
from copy import deepcopy
import inspect
import math
import re
import time

from prompits.core.call_grants import operation_binding
from prompits.core.capabilities import digest


class VerifiedOperationGrantIssuer:
    """Trusted host supplies current original-user and service authentication.

    Credentials are used only for Plaza admission, never placed in owner view
    messages, manifests or publisher operation requests. This does not create an
    identity, infer owner ACLs, grant paid execution or widen downstream policy.
    """
    def __init__(self, client, *, user_context, service_headers,service_actor_id=None,dependency_admission=None):
        self.client = client
        self.user_context = user_context
        self.service_headers = service_headers
        self.service_actor_id=service_actor_id;self.dependency_admission=dependency_admission

    async def onward_limits(self,*,actor,audience,operation_ref,value,dependencies,context):
        compiler=self.dependency_admission
        if not callable(compiler):
            raise PermissionError('Declared dependency field/call limits require an owner enforcement adapter.')
        inputs={'actor':actor,'publisherAgentId':audience,'operationRef':operation_ref,
            'inputDigest':digest(value),'manifestDigest':context.get('manifest_digest')}
        decision=compiler({**deepcopy(inputs),'input':deepcopy(value),'dependencies':deepcopy(dependencies)})
        decision=await decision if inspect.isawaitable(decision) else decision
        if (not isinstance(decision,dict) or any(decision.get(key)!=expected for key,expected in inputs.items())
                or not re.fullmatch(r'[a-f0-9]{64}',str(decision.get('provenanceDigest','')))):
            raise PermissionError('Owner dependency provenance is missing or bound to different input.')
        limits=decision.get('onwardLimits')
        if not isinstance(limits,list) or len(limits)>32:
            raise PermissionError('Owner dependency admission must cover the declared exact targets.')
        calls={}
        for limit in limits:
            if not isinstance(limit,dict) or set(limit)!={'audience','operationRef','maxCalls','fields'}:
                raise PermissionError('Owner dependency limits have an unsupported shape.')
            index=next((index for index,dep in enumerate(dependencies) if dep['agentId']==limit['audience'] and dep['ref']==limit['operationRef']),None)
            if (index is None or type(limit['maxCalls']) is not int
                    or not 1<=limit['maxCalls']<=dependencies[index]['maxCalls']
                    or not isinstance(limit['fields'],list) or any(not isinstance(field,str) or not field.startswith('/') for field in limit['fields'])
                    or not set(limit['fields']).issubset(dependencies[index]['fields'])):
                raise PermissionError('Owner dependency policy exceeds its manifest ceilings.')
            calls[index]=calls.get(index,0)+limit['maxCalls']
            if calls[index]>dependencies[index]['maxCalls']:raise PermissionError('Owner dependency call totals exceed the manifest ceiling.')
        return deepcopy(limits)

    async def headers(self,actor):
        value=self.user_context(actor)
        identity,headers=await value if inspect.isawaitable(value) else value
        if identity!=actor or set(headers)!={'Authorization'} or not headers['Authorization']:
            raise PermissionError('A current original-user Plaza session is required.')
        value=self.service_headers();service=await value if inspect.isawaitable(value) else value
        if set(service)!={'X-Agent-Token'} or not service['X-Agent-Token']:
            raise PermissionError('Verified host service admission is required.')
        return {**headers,**service}

    @staticmethod
    def validate_response(grant,*,actor,audience,operation_ref,request_binding,
                          operation=None,service_actor=None,continuation=None):
        if (not isinstance(grant,dict) or not re.fullmatch(r'pc-call-[A-Za-z0-9_-]{16,128}',str(grant.get('token','')))
                or not grant.get('id') or type(grant.get('expiresAt')) not in (int,float)
                or not math.isfinite(grant['expiresAt']) or grant['expiresAt']<=time.time() or grant.get('user_id')!=actor
                or grant.get('audience')!=audience or grant.get('operation_ref')!=operation_ref
                or grant.get('request_binding')!=request_binding or not grant.get('service_actor_id')
                or (operation is not None and grant.get('operation')!=operation)
                or (service_actor is not None and grant.get('service_actor_id')!=service_actor)
                or (continuation is not None and grant.get('continuationOf')!=continuation)):
            raise PermissionError('Plaza grant response does not match the original user and exact request.')
        return deepcopy(grant)

    async def __call__(self, *, actor, audience, operation_ref, wire, value,
                       input_binding, intent_id, resource_refs, max_points,
                       dependencies, **context):
        limits=await self.onward_limits(actor=actor,audience=audience,operation_ref=operation_ref,
            value=value,dependencies=dependencies,context=context) if dependencies else None
        headers=await self.headers(actor)
        payload = {'audience': audience, 'operation_ref': deepcopy(operation_ref),
                   'resource_refs': deepcopy(resource_refs), 'intent_id': intent_id,
                   'max_points': max_points, **{k: wire[k] for k in ('method', 'path', 'query', 'body')}}
        if input_binding == 'http-envelope':
            payload['input'] = deepcopy(value)
        if limits is not None:payload['onward_limits']=limits
        response = await self.client.post('/api/operation-grants', headers=headers, json=payload,follow_redirects=False)
        if response.status_code != 200:
            
            try:detail=response.json().get('detail','Verified operation admission rejected this exact request.')
            except ValueError:detail='Verified operation admission rejected this exact request.'
            detail=detail if isinstance(detail,str) else 'Operation admission rejected the selected request.'
            detail=re.sub(r'[a-fA-F0-9]{8}(?:-[a-fA-F0-9]{4}){3}-[a-fA-F0-9]{12}','selected resource',detail.splitlines()[0][:500])
            raise PermissionError(detail)
        return self.validate_response(response.json(),actor=actor,audience=audience,operation_ref=operation_ref,
            request_binding=operation_binding(wire['method'],wire['path'],wire['body'],wire['query']),
            operation=context.get('contract_operation'),service_actor=self.service_actor_id)

    async def continuation(self,*,actor,receipt):
        """Trusted host/status adapter only; no frame renew/mutation endpoint.

        Receipt metadata comes from the encrypted host journal, never an owner
        view message. Renewal changes only transport lifetime; it cannot select
        a new body, intent, recipient, resource, point ceiling or field policy.
        """
        if not isinstance(receipt,dict) or receipt.get('user_id')!=actor:
            raise PermissionError('Continuation requires the original host receipt.')
        response=await self.client.post('/api/operation-grants/continuations',headers=await self.headers(actor),
            json={'grant_id':receipt['id']},follow_redirects=False)
        if response.status_code!=200:raise PermissionError('Current user/resource authority denied continuation.')
        return self.validate_response(response.json(),actor=actor,audience=receipt['audience'],
            operation_ref=receipt['operation_ref'],request_binding=receipt['request_binding'],
            operation=receipt['operation'],service_actor=receipt['service_actor_id'],continuation=receipt['id'])
