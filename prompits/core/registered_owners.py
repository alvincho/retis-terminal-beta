"""Plaza-owned evidence of authenticated agent registration, never a trust list."""
import hashlib
import sqlite3
from pathlib import Path

from fastapi import HTTPException


class RegisteredOwners:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS registered_agent_owners('
                       'agent TEXT PRIMARY KEY, credential TEXT NOT NULL, owner TEXT NOT NULL)')
        self.path.chmod(0o600)

    def connect(self):
        return sqlite3.connect(self.path, timeout=10)

    @staticmethod
    def digest(credential):
        return hashlib.sha256(credential.encode()).hexdigest()

    def owner(self, agent, credential):
        if not agent or not credential:
            return None
        with self.connect() as db:
            row = db.execute('SELECT owner FROM registered_agent_owners WHERE agent=? AND credential=?',
                             (agent, self.digest(credential))).fetchone()
        return row[0] if row else None

    def owner_of(self, agent):
        """The verified owner recorded for an agent; it decides who may remove the agent's directory entry."""
        with self.connect() as db:
            row = db.execute('SELECT owner FROM registered_agent_owners WHERE agent=?', (agent,)).fetchone()
        return row[0] if row else None

    def agents_of(self, owner):
        with self.connect() as db:
            return [row[0] for row in db.execute('SELECT agent FROM registered_agent_owners WHERE owner=?', (owner,))]

    def bind(self, agent, credential, authenticated_owner=None):
        """A credential retains its verified owner; ownership changes need a separate flow."""
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT credential,owner FROM registered_agent_owners WHERE agent=?', (agent,)).fetchone()
            if row and (row[0] != self.digest(credential) or authenticated_owner and row[1] != authenticated_owner):
                raise HTTPException(403, 'Registered agent ownership does not match its authenticated registration.')
            if authenticated_owner:
                db.execute('INSERT OR REPLACE INTO registered_agent_owners VALUES(?,?,?)',
                           (agent, self.digest(credential), authenticated_owner))
            return authenticated_owner or (row[1] if row else None)


def verified_owner(plaza, entry):
    """Resolve only the exact live identity/credential, never publisher owner metadata."""
    practice = plaza._get_plaza_practice()
    state = getattr(practice, 'state', None)
    card = entry.get('card') or {}
    identity = entry.get('agent_id') or card.get('agent_id')
    if state is None or not identity:
        return None
    with state.lock:
        current = state.agent_cards.get(identity) or {}
        credential = (state.credentials_by_id.get(identity) or {}).get('api_key')
        address = str(card.get('address') or '').rstrip('/')
        if not address or address != str(current.get('address') or '').rstrip('/'):
            return None
        if str(state.registry.get(identity) or '').rstrip('/') != address:
            return None
    return plaza._registered_agent_owners().owner(identity, credential)
