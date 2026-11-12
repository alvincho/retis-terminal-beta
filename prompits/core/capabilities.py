"""Product-neutral, owned and versioned capability definitions and matching."""
from __future__ import annotations

import copy
import hashlib
import json
import re
import sqlite3
import time
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from pathlib import Path

NAMESPACE = re.compile(r"^[a-z][a-z0-9.-]{1,63}$")
CAPABILITY = re.compile(r"^[a-z][a-z0-9.-]{1,63}:[a-z][a-z0-9._-]{0,127}$")
VERSION = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def version(value):
    if not isinstance(value, str) or not VERSION.fullmatch(value):
        raise ValueError('Use a stable major.minor.patch capability version.')
    return tuple(map(int, value.split('.')))


def validate_definition(value):
    if not isinstance(value, dict) or not CAPABILITY.fullmatch(str(value.get('id', ''))):
        raise ValueError('A namespaced capability ID is required.')
    version(value.get('version'))
    if len(canonical(value).encode()) > 128_000:
        raise ValueError('Capability definition exceeds 128 KB.')
    if not isinstance(value.get('description'), str) or not value['description'].strip():
        raise ValueError('Describe the behavior guaranteed by this capability.')
    for key in ('configurationSchema', 'inputSchema', 'outputSchema'):
        if not isinstance(value.get(key), dict):
            raise ValueError(key + ' must be a schema object.')
        # Registry definitions are data. Remote schema loading is never implicit.
        def inspect(node, depth=0):
            if depth > 32: raise ValueError('Schema nesting exceeds 32 levels.')
            if isinstance(node, dict):
                if any(key in node and not str(node[key]).startswith('#') for key in ('$ref', '$dynamicRef', '$recursiveRef')):
                    raise ValueError('Schemas must use local references.')
                for item in node.values(): inspect(item, depth+1)
            elif isinstance(node, list):
                for item in node: inspect(item, depth+1)
        inspect(value[key])
        try: Draft202012Validator.check_schema(value[key])
        except SchemaError as exc: raise ValueError('Invalid capability schema: ' + key) from exc
    dependencies = value.get('dependencies', [])
    if not isinstance(dependencies, list) or len(dependencies) > 64:
        raise ValueError('Invalid capability dependencies.')
    for item in dependencies: validate_requirement(item)
    examples = value.get('examples', [])
    if not isinstance(examples, list) or len(examples) > 32:
        raise ValueError('At most 32 conformance examples are supported.')
    for example in examples:
        if not isinstance(example, dict) or not isinstance(example.get('name'), str):
            raise ValueError('Conformance examples require a name.')
        for field, schema in [('configuration', 'configurationSchema'), ('input', 'inputSchema'), ('output', 'outputSchema')]:
            if field in example:
                errors = list(Draft202012Validator(value[schema]).iter_errors(example[field]))
                if bool(errors) == example.get('valid', True):
                    raise ValueError('Conformance example does not match its expected validity: ' + example['name'])
    return copy.deepcopy(value)


def validate_requirement(item):
    if not isinstance(item, dict) or not CAPABILITY.fullmatch(str(item.get('id', ''))):
        raise ValueError('Invalid capability requirement.')
    version(item.get('version'))
    if 'required' in item and not isinstance(item['required'], bool):
        raise ValueError('Capability required must be boolean.')
    if not isinstance(item.get('features', []), list) or any(not isinstance(x, str) for x in item.get('features', [])):
        raise ValueError('Capability features must be strings.')
    if not isinstance(item.get('limits', {}), dict) or any(isinstance(v, bool) or not isinstance(v, (float, int)) or v < 0 for v in item.get('limits', {}).values()):
        raise ValueError('Capability limits must be nonnegative numbers.')
    canonical(item)


def consolidate_requirements(requirements):
    """Combine repeated requirements without weakening their constraints."""
    grouped = {}
    for item in requirements:
        validate_requirement(item)
        key = (item['id'], item['version'])
        if key not in grouped:
            grouped[key] = copy.deepcopy(item)
            continue
        current = grouped[key]
        current['required'] = current.get('required', True) or item.get('required', True)
        if current.get('features') or item.get('features'):
            current['features'] = sorted(set(current.get('features', [])) | set(item.get('features', [])))
        for name, limit in item.get('limits', {}).items():
            current.setdefault('limits', {})[name] = max(current.get('limits', {}).get(name, 0), limit)
    return list(grouped.values())


def negotiate(requirements, advertisement):
    """Same-major additive matching; limits describe resources requested/available."""
    if not isinstance(requirements, list) or len(requirements) > 128:
        raise ValueError('At most 128 capability requirements are supported.')
    if not isinstance(advertisement, dict) or advertisement.get('schema') != 'capability-advertisement/1':
        raise ValueError('Unsupported capability advertisement.')
    capabilities = advertisement.get('capabilities')
    if not isinstance(capabilities, list) or len(capabilities) > 256:
        raise ValueError('Invalid capability advertisement.')
    offered = {}
    for entry in capabilities:
        validate_requirement(entry)
        key = (entry['id'], entry['version'])
        if key in offered: raise ValueError('Duplicate capability advertisement.')
        offered[key] = entry
    missing, optional, matched = [], [], []
    for need in requirements:
        validate_requirement(need)
        requested = version(need['version'])
        candidates = [v for (id, ver), v in offered.items() if id == need['id'] and version(ver)[0] == requested[0] and version(ver) >= requested]
        compatible = [v for v in candidates if set(need.get('features', [])).issubset(v.get('features', [])) and all(v.get('limits', {}).get(k, -1) >= n for k, n in need.get('limits', {}).items())]
        if compatible:
            selected = min(compatible, key=lambda v: version(v['version']))
            # Verification supplied by a client is not registry endorsement.
            matched.append({'id': need['id'], 'version': selected['version'], 'verification': 'declared'})
        else:
            issue = {'id': need['id'], 'version': need['version'], 'reason': 'constraints' if candidates else 'unsupported-version', 'limits': need.get('limits', {}), 'features': need.get('features', [])}
            (missing if need.get('required', True) else optional).append(issue)
    return {'compatible': not missing, 'missing': missing, 'optionalMissing': optional, 'matched': matched, 'advertisementHash': digest(advertisement)}


class CapabilityRegistry:
    """SQLite persistence with atomic ownership and immutable publication checks."""
    def __init__(self, path, *, reserved=()):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.reserved = set(reserved) | {'system', 'plaza'}
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS capability_namespaces(id TEXT PRIMARY KEY, owner TEXT NOT NULL, created REAL NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS capability_definitions(id TEXT, version TEXT, owner TEXT, digest TEXT, payload TEXT, published REAL, deprecated INTEGER DEFAULT 0, PRIMARY KEY(id,version))')
            db.execute('CREATE TABLE IF NOT EXISTS capability_evidence(id TEXT PRIMARY KEY, capability TEXT, version TEXT, implementation TEXT, issuer TEXT, payload TEXT, created REAL, revoked INTEGER DEFAULT 0)')

    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        return db

    def claim(self, namespace, owner, *, admin=False):
        if not isinstance(namespace, str) or not NAMESPACE.fullmatch(namespace) or not owner:
            raise ValueError('A namespace and authenticated owner are required.')
        if namespace in self.reserved and not admin:
            raise PermissionError('This namespace is reserved for an administrator.')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT owner FROM capability_namespaces WHERE id=?', (namespace,)).fetchone()
            if old and old['owner'] != owner:
                raise PermissionError('Namespace already belongs to another owner.')
            db.execute('INSERT OR IGNORE INTO capability_namespaces VALUES(?,?,?)', (namespace, owner, time.time()))
        return {'namespace': namespace, 'owner': owner}

    def namespaces(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute('SELECT * FROM capability_namespaces ORDER BY id')]

    def negotiate(self, requirements, advertisement):
        """Resolve registered dependencies; unknown definitions are explicit errors."""
        negotiate(requirements, advertisement)  # Validate before graph traversal.
        expanded, visiting, seen = [], set(), set()
        def visit(need, required=True):
            key = (need['id'], need['version'])
            if key in visiting: raise ValueError('Cyclic capability dependency.')
            mandatory = required and need.get('required', True)
            identity = (*key, mandatory, digest(need))
            if identity in seen: return
            if len(seen) >= 128: raise ValueError('Capability dependency graph exceeds 128 nodes.')
            record = self.get(*key)
            seen.add(identity); visiting.add(key)
            expanded.append({**need, 'required': mandatory})
            for child in record['definition'].get('dependencies', []): visit(child, mandatory)
            visiting.remove(key)
        for need in requirements: visit(need)
        result = negotiate(expanded, advertisement)
        implementation = advertisement.get('implementationHash')
        for match in result['matched']:
            try:evidence = self.evidence(match['id'], match['version'], implementation) if implementation else []
            except KeyError:evidence = []  # An unregistered newer implementation is only declared.
            tested = [e for e in evidence if e['trusted'] and not e['revoked'] and e['passed']]
            if tested:
                match.update(verification='tested', evidence=[e['id'] for e in tested])
        return result

    def attest(self, value, issuer, *, admin=False):
        """Record bounded evidence tied to both an immutable contract and build.

        A publisher's self-report is visible but cannot become trusted evidence.
        Only the Plaza administrator can attest a locally reviewed test report.
        This is test evidence, never a code-installation permission or endorsement.
        """
        record = self.get(value.get('capability'), value.get('version'))
        if record['owner'] != issuer and not admin:
            raise PermissionError('Only the publisher or a verifier can submit evidence.')
        if value.get('definitionHash') != record['contentHash']:
            raise ValueError('Evidence must identify the published definition hash.')
        for field in ('implementationHash', 'reportHash'):
            if not re.fullmatch('[0-9a-f]{64}', str(value.get(field, ''))):
                raise ValueError('Evidence requires a SHA-256 ' + field)
        cases = value.get('cases')
        if not isinstance(cases, list) or not cases or len(cases) > 500 or any(not isinstance(c, dict) or not isinstance(c.get('name'), str) or type(c.get('passed')) is not bool for c in cases):
            raise ValueError('Evidence requires named test case results.')
        payload = {k:copy.deepcopy(value[k]) for k in ('capability', 'version', 'definitionHash', 'implementationHash', 'reportHash', 'cases')}
        payload.update(issuer=issuer, trusted=bool(admin), passed=all(c['passed'] for c in cases))
        if len(canonical(payload).encode()) > 128_000: raise ValueError('Evidence exceeds 128 KB.')
        id = digest(payload)
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO capability_evidence VALUES(?,?,?,?,?,?,?,0)', (id, value['capability'], value['version'], value['implementationHash'], issuer, canonical(payload), time.time()))
        return next(e for e in self.evidence(value['capability'], value['version']) if e['id'] == id)

    def evidence(self, id, ver, implementation=None):
        self.get(id, ver)
        with self.connect() as db:
            rows = db.execute('SELECT * FROM capability_evidence WHERE capability=? AND version=? ORDER BY created DESC', (id, ver)).fetchall()
        return [{**json.loads(r['payload']), 'id':r['id'], 'createdAt':r['created'], 'revoked':bool(r['revoked'])} for r in rows if not implementation or r['implementation'] == implementation]

    def revoke_evidence(self, id, issuer, *, admin=False):
        with self.connect() as db:
            row = db.execute('SELECT issuer FROM capability_evidence WHERE id=?', (id,)).fetchone()
            if not row: raise KeyError(id)
            if row['issuer'] != issuer and not admin: raise PermissionError('Only the issuer or administrator may revoke evidence.')
            db.execute('UPDATE capability_evidence SET revoked=1 WHERE id=?', (id,))
        return {'id':id, 'revoked':True}

    def publish(self, value, owner):
        definition = validate_definition(value)
        fingerprint = digest(definition)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            namespace = db.execute('SELECT owner FROM capability_namespaces WHERE id=?', (definition['id'].split(':')[0],)).fetchone()
            if not namespace or namespace['owner'] != owner:
                raise PermissionError('Only the namespace owner may publish definitions.')
            old = db.execute('SELECT digest FROM capability_definitions WHERE id=? AND version=?', (definition['id'], definition['version'])).fetchone()
            if old and old['digest'] != fingerprint:
                raise ValueError('Published capability versions are immutable; publish a new version.')
            db.execute('INSERT OR IGNORE INTO capability_definitions(id,version,owner,digest,payload,published) VALUES(?,?,?,?,?,?)', (definition['id'], definition['version'], owner, fingerprint, canonical(definition), time.time()))
        return self.get(definition['id'], definition['version'])

    def get(self, id, ver):
        with self.connect() as db:
            row = db.execute('SELECT * FROM capability_definitions WHERE id=? AND version=?', (id, ver)).fetchone()
        if not row: raise KeyError(id)
        return self._public(row)

    @staticmethod
    def _public(row):
        return {'definition': json.loads(row['payload']), 'owner': row['owner'], 'contentHash': row['digest'], 'publishedAt': row['published'], 'deprecated': bool(row['deprecated']), 'verification': 'declared'}

    def search(self, query='', namespace='', limit=100, offset=0):
        with self.connect() as db:
            rows = db.execute('SELECT * FROM capability_definitions ORDER BY id, published').fetchall()
        records = [self._public(r) for r in rows if (not namespace or r['id'].split(':')[0] == namespace) and (not query or query.lower() in r['payload'].lower())]
        return {'records': records[offset:offset+limit], 'total': len(records)}

    def deprecate(self, id, ver, owner):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT owner FROM capability_definitions WHERE id=? AND version=?', (id, ver)).fetchone()
            if not row: raise KeyError(id)
            if row['owner'] != owner: raise PermissionError('Only the publisher may deprecate this definition.')
            db.execute('UPDATE capability_definitions SET deprecated=1 WHERE id=? AND version=?', (id, ver))
        return self.get(id, ver)
