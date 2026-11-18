"""Provider-owned operations. The host transports opaque requests only."""
import copy
import math
from dataclasses import asdict
from types import MethodType

from jsonschema import Draft202012Validator, FormatChecker, ValidationError

from .vault import AccessDenied, Conflict, canonical
from ..scaffold.connections import PROVIDER_CONNECTION_PROFILES


SECRET_NAMES = {'api_key','apikey','api-key','api_token','token','password','authorization','secret','client_secret','auth_context'}


def reject_secrets(value):
    if isinstance(value,dict):
        if any(str(k).lower() in SECRET_NAMES for k in value):
            raise ValueError('Use an opaque source connection reference, not credentials.')
        for v in value.values():
            reject_secrets(v)
    elif isinstance(value,list):
        for v in value:
            reject_secrets(v)


def sanitize(value, secret):
    if isinstance(value,dict):
        return {sanitize(k,secret):sanitize(v,secret) for k,v in value.items()}
    if isinstance(value,(list,tuple)):
        return [sanitize(v,secret) for v in value]
    if isinstance(value,str) and secret:
        return value.replace(secret,'[redacted]')
    return value


class ProviderService:
    """A service constructed INSIDE the selected DataSource process.

    policy is source-operator supplied, never client-supplied. No dynamic import,
    vendor URL, provider switch, or shared/environment key fallback is accepted.
    """
    def __init__(self, source, vault, policy, *, probe=None):
        if source.vendor_id != vault.source_id:
            raise ValueError('Source and credential store identities differ.')
        self.source, self.vault = source, vault
        self.source_id = source.vendor_id
        self.policy = copy.deepcopy(policy)
        retention=self.policy.get('result_retention_seconds',0)
        if type(retention) is not int or retention<0:
            raise ValueError('Source result retention must be an explicit nonnegative number of seconds.')
        self.probe = copy.deepcopy(probe)

    def profile(self):
        configured = PROVIDER_CONNECTION_PROFILES.get(self.source_id,{})
        mode = configured.get('credential_mode','api_key')
        return {'schema':'source-interface/1','source_id':self.source_id,
                'execution_owner':self.source_id,'credential_mode':mode,
                'manual_fallback':mode=='api_key','login_status':'external-source-setup-required' if mode!='api_key' else 'manual-fallback-available',
                'policy':self.policy,'probe_available':self.probe is not None,
                'snapshots':'read-only; no source refresh','fallback':'forbidden'}

    def save_connection(self, owner, window, body, request_id):
        allowed = {'id','expected_revision','label','enabled','priority','api_key'}
        if not isinstance(body,dict) or set(body)-allowed or not {'id','expected_revision'} <= set(body):
            raise ValueError('Unsupported connection fields.')
        if 'api_key' in body:
            # Users' keys stay in their own Terminal, which calls the vendor directly.
            raise AccessDenied('This source does not accept user keys. Save your key in your Terminal.')
        return self.vault.mutate(owner,window,body['id'],body['expected_revision'],request_id,
            secret=body.get('api_key'),changes={k:v for k,v in body.items() if k in ('label','enabled','priority')},
            policy=self.policy)

    def _record(self, owner, window, body):
        if body.get('source_id') != self.source_id:
            raise AccessDenied('Explicit source does not match this agent.')
        if body.get('source_mode','parameters') != 'parameters':
            raise ValueError('Snapshot mode cannot invoke or refresh provider operations.')
        if type(body.get('connection_revision')) is not int:
            raise ValueError('Expected source connection revision required.')
        if body.get('connection_id') == 'public' and body['connection_revision'] == 0:
            from ..scaffold.connections import OPTIONAL_BROWSER_KEY_SOURCE_IDS
            if self.source_id not in OPTIONAL_BROWSER_KEY_SOURCE_IDS or self.policy.get('public_access') is not True:
                raise AccessDenied('Anonymous provider execution is not enabled on this source.')
            return {'id':'public','revision':0,'secret':'','enabled':True,'status':'public','policy':self.policy}
        record = self.vault.resolve(owner,window,body.get('connection_id'),body['connection_revision'])
        if not set(record.get('policy', {}).get('allowed_operations', [])).issubset(self.policy.get('allowed_operations', [])):
            raise AccessDenied('Source execution policy changed; reauthorize the selected connection.')
        return record

    def _facade(self, record):
        scoped = copy.copy(self.source)
        # Never modify shared credentials, source status, or config on a user call.
        scoped._credential_store = None
        scoped._connection_resolver = None
        scoped._record_data_access_result = lambda result: None
        def resolve(instance, source_id, context, **kwargs):
            if source_id != self.source_id or context.get('connection_id') != record['id']:
                raise AccessDenied('The selected source connection must be explicit.')
            return record['secret']
        scoped._resolve_source_owned_credential = MethodType(resolve,scoped)
        original = scoped.execute_endpoint
        def execute(endpoint, parameters, context):
            policy = record.get('policy',{})
            if endpoint.vendor_id != self.source_id:
                raise AccessDenied('Endpoint belongs to another source.')
            if endpoint.endpoint_id not in policy.get('allowed_operations',[]):
                raise AccessDenied('Endpoint is outside the source connection scope.')
            if endpoint.endpoint_id not in self.policy.get('allowed_operations', []):
                raise AccessDenied('Endpoint is no longer permitted by the source execution policy.')
            if not scoped.is_endpoint_executable(endpoint):
                raise ValueError('Selected source operation is not executable.')
            if endpoint.transport == 'websocket':
                raise ValueError('Use the managed source stream contract.')
            amount = endpoint.cost.get('amount')
            ceiling = policy.get('max_request_cost',0)
            if type(amount) not in (float,int) or not math.isfinite(amount) or amount < 0 or amount > ceiling:
                raise AccessDenied('Endpoint cost is unknown or exceeds the declared limit.')
            if amount and endpoint.cost.get('currency') != policy.get('cost_currency'):
                raise AccessDenied('Endpoint cost currency differs from the configured limit.')
            schema={'type':'object','properties':{p.name:dict(p.schema) for p in endpoint.parameters},
                    'required':[p.name for p in endpoint.parameters if p.required and p.default is None],
                    'additionalProperties':False}
            Draft202012Validator(schema,format_checker=FormatChecker()).validate(parameters)
            self.vault.consume(record)
            return original(endpoint,parameters,context)
        scoped.execute_endpoint = execute
        return scoped

    def fetch(self, owner, window, body, request_id):
        allowed={'source_id','source_mode','connection_id','connection_revision','endpoint_id','parameters','model_request','target_model','mapping_ref','mapping_chain'}
        if not isinstance(body,dict) or set(body)-allowed:
            raise ValueError('Unsupported execution request fields.')
        reject_secrets(body)
        record=self._record(owner,window,body)
        # SDK/desktop sessions must gain an explicit per-owner credential adapter
        # before this API can route them; never use a process-global session.
        if not self.profile()['manual_fallback'] and record['id'] != 'public':
            raise AccessDenied('A per-owner delegated-session execution adapter is required.')
        def operation():
            scoped=self._facade(record)
            context={'connection_id':record['id']}
            try:
                if body.get('target_model'):
                    from ..canonical.service import execute_model
                    result=execute_model(scoped,{k:v for k,v in body.items() if k in ('target_model','model_request','mapping_ref','mapping_chain')},context)
                else:
                    endpoint=scoped.resolve_endpoint(endpoint_id=body.get('endpoint_id',''))
                    result=asdict(scoped.execute_endpoint(endpoint,body.get('parameters',{}),context))
                    from .taiwan_contracts import DAILY, PRODUCTS, UNDERLYINGS, normalize, futures_interface_batch
                    if (self.source_id=='taifex' and endpoint.endpoint_id in {DAILY,*PRODUCTS}) or (self.source_id in UNDERLYINGS and endpoint.endpoint_id==UNDERLYINGS[self.source_id][0]):
                        if result.get('status')=='completed':
                            result['source_facts']=normalize(self.source_id,endpoint.endpoint_id,result['data'])
                            result['futures_interface']=futures_interface_batch(result['source_facts'])
            except (AccessDenied,Conflict,ValueError,ValidationError):
                raise
            except Exception:
                # No provider exception text or request URL can echo secrets.
                return {'status':'failed','source_id':self.source_id,'error':'Source execution failed; inspect source-local diagnostics.'}
            return sanitize({**result,'provenance':{'source_id':self.source_id,'execution_owner':self.source_id,
                'connection_id':record['id'],'connection_revision':record['revision'],
                'license':record.get('policy',{}).get('license','Provider license applies'),
                'source_mode':'parameters','fallback':False}},record['secret'])
        return self.vault.execute_once(owner,window,request_id,body,operation,
            retention_seconds=record.get('policy',{}).get('result_retention_seconds',0))

    def probe_connection(self, owner, window, body, request_id):
        if self.probe is None:
            raise AccessDenied('No source-owned safe probe is configured for this provider.')
        if set(body)-{'source_id','connection_id','connection_revision'}:
            raise ValueError('Probe parameters are controlled by the source.')
        endpoint=self.source.resolve_endpoint(endpoint_id=self.probe['endpoint_id'])
        if endpoint.cost.get('amount') != 0:
            raise AccessDenied('Connection probes require an advertised zero-cost operation.')
        return self.fetch(owner,window,{**body,**self.probe},request_id)

    def snapshots(self, owner, window):
        with self.vault.db() as db:
            rows=db.execute('SELECT request FROM receipts WHERE owner=? AND window=? AND request LIKE ?', (owner,window,'execute:%')).fetchall()
        return [{'id':r[0][8:],'label':'Saved provider result '+str(i+1)} for i,r in enumerate(rows)]

    def snapshot(self, owner, window, id):
        with self.vault.db() as db:
            row=db.execute('SELECT result FROM receipts WHERE owner=? AND window=? AND request=?',(owner,window,'execute:'+id)).fetchone()
            content=self.vault.execution_result(db,owner,window,'execute:'+id,row[0]) if row else None
        if not row:
            raise AccessDenied('Snapshot unavailable to this owner and window.')
        if content.get('status') in ('retention_expired','completed_without_retention','pending_reconciliation'):
            raise AccessDenied('No retained provider result is available under this source license policy.')
        return {'source_mode':'snapshot','source_id':self.source_id,'read_only':True,'content':content}
