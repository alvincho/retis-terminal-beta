"""Per-source encrypted durable state; no browser or Terminal dependency."""
from contextlib import contextmanager
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import sqlite3
import time

from cryptography.fernet import Fernet


class Conflict(ValueError):
    pass


class AccessDenied(PermissionError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


class ProviderVault:
    """One database/key pair per provider; owner and window remain distinct.

    Callers never choose a provider after construction. All reads, including
    idempotent replays, require the same authenticated owner and window.
    """
    def __init__(self, root, source_id):
        if not source_id or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789_' for c in source_id):
            raise ValueError('Invalid source identity.')
        self.source_id = source_id
        self.root = Path(root).resolve() / source_id
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        key_path = self.root / 'vault.key'
        if not key_path.exists():
            try:
                fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                pass
            else:
                with os.fdopen(fd, 'wb') as f:
                    f.write(Fernet.generate_key())
        self._key = key_path.read_bytes()
        self._cipher = Fernet(self._key)
        self.path = self.root / 'connections.sqlite'
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS connections(
                  owner TEXT, window TEXT, id TEXT, revision INTEGER, payload BLOB,
                  PRIMARY KEY(owner,window,id));
                CREATE TABLE IF NOT EXISTS receipts(
                  owner TEXT, window TEXT, request TEXT, fingerprint TEXT,
                  result BLOB, PRIMARY KEY(owner,window,request));
                CREATE TABLE IF NOT EXISTS quota(scope TEXT, at REAL);
            ''')
        os.chmod(self.path, 0o600)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=15)
        try:
            with db:
                yield db
        finally:
            db.close()

    def seal(self, value):
        return self._cipher.encrypt(canonical(value).encode())

    def unseal(self, value):
        return json.loads(self._cipher.decrypt(value))

    def fingerprint(self, value):
        return hmac.new(self._key, canonical(value).encode(), hashlib.sha256).hexdigest()

    def _read(self, db, owner, window, id):
        row = db.execute('SELECT revision,payload FROM connections WHERE owner=? AND window=? AND id=?', (owner,window,id)).fetchone()
        if not row:
            raise AccessDenied('Selected source connection is unavailable; no fallback is permitted.')
        return {**self.unseal(row[1]), 'id':id, 'revision':row[0], 'source_id':self.source_id}

    @staticmethod
    def public(record):
        return {k:record[k] for k in ('id','revision','source_id','label','enabled','status','priority','credentialVersion','policy') if k in record}

    def list(self, owner, window=''):
        with self.db() as db:
            ids = [r[0] for r in db.execute('SELECT id FROM connections WHERE owner=? AND window=? ORDER BY id', (owner,window))]
            return [self.public(self._read(db, owner, window, id)) for id in ids]

    def resolve(self, owner, window, id, revision=None):
        with self.db() as db:
            record = self._read(db,owner,window,id)
        if revision is not None and record['revision'] != revision:
            raise Conflict('Source connection revision changed.')
        if not record['enabled'] or record['status'] in ('denied','revoked','migration_pending'):
            raise AccessDenied('Selected source connection is disabled or lacks entitlement.')
        return record

    def mutate(self, owner, window, id, expected_revision, request_id, *, secret=None, changes=None, policy=None, origin=None):
        if not owner or not id or not request_id or type(expected_revision) is not int or expected_revision < 0:
            raise ValueError('Owner, connection, expected revision and idempotency key are required.')
        changes = dict(changes or {})
        if set(changes) - {'label','enabled','priority','status'}:
            raise ValueError('Unsupported connection setting.')
        if 'enabled' in changes and type(changes['enabled']) is not bool:
            raise ValueError('enabled must be boolean.')
        if 'priority' in changes and (type(changes['priority']) is not int or not 0 <= changes['priority'] <= 1000):
            raise ValueError('Invalid priority.')
        if 'label' in changes and (not isinstance(changes['label'],str) or not 1 <= len(changes['label']) <= 80):
            raise ValueError('Invalid connection label.')
        if secret is not None and (not isinstance(secret,str) or not 4 <= len(secret) <= 16384):
            raise ValueError('Invalid credential.')
        payload = dict(id=id, revision=expected_revision, secret=secret, changes=changes, policy=policy, origin=origin)
        fingerprint = self.fingerprint(payload)
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            receipt = db.execute('SELECT fingerprint,result FROM receipts WHERE owner=? AND window=? AND request=?', (owner,window,request_id)).fetchone()
            if receipt:
                if receipt[0] != fingerprint:
                    raise Conflict('Idempotency key was reused with different content.')
                return self.unseal(receipt[1])
            row = db.execute('SELECT revision FROM connections WHERE owner=? AND window=? AND id=?',(owner,window,id)).fetchone()
            if (row[0] if row else 0) != expected_revision:
                raise Conflict('Source connection revision changed.')
            record = self._read(db,owner,window,id) if row else dict(label=id,enabled=True,status='attempt_ready',priority=0)
            if not row and secret is None:
                raise ValueError('New connection requires a credential.')
            if secret is not None:
                record.update(secret=secret,credentialVersion=secrets.token_hex(16),status='attempt_ready')
            record.update(changes)
            if policy is not None:
                record['policy'] = policy
            if origin is not None:
                record['origin'] = origin
            record.update(id=id,revision=expected_revision+1,source_id=self.source_id)
            db.execute('INSERT OR REPLACE INTO connections VALUES(?,?,?,?,?)',(owner,window,id,record['revision'],self.seal(record)))
            result = self.public(record)
            db.execute('INSERT INTO receipts VALUES(?,?,?,?,?)',(owner,window,request_id,fingerprint,self.seal(result)))
            return result

    def consume(self, record):
        """Atomic provider-key quota across duplicate connections and windows."""
        limit = record.get('policy',{}).get('requests_per_minute')
        if type(limit) is not int or limit < 1:
            raise AccessDenied('Provider request quota has not been configured.')
        scope = self.fingerprint({'credential':record['secret']})
        now = time.time()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM quota WHERE at<?',(now-60,))
            if db.execute('SELECT count(*) FROM quota WHERE scope=?',(scope,)).fetchone()[0] >= limit:
                raise AccessDenied('Provider request quota reached; retry after the quota window.')
            db.execute('INSERT INTO quota VALUES(?,?)',(scope,now))

    def execution_result(self, db, owner, window, id, encrypted):
        saved=self.unseal(encrypted)
        if saved.get('_receipt')=='retained-result':
            if saved['expires_at']>time.time():
                return saved['result']
            expired={'status':'retention_expired','retry_safe':False}
            db.execute('UPDATE receipts SET result=? WHERE owner=? AND window=? AND request=?',
                       (self.seal(expired),owner,window,id))
            return expired
        return saved

    def execute_once(self, owner, window, request_id, request, operation, *, retention_seconds=0):
        """Persist a reservation BEFORE egress; unknown outcomes never auto-retry.

        Repeated reads return the encrypted saved result. Recovery from a process
        crash is explicitly pending, rather than duplicating a paid vendor call.
        """
        if not request_id:
            raise ValueError('Execution idempotency key required.')
        id = 'execute:' + request_id
        fingerprint = self.fingerprint(request)
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT fingerprint,result FROM receipts WHERE owner=? AND window=? AND request=?',(owner,window,id)).fetchone()
            if row:
                if row[0] != fingerprint:
                    raise Conflict('Execution idempotency conflict.')
                return self.execution_result(db,owner,window,id,row[1])
            db.execute('INSERT INTO receipts VALUES(?,?,?,?,?)',(owner,window,id,fingerprint,self.seal({'status':'pending_reconciliation','retry_safe':False})))
        result = operation()
        # A source operator must explicitly permit licensed result retention.
        # Without it, keep only the no-repeat receipt, never vendor data.
        saved=({'_receipt':'retained-result','expires_at':time.time()+retention_seconds,'result':result}
               if retention_seconds>0 else {'status':'completed_without_retention','retry_safe':False})
        with self.db() as db:
            db.execute('UPDATE receipts SET result=? WHERE owner=? AND window=? AND request=?',(self.seal(saved),owner,window,id))
        return result
