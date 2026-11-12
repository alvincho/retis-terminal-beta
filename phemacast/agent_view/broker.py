"""Closed, actor-bound generic transport candidate. No business routes/stores.

Every deployment supplies current owner authorization, exact registry resolution,
scoped grant issuance, trusted transport and owner status/stream adapters. Missing
adapters fail closed. This module does not register HTTP routes or capabilities.
"""
from copy import deepcopy
import asyncio
import json
from pathlib import Path
import secrets
import sqlite3
import time
from cryptography.fernet import Fernet
from prompits.core.capabilities import canonical, digest, negotiate
from prompits.core.call_grants import operation_binding
from prompits.core.operation_input import project_operation_input
from .contracts import (check, exact, manifest_digest, resolve_contracts,
                        validate_manifest, verify_assets,refresh_binding)


class ReceiptJournal:
    """Generic dispatch receipts; owner remains authoritative for application state.

    Crash after reservation is uncertain. Never dispatch again from that receipt.
    Retained results are encrypted and only released after fresh owner ACL checks.
    """
    def __init__(self, directory):
        directory=Path(directory);directory.mkdir(parents=True,exist_ok=True,mode=0o700);directory.chmod(0o700)
        self.path=directory/'receipts.sqlite';key=directory/'receipt.key'
        if not key.exists():
            try:
                with key.open('xb') as out:out.write(Fernet.generate_key())
            except FileExistsError:pass
        key.chmod(0o600);self.cipher=Fernet(key.read_bytes())
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS calls(scope TEXT,key TEXT,fingerprint TEXT,state TEXT,result TEXT,PRIMARY KEY(scope,key))')
            db.execute('CREATE TABLE IF NOT EXISTS operation_receipts(id TEXT PRIMARY KEY,scope TEXT,metadata TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS unresolved_calls(scope TEXT,key TEXT,metadata TEXT,PRIMARY KEY(scope,key))')
        self.path.chmod(0o600)

    def connect(self):return sqlite3.connect(self.path,timeout=10)
    def reserve(self,scope,key,value):
        fingerprint=digest(value)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT fingerprint,state,result FROM calls WHERE scope=? AND key=?',(scope,key)).fetchone()
            if row:
                if row[0]!=fingerprint:raise ValueError('Request key conflicts with its original input.')
                return {'status':row[1], 'value':json.loads(self.cipher.decrypt(row[2].encode())) if row[2] else None}
            db.execute('INSERT INTO calls VALUES(?,?,?,?,?)',(scope,key,fingerprint,'uncertain',None))
        return None
    def complete(self,scope,key,result):
        encrypted=self.cipher.encrypt(canonical(result).encode()).decode()
        with self.connect() as db:db.execute('UPDATE calls SET state=?,result=? WHERE scope=? AND key=?',('completed',encrypted,scope,key))
    def observe(self,scope,key):
        with self.connect() as db:row=db.execute('SELECT state FROM calls WHERE scope=? AND key=?',(scope,key)).fetchone()
        return {'status':row[0] if row else 'unknown','executionReplayAllowed':False}
    def remember_grant(self,scope,metadata):
        if 'token' in metadata:raise ValueError('Never journal bearer transport credentials.')
        value=self.cipher.encrypt(canonical(metadata).encode()).decode()
        with self.connect() as db:db.execute('INSERT INTO operation_receipts VALUES(?,?,?)',(metadata['id'],scope,value))
    def grant_receipt(self,scope,identity):
        with self.connect() as db:row=db.execute('SELECT metadata FROM operation_receipts WHERE scope=? AND id=?',(scope,identity)).fetchone()
        if not row:raise PermissionError('Observer receipt unavailable in this user/view scope.')
        return json.loads(self.cipher.decrypt(row[0].encode()))

    def begin_recovery(self,scope,key,metadata):
        """Remember coordinates before dispatch; never retain replay authority."""
        if set(metadata)!={'reference','requestKey','wireDigest'} or metadata['requestKey']!=key:
            raise ValueError('Exact caller-known recovery coordinates required.')
        encrypted=self.cipher.encrypt(canonical(metadata).encode()).decode()
        with self.connect() as db:
            db.execute('INSERT INTO unresolved_calls VALUES(?,?,?)',(scope,key,encrypted))

    def recoverable(self,scope):
        with self.connect() as db:
            rows=db.execute('SELECT metadata FROM unresolved_calls WHERE scope=? LIMIT 65',(scope,)).fetchall()
        if len(rows)>64:raise PermissionError('Unresolved operation limit exceeded.')
        return [json.loads(self.cipher.decrypt(row[0].encode())) for row in rows]

    def reconciled(self,scope,key):
        with self.connect() as db:db.execute('DELETE FROM unresolved_calls WHERE scope=? AND key=?',(scope,key))


def pointer(value,path):
    for field in path.split('/')[1:]:
        field=field.replace('~1','/').replace('~0','~')
        value=value[int(field)] if isinstance(value,list) else value[field]
    return value


class AgentViewSession:
    def __init__(self,manifest,*,actor,mount_id,approved_digest,assets,resolver,authorize,
                 issue_grant,transport,journal,advertisement,parameters=None,
                 source_mode='parameters',allow_local=False,egress_guard=False,approval=None):
        self.manifest=validate_manifest(manifest,allow_local=allow_local)
        self.digest=manifest_digest(self.manifest)
        if not actor or not mount_id or approved_digest!=self.digest:raise PermissionError('Exact actor/view release approval required.')
        if not negotiate(manifest['requirements'],advertisement)['compatible']:raise ValueError('Required client capability is unsupported.')
        if not isinstance(approval,dict) or approval.get('actor')!=actor or approval.get('mountId')!=mount_id or approval.get('manifestDigest')!=self.digest or approval.get('expiresAt',0)<=time.time():
            raise PermissionError('Current actor/mount-bound approval required.')
        for disclosure in ('codeExecution','privateData','localNetwork'):
            if self.manifest['disclosure'][disclosure] and approval.get(disclosure) is not True:
                raise PermissionError('Separate '+disclosure+' approval required.')
        self.code=verify_assets(self.manifest,assets)
        self.actor,self.mount_id=actor,mount_id
        self.resolver,self.authorize,self.issue_grant,self.transport=resolver,authorize,issue_grant,transport
        self.contracts=resolve_contracts(self.manifest,resolver)
        self.egress_guard=egress_guard
        if any(o['classification']=='confidential' for o in self.manifest['operations']) and not self._guard_valid():
            raise PermissionError('Confidential mounting requires a separately verified egress guard.')
        self.parameters=check(manifest['parameterSchema'],parameters or {})
        def no_credentials(value):
            if isinstance(value,dict):
                for key,item in value.items():
                    if str(key).lower() in {'password','access_token','refresh_token','api_key','apikey','authorization','cookie','caller_plaza_token','caller_user_token','caller_direct_token','token','secret','client_secret','session_token','scope_token','escrow_token','reservation_token','grant_token','bearer_token','private_key'}:
                        raise PermissionError('Credentials cannot be disclosed to owner view code.')
                    no_credentials(item)
            elif isinstance(value,list):
                for item in value:no_credentials(item)
        no_credentials(self.parameters)
        self.no_credentials=no_credentials
        self.source_mode=source_mode;self.set_mode(source_mode)
        self.journal=journal;self.confirmations={};self.sequence={};self.closed=False;self.consumers={}
        self.generation=0;self.approval=deepcopy(approval)

    def _guard_valid(self):
        # Only a trusted host adapter may verify runtime isolation for this exact
        # mount. A boolean or attestation dictionary from UI/Phema is not a guard.
        verify=getattr(self.egress_guard,'verify_scope',None)
        return callable(verify) and verify(actor=self.actor,mount_id=self.mount_id,
            manifest_digest=self.digest,publisher_origin=self.manifest['origin']) is True

    def set_mode(self,mode):
        if mode not in ('parameters','snapshot'):raise ValueError('Select parameters or snapshot.')
        self.source_mode=mode
        if hasattr(self,'confirmations'):self.confirmations.clear()
        if hasattr(self,'generation'):self.generation+=1;self.sequence.clear()
        if hasattr(self,'consumers'):self._stop_consumers()

    def _stop_consumers(self):
        try:current=asyncio.current_task()
        except RuntimeError:current=None
        for task in list(self.consumers.values()):
            if task is not current:task.cancel()

    def close(self):self.closed=True;self.confirmations.clear();self.generation+=1;self._stop_consumers()

    def propose_mode(self,mode):
        if self.closed or self.approval.get('expiresAt',0)<=time.time():raise PermissionError('View approval expired or closed.')
        if mode not in ('parameters','snapshot'):raise ValueError('Select parameters or snapshot.')
        return {'requiresConfirmation':self.source_mode=='snapshot' and mode=='parameters',
            'operationId':'Switch to parameter mode','input':{'sourceMode':mode}}

    def confirm_mode(self,mode):
        # Authenticated host/recent-action endpoint only, as with effect approval.
        self.propose_mode(mode);key=secrets.token_urlsafe(24)
        self.confirmations[key]=(digest(['mode',self.source_mode,mode,self.generation]),time.monotonic()+60)
        return key

    def change_mode(self,request):
        exact(request,['mode'],['confirmationId']);mode=request['mode']
        if self.propose_mode(mode)['requiresConfirmation']:
            approval=self.confirmations.pop(request.get('confirmationId',''),None)
            if (not approval or approval[0]!=digest(['mode',self.source_mode,mode,self.generation])
                    or approval[1]<time.monotonic()):
                raise PermissionError('Leaving snapshot mode requires explicit host confirmation.')
        self.set_mode(mode)
        return {'sourceMode':mode}

    def _prepare(self,request):
        if self.closed:raise PermissionError('View has been closed.')
        if self.approval.get('expiresAt',0)<=time.time():raise PermissionError('View approval expired.')
        if any(o['classification']=='confidential' for o in self.manifest['operations']) and not self._guard_valid():
            raise PermissionError('Verified host egress guard is no longer active.')
        exact(request,['operationId','sourceMode','input'],['expectedRevision','idempotencyKey','confirmationId'])
        if request['sourceMode']!=self.source_mode:raise PermissionError('View source mode mismatch.')
        entry=next((o for o in self.manifest['operations'] if o['id']==request['operationId']),None)
        if not entry or self.source_mode not in entry['sourceModes']:raise PermissionError('Operation is not bound for this view/source mode.')
        # Re-resolve exact publisher ownership/evidence before each interaction.
        fresh=refresh_binding(self.manifest,entry,self.contracts[entry['id']],self.resolver)
        value=check(fresh['inputSchema'],request['input'])
        self.no_credentials(value)
        if not self.authorize(self.actor,entry,fresh,value):raise PermissionError('Owner authorization rejected this resource.')
        constraints=fresh['constraints']
        if constraints['expectedRevision']:
            if 'expectedRevision' not in request:raise ValueError('Expected revision required.')
            if entry.get('revisionPointer') and pointer(value,entry['revisionPointer'])!=request['expectedRevision']:
                raise ValueError('Revision does not match the declared input binding.')
        effect=any(fresh['effects'].values())
        if self.source_mode=='snapshot' and (effect or entry['delivery']!=['snapshot']):raise PermissionError('Snapshot may only read frozen owner content.')
        if constraints['idempotency']=='required':
            key=request.get('idempotencyKey','')
            if not isinstance(key,str) or not 16<=len(key)<=128:raise ValueError('Stable idempotency key required.')
            if entry.get('idempotencyPointer') and pointer(value,entry['idempotencyPointer'])!=key:raise ValueError('Idempotency does not match its input binding.')
        return entry,fresh,value,effect

    def propose(self,request):
        entry,contract,value,effect=self._prepare(request)
        return {'requiresConfirmation':effect,'operationId':entry['id'], 'label':str(entry.get('extensions',{}).get('phemacast:label',entry['id']))[:100], 'effects':contract['effects'],
                'classification':entry['classification'],'input':value}

    def confirm(self,request):
        # This method is invoked only by authenticated host approval UI, never a
        # frame message. Deployment must apply CSRF/session/recent-user-action gate.
        self._prepare(request)
        key=secrets.token_urlsafe(24)
        self.confirmations[key]=(digest({k:v for k,v in request.items() if k!='confirmationId'}),time.monotonic()+60)
        return key

    async def invoke(self,request):
        entry,contract,value,effect=self._prepare(request)
        if effect:
            confirmation=self.confirmations.pop(request.get('confirmationId',''),None)
            if not confirmation or confirmation[0]!=digest({k:v for k,v in request.items() if k!='confirmationId'}) or confirmation[1]<time.monotonic():
                raise PermissionError('Explicit host confirmation required for declared effects.')
        wire={**project_operation_input(contract,value),
              'sourceMode':self.source_mode,'expectedRevision':request.get('expectedRevision'),'idempotencyKey':request.get('idempotencyKey')}
        declared=entry.get('extensions',{}).get('phemacast:dependencies')
        dependencies=self.manifest['dependencies'] if declared is None else [dep for dep in self.manifest['dependencies'] if dep['ref'] in declared]
        if declared is not None and any(ref not in [dep['ref'] for dep in dependencies] for ref in declared):raise PermissionError('Operation selected an undeclared dependency.')
        invocation={**wire,'ref':entry['ref'],'parameters':self.parameters,'dependencies':dependencies}
        scope=digest([self.actor,self.manifest['publisher']['agentId'],entry['ref']])
        key=request.get('idempotencyKey')
        if key:
            retained=self.journal.reserve(scope,key,invocation)
            if retained:
                if retained['status']=='completed':return retained['value']
                return {'schema':'phemacast:agent-state/1','status':'uncertain','executionReplayAllowed':False,
                    'recovery':{'operationId':entry['id'],'requestKey':key,'statusPath':contract['constraints']['statusPath']}}
        # Resource authority and point ceilings come only from this approved host
        # mount, not arbitrary frame inputs or a service-owner impersonation.
        resource_refs=deepcopy(self.approval.get('resourceRefs',[]))
        max_points=self.approval.get('maxPoints',0)
        if type(max_points) is not int or max_points<0:raise PermissionError('Explicit nonnegative point ceiling required.')
        generation=self.generation
        intent=key or secrets.token_urlsafe(24)
        grant=await self.issue_grant(actor=self.actor,audience=self.manifest['publisher']['agentId'],
            operation_ref=entry['ref'],wire=wire,value=value,input_binding=contract.get('inputBinding','body'),
            intent_id=intent,resource_refs=resource_refs,max_points=max_points,
            dependencies=dependencies,source_mode=self.source_mode,
            manifest_digest=self.digest,mount_id=self.mount_id,contract_operation=contract['operation'])
        if grant.get('user_id')==self.actor and grant.get('operation_ref')==entry['ref']:
            metadata={name:grant[name] for name in ('id','user_id','service_actor_id','audience','operation_ref','operation','request_binding')}
            metadata.update(intentId=intent,manifestDigest=self.digest,parameterDigest=digest(self.parameters),
                sourceMode=self.source_mode,requestFingerprint=digest(value))
            self.journal.remember_grant(scope,metadata)
        if self.closed or generation!=self.generation:raise PermissionError('View changed before dispatch.')
        self._prepare(request)
        # transport alone sees the narrow call credential. It may not redirect,
        # resolve new origins or forward broad credentials. Owner enforces ACL/domain.
        result=await self.transport(self.manifest['origin'],wire,grant)
        check(contract['outputSchema'],result)
        self.no_credentials(result)
        # Hide late private results after revocation, source-mode change or close.
        if (self.closed or generation!=self.generation or self.approval.get('expiresAt',0)<=time.time()
                or refresh_binding(self.manifest,entry,contract,self.resolver)!=contract
                or (entry['classification']=='confidential' and not self._guard_valid())
                or not self.authorize(self.actor,entry,contract,value)):
            raise PermissionError('View authority changed while the operation was running.')
        if key:self.journal.complete(scope,key,result)
        return deepcopy(result)

    async def renew_observer(self,request,receipt_id):
        """Server lease/status adapter only. Never renew a mutation for replay."""
        entry,contract,value,_=self._prepare(request)
        if (not set(entry['delivery'])&{'status','stream','job'}
                or any(effect for name,effect in contract['effects'].items() if name!='opensStream')):
            raise PermissionError('Continuation is available only for declared observation authority.')
        scope=digest([self.actor,self.manifest['publisher']['agentId'],entry['ref']])
        receipt=self.journal.grant_receipt(scope,receipt_id)
        wire=project_operation_input(contract,value)
        if (receipt['manifestDigest']!=self.digest or receipt['parameterDigest']!=digest(self.parameters)
                or receipt['sourceMode']!=self.source_mode or receipt['requestFingerprint']!=digest(value)
                or receipt['request_binding']!=operation_binding(wire['method'],wire['path'],wire['body'],wire['query'])):
            raise PermissionError('Observer continuation differs from its reviewed original input.')
        renew=getattr(self.issue_grant,'continuation',None)
        if not callable(renew):raise PermissionError('Verified continuation adapter is unavailable.')
        generation=self.generation;grant=await renew(actor=self.actor,receipt=receipt)
        self._prepare(request)
        if self.closed or generation!=self.generation:raise PermissionError('View changed during renewal.')
        self.journal.remember_grant(scope,{**receipt,'id':grant['id']})
        # Only this trusted server method receives the narrow token. Frame-facing
        # status responses remain owner state and never include a call grant.
        return grant

    async def status(self,request):
        entry,contract,_,effect=self._prepare(request)
        if effect or 'status' not in entry['delivery']:
            raise PermissionError('Recovery requires a declared read-only status binding.')
        return await self.invoke(request)

    async def events(self,operation_id,adapter,*,cursor=None,expected_revision=None):
        if self.source_mode=='snapshot' or self.closed:
            raise PermissionError('Frozen or closed view cannot open events.')
        entry=next((o for o in self.manifest['operations'] if o['id']==operation_id),None)
        if not entry or not set(entry['delivery']) & {'job','stream','status'}:
            raise PermissionError('Undeclared event binding.')
        if operation_id in self.consumers:raise PermissionError('This view binding already has an event consumer.')
        consumer=asyncio.current_task();self.consumers[operation_id]=consumer
        # Adapter receives no browser credential. It must open an already accepted
        # owner job/stream lease, renew narrow grants and recheck resource ACLs.
        generation=self.generation;iterator=None
        try:
            iterator=adapter(cursor).__aiter__()
            async for event in iterator:
                if self.closed or generation!=self.generation:break
                accepted=self.accept_event(operation_id,event,expected_revision=expected_revision)
                yield accepted
                if accepted.get('refreshRequired'):break
                expected_revision=event['revision']
        finally:
            self.consumers.pop(operation_id,None)
            if iterator is not None and hasattr(iterator,'aclose'):await iterator.aclose()

    def accept_event(self,operation_id,event,*,expected_revision=None):
        """Adapter calls per event with resource ACL/lease revalidation.

        Events do not trigger action replay. Gap/conflict causes an explicit state
        read; the owner adapter is responsible for lease renewal and teardown.
        """
        if self.closed or self.source_mode=='snapshot' or self.approval.get('expiresAt',0)<=time.time():raise PermissionError('Frozen, closed or expired view cannot consume events.')
        if any(o['classification']=='confidential' for o in self.manifest['operations']) and not self._guard_valid():
            raise PermissionError('Verified host egress guard is no longer active.')
        entry=next((o for o in self.manifest['operations'] if o['id']==operation_id),None)
        if not entry or not set(entry['delivery']) & {'job','stream','status'}:raise PermissionError('Undeclared event binding.')
        contract=refresh_binding(self.manifest,entry,self.contracts[operation_id],self.resolver)
        if not self.authorize(self.actor,entry,contract,{}):raise PermissionError('Event resource access revoked.')
        exact(event,['schema','operationId','sequence','revision','status','value'],['baseRevision','cursor','extensions'])
        if event['schema']!='phemacast:agent-event/1' or event['operationId']!=operation_id or type(event['sequence']) is not int or event['sequence']<0:
            raise ValueError('Invalid event identity/sequence.')
        previous=self.sequence.get(operation_id)
        if previous is not None and event['sequence']<=previous:raise ValueError('Duplicate or backwards event sequence.')
        if previous is not None and event['sequence']!=previous+1:return {'status':'gap','refreshRequired':True,'executionReplayAllowed':False}
        if 'baseRevision' in event and event['baseRevision']!=expected_revision:return {'status':'conflict','refreshRequired':True,'executionReplayAllowed':False}
        check(contract['outputSchema'],event['value']);self.no_credentials(event['value']);self.sequence[operation_id]=event['sequence']
        return deepcopy(event)
