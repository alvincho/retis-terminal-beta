"""Owner-approved agents: practice calls only, never UI or configuration.

When the Plaza owner approves another account's agent (an agent-trust request),
that agent and the account using it through the approved session may call the
practices of agents the Plaza owner owns. Nothing else is granted: UI routes and
configuration stay with each agent's owner, and personal trust lists confer no
access. Same-owner agents keep connecting automatically.
"""
from fastapi import HTTPException

BASIS = 'owner_approved_agent'
SAME_OWNER = 'same_owner'
# A practice its provider published for any registered agent (paid, or marked public).
PUBLISHED = 'published_practice'
# Another account the viewer chose to trust on the Agent trust page (account_trust.py).
TRUSTED_ACCOUNT = 'viewer_trusted_account'


def card_owner(card):
    card = card or {}
    return card.get('owner_user_id') or (card.get('meta') or {}).get('owner_user_id') or ''


def approval_owner(plaza):
    """The account that reviews agent trust on this Plaza, or '' when none is configured."""
    authority = getattr(getattr(plaza, 'agent_trust_requests', None), 'authority', None)
    if authority is None:
        return ''
    try:
        return str(authority.owner().get('id') or '')
    except HTTPException:
        return ''


def is_practice_operation(operation):
    """Grant operations are pulse or practice names for /use_practice calls and exact paths otherwise."""
    return isinstance(operation, str) and bool(operation) and not operation.startswith('/')


# Path segments that name UI or configuration; an approved agent is never granted them.
# Each agent still enforces owner-only access on its own UI and configuration routes.
OWNER_ONLY_SEGMENTS = {'config', 'configuration', 'settings', 'preferences', 'admin', 'credentials', 'secrets',
                       'install', 'password', 'trust', 'ui-access', 'owner', 'static', 'ui'}


def is_approved_operation(operation):
    """What an owner-approved agent may be granted: a practice, or an /api/ call that is not UI or configuration."""
    if is_practice_operation(operation):
        return True
    if not isinstance(operation, str) or not operation.startswith('/api/') or len(operation) > 200:
        return False
    segments = [part.lower() for part in operation.split('?', 1)[0].split('/') if part]
    return not any(part in OWNER_ONLY_SEGMENTS or part.startswith(('config', 'setting')) for part in segments)


def approved_agent(plaza, card):
    """Whether a registered agent belongs to an installation the Plaza owner currently approves."""
    identity = ((card or {}).get('meta') or {}).get('trusted_agent_identity')
    owner = card_owner(card)
    if not identity or not owner:
        return False
    try:
        plaza.agent_trust_requests.authorize_installation(identity, owner)
    except (AttributeError, HTTPException):
        return False
    return True


def call_basis(plaza, actor_card, audience_card, user=None):
    """Why `actor` may call `audience`: same owner, owner-approved (practices only), or None."""
    actor_owner, audience_owner = card_owner(actor_card), card_owner(audience_card)
    if actor_owner and actor_owner == audience_owner:
        return SAME_OWNER
    if user and user.get('id') != actor_owner:
        return None
    owner = approval_owner(plaza)
    if owner and audience_owner == owner and approved_agent(plaza, actor_card):
        return BASIS
    return None


def _cost(pulse):
    value = pulse.get('cost_points', pulse.get('cost'))
    if isinstance(value, dict):
        value = value.get('amount', value.get('points'))
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def published_practice(audience_card, operation):
    """Whether the provider published this practice for any registered agent: paid, or explicitly public."""
    if not is_practice_operation(operation):
        return False
    card = audience_card or {}
    pulses = (card.get('meta') or {}).get('supported_pulses') or card.get('supported_pulses') or []
    for pulse in pulses:
        if not isinstance(pulse, dict) or operation not in {pulse.get('pulse_name'), pulse.get('name'), pulse.get('id')}:
            continue
        if pulse.get('public') is True or str(pulse.get('access') or '').lower() == 'public' or _cost(pulse) > 0:
            return True
    return False


def viewer_basis(plaza, viewer_profile, agent_owner):
    """Directory decision for an account using an approved agent session: practices of the Plaza owner's agents."""
    profile = viewer_profile or {}
    if not (profile.get('client_session_id') or profile.get('approved_agent')):
        return None
    owner = approval_owner(plaza)
    return BASIS if owner and agent_owner == owner and profile.get('id') != owner else None
