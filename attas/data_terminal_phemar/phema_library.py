"""Phemas this Terminal keeps, so an account can open them without asking the App Consultant again.

Each record is one Phema definition with where it came from: `bundled` (a starter Phema that ships in the package's
seed folder), `local` (a Phema a component running on this computer ships, such as Retis MAS Manager's own page) or
`app-consultant` (saved from the catalog, with the catalog key and revision it had then, so the selector can offer
the catalog's newer revision). The records are the account's own, in this Terminal's store; no agent keeps them.
Bundled and local Phemas are added to an account once; one the user removed stays removed. A local Phema whose
component now ships a newer revision is offered as an update.

Nothing here knows what a Phema shows: it stores, lists, returns and removes definitions.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from fastapi import HTTPException, Request

KIND = 'phema-definition'
REMOVED = 'phema-definition-removed'
SEEDS = Path(__file__).with_name('seed_phemas')
TEXT = 300


def record_id(key: str) -> str:
    return 'p-' + hashlib.sha256(key.encode()).hexdigest()[:24]


def seeds(folder: Path = SEEDS) -> list[dict]:
    rows = []
    for path in sorted(folder.glob('*.json')) if folder.is_dir() else ():
        value = json.loads(path.read_text(encoding='utf-8'))
        rows.append({'key': 'bundled:' + value['id'], 'name': value['name'], 'description': value.get('description', ''),
                     'revision': int(value.get('revision') or 1), 'phema': value['phema'], 'kind': 'bundled'})
    return rows


def _checked(phema):
    from phemacast.core.composition import validate_composition
    from prompits.core.capabilities import digest
    try:
        value = validate_composition(phema)
    except (ValueError, TypeError, KeyError) as error:
        raise HTTPException(422, 'Not a valid Phema: ' + str(error)[:200]) from error
    return value, digest(value)


def _text(value, limit=TEXT):
    return value.strip()[:limit] if isinstance(value, str) else ''


def save(store, owner, *, phema, key, revision, name, description='', kind='app-consultant'):
    if not isinstance(key, str) or not 1 <= len(key) <= 200:
        raise HTTPException(422, 'A Phema kept here needs the catalog key it came from.')
    if type(revision) is not int or revision < 1:
        raise HTTPException(422, 'A Phema kept here needs its catalog revision.')
    value, fingerprint = _checked(phema)
    payload = {'phema': value, 'name': _text(name) or key, 'description': _text(description),
               'source': {'kind': kind, 'key': key, 'revision': revision, 'digest': fingerprint}}
    store.delete(owner, REMOVED, record_id(key))
    return summary(store.put(owner, KIND, record_id(key), payload))


def summary(row):
    return {'id': row['id'], 'name': row.get('name'), 'description': row.get('description', ''),
            'source': row.get('source'), 'savedAt': row.get('updatedAt')}


def listing(store, owner, folder: Path = SEEDS, local=()):
    """The account's kept Phemas, after adding starters and local Phemas it has not seen.

    `local` lists what components on this computer ship now: {'key', 'name', 'revision', 'phema' (or None when
    only its summary was read)}. A new one needs its Phema; one already kept gets `update` when it is newer.
    """
    kept = {row['id']: row for row in store.list(owner, KIND)}
    removed = {row['id'] for row in store.list(owner, REMOVED)}
    for seed in seeds(folder):
        identity = record_id(seed['key'])
        if identity not in kept and identity not in removed:
            save(store, owner, phema=seed['phema'], key=seed['key'], revision=seed['revision'], name=seed['name'],
                 description=seed['description'], kind='bundled')
    offered = {}
    for item in local:
        identity = record_id(item['key'])
        offered[identity] = item
        if identity not in kept and identity not in removed and item.get('phema') is not None:
            save(store, owner, phema=item['phema'], key=item['key'], revision=item['revision'], name=item['name'], kind='local')
    # The bundled copy of a Phema a running component now ships itself stays kept, but only the component's shows.
    shipped = {'bundled:' + item['key'].removeprefix('local:') for item in local}
    rows = []
    for row in store.list(owner, KIND):
        if (row.get('source') or {}).get('kind') == 'bundled' and row['source'].get('key') in shipped:
            continue
        value = summary(row)
        item = offered.get(row['id'])
        if item and (row.get('source') or {}).get('kind') == 'local' and int(item['revision']) > int(row['source'].get('revision') or 0):
            value['update'] = {'revision': int(item['revision'])}
        rows.append(value)
    return rows


async def local_offers(network, token, kept=frozenset(), removed=frozenset()):
    """The Phemas components on this computer ship now; the Phema itself is read only for ones this account lacks."""
    clients = getattr(network, 'local_clients', None) if network is not None else None
    if not clients:
        return []
    offers, seen = [], set()
    for entry in await network.directory(token):
        if not entry.get('localId'):
            continue
        for item in entry.get('phemas') or []:
            phema_id = item.get('id')
            if not isinstance(phema_id, str) or phema_id in seen:
                continue
            seen.add(phema_id)
            key = 'local:' + phema_id
            offer = {'key': key, 'name': _text(item.get('name')) or phema_id, 'revision': int(item.get('revision') or 1),
                     'resource': entry['localId'], 'id': phema_id, 'phema': None}
            identity = record_id(key)
            if identity not in kept and identity not in removed:
                try:offer['phema'] = (await clients.phema(entry['localId'], phema_id))['phema']
                except HTTPException:continue
            offers.append(offer)
    return offers


def install_phema_library_routes(app, store, principal, folder: Path = SEEDS, network=None):
    async def offers(user):
        kept = {row['id'] for row in store.list(user['id'], KIND)}
        removed = {row['id'] for row in store.list(user['id'], REMOVED)}
        try:
            return await local_offers(network, user.get('token', ''), kept, removed)
        except HTTPException:
            return []  # the library still lists what it keeps when local discovery can't answer

    @app.get('/api/kept-phemas')
    async def library(request: Request):
        user = await principal(request)
        return {'phemas': listing(store, user['id'], folder, await offers(user))}

    @app.post('/api/kept-phemas/{identity}/update')
    async def update(identity: str, request: Request):
        """Take the newer revision a component on this computer ships for a local Phema kept here."""
        user = await principal(request)
        row = store.get(user['id'], KIND, identity)
        source = row.get('source') or {}
        if source.get('kind') != 'local':
            raise HTTPException(409, 'Only a Phema from a component on this computer updates here.')
        item = next((o for o in await offers(user) if o['key'] == source.get('key')), None)
        if item is None:
            raise HTTPException(404, 'The component that ships this Phema is not running on this computer.')
        value = await network.local_clients.phema(item['resource'], item['id'])
        return save(store, user['id'], phema=value['phema'], key=item['key'], revision=int(value.get('revision') or item['revision']),
                    name=item['name'], kind='local')

    @app.get('/api/kept-phemas/{identity}')
    async def kept(identity: str, request: Request):
        user = await principal(request)
        row = store.get(user['id'], KIND, identity)
        return {**summary(row), 'phema': row['phema']}

    @app.put('/api/kept-phemas')
    async def keep(request: Request):
        user = await principal(request)
        body = await request.json()
        if not isinstance(body, dict) or set(body) - {'phema', 'key', 'revision', 'name', 'description'}:
            raise HTTPException(422, 'Send the Phema, its catalog key and revision, and its name.')
        return save(store, user['id'], phema=body.get('phema'), key=body.get('key'), revision=body.get('revision'),
                    name=body.get('name'), description=body.get('description', ''))

    @app.delete('/api/kept-phemas/{identity}')
    async def remove(identity: str, request: Request):
        user = await principal(request)
        row = store.get(user['id'], KIND, identity)
        store.delete(user['id'], KIND, identity)
        if (row.get('source') or {}).get('kind') in ('bundled', 'local'):
            store.put(user['id'], REMOVED, identity, {'removed': True})  # a removed starter or local Phema is not added again
        return {'status': 'removed'}
