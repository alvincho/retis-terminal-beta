"""Results an agent view keeps for itself in this Terminal: opaque JSON per account and Phema, never read here.

A view saves its last results under short names and reads them back when it opens again, so it asks its agent
only when something changes. An entry no longer applies once the Phema's definition or the view's release
differs from the one it was kept under. Writing one re-runs nothing and adds no snapshot; the Terminal keeps the
values only, with no business logic.
"""
import hashlib
import json
import re
import time

from fastapi import HTTPException

KIND = 'view-kept'
KEY = re.compile(r'[A-Za-z0-9_.:-]{1,128}')
RELEASE = re.compile(r'[a-f0-9]{64}')
MAX_VALUE, MAX_ENTRIES, MAX_TOTAL = 300_000, 64, 2_000_000
SECRET = re.compile(r'(?:__proto__|prototype|constructor|password|secret|token|access_token|refresh_token|authorization|cookie|api_key|caller_.*token)', re.I)


def canonical(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, 'Keep plain JSON values only.') from exc


def no_secrets(value):
    if isinstance(value, list):
        for item in value:
            no_secrets(item)
    elif isinstance(value, dict):
        for key, item in value.items():
            if SECRET.fullmatch(str(key)):
                raise HTTPException(422, 'Credentials and transport identities are not kept view results.')
            no_secrets(item)


async def instance_route(engine, store, instance_id, request, user):
    """/api/instances/{id}/kept from a mounted view: read, write or drop one named result."""
    child = engine.instances.get(instance_id)
    if not child or not engine.belongs(child, user):
        raise HTTPException(404, 'Browser unavailable in this Terminal window.')
    phema = getattr(child, 'phema', None)
    if not phema:
        raise HTTPException(409, 'The Phema has not finished opening.')
    if request.method != 'POST':
        raise HTTPException(405)
    body = await request.json()
    operation, key, release = body.get('operation'), body.get('key'), body.get('release')
    if operation not in ('read', 'write', 'drop') or not isinstance(key, str) or not KEY.fullmatch(key):
        raise HTTPException(422, 'Name one kept result: read, write or drop.')
    if release is not None and (not isinstance(release, str) or not RELEASE.fullmatch(release)):
        raise HTTPException(422, 'Invalid view release.')
    owner, phema_id = user['id'], str(phema.get('id') or '')
    entry = hashlib.sha256(canonical([phema_id, key]).encode()).hexdigest()
    digest = hashlib.sha256(canonical(phema).encode()).hexdigest()
    if operation == 'drop':
        store.delete(owner, KIND, entry)
        return {'key': key, 'dropped': True}
    if operation == 'read':
        try:
            saved = store.get(owner, KIND, entry)
        except HTTPException as exc:
            if exc.status_code == 404:
                return {'key': key, 'value': None}
            raise
        # Kept under another Phema definition or view release: it no longer applies.
        if saved.get('phemaDigest') != digest or release and saved.get('release') not in (None, release):
            store.delete(owner, KIND, entry)
            return {'key': key, 'value': None}
        return {'key': key, 'value': saved.get('value'), 'keptAt': saved.get('keptAt')}
    value = body.get('value')
    if len(canonical(value)) > MAX_VALUE:
        raise HTTPException(413, 'A kept view result is limited to 300 KB.')
    no_secrets(value)
    kept_at = time.time()
    store.put(owner, KIND, entry, {'phemaId': phema_id, 'key': key, 'value': value, 'keptAt': kept_at,
                                   'release': release, 'phemaDigest': digest})
    # Bounded per Phema: the oldest entries give way.
    total = 0
    for index, row in enumerate(row for row in store.list(owner, KIND) if row.get('phemaId') == phema_id):
        total += len(canonical(row.get('value')))
        if index >= MAX_ENTRIES or total > MAX_TOTAL:
            store.delete(owner, KIND, row['id'])
    return {'key': key, 'keptAt': kept_at}
