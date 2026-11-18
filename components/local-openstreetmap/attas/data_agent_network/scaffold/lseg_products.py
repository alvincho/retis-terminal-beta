"""DataScope Select extraction jobs and World-Check One v3 screening transport.

Credentials, tokens and durable request receipts stay inside the source. Catalog
requests never authenticate, fetch licensed content or create screening cases.
"""
from __future__ import annotations

import base64
import csv
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import hmac
import io
import json
from pathlib import Path
import re
import sqlite3
import threading
import time
from urllib.parse import quote, unquote, urljoin, urlparse
import xml.etree.ElementTree as ET

import httpx
from cryptography.fernet import Fernet
from .models import EndpointExecutionResult, EndpointParameterSpec, EndpointSpec

DSS = 'lseg_datascope_select'
WC1 = 'lseg_worldcheck_one'
DSS_BASE = 'https://selectapi.datascope.lseg.com/RestApi/v1'
DSS_DOC = 'https://developers.lseg.com/en/api-catalog/datascope-select/datascope-select-rest-api/tutorials'
WC1_DOC = 'https://developers.lseg.com/en/api-catalog/customer-and-third-party-screening/world-check-one-api/quick-start'
SECRET_NAMES = {'password','secret','api_key','apikey','api_secret','access_token','authorization','credentials','service_account_password'}
JSON_OBJECT = {'type':'object','additionalProperties':True}


def param(name, location='query', required=False, schema=None, description=''):
    return EndpointParameterSpec(name=name,location=location,required=required,schema=schema or {'type':'string'},description=description)


def spec(vendor, name, route, method='GET', parameters=(), category='', response=None, effect='read'):
    return EndpointSpec(endpoint_id=vendor+'.'+name, vendor_id=vendor,
        name=name.replace('_',' ').title(), description=('Creates a vendor screening case. Explicit request only; reuse request_id on retries.' if effect=='create_case' else 'Licensed '+('DataScope Select' if vendor==DSS else 'World-Check One')+' '+category+' operation.'),
        category=category,transport='https',operation=route,parameters=tuple(parameters),
        response_schema=response or JSON_OBJECT,executable=True,
        authentication={'scheme':'delegated_connection','provider_scheme':'dss_token' if vendor==DSS else 'oauth2_or_hmac','required':True,'credential_reference':'connection_id'},
        cost={'model':'provider_terms','amount':None,'currency':'USD','unit':'request','provider_cost':'Licensed vendor charges depend on contract and content; not included in Plaza points.'},
        quality={'status':'provider_documented','notes':['Provider credentials and content entitlements have not been verified by catalog discovery.']},
        documentation_url=DSS_DOC if vendor==DSS else WC1_DOC,
        extensions={'attas.http_method':method,'attas.schema_provenance':'official_public_documentation','attas.side_effect':effect,'attas.update_mode':'manual' if effect=='create_case' else 'snapshot'})


def product_endpoints(vendor):
    body=param('body','body',True,JSON_OBJECT,'Provider-documented JSON request, without credentials.')
    if vendor==DSS:
        endpoints=[spec(DSS,'field_types',"/Extractions/GetValidContentFieldTypes(ReportTemplateType=DataScope.Select.Api.Extractions.ReportTemplates.ReportTemplateTypes'{report_type}')",parameters=(param('report_type','path',True),),category='Field dictionary')]
        for name in ('InstrumentSearch','EquitySearch','EntitySearch','GovCorpSearch','FundSearch','FuturesAndOptionsSearch','HistoricalSearch','HistoricalChainResolution'):
            endpoints.append(spec(DSS,re.sub(r'(?<!^)(?=[A-Z])','_',name).lower(),'/Search/'+name,'POST',(body,),category='Instrument and entity search'))
        for name in ('ExtractWithNotes','ExtractRaw'):
            endpoints.append(spec(DSS,'extract_with_notes' if name=='ExtractWithNotes' else 'extract_raw','/Extractions/'+name,'POST',(body,param('request_id',description='Optional request identity. Reuse on retries; a new identity requests a new extraction.')),category='On-demand extraction',effect='extraction'))
        endpoints.append(spec(DSS,'resume_extraction','local:resume',parameters=(param('job_id',required=True),),category='Extraction status'))
        for name in ('InstrumentLists','ReportTemplates','Schedules','ReportExtractions'):
            endpoints.append(spec(DSS,re.sub(r'(?<!^)(?=[A-Z])','_',name).lower(),'/Extractions/'+name,parameters=(param('$top',schema={'type':'integer','minimum':1,'maximum':1000}),param('$skip',schema={'type':'integer','minimum':0})),category='Saved extraction configuration'))
        return tuple(endpoints)
    if vendor==WC1:
        return (
            spec(WC1,'groups','/groups',category='Groups'),
            spec(WC1,'case_template','/references/case-template',parameters=(param('groupId',required=True),param('entityType')),category='Screening field dictionary'),
            spec(WC1,'create_and_screen','/cases','POST',(body,param('screen',required=True,schema={'type':'string','enum':['SYNC','ASYNC']}),param('request_id',required=True,description='Stable local request identity; reuse on retry to avoid duplicate vendor cases.')),category='Screening',effect='create_case'),
            spec(WC1,'record','/references/records/{id}',parameters=(param('id','path',True),),category='Matched record'),
        )
    raise ValueError('Unknown LSEG product.')


class ProductSpecParser:
    VERSION='1'
    def __init__(self,vendor):self.vendor=vendor
    def supports(self,source):return source.vendor_id==self.vendor
    def parse(self,source,content):
        if not self.supports(source):raise ValueError('Incorrect LSEG product specification.')
        endpoints=product_endpoints(self.vendor)
        # The DSS service publishes OData XML schemas. Verify our search routes
        # against that machine-readable service definition when it is supplied.
        if self.vendor==DSS and content.lstrip().startswith('<') and '<edmx:' in content:
            root=ET.fromstring(content)
            actions={e.get('Name') for e in root.iter() if e.tag.endswith('Action')}
            return tuple(e for e in endpoints if not e.operation.startswith('/Search/') or e.operation.rsplit('/',1)[1] in actions)
        return endpoints


class DataScopeSelectSpecParser(ProductSpecParser):
    def __init__(self):super().__init__(DSS)


class WorldCheckOneSpecParser(ProductSpecParser):
    def __init__(self):super().__init__(WC1)
    def parse(self,source,content):
        if not self.supports(source):raise ValueError('Incorrect World-Check specification.')
        if source.media_type not in ('application/json','application/yaml','text/yaml'):
            return super().parse(source,content)
        # Owner-provided licensed schema expands read operations without guessing
        # v2 routes or accidentally enabling account administration/deletion.
        from .parsers import PublicOpenAPISpecParser
        class Parser(PublicOpenAPISpecParser):VENDOR_ID=WC1
        imported=Parser().parse(source,content)
        result={e.endpoint_id:e for e in product_endpoints(WC1)}
        for e in imported:
            method=e.extensions.get('attas.http_method')
            route=e.operation
            read_post=route in ('/notifications/search',) or bool(re.fullmatch(r'/cases/\{[\w]+\}/results',route))
            if (method=='GET' and route.startswith(('/cases','/references','/groups','/notifications'))) or (method=='POST' and read_post):
                result[e.endpoint_id]=replace(e,executable=True,extensions={**e.extensions,'attas.side_effect':'read'},authentication={'scheme':'delegated_connection','required':True,'provider_scheme':'oauth2_or_hmac'})
        return tuple(result.values())


class ProductError(Exception):
    def __init__(self,message,status='failed'):super().__init__(message);self.status=status


def reject_secrets(value):
    if isinstance(value,dict):
        if any(str(k).lower() in SECRET_NAMES for k in value):raise ProductError('Credentials belong in the source connection, never in request parameters.')
        for v in value.values():reject_secrets(v)
    elif isinstance(value,list):
        for v in value:reject_secrets(v)


class ProductClient:
    """Source-scoped token cache and encrypted durable, credential-scoped receipts."""
    def __init__(self,vendor,state_dir,http_client=None,wait_seconds=12,clock=time.time,sleep=time.sleep):
        self.vendor=vendor;self.http=http_client;self.clock=clock;self.sleep=sleep
        self.wait_seconds=max(0,min(20,wait_seconds));self.tokens={};self.lock=threading.RLock()
        state_dir=Path(state_dir);state_dir.mkdir(parents=True,exist_ok=True,mode=0o700)
        key=state_dir/'receipts.key'
        if not key.exists():
            try:
                with key.open('xb') as out:out.write(Fernet.generate_key())
                key.chmod(0o600)
            except FileExistsError:pass
        self.cipher=Fernet(key.read_bytes())
        self.db=sqlite3.connect(state_dir/'receipts.sqlite',check_same_thread=False)
        self.db.execute('CREATE TABLE IF NOT EXISTS receipts (id TEXT PRIMARY KEY, scope TEXT NOT NULL, fingerprint TEXT NOT NULL, payload BLOB NOT NULL)');self.db.commit()
        (state_dir/'receipts.sqlite').chmod(0o600)

    def close(self):self.db.close()
    def _save(self,id,scope,fingerprint,row):
        blob=self.cipher.encrypt(json.dumps(row,ensure_ascii=False,allow_nan=False).encode())
        self.db.execute('INSERT OR REPLACE INTO receipts VALUES (?,?,?,?)',(id,scope,fingerprint,blob));self.db.commit()
    def _load(self,id,scope):
        row=self.db.execute('SELECT fingerprint,payload FROM receipts WHERE id=? AND scope=?',(id,scope)).fetchone()
        return (row[0],json.loads(self.cipher.decrypt(row[1]))) if row else None
    @staticmethod
    def credential(value,vendor):
        if not value:raise ProductError('Configure the source-owned '+('DSS username and password.' if vendor==DSS else 'World-Check One service account.'),'authentication_required')
        try:c=json.loads(value)
        except (ValueError,TypeError):raise ProductError('Source connection must be a JSON credential object.','authentication_required')
        if not isinstance(c,dict):raise ProductError('Invalid source credential object.','authentication_required')
        if c.get('access_token'):return c
        keys=('username','password') if vendor==DSS else ('api_key','api_secret') if c.get('auth_mode')=='hmac' else ('service_account_uuid','service_account_password')
        if any(not isinstance(c.get(k),str) or not c[k].strip() for k in keys):raise ProductError('Source connection is missing required credential fields.','authentication_required')
        return c
    @staticmethod
    def _status(response):
        if response.status_code==401:raise ProductError('Provider authentication was rejected.','authentication_required')
        if response.status_code==403:raise ProductError('Provider entitlement or group permission was denied.','permission_denied')
        if response.status_code==429:raise ProductError('Provider rate limit reached; retry later.','rate_limited')
        if not response.is_success:raise ProductError('Provider returned HTTP '+str(response.status_code)+'.')
    def _token(self,client,c,scope,force=False):
        if c.get('access_token'):return c['access_token']
        cached=self.tokens.get(scope)
        if cached and cached[1]>self.clock()+30 and not force:return cached[0]
        if self.vendor==DSS:
            r=client.post(DSS_BASE+'/Authentication/RequestToken',json={'Credentials':{'Username':c['username'],'Password':c['password']}},timeout=10)
        else:
            r=client.post('https://api.risk.lseg.com/auth/oauth2/v1/token',auth=(c['service_account_uuid'],c['service_account_password']),data={'grant_type':'client_credentials'},timeout=10)
        self._status(r)
        try:
            body=r.json();token=body.get('value') if self.vendor==DSS else body.get('access_token');ttl=86400 if self.vendor==DSS else float(body.get('expires_in',300))
            if not isinstance(token,str) or not token or ttl<=0:raise ValueError()
        except (ValueError,TypeError,AttributeError):raise ProductError('Provider returned an invalid authentication response.','authentication_required')
        self.tokens[scope]=(token,self.clock()+ttl);return token
    def _request(self,client,c,scope,method,url,body=None,params=None):
        content=json.dumps(body,ensure_ascii=False,separators=(',',':')).encode() if body is not None else None
        headers={'Accept':'application/json','Content-Type':'application/json'}
        if self.vendor==DSS:headers['Prefer']='respond-async, wait=1'
        request=client.build_request(method,url,params=params,content=content,headers=headers,timeout=15)
        if self.vendor==WC1 and c.get('auth_mode')=='hmac':
            date=datetime.fromtimestamp(self.clock(),timezone.utc).strftime('%a, %d %b %Y %H:%M:%S GMT')
            request.headers['Date']=date
            names=['(request-target)','host','date'];lines=[f'(request-target): {method.lower()} {request.url.raw_path.decode()}',f'host: {request.url.host}',f'date: {date}']
            if content is not None:
                names+=['content-type','content-length'];lines+=['content-type: application/json',f'content-length: {len(content)}']
            signature=base64.b64encode(hmac.new(c['api_secret'].encode(),'\n'.join(lines).encode(),hashlib.sha256).digest()).decode()
            request.headers['Authorization']=f'Signature keyId="{c["api_key"]}",algorithm="hmac-sha256",headers="{" ".join(names)}",signature="{signature}"'
        else:request.headers['Authorization']=('Token ' if self.vendor==DSS else 'Bearer ')+self._token(client,c,scope)
        response=client.send(request,follow_redirects=False)
        # A rejected credential is safe to renew; never replay transport errors or
        # an ambiguous 5xx response to a screening/extraction submission.
        if response.status_code==401 and not c.get('access_token') and c.get('auth_mode')!='hmac':
            request.headers['Authorization']=('Token ' if self.vendor==DSS else 'Bearer ')+self._token(client,c,scope,force=True)
            response=client.send(request,follow_redirects=False)
        self._status(response);return response
    def _safe_dss_url(self,value):
        parsed=urlparse(value)
        if parsed.scheme!='https' or parsed.netloc!='selectapi.datascope.lseg.com' or not parsed.path.startswith('/RestApi/v1/Extractions/') or parsed.fragment or '..' in unquote(parsed.path).split('/') or '\\' in unquote(parsed.path) or '%' in unquote(parsed.path):
            raise ProductError('Provider returned an untrusted extraction result location.')
        return value
    def _dss_finish(self,client,c,scope,id,fingerprint,row,response=None):
        deadline=time.monotonic()+self.wait_seconds
        while True:
            if response is None:
                response=self._request(client,c,scope,'GET',self._safe_dss_url(row['location']))
            if response.status_code==202:
                location=response.headers.get('Location') or row.get('location')
                if not location:raise ProductError('Pending extraction did not return a result location.')
                row.update(state='pending',location=self._safe_dss_url(urljoin(DSS_BASE+'/',location)))
                self._save(id,scope,fingerprint,row)
                if time.monotonic()>=deadline:return {'job_id':id,'state':'pending','retryAfterSeconds':2},'pending',()
                self.sleep(min(2,max(0,deadline-time.monotonic())));response=None;continue
            if 'json' in response.headers.get('Content-Type',''):
                data=response.json()
                if isinstance(data,dict) and data.get('JobId'):
                    job=str(data['JobId'])
                    if not re.fullmatch('[A-Za-z0-9-]{1,160}',job):raise ProductError('Invalid raw extraction job identifier.')
                    row.update(state='pending',location=DSS_BASE+f"/Extractions/RawExtractionResults('{job}')/$value",notes=data.get('Notes') or row.get('notes',[]))
                    self._save(id,scope,fingerprint,row);response=None;continue
                if isinstance(data,dict) and isinstance(data.get('Contents'),list):data={**data,'rows':data['Contents']}
            else:
                data={'rows':list(csv.DictReader(io.StringIO(response.text))),'format':'csv'}
            data=data if isinstance(data,dict) else {'items':data}
            notes=data.get('Notes') or row.get('notes') or []
            warnings=tuple(str(n) for n in notes) if isinstance(notes,list) else (str(notes),)
            row.update(state='completed',data=data,warnings=warnings,completedAt=self.clock())
            self._save(id,scope,fingerprint,row)
            return data,'completed',warnings
    def execute(self,endpoint,parameters,credential):
        result=lambda status,data=None,error='',warnings=():EndpointExecutionResult(status=status,endpoint_id=endpoint.endpoint_id,data=data or {},data_schema=endpoint.response_schema,cost=endpoint.cost,error=error,warnings=warnings)
        with self.lock:
            client=self.http or httpx.Client(follow_redirects=False)
            id=None;row=None;scope=None;fingerprint=None
            try:
                reject_secrets(parameters)
                c=self.credential(credential,self.vendor)
                scope=hashlib.sha256((self.vendor+'|'+credential).encode()).hexdigest()
                known={p.name:p for p in endpoint.parameters}
                if set(parameters)-set(known):raise ProductError('Unknown request parameter(s): '+', '.join(sorted(set(parameters)-set(known))))
                for name,p in known.items():
                    value=parameters.get(name)
                    if p.required and (value is None or value==''):raise ProductError('Missing parameter: '+name)
                    if value is not None and p.schema.get('enum') and value not in p.schema['enum']:raise ProductError('Invalid value for '+name)
                if endpoint.operation=='local:resume':
                    id=str(parameters['job_id']);stored=self._load(id,scope)
                    if not stored:raise ProductError('Extraction job is not available for this connection.')
                    fp,row=stored
                    if row['state']=='completed':return result('completed',row['data'],warnings=tuple(row.get('warnings',())))
                    if row['state']!='pending':raise ProductError('Submission outcome is unknown; reconcile with the provider before submitting again.','outcome_unknown')
                    data,status,warnings=self._dss_finish(client,c,scope,id,fp,row);return result(status,data,warnings=warnings)
                route=endpoint.operation;query={};body=None
                for name,value in parameters.items():
                    if name=='request_id':continue
                    p=known[name]
                    if p.location=='path':
                        value=str(value)
                        if not value or value in ('.','..'):raise ProductError('Invalid path identifier.')
                        if name=='report_type' and not re.fullmatch('[A-Za-z][A-Za-z0-9]{0,79}',value):raise ProductError('Invalid report template type.')
                        route=route.replace('{'+name+'}',quote(value,safe=''))
                    elif p.location=='body':
                        if not isinstance(value,dict):raise ProductError('Request body must be an object.')
                        body=value
                    elif p.location=='query':query[name]=value
                    else:raise ProductError('Unsupported request parameter location.')
                if '{' in route or not route.startswith('/') or route.startswith('//') or '?' in route or '#' in route or '..' in route.split('/'):raise ProductError('Invalid operation route.')
                method=endpoint.extensions.get('attas.http_method','GET')
                effect=endpoint.extensions.get('attas.side_effect','read')
                if method not in ('GET','POST'):raise ProductError('Unsupported operation method.')
                base=DSS_BASE if self.vendor==DSS else 'https://api.risk.lseg.com/screening/v3'
                fingerprint=hashlib.sha256(json.dumps([method,route,query,body],sort_keys=True).encode()).hexdigest()
                id=None;row=None
                if effect in ('extraction','create_case'):
                    if effect=='create_case':
                        if not all(body and body.get(k) for k in ('name','groupId','entityType')):raise ProductError('Screening requires name, groupId and entityType.')
                    request_id=str(parameters.get('request_id') or fingerprint)
                    if len(request_id)>160:raise ProductError('Request identity is too long.')
                    id=hashlib.sha256((scope+'|'+request_id).encode()).hexdigest()
                    stored=self._load(id,scope)
                    if stored:
                        previous,row=stored
                        if previous!=fingerprint:raise ProductError('Request identity is already used for a different request.')
                        if row['state']=='completed' and (effect=='create_case' or parameters.get('request_id') or self.clock()-row['completedAt']<300):return result('completed',row['data'],warnings=tuple(row.get('warnings',())))
                        if row['state']=='pending':
                            data,status,warnings=self._dss_finish(client,c,scope,id,fingerprint,row);return result(status,data,warnings=warnings)
                        if row['state']=='submitting':raise ProductError('Submission outcome is unknown; reconcile with the provider before submitting again.','outcome_unknown')
                    # Authenticate before recording a submission that may have side effects.
                    if c.get('auth_mode')!='hmac':self._token(client,c,scope)
                    row={'state':'submitting','createdAt':self.clock()};self._save(id,scope,fingerprint,row)
                response=self._request(client,c,scope,method,base+route,body,query)
                if effect=='extraction':
                    data,status,warnings=self._dss_finish(client,c,scope,id,fingerprint,row,response);return result(status,data,warnings=warnings)
                data=response.json()
                data=data if isinstance(data,dict) else {'items':data}
                warnings=('Potential screening matches require review; they are not findings of wrongdoing.',) if self.vendor==WC1 and effect=='create_case' else ()
                if id:
                    row.update(state='completed',data=data,warnings=warnings,completedAt=self.clock());self._save(id,scope,fingerprint,row)
                return result('completed',data,warnings=warnings)
            except ProductError as error:
                if id and row and row.get('state')=='submitting' and error.status in ('authentication_required','permission_denied','rate_limited'):
                    row['state']='rejected';self._save(id,scope,fingerprint,row)
                return result(error.status,error=str(error))
            except (httpx.HTTPError,ValueError,TypeError,KeyError,OverflowError):
                return result('failed',error='Provider request could not be completed. Submitted operations are not automatically repeated; reuse the request identity to check its receipt.')
            finally:
                if self.http is None:client.close()
