"""Server-configured interface trust never authorizes an operation's effects."""

SCHEMA = 'phemacast:agent-view-trust/1'
DISCLOSURES = ('codeExecution', 'privateData', 'localNetwork')


def trusted_interface_approval(entry, peer, context, manifest, *, successor=False):
    """Reuse explicit trust for one verified publisher owner, actor and window.

    The container has already verified pairing, current discovery, origin,
    immutable assets and operation contracts. Neither a Phema nor publisher
    metadata can supply this server-side policy. Every effect still requires
    its ordinary confirmation, scoped grant and current owner authorization.
    """
    policy = entry.get('interfaceTrust')
    fields = {'schema', 'actor', 'window', 'publisherOwnerId',
              'followPublisherReleases', 'disclosure'}
    if not isinstance(policy, dict) or set(policy) != fields or policy['schema'] != SCHEMA:
        return None
    identities = ('actor', 'window', 'publisherOwnerId')
    if any(not isinstance(policy[key], str) or not 1 <= len(policy[key]) <= 200 for key in identities):
        return None
    if type(policy['followPublisherReleases']) is not bool or successor and not policy['followPublisherReleases']:
        return None
    choices = policy['disclosure']
    if not isinstance(choices, dict) or set(choices) != set(DISCLOSURES):
        return None
    if any(type(choices[key]) is not bool for key in DISCLOSURES):
        return None
    card = peer.get('card') or {}
    declared = (card.get('owner_user_id'), (peer.get('meta') or {}).get('owner_user_id'))
    if any(owner is not None and owner != '' and not isinstance(owner, str) for owner in declared):
        return None
    owners = {owner for owner in declared if isinstance(owner, str) and owner}
    if owners != {policy['publisherOwnerId']}:
        return None
    if (policy['actor'], policy['window']) != (context['actor'], context['window']):
        return None
    if any(manifest['disclosure'][key] and not choices[key] for key in DISCLOSURES):
        return None
    return dict(choices)
