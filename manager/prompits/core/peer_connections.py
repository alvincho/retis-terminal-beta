"""Registered owner connection policy; it grants no operation authority."""
from urllib.parse import urlsplit

from fastapi import HTTPException, Request
from starlette.concurrency import run_in_threadpool

from .registered_owners import verified_owner


def owner_connection(plaza, actor_id, peer_id):
    state=plaza._get_plaza_practice().state
    card=state.agent_cards.get(peer_id)
    if not isinstance(card,dict):raise HTTPException(404,'Registered peer unavailable.')
    def owner(identity):
        row=state.agent_cards.get(identity)
        if not isinstance(row,dict):raise HTTPException(404,'Registered peer unavailable.')
        identity_owner=verified_owner(plaza,{'agent_id':identity,'card':row})
        profile=plaza._get_ui_user(identity_owner) if identity_owner else None
        if not profile or not plaza._ui_user_status_is_active(profile):
            raise HTTPException(403,'Plaza has no active authenticated registration owner for this agent.')
        return profile
    actor_owner=owner(actor_id)
    peer_owner=owner(peer_id)
    origin=str(card.get('address') or '').rstrip('/')
    parsed=urlsplit(origin)
    if (parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password
            or parsed.path or parsed.query or parsed.fragment):
        raise HTTPException(409,'Registered peer has no direct execution origin.')
    accepted=str(actor_owner['id'])==str(peer_owner['id'])
    reason='same_registered_owner' if accepted else 'different_registered_owner'
    if accepted:
        entry=plaza._attach_effective_trust({'agent_id':peer_id,'card':card},actor_owner,trust_evaluated=True)
        if entry.get('trust_level') not in ('trusted','approved'):
            accepted=False;reason='owner_blocked_peer' if entry.get('trust_level')=='blocked' else 'owner_denied_peer'
    return {'schema':'owner-peer-connection/1','actor':actor_id,'peer':peer_id,'origin':origin,
            'accepted':accepted,'reason':reason}


def mount_owner_connections(plaza, agent_auth):
    @plaza.app.get('/api/agent-peer-connection')
    async def connection(request:Request, peer:str):
        actor=await run_in_threadpool(agent_auth,request.headers.get('authorization','').removeprefix('Bearer '))
        return await run_in_threadpool(owner_connection,plaza,actor['agent_id'],peer)


async def accepts_owner_connection(client, plaza, token, *, actor, peer, origin):
    """Verify the exact pair and origin with the caller's registration authority.

    Identity/ownership results are never stored as durable origin permissions.
    Every check is fresh; operation grants, ACLs and effects remain separate.
    """
    if not token or not actor or not peer:return False
    response=await client.get(plaza.rstrip('/')+'/api/agent-peer-connection',
        headers={'Authorization':'Bearer '+token},params={'peer':peer},timeout=10,follow_redirects=False)
    if response.status_code in (403,404,409):return False
    response.raise_for_status()
    value=response.json()
    return (value.get('schema')=='owner-peer-connection/1' and value.get('actor')==actor
            and value.get('peer')==peer and value.get('origin')==origin
            and value.get('accepted') is True and value.get('reason')=='same_registered_owner')
