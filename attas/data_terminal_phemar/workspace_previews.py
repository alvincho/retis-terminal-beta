"""Private, bounded last-known screenshots, separate from workspace definitions."""
import asyncio
import base64
import binascii
import io
import hashlib
import json
import re
import time

from fastapi import HTTPException, Request
from PIL import Image, UnidentifiedImageError

from .workspace_state import read
from .window_sessions import state_key

KIND = 'workspace-preview'
LAYOUT_KIND = 'layout-preview'
# The screenshot taken with one saved Browser snapshot, shown when New Data Browser offers it.
SNAPSHOT_KIND = 'snapshot-preview'
ID = re.compile(r'^[A-Za-z0-9_-]{1,80}$')
PREFIX = 'data:image/jpeg;base64,'
MAX_BYTES = 180_000


def members(store, owner, window='default'):
    return {(w['id'], b['previewId']): b for w in (read(store, owner,window)['document'] or {}).get('workspaces', [])
            for b in w['browsers'] if b.get('previewId')}


def key(workspace, browser):
    if not ID.fullmatch(workspace) or not ID.fullmatch(browser):
        raise HTTPException(422, 'Invalid preview identifier.')
    return workspace + ':' + browser


def raster(value):
    if not isinstance(value, str) or not value.startswith(PREFIX):
        raise HTTPException(422, 'A JPEG screenshot is required.')
    if len(value) > MAX_BYTES * 4 // 3 + len(PREFIX) + 4:
        raise HTTPException(413, 'Preview exceeds 180 KB.')
    try:
        raw = base64.b64decode(value[len(PREFIX):], validate=True)
        if len(raw) > MAX_BYTES:
            raise HTTPException(413, 'Preview exceeds 180 KB.')
        with Image.open(io.BytesIO(raw)) as image:
            if image.format != 'JPEG' or not 1 <= image.width <= 1600 or not 1 <= image.height <= 1200:
                raise HTTPException(422, 'Invalid screenshot dimensions or format.')
            image.load()
            image = image.convert('RGB')
            width, height = image.size
            # Re-encode pixels only; never retain EXIF, embedded metadata or markup.
            output = io.BytesIO(); image.save(output, 'JPEG', quality=82)
            if output.tell() > MAX_BYTES:
                output = io.BytesIO(); image.save(output, 'JPEG', quality=60)
            if output.tell() > MAX_BYTES:
                raise HTTPException(413, 'Preview exceeds 180 KB.')
            image.thumbnail((240, 150))
            thumb = io.BytesIO(); image.save(thumb, 'JPEG', quality=70)
            encode = lambda data: PREFIX + base64.b64encode(data).decode('ascii')
            return {'image': encode(output.getvalue()), 'thumbnail': encode(thumb.getvalue()), 'width': width, 'height': height}
    except (binascii.Error, UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise HTTPException(422, 'Invalid JPEG screenshot.') from exc


def metadata(store, record):
    value = store.decrypt(record['secret'])
    return {k: value[k] for k in ('workspaceId', 'previewId', 'capturedAt', 'title', 'command', 'width', 'height', 'thumbnail', 'layout', 'layoutPreviewKey') if k in value}


def remember_layout(store, owner, browser, value):
    entry = browser.get('customLayout') or {}
    if not entry.get('id'): return value
    publication = browser.get('layoutPublication') or {}
    layout = {'id': entry['id'], 'consultantId': browser.get('consultantId') or '',
              'publicationId': publication.get('publicationId') or '', 'revision': publication.get('revision')}
    preview_key = hashlib.sha256(json.dumps(layout, sort_keys=True).encode()).hexdigest()
    value = {**value, 'layout': layout, 'layoutPreviewKey': preview_key}
    try: previous = store.get(owner, LAYOUT_KIND, preview_key)
    except HTTPException as exc:
        if exc.status_code != 404: raise
        previous = {'revision': 0}
    if not previous.get('secret') or store.decrypt(previous['secret'])['capturedAt'] < value['capturedAt']:
        store.put(owner, LAYOUT_KIND, preview_key, {'secret': store.encrypt(value)}, previous['revision'])
    return value


def snapshots(store):
    from phemacast.core.composition_resolution import ResolutionStore
    return ResolutionStore(store.path.parent / 'composition-results.sqlite')


def own_snapshot(store, user, snapshot):
    """One of this account's saved Browser snapshots, or 404."""
    if not isinstance(snapshot, str) or not re.fullmatch(r'[a-f0-9]{64}', snapshot): raise HTTPException(404, 'Snapshot unavailable.')
    try: value = snapshots(store).read(user['id'], snapshot)
    except KeyError as exc: raise HTTPException(404, 'Snapshot unavailable.') from exc
    if 'financialResults' not in value: raise HTTPException(404, 'Snapshot unavailable.')
    return value


def table_rows(result):
    value = (result or {}).get('value')
    return len(value['rows']) if isinstance(value, dict) and isinstance(value.get('rows'), list) else None


def install_preview_routes(app, store, principal):
    # New Data Browser opens a Phema from one of this account's own saved Browser snapshots.
    @app.get('/api/phema-snapshots')
    async def phema_snapshots(request: Request, phemaId: str):
        user = await principal(request)
        from .contracts import identifier
        shots = {record['id'] for record in store.list(user['id'], SNAPSHOT_KIND)}
        rows = [row for row in snapshots(store).list(user['id'], identifier(phemaId)) if row['browserSnapshot']][:50]
        return {'snapshots': [{**{k: row[k] for k in ('id', 'createdAt', 'status', 'parameters', 'label')}, 'screenshot': row['id'] in shots} for row in rows]}

    @app.get('/api/phema-snapshots/{snapshot}')
    async def phema_snapshot(snapshot: str, request: Request):
        user = await principal(request); value = own_snapshot(store, user, snapshot)
        # Snapshots saved before panel titles were kept show the reference id instead.
        titles = value.get('panelTitles') or {}
        return {'id': snapshot, 'phemaId': value['phemaId'], 'capturedAt': value.get('capturedAt'), 'label': value.get('label'),
                'status': value.get('status'), 'parameters': value.get('parameters', {}),
                'panels': [{'id': id, 'title': titles.get(id), 'status': (result or {}).get('status'), 'rows': table_rows(result)} for id, result in (value.get('results') or {}).items()]}

    @app.put('/api/snapshot-previews/{snapshot}')
    async def save_snapshot_preview(snapshot: str, request: Request):
        user = await principal(request); own_snapshot(store, user, snapshot)
        body = await request.json(); captured = body.get('capturedAt'); now = time.time() * 1000
        if type(captured) not in (int, float) or not now - 300_000 <= captured <= now + 30_000:
            raise HTTPException(422, 'Invalid screenshot capture time.')
        image = await asyncio.to_thread(raster, body.get('image'))
        try: previous = store.get(user['id'], SNAPSHOT_KIND, snapshot)
        except HTTPException as exc:
            if exc.status_code != 404: raise
            previous = {'revision': 0}
        # A snapshot keeps the picture taken when it was saved.
        if previous.get('secret'): raise HTTPException(409, 'This snapshot already has its screenshot.')
        store.put(user['id'], SNAPSHOT_KIND, snapshot, {'secret': store.encrypt({**image, 'snapshotId': snapshot, 'capturedAt': captured})}, previous['revision'])
        return {'snapshotId': snapshot, 'capturedAt': captured, 'thumbnail': image['thumbnail'], 'width': image['width'], 'height': image['height']}

    @app.get('/api/snapshot-previews/{snapshot}')
    async def snapshot_preview(snapshot: str, request: Request):
        user = await principal(request); own_snapshot(store, user, snapshot)
        try: return store.decrypt(store.get(user['id'], SNAPSHOT_KIND, snapshot)['secret'])
        except HTTPException as exc:
            if exc.status_code == 404: raise HTTPException(404, 'No screenshot was saved with this snapshot.') from exc
            raise

    @app.get('/api/layout-previews')
    async def layout_previews(request: Request):
        user = await principal(request)
        return {'previews': [metadata(store, r) for r in store.list(user['id'], LAYOUT_KIND)]}

    @app.get('/api/layout-previews/{preview_key}')
    async def layout_preview(preview_key: str, request: Request):
        user = await principal(request)
        if not re.fullmatch(r'[a-f0-9]{64}', preview_key): raise HTTPException(404, 'Preview unavailable.')
        return store.decrypt(store.get(user['id'], LAYOUT_KIND, preview_key)['secret'])

    @app.get('/api/workspace-previews')
    async def previews(request: Request):
        user = await principal(request)
        valid = members(store, user['id'],state_key(user))
        return {'previews': [metadata(store, r) for r in store.list(user['id'], KIND)
                             if (not user.get('terminalWindow') or r['id'].startswith(state_key(user)+':')) and tuple(r['id'].split(':')[-2:]) in valid]}

    @app.get('/api/workspace-previews/{workspace}/{browser}')
    async def preview(workspace: str, browser: str, request: Request):
        user = await principal(request); record_key = (state_key(user)+':' if user.get('terminalWindow') else '')+key(workspace, browser)
        if (workspace, browser) not in members(store, user['id'],state_key(user)):
            raise HTTPException(404, 'Preview unavailable.')
        record = store.get(user['id'], KIND, record_key)
        return store.decrypt(record['secret'])

    @app.put('/api/workspace-previews/{workspace}/{browser}')
    async def save_preview(workspace: str, browser: str, request: Request):
        user = await principal(request); record_key = (state_key(user)+':' if user.get('terminalWindow') else '')+key(workspace, browser)
        saved = members(store, user['id'],state_key(user)).get((workspace, browser))
        if saved is None:
            raise HTTPException(404, 'Save this browser in its workspace before capturing a preview.')
        body = await request.json(); captured = body.get('capturedAt')
        now = time.time() * 1000
        if type(captured) not in (int, float) or not now - 300_000 <= captured <= now + 30_000:
            raise HTTPException(422, 'Invalid screenshot capture time.')
        image = await asyncio.to_thread(raster, body.get('image'))
        try:
            previous = store.get(user['id'], KIND, record_key)
        except HTTPException as exc:
            if exc.status_code != 404: raise
            previous = {'revision': 0}
        if previous.get('secret') and store.decrypt(previous['secret'])['capturedAt'] >= captured:
            raise HTTPException(409, 'A newer preview is already saved.')
        value = {**image, 'workspaceId': workspace, 'previewId': browser, 'capturedAt': captured,
                 'title': (saved.get('customLayout') or {}).get('name') or saved['command'], 'command': saved['command']}
        value = remember_layout(store, user['id'], saved, value)
        record = store.put(user['id'], KIND, record_key, {'secret': store.encrypt(value)}, previous['revision'])
        # Deleted workspaces/browsers should not leave screenshots behind.
        valid = members(store, user['id'],state_key(user))
        for stale in store.list(user['id'], KIND):
            if (stale['id'].startswith(state_key(user)+':') if user.get('terminalWindow') else not stale['id'].startswith('window.')) and tuple(stale['id'].split(':')[-2:]) not in valid:
                store.delete(user['id'], KIND, stale['id'])
        return metadata(store, record)
