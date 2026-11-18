"""Owner-installed model projections, persisted with the source catalog."""
import copy
import json
import os
from pathlib import Path
import sqlite3
import time

from prompits.core.capabilities import canonical, digest
from prompits.core.resource_registry import reference
from phemacast.core.model_execution import verify_bundle, verify_vectors, execute_chain
from .catalog import PROFILES, profile


def store_path(source):
    config = getattr(source, '_config_data', {})
    root = config.get('spec_catalog_root') or config.get('endpoint_catalog_root')
    if not root:
        root = getattr(getattr(source, 'spec_repository', None), 'root', None)
    if not root:
        raise ValueError('Configure persistent source model storage outside the source checkout.')
    return Path(root) / source.vendor_id / 'installed-models.sqlite'


def database(source):
    path = store_path(source); path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=10); db.row_factory = sqlite3.Row
    db.execute('CREATE TABLE IF NOT EXISTS installed_models(id TEXT PRIMARY KEY,payload TEXT,owner TEXT,installed REAL,enabled INTEGER)')
    os.chmod(path, 0o600)
    return db


def install(source, records, chain, vectors, owner):
    index = verify_bundle(records, chain)
    first = index[canonical(chain[0])]['resource']
    base_ref = first.get('source_model_ref')
    schema = next((s for s in PROFILES if reference(profile(s)) == base_ref), None)
    from .service import mapping_resource
    base = mapping_resource(source.vendor_id, schema) if schema else None
    native_endpoint = None
    if not base and base_ref:
        from .native import installed_native
        native = next((e for e in installed_native(source,[base_ref]) if reference(e['model'])==base_ref),None)
        if native:
            base=native['mapping'];native_endpoint=native['endpoint'].endpoint_id
    if not base:
        raise ValueError('This source does not implement the starting model of the chain.')
    if any(r.get('visibility') != 'public' for r in records):
        raise ValueError('A source-wide model installation requires a public dependency bundle.')
    evidence = verify_vectors(chain, vectors, records)
    target = index[canonical(chain[-1])]['resource']['target_ref']
    model = index[canonical(target)]['resource']
    final = index[canonical(chain[-1])]['resource']
    entry = {'id': digest({'source': source.vendor_id, 'chain': chain}), 'modelRef': target,
             'mappingRefs': [reference(base), *chain], 'mappingRef': reference(base), 'chain': chain,
             'baseSchema': schema, 'baseRef':base_ref,'baseEndpointId':native_endpoint,'fields': sorted(final['execution']['fields']),
             'records': records, 'evidence': evidence, 'delivery': ['snapshot'],
             'verification': 'fixture-verified', 'execution': 'source-owned',
             'legacySchema': model.get('extensions', {}).get('ai.attas:legacySchema', schema)}
    with database(source) as db:
        previous = db.execute('SELECT payload FROM installed_models WHERE id=?', (entry['id'],)).fetchone()
        if previous and json.loads(previous['payload']) != entry:
            raise ValueError('An installed chain version is immutable; publish a new version.')
        db.execute('INSERT OR REPLACE INTO installed_models VALUES(?,?,?,?,1)', (entry['id'], canonical(entry), owner, time.time()))
    return {k: v for k, v in entry.items() if k != 'records'}


def installations(source):
    try:
        path = store_path(source)
        if not path.exists(): return []
        with database(source) as db:
            return [json.loads(r['payload']) for r in db.execute('SELECT payload FROM installed_models WHERE enabled=1 ORDER BY id')]
    except (ValueError, sqlite3.Error):
        return []


def advertisements(source, endpoint=None):
    from .service import endpoint_support
    supported = endpoint_support(source.vendor_id, endpoint) if endpoint else None
    models = {canonical(s['modelRef']) for s in supported} if supported is not None else None
    return [{k: v for k, v in entry.items() if k not in ('records', 'chain', 'baseSchema')}
            for entry in installations(source) if models is None or canonical(entry['baseRef']) in models or entry.get('baseEndpointId')==getattr(endpoint,'endpoint_id',None)]


def execute_custom(source, request, auth_context):
    from .service import execute_model
    entry = next((entry for entry in installations(source) if entry['modelRef'] == request['target_model']
                  and entry['mappingRefs'] == [request.get('mapping_ref'), *request.get('mapping_chain', [])]), None)
    if not entry:
        raise ValueError('The requested target and exact tested mapping chain are not installed.')
    query = request.get('model_request') or {}
    fields = query.get('fields', entry['fields'])
    if not fields or any(f not in entry['fields'] for f in fields):
        raise ValueError('Requested fields are outside the installed model projection.')
    first = next(r['resource'] for r in entry['records'] if r['ref'] == entry['chain'][0])
    input_paths=set(first.get('extensions',{}).get('phemacast:preconditions',{}))
    for field in first['execution']['fields'].values():
        if field.get('path'):input_paths.add(field['path'])
        input_paths.update(field.get('coalesce',[]))
        input_paths.update(step['unitPath'] for step in field.get('steps',[]) if step.get('unitPath'))
    input_fields=sorted({path.split('.')[0] for path in input_paths})
    base_model=next(r['resource'] for r in entry['records'] if r['ref']==entry['baseRef'])
    available=list(base_model.get('fields') or base_model['interface']['response_schema'].get('items',{}).get('properties',{}))
    native_request = {**copy.deepcopy(request), 'target_model': entry['baseRef'],
                      'mapping_ref': entry['mappingRefs'][0], 'mapping_chain': [],
                      'model_request': {**query, 'fields': input_fields or available}}
    result = execute_model(source, native_request, auth_context)
    base = result['canonical_result']
    mapped = execute_chain(entry['chain'], base['payload'], entry['records'], parameters=query.get('arguments'), allow_lossy=query.get('allowLossy') is True)
    missing = sorted(f for f in fields if not mapped['payload'] or any(row.get(f) is None for row in mapped['payload']))
    envelope = {**base, **mapped, 'mappingRefs': entry['mappingRefs'], 'missingFields': missing,
                'issues': [*base['issues'], *mapped['issues']], 'inputLineage': base['lineage'],
                'verification': 'fixture-verified', 'chainEvidence': entry['evidence']}
    result.update(canonical_result=envelope, canonical_data={'items': mapped['payload']}, data={'items': mapped['payload']})
    result['data_schema'] = {'type': 'object', 'properties': {'items': next(r['resource']['interface']['response_schema'] for r in entry['records'] if r['ref'] == entry['modelRef'])}}
    return result
