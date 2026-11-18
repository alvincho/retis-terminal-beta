"""Enterprise product catalogs and source-owned delivery connections.

No licensed endpoint is guessed. Public OpenAPI schemas and owner-installed
dataset manifests describe executable routes. Transport configuration never
comes from a Browser request.
"""
from __future__ import annotations

import csv
import hashlib
import importlib
import io
import json
import re
import sqlite3
import threading
from dataclasses import replace
from pathlib import Path
from urllib.parse import quote, urlparse

import httpx
import jsonschema
from .models import EndpointSpec, EndpointParameterSpec, EndpointExecutionResult

PRODUCTS = {
    'bloomberg_data_license': ('Bloomberg Data License', 'BloombergDataLicenseSource', 28270,
        'https://professional.bloomberg.com/products/data/data-license/',
        ['reference', 'pricing', 'corporate actions', 'fundamentals', 'estimates', 'ESG', 'bulk', 'Data License Plus']),
    'bloomberg_event_feeds': ('Bloomberg Event-Driven Feeds', 'BloombergEventFeedSource', 28271,
        'https://professional.bloomberg.com/products/data/enterprise-catalog/event-driven-feeds/',
        ['news', 'news analytics', 'economic releases']),
    'factset_standard_feeds': ('FactSet Standard DataFeeds', 'FactSetStandardFeedSource', 28272,
        'https://download.factset.com/', ['bulk', 'fundamentals', 'ownership', 'estimates']),
    'factset_exchange_feed': ('FactSet Exchange DataFeed', 'FactSetExchangeFeedSource', 28273,
        'https://github.com/factset/enterprise-sdk/tree/main/specs', ['quotes', 'tick history', 'exchange metadata', 'streaming']),
    'factset_ondemand': ('FactSet OnDemand', 'FactSetOnDemandSource', 28274,
        'https://go.factset.com/hubfs/Website/Website_Downloads/Statistical%20Package%20Integration/factset%20ondemand%20web%20services%20reference%20manual_2.0.pdf',
        ['formulas', 'screening', 'historical data', 'corporate actions', 'estimates']),
    'factset_partners': ('Open:FactSet Partner Data', 'FactSetPartnerSource', 28275,
        'https://github.com/factset/enterprise-sdk/tree/main/specs', ['partner documents', 'alternative data', 'partner funds']),
    'bloomberg_bpipe': ('Bloomberg B-PIPE', 'BloombergBPipeSource', 28276,
        'https://bloomberg.github.io/blpapi-docs/', ['real-time quotes', 'market bars']),
}
PUBLIC_SPECS = {
    'factset_exchange_feed': ['ExchangeDataFeedSnapshotAPISymbolList.v1.yaml', 'ExchangeDataFeedSnapshotAPIEntireExchange.v1.yaml', 'ExchangeDataFeedDataModel.v2.yaml'],
    'factset_partners': ['OpenFactSetPartnersDocuments.v2.yaml', 'FactSetPartnerFunds.v1.yaml'],
}
COST = {'model': 'provider_terms', 'amount': None, 'currency': 'USD', 'unit': 'request', 'provider_cost': 'Licensed vendor charges are determined by contract, independently of Plaza points.'}
OBJECT = {'type': 'object', 'additionalProperties': True}
SECRET_NAMES = {'password', 'secret', 'api_key', 'apikey', 'api_secret', 'access_token', 'token', 'authorization', 'credentials', 'username'}


def reject_secrets(value):
    if isinstance(value, dict):
        if any(str(k).lower() in SECRET_NAMES for k in value):
            raise ValueError('Use source-owned credential settings, not request parameters.')
        for item in value.values(): reject_secrets(item)
    elif isinstance(value, list):
        for item in value: reject_secrets(item)


def endpoint(vendor, key, operation, parameters=(), schema=None, **metadata):
    return EndpointSpec(endpoint_id=vendor+'.'+key, vendor_id=vendor,
        name=metadata.pop('name', key.replace('_', ' ').title()), description=metadata.pop('description', PRODUCTS[vendor][0]+' data operation.'),
        category=metadata.pop('category', PRODUCTS[vendor][0]), transport=metadata.pop('transport', 'https'),
        operation=operation, parameters=tuple(parameters), response_schema=schema or OBJECT,
        authentication={'scheme': 'delegated_connection', 'required': True, 'credential_reference': 'connection_id'},
        cost=COST, quality={'status': 'definition_only', 'notes': ['Live entitlement and vendor responses have not been verified.']},
        documentation_url=PRODUCTS[vendor][3], executable=metadata.pop('executable', True), extensions=metadata)


def parameter(name, schema=None, required=False, location='query', default=None):
    return EndpointParameterSpec(name=name, schema=schema or {'type': 'string'}, required=required, location=location, default=default)


def resolve_schema(document, value, seen=()):
    """Resolve local references while retaining recursive definitions safely."""
    if isinstance(value, list): return [resolve_schema(document, x, seen) for x in value]
    if not isinstance(value, dict): return value
    if '$ref' in value:
        ref=value['$ref']
        if not ref.startswith('#/'): raise ValueError('External schema references must be bundled before import.')
        if ref in seen:
            # Preserve recursion through the original document components.
            return {'$ref': ref}
        target=document
        for part in ref[2:].split('/'): target=target[part.replace('~1','/').replace('~0','~')]
        return resolve_schema(document, {**target, **{k:v for k,v in value.items() if k!='$ref'}}, (*seen,ref))
    out={k:resolve_schema(document,v,seen) for k,v in value.items()}
    if out.pop('nullable', False) and isinstance(out.get('type'), str): out['type']=[out['type'],'null']
    return out


def import_openapi(vendor, document, *, allowed_hosts=()):
    """Import read operations and fully typed models, preserving distinct APIs."""
    if str(document.get('openapi',''))[:2] != '3.': raise ValueError('Import a bundled OpenAPI 3 specification.')
    result=[]
    for route, path in document.get('paths',{}).items():
        for method, raw in path.items():
            if method not in ('get','post'): continue
            # FactSet published snapshot/fund APIs use POST for retrieval.
            if method=='post' and not (vendor in PUBLIC_SPECS or raw.get('x-attas-read-only') is True): continue
            servers=raw.get('servers') or path.get('servers') or document.get('servers') or []
            base=str(servers[0]['url']).rstrip('/') if servers else ''
            u=urlparse(base)
            trusted=set(allowed_hosts) | ({'api.factset.com','api-sandbox.factset.com'} if vendor.startswith('factset_') else set())
            if u.scheme!='https' or u.hostname not in trusted or u.username or u.password or u.query or u.fragment:
                raise ValueError('API server requires an explicitly trusted HTTPS host in the source configuration.')
            params=[]; encodings={}
            for p in path.get('parameters',[])+raw.get('parameters',[]):
                p=resolve_schema(document,p)
                if p['name'].lower() in SECRET_NAMES or p['in'] not in ('path','query'): continue
                definition=resolve_schema(document,p.get('schema',{}))
                default=definition.get('default')
                # Prefer the documented JSON output for machine consumers.
                if p['name']=='format' and 'JSON' in definition.get('enum',[]): default='JSON'
                params.append(parameter(p['name'],definition,p.get('required',False),p['in'],default=default))
                encodings[p['name']]={'style':p.get('style','form'),'explode':p.get('explode',True)}
            if 'requestBody' in raw:
                body=resolve_schema(document,raw['requestBody'])
                media=body.get('content',{}).get('application/json')
                if media is None: continue
                params.append(parameter('body',media.get('schema',OBJECT),body.get('required',False),'body'))
            responses=raw.get('responses',{})
            response=next((resolve_schema(document,v) for k,v in responses.items() if str(k).startswith('2')), {})
            schema=resolve_schema(document,response.get('content',{}).get('application/json',{}).get('schema',OBJECT))
            schema={**schema,'components':document.get('components',{})}
            identity=hashlib.sha256((base+' '+method+' '+route).encode()).hexdigest()[:12]
            result.append(endpoint(vendor, re.sub('[^a-zA-Z0-9_]','_',raw.get('operationId',method+'_'+route))+'_'+identity,
                route,params,schema,name=raw.get('summary') or raw.get('operationId') or route,
                **{'attas.http_method':method.upper(),'attas.base_url':base,'attas.query_encoding':encodings,
                   'attas.schema_provenance':'official_openapi','attas.product_version':document.get('info',{}).get('version'),
                   'attas.environment':'sandbox' if 'sandbox' in u.hostname else 'production'}))
    return tuple(result)


FACTLETS = ('ExtractDataSnapshot','ExtractFormulaHistory','CorporateActionsDividends','CorporateActionsSplits',
    'ExtractBenchmarkDetail','ExtractOFDBItem','ExtractScreenUniverse','ExtractOptionsSnapshot','ExtractSPARData',
    'ExtractVectorFormula','ExtractEconData','ExtractAlphaTestingSnapshot','LSD_Ownership','EstimatesOnDemand')


def builtin_endpoints(vendor):
    if vendor=='factset_ondemand':
        common=[parameter(n) for n in ('ids','items','dates','currency','orientation','cutoff','ison','isonParams')]
        return tuple(endpoint(vendor,name,'/services/FASTFetch',[*common,parameter('options',OBJECT)],
            {'type':'object','properties':{'result':OBJECT}},
            **{'attas.factlet':name,'attas.http_method':'POST','attas.base_url':'https://datadirect.factset.com'}) for name in FACTLETS)
    # This is a local capability descriptor, never a fake licensed dataset.
    entries=(endpoint(vendor,'connection_status','local:status',schema={'type':'object','properties':{
        'configured':{'type':'boolean'},'datasets':{'type':'array','items':{'type':'string'}},'required':{'type':'array','items':{'type':'string'}}}},
        category='Connection configuration',transport='local'),)
    if vendor=='bloomberg_bpipe':
        from .provider_catalog import provider_spec_sources
        from .parsers import BloombergSpecParser
        source=provider_spec_sources('bloomberg')[0]
        entries += tuple(replace(e,endpoint_id=e.endpoint_id.replace('bloomberg.','bloomberg_bpipe.',1),vendor_id=vendor)
            for e in BloombergSpecParser().parse(source,'') if e.extensions.get('bloomberg.service_name') in ('//blp/mktdata','//blp/mktbar'))
    return entries


class EnterpriseSpecParser:
    VERSION='1'
    def supports(self,source): return source.vendor_id in PRODUCTS
    def parse(self,source,content):
        if not self.supports(source): raise ValueError('Unsupported enterprise source.')
        if source.media_type=='application/vnd.attas.delivery+json':
            return manifest_endpoints(source.vendor_id,json.loads(content))
        if source.media_type in ('application/json','application/yaml','text/yaml'):
            import yaml  # only enterprise OpenAPI documents need it; local data sources install no YAML library
            return import_openapi(source.vendor_id,yaml.safe_load(content),allowed_hosts=source.options.get('allowed_hosts',[]))
        return builtin_endpoints(source.vendor_id)


def manifest_endpoints(vendor, config):
    result=[]
    for name, item in config.get('datasets',{}).items():
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',name): raise ValueError('Invalid dataset ID.')
        schema=item.get('schema')
        if not isinstance(schema,dict) or not schema.get('properties'): raise ValueError('Every dataset requires an explicit row schema with properties.')
        jsonschema.Draft7Validator.check_schema(schema)
        result.append(endpoint(vendor,'dataset_'+name,'dataset:'+name,
            [parameter('limit',{'type':'integer','minimum':1,'maximum':10000},default=1000),parameter('offset',{'type':'integer','minimum':0},default=0)],
            {'type':'object','properties':{'rows':{'type':'array','items':schema},'nextOffset':{'type':['integer','null']}}},
            name=item.get('name',name), transport=item.get('transport','file'),
            **{'attas.schema_provenance':'owner_supplied_licensed_manifest','attas.delivery_mode':item.get('mode','snapshot')}))
    return tuple(result)


def credentials(raw):
    if not raw: raise PermissionError('Configure the source-owned vendor connection.')
    try: data=json.loads(raw)
    except (TypeError,ValueError): data={'access_token':raw}
    if not isinstance(data,dict): raise ValueError('Credential must be a token or a JSON connection object.')
    return data


def auth_headers(raw):
    data=credentials(raw)
    if data.get('access_token'): return {'Authorization':'Bearer '+data['access_token']}
    if data.get('username') and (data.get('api_key') or data.get('password')):
        import base64
        value=base64.b64encode((data['username']+':'+str(data.get('api_key') or data['password'])).encode()).decode()
        return {'Authorization':'Basic '+value}
    raise PermissionError('Configure an access token or username and API key/password.')


class EnterpriseClient:
    def __init__(self,vendor,config,state_dir,http_client=None):
        self.vendor=vendor;self.config=config;self.http=http_client;self.lock=threading.RLock()
        root=Path(state_dir);root.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.db=sqlite3.connect(root/'delivery.sqlite',check_same_thread=False)
        self.db.execute('CREATE TABLE IF NOT EXISTS batches (scope TEXT,digest TEXT,PRIMARY KEY(scope,digest))')
        self.db.execute('CREATE TABLE IF NOT EXISTS rows (scope TEXT,key TEXT,value TEXT,PRIMARY KEY(scope,key))');self.db.commit()
        (root/'delivery.sqlite').chmod(0o600)

    def close(self): self.db.close()

    def _request(self, method,url,**kwargs):
        if self.http is not None: return self.http.request(method,url,**kwargs)
        with httpx.Client(timeout=30,follow_redirects=False) as c: return c.request(method,url,**kwargs)

    def read_transport(self,item,raw):
        mode=item.get('transport','file')
        max_bytes=min(int(item.get('max_bytes',20_000_000)),100_000_000)
        if mode=='file':
            path=Path(item['path']).expanduser().resolve()
            if path.stat().st_size>max_bytes: raise ValueError('Dataset exceeds the configured byte limit.')
            return path.read_bytes()
        if mode=='https':
            u=urlparse(item['url'])
            if u.scheme!='https' or u.hostname not in self.config.get('allowed_hosts',[]) or u.username or u.password:
                raise ValueError('Delivery URL requires a source-configured trusted HTTPS host.')
            r=self._request('GET',item['url'],headers=auth_headers(raw))
            if r.status_code in (401,403): raise PermissionError('Delivery credential or entitlement rejected.')
            r.raise_for_status()
            if len(r.content)>max_bytes: raise ValueError('Dataset exceeds the configured byte limit.')
            return r.content
        if mode=='sftp':
            import paramiko
            c=credentials(raw);ssh=paramiko.SSHClient();ssh.load_host_keys(item['known_hosts'])
            ssh.set_missing_host_key_policy(paramiko.RejectPolicy())
            try:
                ssh.connect(item['host'],port=int(item.get('port',22)),username=c['username'],password=c.get('password'),key_filename=c.get('key_filename'),timeout=20,look_for_keys=False,allow_agent=False,disabled_algorithms={'keys':['ssh-rsa'],'pubkeys':['ssh-rsa']})
                with ssh.open_sftp() as sftp:
                    if sftp.stat(item['path']).st_size>max_bytes: raise ValueError('Dataset exceeds the configured byte limit.')
                    with sftp.open(item['path'],'rb') as f:
                        data=f.read(max_bytes+1)
                        if len(data)>max_bytes: raise ValueError('Dataset exceeds the configured byte limit.')
                        return data
            finally: ssh.close()
        if mode=='driver':
            # Local owner-selected adapters may wrap a licensed SDK or cloud SQL.
            module,name=item['driver'].split(':',1)
            fn=getattr(importlib.import_module(module),name)
            rows=fn(connection=credentials(raw),dataset=dict(item))
            return json.dumps(rows,allow_nan=False).encode()
        raise ValueError('Unsupported delivery transport.')

    def dataset(self,name,p,raw):
        item=self.config.get('datasets',{}).get(name)
        if not item: raise ValueError('Dataset is not configured.')
        data=self.read_transport(item,raw)
        if len(data)>min(int(item.get('max_bytes',20_000_000)),100_000_000): raise ValueError('Dataset exceeds the configured byte limit.')
        fmt=item.get('format','json');schema=item['schema']
        if fmt=='json': rows=json.loads(data)
        elif fmt=='jsonl': rows=[json.loads(line) for line in data.splitlines() if line.strip()]
        elif fmt=='csv':
            rows=list(csv.DictReader(io.StringIO(data.decode('utf-8-sig')),delimiter=item.get('delimiter',',')))
            for row in rows:
                for k,v in list(row.items()):
                    definition=schema.get('properties',{}).get(k,{});kind=definition.get('type')
                    if isinstance(kind,list): kind=next((x for x in kind if x!='null'),None)
                    if v=='' and 'null' in definition.get('type',[]): row[k]=None
                    elif kind in ('number','integer') and v!='': row[k]=float(v) if kind=='number' else int(v)
                    elif kind=='boolean':
                        if v.lower() not in ('true','false'): raise ValueError('Invalid boolean in feed.')
                        row[k]=v.lower()=='true'
        elif fmt=='parquet':
            import pyarrow.parquet as pq
            rows=pq.read_table(io.BytesIO(data)).to_pylist()
        else: raise ValueError('Unsupported dataset format.')
        if isinstance(rows,dict):
            for k in item.get('rows_path','data').split('.'): rows=rows[k]
        if not isinstance(rows,list) or len(rows)>100000: raise ValueError('Delivery must contain a bounded row array.')
        scope=hashlib.sha256((self.vendor+name+raw+json.dumps(item,sort_keys=True)).encode()).hexdigest()
        keys=item.get('primary_key',[]);incremental=item.get('mode')=='incremental'
        if incremental and not keys: raise ValueError('Incremental feeds require primary_key.')
        for row in rows:
            if item.get('delete_field') and row.get(item['delete_field'])==item.get('delete_value','D'):
                if any(k not in row for k in keys): raise ValueError('Deletion has no primary key.')
            else: jsonschema.validate(row,schema)
        if incremental:
            digest=hashlib.sha256(data).hexdigest()
            with self.lock, self.db:
                if not self.db.execute('SELECT 1 FROM batches WHERE scope=? AND digest=?',(scope,digest)).fetchone():
                    for row in rows:
                        key=json.dumps([row[k] for k in keys],sort_keys=True)
                        if item.get('delete_field') and row.get(item['delete_field'])==item.get('delete_value','D'):
                            self.db.execute('DELETE FROM rows WHERE scope=? AND key=?',(scope,key))
                        else:self.db.execute('INSERT OR REPLACE INTO rows VALUES(?,?,?)',(scope,key,json.dumps(row)))
                    self.db.execute('INSERT INTO batches VALUES(?,?)',(scope,digest))
                rows=[json.loads(r[0]) for r in self.db.execute('SELECT value FROM rows WHERE scope=? ORDER BY key',(scope,))]
        offset=int(p.get('offset',0));limit=int(p.get('limit',1000));end=offset+limit
        return {'rows':rows[offset:end],'nextOffset':end if end<len(rows) else None,'total':len(rows)}

    def execute(self,e,p,raw):
        try:
            reject_secrets(p)
            props={x.name:x.schema for x in e.parameters}
            schema={'type':'object','properties':props,'required':[x.name for x in e.parameters if x.required],'additionalProperties':False,'components':e.response_schema.get('components',{})}
            jsonschema.validate(p,schema)
            p={**{x.name:x.default for x in e.parameters if x.default is not None},**p}
            if e.operation=='local:status':
                data={'configured':bool(self.config.get('datasets') or self.config.get('api_specs') or self.config.get('stream_driver')),
                    'datasets':list(self.config.get('datasets',{})), 'required':['Licensed dataset manifest/API schema and source-owned connection']}
            elif e.operation.startswith('dataset:'): data=self.dataset(e.operation.split(':',1)[1],p,raw)
            else:
                headers=auth_headers(raw);base=e.extensions['attas.base_url'];route=e.operation
                if any(x in route for x in ('..','\\','?','#')): raise ValueError('Invalid operation path.')
                query={};body=None
                for spec in e.parameters:
                    if spec.name not in p: continue
                    v=p[spec.name]
                    if spec.location=='path': route=route.replace('{'+spec.name+'}',quote(str(v),safe=''))
                    elif spec.location=='body': body=v
                    else:
                        encoding=e.extensions.get('attas.query_encoding',{}).get(spec.name,{})
                        query[spec.name]=','.join(map(str,v)) if isinstance(v,list) and not encoding.get('explode',True) else v
                if '{' in route: raise ValueError('Missing route parameter.')
                options={}
                if self.vendor=='factset_ondemand':
                    extra=query.pop('options',{})
                    if any(k.lower() in ('factlet','format') or k.lower() in SECRET_NAMES for k in extra): raise ValueError('Reserved OnDemand option.')
                    form={**query,**extra,'Factlet':e.extensions['attas.factlet'],'format':'XML'}
                    options={'data':form}
                else: options={'params':query,**({'json':body} if body is not None else {})}
                r=self._request(e.extensions.get('attas.http_method','GET'),base+route,headers=headers,**options)
                if r.status_code in (401,403): raise PermissionError('Provider rejected credentials or entitlement.')
                if r.status_code==429: return EndpointExecutionResult(status='rate_limited',endpoint_id=e.endpoint_id,error='Provider rate limit reached; retry later.',data={'retryAfter':r.headers.get('retry-after')})
                r.raise_for_status()
                if 300<=r.status_code<400: raise ValueError('Provider redirect requires an explicitly configured delivery endpoint.')
                if r.status_code==202:
                    return EndpointExecutionResult(status='pending',endpoint_id=e.endpoint_id,data={'providerStatus':202,'result':r.json() if r.content else {}},data_schema=e.response_schema,cost=COST)
                if self.vendor=='factset_ondemand':
                    from defusedxml import ElementTree
                    root=ElementTree.fromstring(r.content)
                    if any('error' in el.tag.lower() and (el.text or '').strip() for el in root.iter()): raise ValueError('OnDemand returned an error.')
                    def xml(el):
                        if not list(el):return el.text
                        result={}
                        for child in el:
                            k=child.tag.split('}')[-1];v=xml(child)
                            if k in result: result[k]=result[k]+[v] if isinstance(result[k],list) else [result[k],v]
                            else: result[k]=v
                        return result
                    data={'result':xml(root)}
                else:
                    fmt=str(query.get('format','JSON')).upper()
                    if fmt=='CSV':data={'rows':list(csv.DictReader(io.StringIO(r.text)))}
                    elif fmt=='XML':
                        from defusedxml import ElementTree
                        root=ElementTree.fromstring(r.content)
                        data={'elements':[{'tag':el.tag,'attributes':dict(el.attrib),'text':el.text} for el in root.iter()]}
                    else:
                        result=r.json();data=result if isinstance(result,dict) else {'rows':result}
            return EndpointExecutionResult(status='completed',endpoint_id=e.endpoint_id,data=data,data_schema=e.response_schema,cost=COST)
        except PermissionError:
            return EndpointExecutionResult(status='authentication_required',endpoint_id=e.endpoint_id,error='Configure a valid source-owned connection and vendor entitlement.')
        except ImportError:
            return EndpointExecutionResult(status='configuration_required',endpoint_id=e.endpoint_id,error='Install the configured licensed SDK or transport dependency on this source.')
        except Exception:
            return EndpointExecutionResult(status='failed',endpoint_id=e.endpoint_id,error='Source request failed validation, delivery, or response parsing. Check the source configuration and licensed schema.')
