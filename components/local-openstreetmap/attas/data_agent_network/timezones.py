"""User clock context at the source boundary; vendor timestamps retain their encoding."""
from copy import deepcopy
from datetime import datetime, timezone
from functools import wraps
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import re

TIMEZONE_SCHEMA = {'type':'string','minLength':1,'maxLength':100,
                  'description':'Resolved IANA user timezone, e.g. Asia/Taipei. Interpret unzoned request datetimes here; explicit offsets win. Calendar dates and source timestamp encoding are unchanged.'}
TIMEZONE_SUPPORT = {'request_field':'timezone','kind':'iana','default':'explicit-offset-or-source-default',
                    'datetime_parameters':'normalized-to-UTC','calendar_dates':'source-calendar',
                    'response_timestamps':'source-defined','response_context_field':'request_timezone'}
BOUND_FIELDS = frozenset(('start','end','from','to','startDate','endDate','start_date','end_date','start_time','end_time','date_from','date_to','since','until'))


def zone(value):
    if not isinstance(value,str) or not 1<=len(value)<=100:
        raise ValueError('Timezone must be a resolved IANA timezone.')
    try:return ZoneInfo(value)
    except (ZoneInfoNotFoundError,ValueError):
        raise ValueError('Unknown IANA timezone: '+value) from None


def datetime_utc(value, target):
    if not isinstance(value,str) or not re.match(r'^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}',value):return value
    try:date=datetime.fromisoformat(value.replace('Z','+00:00'))
    except ValueError:raise ValueError('Invalid request datetime: '+value) from None
    if date.tzinfo is None:
        candidates={}
        for fold in (0,1):
            candidate=date.replace(tzinfo=target,fold=fold).astimezone(timezone.utc)
            if candidate.astimezone(target).replace(tzinfo=None)==date:candidates[candidate.timestamp()]=candidate
        if not candidates:raise ValueError('Request time does not exist in '+target.key+' because the clocks change.')
        if len(candidates)>1:raise ValueError('Request time occurs twice in '+target.key+'; supply an explicit UTC offset.')
        date=next(iter(candidates.values()))
    return date.astimezone(timezone.utc).isoformat()


def parameters_in_zone(value, target, schema=None):
    schema=schema or {}
    if schema.get('format')=='date-time':return datetime_utc(value,target)
    if isinstance(value,dict):
        props=schema.get('properties',{})
        return {key:datetime_utc(item,target) if key in BOUND_FIELDS and props.get(key,{}).get('format')!='date'
                else parameters_in_zone(item,target,props.get(key,{})) for key,item in value.items()}
    if isinstance(value,list):return [parameters_in_zone(item,target,schema.get('items',{})) for item in value]
    return value


def normalize_request(request, parameter_schema=None):
    if 'timezone' not in request:return dict(request)
    target=zone(request['timezone']);result=deepcopy(dict(request))
    if 'parameters' in result:result['parameters']=parameters_in_zone(result['parameters'],target,parameter_schema)
    if isinstance(result.get('model_request'),dict):
        query=result['model_request']
        if 'arguments' in query:query['arguments']=parameters_in_zone(query['arguments'],target)
    for key in ('start','end'):
        if key in result:result[key]=datetime_utc(result[key],target)
    return result


def timezone_fetch(function):
    """Shared by concrete providers, legacy pulse-backed sources and brokers."""
    @wraps(function)
    def fetch(source, request):
        schema=None
        if 'timezone' in request:
            zone(request['timezone'])
            ident=request.get('endpoint_id') or request.get('dataset_id')
            if ident and not request.get('target_model'):
                resolver=getattr(source,'resolve_endpoint',None)
                if resolver:
                    try:schema=source._endpoint_input_schema(resolver(endpoint_id=ident))
                    except (ValueError,KeyError,AttributeError):pass
                if schema is None and hasattr(source,'resolve_catalog_entry'):
                    entry=source.resolve_catalog_entry({'dataset_id':ident}) or {}
                    schema=entry.get('input_schema')
        result=function(source,normalize_request(request,schema))
        if isinstance(result,dict) and 'timezone' in request:result={**result,'request_timezone':request['timezone']}
        return result
    return fetch
