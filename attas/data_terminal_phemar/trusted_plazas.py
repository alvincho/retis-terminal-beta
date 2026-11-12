"""Terminal-host registry of Plazas; tokens remain in each Plaza's own vault."""
import asyncio
import hashlib
import json
from pathlib import Path

from fastapi import HTTPException, Request
import httpx

from prompits.core.file_lock import exclusive


def plaza_key(origin):
    return hashlib.sha256(origin.encode()).hexdigest()[:20]


class TrustedPlazas:
    def __init__(self, path):
        self.path = Path(path)

    def update(self, change):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with exclusive(self.path.with_suffix('.lock')):
            data = self.read()
            change(data)
            temporary = self.path.with_suffix('.tmp')
            temporary.write_text(json.dumps(data, indent=2)+'\n')
            temporary.chmod(0o600); temporary.replace(self.path)

    def read(self):
        return json.loads(self.path.read_text()) if self.path.exists() else {'plazas': {}, 'active': None}

    def remember(self, origin, identity, directory):
        key = plaza_key(origin)
        def change(data):
            data['plazas'][key] = {'id': key, 'origin': origin, 'agentIdentity': identity, 'stateDir': str(directory)}
            data['active'] = key
        self.update(change)

    def forget(self, key):
        def change(data):
            data['plazas'].pop(key, None)
            if data['active'] == key: data['active'] = None
        self.update(change)


def own_origin(enrollment):
    return (enrollment.connection or {}).get('publicOrigin')


def install_trusted_plazas(app, registry, enrollment):
    @app.get('/api/trusted-plazas')
    async def list_plazas():
        # Runtime's loopback origin/CSRF boundary also covers signed-out settings.
        from .store import Store
        # The saved request only changes while the access panel is checking, so
        # ask Plaza first; an unreachable Plaza leaves the last saved status.
        try: await asyncio.wait_for(enrollment.status(), 5)
        except Exception: pass
        data = registry.read()
        result = []
        for row in data['plazas'].values():
            # This window's Plaza reads its live store; the others read their own installation's.
            current = row['origin'] == own_origin(enrollment)
            store = enrollment.store if current else Store(Path(row['stateDir'])/'state')
            scope = json.loads((Path(row['stateDir'])/'public-network.json').read_text())['plazaOrigin']+'|'+row['agentIdentity']
            key = hashlib.sha256(scope.encode()).hexdigest()
            try: state = store.decrypt(store.get('_installation', 'enrollment', key)['secret']).get('state', {})
            except HTTPException: state = {}
            result.append({k: row[k] for k in ('id', 'origin', 'agentIdentity')} | {
                'active': row['id'] == data['active'], 'current': current, 'status': state.get('accessState') or state.get('status', 'not-requested'), 'ownerEmail': state.get('email'),
                'validFrom':state.get('validFrom'),'validUntil':state.get('validUntil')})
        return {'plazas': result}

    @app.delete('/api/trusted-plazas/{key}')
    async def remove_plaza(key: str, request: Request):
        from .store import Store
        data = registry.read()
        row = data['plazas'].get(key)
        if not row: raise HTTPException(404, 'Plaza is not configured.')
        # Removing this window's own Plaza also stops its live session and registration.
        active = row['origin'] == own_origin(enrollment)
        store = enrollment.store if active else Store(Path(row['stateDir'])/'state')
        scope = json.loads((Path(row['stateDir'])/'public-network.json').read_text())['plazaOrigin']+'|'+row['agentIdentity']
        secret_key = hashlib.sha256(scope.encode()).hexdigest()
        try: saved = store.decrypt(store.get('_installation', 'enrollment', secret_key)['secret'])
        except HTTPException: saved = None
        if saved and saved.get('state', {}).get('id'):
            # Revocation must be acknowledged before forgetting the only proof.
            # Offline removal stays retryable and never silently leaves a grant.
            from .tls import verify
            async with httpx.AsyncClient(trust_env=False, verify=verify(), follow_redirects=False, timeout=15) as client:
                try:
                    response = await client.post(row['origin']+'/_network/plaza/api/agent-trust/disconnect', json={
                        'id': saved['state']['id'], 'code_verifier': saved['verifier']})
                except httpx.HTTPError:
                    raise HTTPException(502, 'Plaza did not confirm removal. Retry when it is reachable.') from None
                if not response.is_success: raise HTTPException(502, 'Plaza did not confirm removal. Retry when it is reachable.')
            saved['enabled'] = False; saved['state']['status'] = response.json()['status']
            store.put('_installation', 'enrollment', secret_key, {'secret': store.encrypt(saved)})
        # Forgetting an installation's Plaza is deliberately broader than
        # signing out of one window: invalidate all its window bindings, while
        # retaining sessions still used by a different installation.
        prefix = scope+'|window|'
        with store.connect() as db:
            bindings = db.execute('SELECT * FROM terminal_logins WHERE terminal=? OR substr(terminal,1,?)=?',
                                  (scope,len(prefix),prefix)).fetchall()
            db.execute('DELETE FROM terminal_logins WHERE terminal=? OR substr(terminal,1,?)=?',
                       (scope,len(prefix),prefix))
            for binding in bindings:
                if not db.execute('SELECT 1 FROM terminal_logins WHERE session_id=?',(binding['session_id'],)).fetchone():
                    db.execute('DELETE FROM sessions WHERE id=?',(binding['session_id'],))
        engine = getattr(app.state,'engine',None)
        if active and engine:
            for owner in {binding['owner'] for binding in bindings}:
                await engine.close_owner(owner)
        if active:
            await enrollment.suspend()
            # Leaving this Plaza also leaves its directory; if that fails, Plaza retires the silent entry itself.
            await enrollment.registration.forget()
        registry.forget(key)
        return {'removed': True, 'active': active}
