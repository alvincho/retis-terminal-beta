"""UNINSTALLED generic neutral-form operation adapter candidate.

No routes, registrations, business schemas, domain stores, owner JavaScript or
network destinations are installed here. Host adapters must supply current user,
immutable registry admission, selected mounted reference, paired transport and
zero-point scope. Owner agents remain responsible for ACLs, CAS and operations.
"""
from copy import deepcopy
import asyncio
import inspect
import logging
import re
import secrets

from prompits.core.capabilities import digest
from prompits.core.call_grants import operation_binding
from prompits.core.operation_contracts import validate_operation
from prompits.core.operation_input import project_operation_input
from prompits.core.resource_registry import reference as operation_reference, validate_ref
from phemacast.core.composition import validate_reference
from phemacast.core.schema_security import validate_async
from phemacast.agent_view.broker import ReceiptJournal

EXTENSION = 'phemacast:operation-binding'
SCHEMA = 'phemacast:pulse-operation-binding/1'
CAPABILITY = {'id':'phemacast:neutral-operation-form','version':'1.0.0',
    'features':['immutable-operation','typed-pulse-envelope','known-wire-prepare',
                'observe-only-recovery','revision','zero-points','selected-refresh']}
JOB_CAPABILITY = {**CAPABILITY, 'version':'1.1.0',
    'features':CAPABILITY['features']+['explicit-job-admission','opaque-revision']}
SECRET_KEYS = {'password','token','secret','apikey','accesstoken','refreshtoken',
               'authorization','cookie','callerplazatoken','callerusertoken',
               'clientsecret','privatekey','prototype','constructor','proto'}
logger = logging.getLogger(__name__)


async def returned(value):
    return await value if inspect.isawaitable(value) else value


def refusal(exc):
    """The owner's own short reason for refusing a call (an HTTP 4xx with a text detail), or None."""
    response = getattr(exc, 'response', None)
    status = getattr(response, 'status_code', None)
    if not isinstance(status, int) or not 400 <= status < 500:
        return None
    try:
        detail = response.json().get('detail')
    except Exception:
        return None
    if not isinstance(detail, str) or not detail.strip():
        return None
    return re.sub(r'[\x00-\x1f\x7f]+', ' ', detail).strip()[:300]


def no_secrets(value):
    if isinstance(value,dict):
        for key,item in value.items():
            if re.sub('[^a-z]','',str(key).lower()) in SECRET_KEYS:
                raise PermissionError('Credentials/code controls are not form inputs or results.')
            no_secrets(item)
    elif isinstance(value,list):
        for item in value:no_secrets(item)


def bind_reference(ref, contract, receipt, *, revision_field=None):
    """Owning agent must call with its actual published immutable resources.

    This is not runtime name lookup or an authority grant. Presentation schema
    may narrow/label fields; execution still validates the full pinned contract.
    """
    out=deepcopy(validate_reference(deepcopy(ref)))
    contract=validate_operation(contract);receipt=validate_operation(receipt)
    if (out['kind']!='pulse' or out['target']!=
            {'agentId':contract['publisher']['agentId'],'pulse':contract['operation']}):
        raise PermissionError('Reference must name the exact operation publisher/selector.')
    if (receipt['publisher']!=contract['publisher'] or any(receipt['effects'].values())
            or receipt.get('inputBinding')!='http-envelope'):
        raise PermissionError('Receipt must be the same build/publisher and have no effects.')
    if contract['constraints']['expectedRevision'] != bool(revision_field):
        raise ValueError('Owner must explicitly bind the required revision field.')
    if revision_field and not re.fullmatch('[A-Za-z][A-Za-z0-9_]{0,99}',revision_field):
        raise ValueError('Revision field must be a single declared flat field.')
    binding={'schema':SCHEMA,'operationRef':operation_reference(contract),
        'receiptOperationRef':operation_reference(receipt),
        'inputCodec':'prompits:pulse-http-envelope/1','sourceModes':['parameters']}
    if revision_field:binding['revisionField']=revision_field
    out.setdefault('extensions',{})[EXTENSION]=binding
    # Presentation classification only. The host must compare these effects
    # against the freshly resolved immutable contract before any dispatch.
    out['extensions']['phemacast:operation-effects']=deepcopy(contract['effects'])
    return out


def binding_of(ref):
    validate_reference(ref)
    value=deepcopy(ref.get('extensions',{}).get(EXTENSION))
    required={'schema','operationRef','receiptOperationRef','inputCodec','sourceModes'}
    if (not isinstance(value,dict) or set(value)-required-{'revisionField'}
            or not required<=set(value) or value['schema']!=SCHEMA
            or value['inputCodec']!='prompits:pulse-http-envelope/1'
            or value['sourceModes']!=['parameters']):
        raise PermissionError('Owner-published exact neutral operation binding required.')
    validate_ref(value['operationRef']);validate_ref(value['receiptOperationRef'])
    return value


class PulseFormSession:
    def __init__(self,*,actor,service_actor,window,mount,phema_digest,
                 reference_reader,resolver,current_context,authorize_reference,issuer,transport,journal,
                 allow_writes=False,allow_jobs=False):
        if (not all(isinstance(x,str) and x for x in
                    [actor,service_actor,window,mount,phema_digest])
                or not isinstance(journal,ReceiptJournal)
                or type(allow_writes) is not bool or type(allow_jobs) is not bool):
            raise PermissionError('Actual original user/service/window/mount and host journal required.')
        if not all(callable(x) for x in [reference_reader,resolver,current_context,authorize_reference,issuer,transport]):
            raise PermissionError('Current registry, user and paired host adapters are mandatory.')
        self.actor,self.service_actor,self.window,self.mount=actor,service_actor,window,mount
        self.phema_digest=phema_digest;self.reference_reader=reference_reader
        self.resolver=resolver;self.current_context=current_context
        self.authorize_reference=authorize_reference
        self.issuer,self.transport,self.journal=issuer,transport,journal
        self.allow_writes=allow_writes;self.allow_jobs=allow_jobs;self.plans={};self.lock=asyncio.Lock()
        self.closed=False;self.generation=0

    def close(self):
        self.closed=True;self.generation+=1;self.plans.clear()

    async def current(self):
        context=await returned(self.current_context())
        expected={'actor':self.actor,'serviceActor':self.service_actor,'window':self.window,
                  'mount':self.mount,'phemaDigest':self.phema_digest,'sourceMode':'parameters'}
        if self.closed or context!=expected:
            raise PermissionError('Original user/window/Phema authority changed or mount closed.')

    async def resource(self,ref):
        resolved=await returned(self.resolver(ref))
        if (resolved.get('ref')!=ref or resolved.get('verification')!='tested'
                or operation_reference(resolved['resource'])!=ref):
            raise PermissionError('Current exact tested immutable contract required.')
        resource=validate_operation(resolved['resource'])
        if (resource.get('inputBinding')!='http-envelope' or resource['transport']!=
                {'method':'POST','path':'/use_practice/get_pulse_data'}):
            raise PermissionError('Only the declared generic Pulse HTTP envelope is supported.')
        return resource

    async def admitted(self,ref,resource):
        decision=await returned(self.authorize_reference(self.actor,self.service_actor,
            self.window,self.mount,self.phema_digest,deepcopy(ref),deepcopy(resource)))
        if decision is not True:raise PermissionError('Current host Phema/reference approval required.')

    def envelope(self,resource,inputs):
        return {'path':{},'query':{},'body':{'sender':self.service_actor,
            'receiver':resource['publisher']['agentId'],'msg_type':'use_practice',
            'content':{'pulse_name':resource['operation'],'params':deepcopy(inputs)},
            'caller_agent_address':{'pit_id':self.service_actor,'plazas':[]},
            'caller_protocol_version':'1.0'}}

    def receipt_scope(self,bound):
        return digest([self.actor,self.service_actor,self.window,
                       self.mount,self.phema_digest,bound['operationRef']])

    async def recoverable(self,refs):
        await self.current();result={}
        for path,ref in refs.items():
            if not ref.get('interaction'):continue
            bound=binding_of(ref)
            if not bound:continue
            known=[r for r in self.journal.recoverable(self.receipt_scope(bound)) if r['reference']==path]
            if known:result[path]=known
        return result

    async def prepare(self,request):
        if (not isinstance(request,dict) or set(request)!={'reference','inputs','requestKey'}
                or not re.fullmatch('[A-Za-z0-9_-]{16,128}',str(request['requestKey']))):
            raise ValueError('Selected reference, typed inputs and stable request key required.')
        await self.current();ref=deepcopy(await returned(self.reference_reader(request['reference'])))
        bound=binding_of(ref);resource=await self.resource(bound['operationRef'])
        if ref['target']!={'agentId':resource['publisher']['agentId'],'pulse':resource['operation']}:
            raise PermissionError('Phema target differs from its immutable operation.')
        await self.admitted(ref,resource)
        inputs=deepcopy(ref.get('inputs',{}))
        if ref.get('interaction'):
            await validate_async(ref['interaction']['inputSchema'],request['inputs'])
            inputs.update(request['inputs'])
        elif request['inputs']:
            raise PermissionError('Read-only reference inputs cannot be changed.')
        no_secrets(inputs)
        prohibited={'externalSend','paidExecution','opensStream'}
        if any(resource['effects'][key] for key in prohibited):
            raise PermissionError('This zero-point form scope does not authorize send/pay/stream effects.')
        if resource['effects']['startsJob'] and not self.allow_jobs:
            raise PermissionError('Current host job admission is required before starting a job.')
        effect=resource['effects']['writesState']
        if effect and not self.allow_writes:raise PermissionError('Host did not authorize state mutation.')
        revision_field=bound.get('revisionField')
        if resource['constraints']['expectedRevision']!=bool(revision_field):
            raise PermissionError('Required revision is not bound by the owning agent.')
        if revision_field:
            revision=inputs.get(revision_field)
            integer=type(revision) is int and revision>=0
            opaque=self.allow_jobs and isinstance(revision,str) and 1<=len(revision)<=200
            if not (integer or opaque):
                raise ValueError('Exact owner revision must be a nonnegative integer or admitted bounded opaque value.')
        value=self.envelope(resource,inputs);wire=project_operation_input(resource,value)
        key=request['requestKey'];scope=self.receipt_scope(bound)
        call={'reference':request['reference'],'operationRef':bound['operationRef'],
            'input':value,'wire':wire,'expectedRevision':inputs.get(revision_field),'sourceMode':'parameters'}
        receipt={'intentId':key,'wireDigest':operation_binding(**wire),'executionReplayAllowed':False}
        async with self.lock:
            if any(r['reference']==request['reference'] and r['requestKey']!=key
                   for r in self.journal.recoverable(scope)):
                raise PermissionError('Observe the original unresolved operation before submitting another one.')
            previous=self.journal.reserve(scope,key,call)
            if previous:return {**receipt,'state':previous['status'],'prepareOnly':True}
            self.journal.remember_grant(scope,{'id':digest([scope,key]),
                **receipt,'operationRef':bound['operationRef'],'sourceMode':'parameters'})
            plan={'scope':scope,'key':key,'referenceKey':request['reference'],'ref':ref,'binding':bound,'resource':resource,
                  'value':value,'wire':wire,'receipt':receipt,'generation':self.generation,
                  'state':'prepared','grant':None}
            self.plans[key]=plan
        # Admission may compile owner-current dependencies; nothing is executed.
        grant=await self.issuer(actor=self.actor,audience=resource['publisher']['agentId'],
            operation_ref=bound['operationRef'],wire=wire,value=value,input_binding='http-envelope',
            intent_id=key,resource_refs=[],max_points=0,dependencies=[],
            contract_operation=resource['operation'])
        await self.current()
        if self.generation!=plan['generation']:raise PermissionError('Mount changed before dispatch admission.')
        plan['grant']=grant
        return {**receipt,'state':'prepared','prepareOnly':True,'effects':deepcopy(resource['effects'])}

    async def invoke(self,request):
        if not isinstance(request,dict) or set(request)!={'reference','requestKey','wireDigest'}:
            raise ValueError('Invoke only the caller-known prepared intent and wire.')
        await self.current();key=request['requestKey'];plan=self.plans.get(key)
        if not plan or request['reference']!=plan['referenceKey'] or request['wireDigest']!=plan['receipt']['wireDigest']:
            raise PermissionError('Exact prepared caller-known wire unavailable; observe, never replay.')
        if await self.resource(plan['binding']['operationRef'])!=plan['resource']:
            raise PermissionError('Operation resource changed before dispatch.')
        if deepcopy(await returned(self.reference_reader(plan['referenceKey'])))!=plan['ref']:
            raise PermissionError('Selected Phema reference changed before dispatch.')
        await self.admitted(plan['ref'],plan['resource'])
        async with self.lock:
            if plan['state']!='prepared' or plan['grant'] is None:
                return {**plan['receipt'],'state':plan['state'],'executionReplayAllowed':False}
            if self.generation!=plan['generation']:raise PermissionError('Prepared mount generation is stale.')
            if any(plan['resource']['effects'].values()):
                self.journal.begin_recovery(plan['scope'],key,{'reference':plan['referenceKey'],
                    'requestKey':key,'wireDigest':plan['receipt']['wireDigest']})
            plan['state']='uncertain'  # Reserve before the only transport call.
            if any(plan['resource']['effects'].values()):self.generation+=1
            generation=self.generation
        stage='transport'
        try:
            result=await self.transport(plan['resource'],plan['wire'],plan['grant'])
            stage='current-mount'
            await self.current()
            if self.generation!=generation:raise PermissionError('Late result belongs to an invalidated read generation.')
            stage='current-resource'
            if await self.resource(plan['binding']['operationRef'])!=plan['resource']:
                raise PermissionError('Current operation admission changed before result disclosure.')
            stage='current-admission'
            await self.admitted(plan['ref'],plan['resource'])
            stage='result-validation'
            await validate_async(plan['resource']['outputSchema'],result);no_secrets(result)
        except Exception as exc:
            # Operational diagnosis only: never log inputs, results, identities,
            # bearer credentials or exception messages that may contain them.
            logger.warning('Protected operation acknowledgement withheld: stage=%s error_type=%s',
                           stage,type(exc).__name__)
            withheld={**plan['receipt'],'state':'uncertain','executionReplayAllowed':False}
            # An effect-free read the owner refused ran nothing: the viewer who asked sees the owner's
            # reason instead of a bare failure. It is never logged.
            reason=refusal(exc) if stage=='transport' and not any(plan['resource']['effects'].values()) else None
            return {**withheld,'refusal':reason} if reason else withheld
        # Do not cache/re-disclose application payloads from this host journal.
        acknowledgement={**plan['receipt'],'state':'complete','executionReplayAllowed':False}
        self.journal.complete(plan['scope'],key,acknowledgement);plan['state']='complete'
        self.journal.reconciled(plan['scope'],key)
        return {**acknowledgement,'value':deepcopy(result)}

    async def observe(self,request):
        if not isinstance(request,dict) or set(request)!={'reference','intentId','wireDigest'}:
            raise ValueError('Status requires the selected reference and caller-known original wire.')
        await self.current();ref=deepcopy(await returned(self.reference_reader(request['reference'])))
        bound=binding_of(ref);resource=await self.resource(bound['receiptOperationRef'])
        original=await self.resource(bound['operationRef'])
        await self.admitted(ref,original)
        if resource['publisher']!=original['publisher'] or any(resource['effects'].values()):
            raise PermissionError('Observe-only exact publisher receipt operation required.')
        if (not re.fullmatch('[A-Za-z0-9_-]{16,128}',str(request['intentId']))
                or not re.fullmatch('[a-f0-9]{64}',str(request['wireDigest']))):
            raise ValueError('Original caller-known intent and wire required.')
        scope=self.receipt_scope(bound)
        known=self.journal.grant_receipt(scope,digest([scope,request['intentId']]))
        if known.get('wireDigest')!=request['wireDigest'] or known.get('operationRef')!=bound['operationRef']:
            raise PermissionError('Status must use this window/mount/reference original known wire.')
        inputs={'intent':request['intentId'],'wire_digest':request['wireDigest']}
        value=self.envelope(resource,inputs);wire=project_operation_input(resource,value)
        # A fresh read admission observes the same original command. Reusing
        # the read intent would expire its authority while a long job runs.
        intent='observe-'+secrets.token_hex(24)
        grant=await self.issuer(actor=self.actor,audience=resource['publisher']['agentId'],
            operation_ref=bound['receiptOperationRef'],wire=wire,value=value,input_binding='http-envelope',
            intent_id=intent,resource_refs=[],max_points=0,dependencies=[],
            contract_operation=resource['operation'])
        generation=self.generation
        result=await self.transport(resource,wire,grant)
        await self.current()
        if generation!=self.generation:raise PermissionError('View changed during receipt observation.')
        await self.admitted(ref,original)
        await validate_async(resource['outputSchema'],result);no_secrets(result)
        if result.get('state') in ('complete','completed','failed','cancelled'):
            self.journal.reconciled(scope,request['intentId'])
        return {'intentId':request['intentId'],'wireDigest':request['wireDigest'],
                'executionReplayAllowed':False,'receipt':result}
