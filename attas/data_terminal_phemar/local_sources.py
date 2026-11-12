"""A layout panel's source request goes to the user's own paired copy of that source when there is one.

The Terminal matches only what sources publish about themselves: the source id
and the exact model version. It knows nothing about any vendor. A paired copy
that is not responding is reported as such; the request never moves to another
connection on its own, so a user who runs their own copy is never routed
through Plaza without seeing it. Choosing a source for the panel, or
disconnecting the copy in Terminal settings, returns the request to Plaza's
source. A processor's data request (data_requests.py) uses exactly the source
its user chose, which may be the paired copy itself.
"""
from fastapi import HTTPException
from phemacast.network.client import UNREACHABLE
from .source_keys import source_of


class LocalSourceUnreachable(HTTPException):
    failure_category = 'local-source-unreachable'

    def __init__(self, name):
        super().__init__(503, (name or 'Your local data source') + ' is not responding. Start it again, or disconnect it in '
                         'Terminal settings → Connections to use Plaza’s source.')


class LocalPairingRejected(HTTPException):
    failure_category = 'local-pairing-rejected'

    def __init__(self, name):
        super().__init__(401, (name or 'Your local data source') + ' no longer accepts this Terminal’s pairing. Disconnect it in '
                         'Terminal settings → Connections, then pair it again.')


def serves(entry, pulse, parameters):
    """This paired copy shares the operation and, for a model request, the exact model version."""
    pulses = {p.get('pulse_name') or p.get('name') for p in (entry.get('meta') or {}).get('supported_pulses') or [] if isinstance(p, dict)}
    if pulse not in pulses:
        return False
    model = parameters.get('target_model') if isinstance(parameters, dict) else None
    if model is None:
        return True
    return any(isinstance(row, dict) and row.get('modelRef') == model for row in source_of(entry).get('modelSupport') or [])


async def local_copy(network, user, agent, pulse, parameters):
    """The user's paired local agent publishing the same source as `agent`, or None for the agent path."""
    if pulse != 'data_fetch' or agent.get('localId') or not getattr(network, 'local_clients', None):
        return None
    source = source_of(agent).get('source_id')
    owner = network.local_owner(user['token'])
    if not isinstance(source, str) or not source or not owner:
        return None
    for entry in await network.directory(user['token']):
        if (entry.get('localId') and entry.get('localOwner') == owner
                and source_of(entry).get('source_id') == source and serves(entry, pulse, parameters)):
            return entry
    return None


async def call(network, user, entry, pulse, parameters):
    try:
        return await network.pulse(entry, pulse, parameters, user['token'])
    except HTTPException as error:
        if error.status_code == 503 and error.detail == UNREACHABLE:
            raise LocalSourceUnreachable(entry.get('name')) from error
        if error.status_code == 401:
            # The local source refused this Terminal's grant (revoked by its owner, or its state was reset): not a Plaza sign-in.
            raise LocalPairingRejected(entry.get('name')) from error
        raise
