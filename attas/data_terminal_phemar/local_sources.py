"""A layout panel's source request goes to the user's own copy of that source when one runs on this computer.

The Terminal matches only what sources publish about themselves: the source id
and the exact model version. It knows nothing about any vendor. A copy that
runs on this computer is used at once, with no pairing, and costs nothing. A
copy that stops answering mid-request is reported as such; once it no longer
runs, requests use Plaza's source again. Choosing a source for the panel
overrides this. A processor's data request (data_requests.py) uses exactly the
source its user chose, which may be the local copy itself.
"""
from fastapi import HTTPException
from phemacast.network.client import UNREACHABLE
from .source_keys import source_of


class LocalSourceUnreachable(HTTPException):
    failure_category = 'local-source-unreachable'

    def __init__(self, name):
        super().__init__(503, (name or 'Your local data source') + ' is not responding. Start it again in Retis MAS Manager, '
                         'or choose another source for this panel.')


def serves(entry, pulse, parameters):
    """This local copy shares the operation and, for a model request, the exact model version."""
    pulses = {p.get('pulse_name') or p.get('name') for p in (entry.get('meta') or {}).get('supported_pulses') or [] if isinstance(p, dict)}
    if pulse not in pulses:
        return False
    model = parameters.get('target_model') if isinstance(parameters, dict) else None
    if model is None:
        return True
    return any(isinstance(row, dict) and row.get('modelRef') == model for row in source_of(entry).get('modelSupport') or [])


async def local_copy(network, user, agent, pulse, parameters):
    """The local agent on this computer publishing the same source as `agent`, or None for the agent path."""
    if pulse != 'data_fetch' or agent.get('localId') or not getattr(network, 'local_clients', None):
        return None
    source = source_of(agent).get('source_id')
    if not isinstance(source, str) or not source:
        return None
    for entry in await network.directory(user['token']):
        if entry.get('localId') and source_of(entry).get('source_id') == source and serves(entry, pulse, parameters):
            return entry
    return None


async def call(network, user, entry, pulse, parameters):
    try:
        return await network.pulse(entry, pulse, parameters, user['token'])
    except HTTPException as error:
        if error.status_code == 503 and error.detail == UNREACHABLE or error.status_code == 403 and 'not running' in str(error.detail):
            raise LocalSourceUnreachable(entry.get('name')) from error
        raise
