"""Which agents this account trusts now, and the connections it made, as Plaza decides and records them.

Generic container infrastructure: the Terminal shows Plaza's own answers and keeps none of them.
Agents and accounts show by name, never by id.
"""
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException

SCOPES = {'owner': 'own', 'practice': 'plaza-owner', 'account': 'trusted-account'}


async def plaza_get(network, user, path, params=None):
    try:
        response = await network.client.get(network.plaza + path, headers={'Authorization': 'Bearer ' + user['token']},
                                            params=params or {}, timeout=30)
    except httpx.RequestError:
        raise HTTPException(502, 'Plaza is temporarily unavailable. Try again.') from None
    if response.status_code in (401, 403):
        raise HTTPException(401, 'Your Plaza session expired. Sign in to see your trusted agents.')
    if not response.is_success:
        raise HTTPException(502, 'Plaza is temporarily unavailable. Try again.')
    return response.json()


def agent_row(row, registration):
    """One trusted agent by name. This Terminal shows by the name its owner gave it, not its registered id, and an
    owner that published no name says so rather than leave a bare account suffix unexplained."""
    from prompits.core.account_labels import UNNAMED  # not account_trust, which would bring Plaza's server into the kit
    owner = str(row.get('_plazaOwnerName') or '')
    own = getattr(registration, 'agent_id', None)
    current = bool(own) and row.get('id') == own
    name = row.get('name')
    if current:
        given = getattr(registration, 'display_name', None)
        name = (given() if callable(given) else given) or name
    return {'name': str(name or 'Agent'), **({'current': True} if current else {}), 'owner': owner,
            **({'ownerUnnamed': True} if owner.startswith(UNNAMED) else {}),
            'reason': SCOPES.get(row.get('_plazaScope'), 'plaza-owner'),
            'online': str(row.get('availability') or '').strip().lower() != 'offline'}


async def trusted(network, user):
    rows = await network.plaza_trust.entries(user['token'])
    registration = getattr(network, 'registration', None)
    agents = [agent_row(row, registration)
              for row in rows if row.get('_plazaTrusted') is True and row.get('_plazaViewer') == user['id']]
    # The site's gateway names Plaza's agent-trust routes /api/client-enrollment for Terminals.
    listed = await plaza_get(network, user, '/api/client-enrollment/accounts')
    accounts = [{'name': str(a.get('name') or ''), 'agentCount': int(a.get('agentCount') or 0)} for a in listed.get('accounts') or []]
    # Plaza's own Agent trust page, where trusted accounts are added and removed.
    site = urlsplit(network.plaza)
    return {'agents': sorted(agents, key=lambda a: (a['reason'], a['name'].casefold())), 'accounts': accounts,
            'trustPage': site.scheme + '://' + site.netloc + '/trusted-agents/accounts'}


async def history(network, user, *, limit=10, before=None):
    params = {'limit': limit, **({'before': before} if before else {})}
    value = await plaza_get(network, user, '/api/call-grants/history', params)
    keys = ('issuedAt', 'agent', 'agentOnline', 'caller', 'operation', 'basis', 'onward', 'outcome')
    return {'entries': [{k: entry.get(k) for k in keys} for entry in value.get('entries') or []],
            'next': value.get('next'), 'keptDays': value.get('keptDays')}
