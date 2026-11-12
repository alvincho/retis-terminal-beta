"""A user's provider keys, kept only in this Terminal.

Each key is encrypted in this Terminal's vault and scoped to one Plaza account
and Terminal window. It is used only for this Terminal's own requests to the
vendor hosts the source published when the key was saved. No agent, Plaza or
Retis server receives it. Only the key's presence and a mask leave this module.
"""
import asyncio
import hashlib
import re
import secrets

from fastapi import HTTPException, Request

from .contracts import identifier
from .window_sessions import preferences, state_key

KIND = 'connection'
MODES = ('own', 'source')
HOST = re.compile(r'[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?')
# An LLM provider an agent publishes (meta.llm_providers) is kept like a source, as `llm.<provider id>`.
LLM = 'llm.'
PROVIDER_ID = re.compile(r'[A-Za-z0-9_-]{1,64}')



# The pages' own message catalog (web/ui-messages.js, built from ui-messages.tsv), so a message the server sends a
# window reads in that window's language like the rest of the Terminal.
_MESSAGES = None


def _catalog():
    global _MESSAGES
    if _MESSAGES is None:
        import json
        from pathlib import Path
        try:
            text = (Path(__file__).resolve().parent / 'web' / 'ui-messages.js').read_text(encoding='utf-8')
            _MESSAGES = json.loads(text.split('export const MESSAGES = ', 1)[1].strip().rstrip(';'))
        except (OSError, IndexError, ValueError):
            _MESSAGES = {}
    return _MESSAGES


def say(store, user, key, *values):
    """`key` in the asking window's UI language, else the installation's, else English; {0}… filled with `values`."""
    language = None
    for read in (lambda: preferences(store, user), lambda: store.get('_installation', 'ui-language', 'terminal')):
        try:
            language = (read() or {}).get('uiLanguage')
        except Exception:
            language = None
        if language:
            break
    text = (_catalog().get(key) or {}).get(language) or key
    for index, value in enumerate(values):
        text = text.replace('{' + str(index) + '}', str(value))
    return text

def source_of(agent):
    value = (agent.get('meta') or {}).get('data_source') if isinstance(agent, dict) else None
    return value if isinstance(value, dict) else {}


def llm_providers(agent):
    """The LLM providers an agent publishes for own-key calls (no vendor names here)."""
    rows = (agent.get('meta') or {}).get('llm_providers') if isinstance(agent, dict) else None
    return [p for p in rows or [] if isinstance(p, dict) and isinstance(p.get('id'), str) and PROVIDER_ID.fullmatch(p['id'])]


def llm_provider(agent, source):
    return next((p for p in llm_providers(agent) if LLM + p['id'] == source), None)


def sources_of(agent):
    """Every key-holding source an agent publishes: its data source and its LLM providers."""
    ids = [LLM + p['id'] for p in llm_providers(agent)]
    source = source_of(agent).get('source_id')
    return ([source] if isinstance(source, str) and source else []) + ids


def info_of(agent, source):
    """What an agent publishes about one of its sources, in the data-source shape."""
    if source.startswith(LLM):
        provider = llm_provider(agent, source)
        if provider is None:
            return {}
        hosts = (provider.get('directAccess') or {}).get('hosts') if isinstance(provider.get('directAccess'), dict) else []
        return {'source_id': source, 'source_name': provider.get('name') if isinstance(provider.get('name'), str) else source[len(LLM):],
                'kind': 'llm', 'directAccess': {'hosts': hosts or [], 'credential': 'required'}}
    info = source_of(agent)
    return info if info.get('source_id') == source else {}


def direct(info):
    """What a source publishes about calling its vendor directly (no vendor names here)."""
    access = info.get('directAccess') if isinstance(info.get('directAccess'), dict) else {}
    hosts = sorted({h for h in access.get('hosts') or [] if isinstance(h, str) and HOST.fullmatch(h)})[:8]
    credential = access.get('credential') if access.get('credential') in ('required', 'optional') else 'none'
    return {'hosts': hosts, 'credential': credential, 'streams': access.get('streams') is True}


def saved(store, user, source=None):
    window = user.get('terminalWindow', '')
    return [r for r in store.list(user['id'], KIND)
            if r.get('terminalWindow', '') == window and (source is None or r.get('source') == source)]


def elsewhere(store, user, source):
    """Whether this account keeps a usable key for the source in another Terminal window (presence only)."""
    window = user.get('terminalWindow', '')
    return any(r.get('source') == source and r.get('terminalWindow', '') != window and r.get('secret')
               and r.get('enabled', True) is not False and r.get('status') not in ('denied', 'revoked')
               for r in store.list(user['id'], KIND))


def mode(policy, source, info):
    chosen = (policy.get('sourceCredentials') or {}).get(source, 'own')
    # "Use source key" exists only for data the source's operator owns.
    return 'source' if chosen == 'source' and info.get('operatorOwnedData') is True else 'own'


def usable_key(store, user, source):
    """This window's saved key for a source, decrypted only for the vendor request."""
    for record in saved(store, user, source):
        if record.get('enabled', True) is not False and record.get('status') not in ('denied', 'revoked'):
            try:
                key = store.decrypt(record['secret']).get('api_key', '')
            except Exception:
                continue
            if key:
                return record, key
    return None, ''


def route(store, user, agent, policy=None):
    """The own-key plan for a request to this source agent, or None for the agent path.

    Keyless public sources, and sources whose data their operator owns when the
    user chose its key, keep the existing agent path. Every other request goes
    to the vendor directly, or fails here without contacting the agent.
    """
    info = source_of(agent)
    source = info.get('source_id')
    if not isinstance(source, str) or not source:
        return None
    access = direct(info)
    policy = preferences(store, user) if policy is None else policy
    if access['credential'] == 'none' or mode(policy, source, info) == 'source':
        return None
    name = agent.get('name') or source
    if not access['hosts']:
        raise HTTPException(409, name + ' does not publish a vendor API your Terminal can call with your key.')
    record, key = usable_key(store, user, source)
    if record is None:
        if access['credential'] == 'required':
            raise HTTPException(409, say(store, user, 'Add your {0} key in Terminal settings → Data sources.', name))
        return {'source': source, 'record': None, 'key': '', 'hosts': access['hosts']}
    hosts = [h for h in record.get('vendorHosts') or [] if isinstance(h, str)]
    if not hosts:
        raise HTTPException(409, 'Save your ' + name + ' key again so this Terminal can pin its vendor host.')
    return {'source': source, 'record': record, 'key': key, 'hosts': hosts}


def llm_key(store, user, agent, provider):
    """This window's own key for one LLM provider the agent publishes, and its pinned hosts; else where to add it."""
    source = LLM + provider['id']
    name = provider.get('name') if isinstance(provider.get('name'), str) and provider.get('name') else provider['id']
    record, key = usable_key(store, user, source)
    if record is None:
        raise HTTPException(409, say(store, user, 'Add your {0} key in Terminal settings → LLM keys.', name))
    hosts = [h for h in record.get('vendorHosts') or [] if isinstance(h, str)]
    published = direct(info_of(agent, source))['hosts']
    if not hosts or not published or not set(published) <= set(hosts):
        raise HTTPException(409, 'Save your ' + name + ' key again so this Terminal can pin its current host.')
    return key, hosts


def row(store, user, source, agents, policy):
    info = info_of(agents[0], source) if agents else {}
    access = direct(info)
    records = saved(store, user, source)
    record = records[0] if records else None
    pinned = list(record.get('vendorHosts') or []) if record else []
    chosen = mode(policy, source, info)
    reason = ''
    if not agents:
        reason = 'This source is not available in Plaza now.'
    elif chosen == 'own' and access['credential'] != 'none':
        if not access['hosts']:
            reason = 'This source does not publish a vendor API your Terminal can call yet.'
        elif record and not pinned:
            reason = 'Save your key again so this Terminal can pin its vendor host.'
        elif record and not set(access['hosts']) <= set(pinned):
            reason = 'The source now names another vendor host. Save your key again to send it there.'
        elif access['credential'] == 'required' and not record:
            reason = 'Add your key to use this source.'
    llm = source.startswith(LLM)
    name = (info.get('source_name') if llm else agents[0].get('name') if agents else '') or source.removeprefix(LLM).replace('_', ' ').title()
    return {'sourceId': source, 'name': name, 'kind': 'llm' if llm else 'data',
            'mode': chosen, 'credential': access['credential'], 'keyConfigured': bool(record),
            'keyInOtherWindow': not record and elsewhere(store, user, source),
            'maskedKey': (record or {}).get('masked', ''), 'vendorHosts': pinned or access['hosts'], 'pinned': bool(pinned),
            'ownKeySupported': bool(access['hosts']) and access['credential'] != 'none',
            'sourceKeySupported': info.get('operatorOwnedData') is True, 'streams': access['streams'],
            'accessible': not reason, 'reason': reason}


def save_source(store, user, source, body, agents):
    """Save this window's choice and key; `agents` are the Plaza-trusted agents for the source."""
    source = identifier(source)
    if not isinstance(body, dict) or set(body) - {'mode', 'apiKey', 'removeKey'} or body.get('mode') not in MODES:
        raise HTTPException(422, 'Choose Use my key or Use source key.')
    key = body.get('apiKey')
    if key is not None and (not isinstance(key, str) or not 4 <= len(key.strip()) <= 4096 or any(c in key for c in '\r\n\0')):
        raise HTTPException(422, 'Enter a valid API key.')
    if 'removeKey' in body and type(body['removeKey']) is not bool:
        raise HTTPException(422, 'Invalid key removal request.')
    if key is not None and body.get('removeKey'):
        raise HTTPException(422, 'Save a key or remove it, not both.')
    infos = [info_of(a, source) for a in agents]
    if body['mode'] == 'source' and not (infos and all(i.get('operatorOwnedData') is True for i in infos)):
        raise HTTPException(422, 'This source serves third-party data. Use your own key.')
    hosts = []
    if key is not None:
        published = {tuple(direct(i)['hosts']) for i in infos if direct(i)['credential'] != 'none'}
        if not infos or not published or published == {()}:
            raise HTTPException(422, 'This source does not publish a vendor API that takes your key.')
        if len(published) != 1:
            # Two agents for one source disagree; never pin a host only one of them names.
            raise HTTPException(409, 'Agents for this source name different vendor hosts: ' + ', '.join(sorted({h for p in published for h in p})) + '.')
        hosts = list(next(iter(published)))
    window = user.get('terminalWindow', '')
    if key is not None or body.get('removeKey'):
        # Replacement and removal touch only this account and Terminal window.
        for old in saved(store, user, source):
            store.delete(user['id'], KIND, old['id'])
    if key is not None:
        key = key.strip()
        store.put(user['id'], KIND, hashlib.sha256((window + '|' + source).encode()).hexdigest()[:32], {
            'source': source, 'label': source, 'terminalWindow': window, 'enabled': True, 'priority': 0,
            'status': 'attempt_ready', 'credentialVersion': secrets.token_hex(16),
            'secret': store.encrypt({'api_key': key}), 'masked': '••••' + key[-4:], 'vendorHosts': hosts})
    old = preferences(store, user)
    value = {k: v for k, v in old.items() if k not in ('id', 'revision', 'updatedAt')}
    value['sourceCredentials'] = {**(old.get('sourceCredentials') or {}), source: body['mode']}
    store.put(user['id'], 'settings', state_key(user) if window else 'preferences', value)
    return row(store, user, source, agents, preferences(store, user))


def mount_source_keys(app, principal, configuration_owner, store, network):
    async def directory(user, trusted=False):
        rows = await asyncio.wait_for(network.trusted_agents(user['token']) if trusted else network.directory(user['token']), 8)
        grouped = {}
        for agent in rows:
            for source in sources_of(agent):
                grouped.setdefault(source, []).append(agent)
        return grouped

    @app.get('/api/sources')
    async def sources(request: Request):
        user = await principal(request)
        warning = ''
        try:
            grouped = await directory(user)
        except Exception:
            grouped, warning = {}, 'Source discovery is unavailable. Showing the keys saved in this Terminal.'
        policy = preferences(store, user)
        known = set(grouped) | {r.get('source') for r in saved(store, user) if isinstance(r.get('source'), str)}
        rows = [row(store, user, source, grouped.get(source, []), policy) for source in known]
        return {'sources': sorted(rows, key=lambda r: (r['credential'] == 'none', r['name'].lower())), 'warning': warning}

    @app.put('/api/sources/{source}')
    async def configure(source: str, request: Request):
        user = await configuration_owner(request)
        body = await request.json()
        source = identifier(source)
        agents = []
        # Pinning a key's vendor host, or choosing a source's key, needs Plaza's
        # current trust decision. Removing a key or choosing your own never does.
        if isinstance(body, dict) and (body.get('apiKey') is not None or body.get('mode') == 'source'):
            try:
                agents = (await directory(user, trusted=True)).get(source, [])
            except Exception:
                raise HTTPException(503, 'Plaza is unavailable, so this Terminal cannot confirm the source’s vendor host. Try again.') from None
        result = save_source(store, user, source, body, agents)
        if not agents:
            try:
                result = row(store, user, source, (await directory(user)).get(source, []), preferences(store, user))
            except Exception:
                pass
        return result
