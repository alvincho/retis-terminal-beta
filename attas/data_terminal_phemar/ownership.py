"""Use an existing Plaza profile as owner; never provision a synthetic owner."""
import copy
import json
from pathlib import Path
from phemacast.agents.plaza_client import PlazaClient


class OwnerAwarePlazaClient(PlazaClient):
    """Publish Terminal login-owner changes on the normal registry heartbeat."""
    _published_owner = None
    _registering_owner = None
    user_session = None
    session_owner = None
    _credential_owner = None

    def _registration_payload(self, saved):
        payload = super()._registration_payload(saved)
        if self.user_session is not None:
            payload['accepts_inbound_from_plaza'] = False
            payload['card']['accepts_inbound_from_plaza'] = False
        self._registering_owner = payload['card'].get('owner_user_id', '')
        return payload

    async def register_once(self):
        if self.user_session is not None:
            session = self.user_session()
            if not session or not session.get('access_token'):
                self._last_result = 'sign-in-required'
                return self._last_result
            owner = self.session_owner() if self.session_owner else ''
            if owner != self._credential_owner:
                import hashlib
                from phemacast.agents.plaza_client import CredentialStore
                if not hasattr(self, '_installation_credentials'):
                    self._installation_credentials = self.credentials.path
                    self._installation_name = self.agent_name
                suffix = hashlib.sha256(owner.encode()).hexdigest()[:16]
                self.credentials = CredentialStore(self._installation_credentials.parent/'owners'/(suffix+'.json'))
                saved = self.credentials.load()
                self._agent_id = saved.agent_id if saved else None
                self._pending_credentials = None
                self._token = None
                self.agent_name = self._installation_name + ' ' + suffix[:8]
                self._credential_owner = owner
            self._client.headers['Authorization'] = 'Bearer ' + session['access_token']
        result = await super().register_once()
        if result == 'success': self._published_owner = self._registering_owner
        return result

    async def heartbeat_once(self):
        if self._card_factory(self.public_url).get('owner_user_id', '') != self._published_owner:
            return await self.register_once()
        return await super().heartbeat_once()


def owner_fields(profile):
    id = str(profile.get('id') or profile.get('user_id') or '')
    email = str(profile.get('email') or '').strip().lower()
    if not id or not email or profile.get('status', 'active') != 'active':
        raise ValueError('An active, resolved Plaza owner profile is required.')
    public = {'id': id, 'username': profile.get('username', ''), 'email': email,
              'display_name': profile.get('display_name') or profile.get('name') or email}
    return {'owner': email, 'owner_id': id, 'owner_user_id': id,
            'owner_username': public['username'], 'owner_email': email, 'owner_profile': public}


def bind_owner(document, profile):
    result = copy.deepcopy(document)
    fields = owner_fields(profile)
    result['plaza_owner_account'] = {'user_id': fields['owner_user_id'],
        'username': fields['owner_username'], 'email': fields['owner_email'],
        'display_name': fields['owner_profile']['display_name'], 'status': 'active',
        'basis': 'existing-plaza-profile'}
    # An old owner's key must never silently override the resolved identity.
    for container in (result, result.setdefault('agent_card', {}), result['agent_card'].setdefault('meta', {})):
        for key in ('plaza_owner_key', 'plaza_owner_key_id', 'owner_key', 'owner_key_id', 'plaza_owner_key_secret', 'owner_key_secret'):
            container.pop(key, None)
    result['agent_card'].update(fields)
    result['agent_card']['meta'].update(fields)
    # Respect the native Persona access-control structure where present.
    for key in ('persona_access_control', 'access_control'):
        if isinstance(result.get(key), dict) and any(k in result[key] for k in ('owner_user_id', 'owner_id')):
            for name in ('owner_user_id', 'owner_id'):
                if name in result[key]: result[key][name] = fields['owner_user_id']
    return result


def registration_owner(kind, store, terminal_scope, state_dir):
    if kind == 'data-terminal':
        binding = store.terminal_login(terminal_scope)
        # Terminal has no fixed deployment payee. Its verified login is its owner.
        id = store.installation_owner(terminal_scope) or (binding['owner'] if binding else '')
        return {'owner_mode': 'signed-in-user', 'owner_user_id': id, 'owner_id': id,
                'owner': '', 'owner_email': '', 'owner_username': '', 'owner_profile': None}
    path = Path(state_dir) / 'agent-owner-profile.json'
    if not path.exists(): return {}
    return {**owner_fields(json.loads(path.read_text())), 'owner_mode': 'registered-profile'}


def configuration_access(kind, store, terminal_scope, state_dir, user):
    """Use the server's owner binding, never a caller-supplied role or owner ID."""
    owner = registration_owner(kind, store, terminal_scope, state_dir)
    owner_id = owner.get('owner_user_id') or ''
    return {'canConfigure': bool(user and owner_id and user['id'] == owner_id),
            'ownerConfigured': bool(owner_id)}
