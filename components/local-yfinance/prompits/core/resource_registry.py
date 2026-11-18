"""Product-neutral immutable resources, ownership, evidence and dependency bundles.

Resource families install their own validators. No network access or executable
code is derived from a published document. Every read, including dependency
resolution, applies the caller's current audience permissions.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import re
import sqlite3
import time

from .capabilities import canonical, digest, version

NAMESPACE = re.compile(r"^[a-z][a-z0-9]*(?:[.-][a-z0-9][a-z0-9_-]*)*$")
HASH = re.compile(r"^[0-9a-f]{64}$")
MAX_BYTES = 512_000
MAX_BUNDLE = 256


def reference(resource):
    return {"id": resource["id"], "version": resource["version"], "digest": digest(resource)}


def validate_ref(value):
    if not isinstance(value, dict) or set(value) != {"id", "version", "digest"}:
        raise ValueError("An exact id, version and digest reference is required.")
    if not isinstance(value["id"], str) or not 1 <= len(value["id"]) <= 256:
        raise ValueError("Invalid resource identifier.")
    version(value["version"])
    if not isinstance(value["digest"], str) or not HASH.fullmatch(value["digest"]):
        raise ValueError("Resource digest must be SHA-256.")
    return value


def validate_resource(value):
    if not isinstance(value, dict) or len(canonical(value).encode()) > MAX_BYTES:
        raise ValueError("Resource must be an object of at most 512 KB.")
    ns = value.get("namespace", "")
    if not isinstance(ns, str) or len(ns) > 128 or not NAMESPACE.fullmatch(ns):
        raise ValueError("A qualified resource namespace is required.")
    name = value.get("id", "")
    if not isinstance(name, str) or not name.startswith((ns + ".", ns + ":")) or len(name) > 256:
        raise ValueError("Resource ID must belong to its declared namespace.")
    version(value.get("version"))
    if not isinstance(value.get("resource_type"), str) or not value["resource_type"]:
        raise ValueError("Resource type is required.")
    dependencies = value.get("dependencies", [])
    if not isinstance(dependencies, list) or len(dependencies) > 64:
        raise ValueError("A resource supports at most 64 direct dependencies.")
    seen = set()
    for ref in dependencies:
        validate_ref(ref)
        key = (ref["id"], ref["version"])
        if key in seen or key == (name, value["version"]):
            raise ValueError("Duplicate or self-referencing dependency.")
        seen.add(key)
    return copy.deepcopy(value)


class ResourceRegistry:
    def __init__(self, path, *, validators=None, comparators=None, reserved=()):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.validators = dict(validators or {})
        self.comparators = dict(comparators or {})
        self.reserved = set(reserved) | {"system", "plaza"}
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS resource_namespaces(
                    namespace TEXT PRIMARY KEY, owner TEXT NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS resource_delegates(
                    namespace TEXT, delegate TEXT, PRIMARY KEY(namespace,delegate));
                CREATE TABLE IF NOT EXISTS resource_versions(
                    id TEXT, version TEXT, namespace TEXT, family TEXT, owner TEXT,
                    digest TEXT, payload TEXT, visibility TEXT, audience TEXT,
                    published REAL, deprecated INTEGER DEFAULT 0,
                    PRIMARY KEY(id,version));
                CREATE INDEX IF NOT EXISTS resource_search ON resource_versions(namespace,family,id);
                CREATE TABLE IF NOT EXISTS resource_proposals(
                    id TEXT PRIMARY KEY, owner TEXT, payload TEXT, created REAL);
                CREATE TABLE IF NOT EXISTS resource_evidence(
                    id TEXT PRIMARY KEY, resource_id TEXT, version TEXT, issuer TEXT,
                    payload TEXT, revoked INTEGER DEFAULT 0);
                CREATE TABLE IF NOT EXISTS resource_audit(
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, actor TEXT, action TEXT,
                    resource TEXT, version TEXT, created REAL);
            """)
        os.chmod(self.path, 0o600)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    @staticmethod
    def _audit(db, actor, action, id, ver=""):
        db.execute("INSERT INTO resource_audit(actor,action,resource,version,created) VALUES(?,?,?,?,?)",
                   (actor, action, id, ver, time.time()))

    @staticmethod
    def _authority(db, namespace, actor):
        rows = list(db.execute("SELECT namespace,owner FROM resource_namespaces"))
        parents = [r for r in rows if namespace == r["namespace"] or namespace.startswith(r["namespace"] + ".")]
        if not parents:
            raise PermissionError("Claim the resource namespace before publishing.")
        row = max(parents, key=lambda r: len(r["namespace"]))
        delegated = db.execute("SELECT 1 FROM resource_delegates WHERE namespace=? AND delegate=?",
                               (row["namespace"], actor)).fetchone()
        if row["owner"] != actor and not delegated:
            raise PermissionError("Only the namespace owner or an active delegate may publish.")
        return row["owner"]

    def claim(self, namespace, actor, *, admin=False):
        if not actor or not isinstance(namespace, str) or len(namespace) > 128 or not NAMESPACE.fullmatch(namespace):
            raise ValueError("An authenticated owner and qualified namespace are required.")
        if any(namespace == n or namespace.startswith(n + ".") for n in self.reserved) and not admin:
            raise PermissionError("Namespace is reserved.")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = list(db.execute("SELECT namespace,owner FROM resource_namespaces"))
            related = [r for r in rows if r["namespace"] == namespace or r["namespace"].startswith(namespace + ".") or namespace.startswith(r["namespace"] + ".")]
            if any(r["owner"] != actor for r in related):
                raise PermissionError("This namespace hierarchy belongs to another owner.")
            db.execute("INSERT OR IGNORE INTO resource_namespaces VALUES(?,?,?)", (namespace, actor, time.time()))
            self._audit(db, actor, "claim", namespace)
        return {"namespace": namespace, "owner": actor}

    def namespaces(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM resource_namespaces ORDER BY namespace")]

    def delegate(self, namespace, actor, delegate, *, enabled=True):
        if not isinstance(delegate, str) or not delegate:
            raise ValueError("Delegate identity is required.")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT owner FROM resource_namespaces WHERE namespace=?", (namespace,)).fetchone()
            if not row or row["owner"] != actor:
                raise PermissionError("Only the namespace owner may manage delegates.")
            if enabled:
                db.execute("INSERT OR IGNORE INTO resource_delegates VALUES(?,?)", (namespace, delegate))
            else:
                db.execute("DELETE FROM resource_delegates WHERE namespace=? AND delegate=?", (namespace, delegate))
            self._audit(db, actor, "delegate" if enabled else "revoke-delegate", namespace)
        return {"namespace": namespace, "delegate": delegate, "enabled": enabled}

    @staticmethod
    def _readable(row, actor):
        return row["visibility"] == "public" or row["owner"] == actor or actor in json.loads(row["audience"])

    @staticmethod
    def _record(row):
        return {"resource": json.loads(row["payload"]), "ref": {"id": row["id"], "version": row["version"], "digest": row["digest"]},
                "owner": row["owner"], "publishedAt": row["published"], "deprecated": bool(row["deprecated"]),
                "visibility": row["visibility"], "verification": "declared"}

    def _get(self, db, ref, actor):
        validate_ref(ref)
        row = db.execute("SELECT * FROM resource_versions WHERE id=? AND version=?", (ref["id"], ref["version"])).fetchone()
        if not row or not self._readable(row, actor):
            raise KeyError("Resource unavailable.")
        if row["digest"] != ref["digest"]:
            raise ValueError("Resource digest mismatch.")
        return self._record(row)

    def get(self, id, ver, actor="", *, expected_digest=None):
        version(ver)
        with self.connect() as db:
            row = db.execute("SELECT * FROM resource_versions WHERE id=? AND version=?", (id, ver)).fetchone()
            if not row or not self._readable(row, actor):
                raise KeyError("Resource unavailable.")
            if expected_digest and row["digest"] != expected_digest:
                raise ValueError("Resource digest mismatch.")
            return self._record(row)

    def validate(self, value):
        resource = validate_resource(value)
        if resource["resource_type"] == "operation-contract":
            from .operation_contracts import validate_operation
            validate_operation(resource)
        validator = self.validators.get(resource["resource_type"])
        if validator:
            validator(resource)
        return resource

    def publish(self, value, actor, *, visibility="public", audience=()):
        resource = self.validate(value)
        if visibility not in ("public", "private") or not isinstance(audience, (list, tuple)) or len(audience) > 100 or any(not isinstance(x, str) or not x for x in audience):
            raise ValueError("Invalid publication audience.")
        ref = reference(resource)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            owner = self._authority(db, resource["namespace"], actor)
            old = db.execute("SELECT * FROM resource_versions WHERE id=? AND version=?", (ref["id"], ref["version"])).fetchone()
            if old:
                if old["digest"] != ref["digest"] or old["visibility"] != visibility or json.loads(old["audience"]) != sorted(set(audience)):
                    raise ValueError("Published content and audience are immutable; publish a new version.")
                return self._record(old)
            previous = list(db.execute("SELECT payload FROM resource_versions WHERE id=?", (ref["id"],)))
            compare = self.comparators.get(resource["resource_type"])
            if compare:
                for row in previous:
                    outcome = compare(json.loads(row["payload"]), resource)
                    if outcome.get("semanticChange"):
                        raise ValueError("Meaning changed; publish a new resource ID.")
                    if outcome.get("breaking") and version(resource["version"])[0] <= version(json.loads(row["payload"])["version"])[0]:
                        raise ValueError("Incompatible contract change requires a new major version.")
            for dependency in resource.get("dependencies", []):
                record = self._get(db, dependency, actor)
                if visibility == "public" and record["visibility"] != "public":
                    raise ValueError("A public resource cannot depend on a private resource.")
                self._bundle(db, [dependency], actor)
            db.execute("INSERT INTO resource_versions VALUES(?,?,?,?,?,?,?,?,?,?,0)",
                       (resource["id"], resource["version"], resource["namespace"], resource["resource_type"], owner,
                        ref["digest"], canonical(resource), visibility, canonical(sorted(set(audience))), time.time()))
            self._audit(db, actor, "publish", ref["id"], ref["version"])
            # A publisher can receive the document it supplied even when the
            # owner excludes it from subsequent private reads.
            return self._record(db.execute("SELECT * FROM resource_versions WHERE id=? AND version=?", (ref["id"], ref["version"])).fetchone())

    def _bundle(self, db, refs, actor):
        if not isinstance(refs, list) or not refs or len(refs) > 64:
            raise ValueError("Resolve between one and 64 roots.")
        seen, active, records = set(), set(), []
        def visit(ref, depth=0):
            validate_ref(ref)
            key = (ref["id"], ref["version"], ref["digest"])
            if key in active or depth > 32:
                raise ValueError("Cyclic or excessively deep dependency graph.")
            if key in seen:
                return
            if len(seen) >= MAX_BUNDLE:
                raise ValueError("Dependency bundle exceeds 256 resources.")
            record = self._get(db, ref, actor)
            seen.add(key); active.add(key)
            for child in record["resource"].get("dependencies", []):
                visit(child, depth + 1)
            records.append(record); active.remove(key)
        for ref in refs:
            visit(ref)
        if len(canonical(records).encode()) > 4_000_000:
            raise ValueError("Dependency bundle exceeds 4 MB.")
        return records

    def bundle(self, refs, actor=""):
        with self.connect() as db:
            records = self._bundle(db, refs, actor)
        return {"schema": "resource-bundle/1", "roots": refs, "records": records, "digest": digest([r["ref"] for r in records])}

    def search(self, actor="", *, query="", namespace="", family="", limit=50, offset=0, include_deprecated=True):
        if type(limit) is not int or not 1 <= limit <= 200 or type(offset) is not int or offset < 0 or len(query) > 500:
            raise ValueError("Invalid resource query or page.")
        with self.connect() as db:
            # Page before loading payloads into Python. A catalog can contain
            # thousands of large schemas; search must not materialize them all.
            # Keep Unicode matching equivalent to the original Python search.
            needle = query.lower()
            db.create_function('resource_matches', 1, lambda payload: needle in payload.lower(), deterministic=True)
            where = """(?='' OR namespace=? OR substr(namespace,1,length(?)+1)=?||'.')
                AND (?='' OR family=?) AND (? OR deprecated=0)
                AND (visibility='public' OR owner=? OR EXISTS (
                    SELECT 1 FROM json_each(resource_versions.audience) WHERE value=?))
                AND (?='' OR resource_matches(payload))"""
            values = (namespace, namespace, namespace, namespace, family, family,
                      bool(include_deprecated), actor, actor, query)
            db.execute('BEGIN')
            total = db.execute('SELECT count(*) FROM resource_versions WHERE '+where, values).fetchone()[0]
            rows = db.execute('SELECT * FROM resource_versions WHERE '+where+' ORDER BY id,version LIMIT ? OFFSET ?',
                              (*values, limit, offset)).fetchall()
        return {"records": [self._record(r) for r in rows], "total": total, "offset": offset}

    def deprecate(self, id, ver, actor):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM resource_versions WHERE id=? AND version=?", (id, ver)).fetchone()
            if not row:
                raise KeyError("Resource unavailable.")
            self._authority(db, row["namespace"], actor)
            db.execute("UPDATE resource_versions SET deprecated=1 WHERE id=? AND version=?", (id, ver))
            self._audit(db, actor, "deprecate", id, ver)
            return self._record(db.execute("SELECT * FROM resource_versions WHERE id=? AND version=?", (id, ver)).fetchone())

    def propose(self, resource, actor):
        value = self.validate(resource)
        if not actor:
            raise PermissionError("An authenticated proposer is required.")
        key = digest({"owner": actor, "resource": value})
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO resource_proposals VALUES(?,?,?,?)", (key, actor, canonical(value), time.time()))
            self._audit(db, actor, "propose", value["id"], value["version"])
        return {"id": key, "owner": actor, "resource": value, "status": "draft"}

    def proposals(self, actor):
        with self.connect() as db:
            return [{"id": r["id"], "owner": r["owner"], "resource": json.loads(r["payload"]), "status": "draft"}
                    for r in db.execute("SELECT * FROM resource_proposals WHERE owner=? ORDER BY created DESC", (actor,))]

    def attest(self, value, actor, *, admin=False):
        ref = validate_ref(value.get("ref"))
        resource = self.get(ref["id"], ref["version"], actor, expected_digest=ref["digest"])
        if resource["owner"] != actor and not admin:
            raise PermissionError("Only the publisher or a verifier may attest evidence.")
        level = value.get("level")
        if level not in ("fixture-verified", "live-verified"):
            raise ValueError("Evidence requires a fixture or live verification level.")
        for key in ("implementationDigest", "reportDigest"):
            if not HASH.fullmatch(str(value.get(key, ""))):
                raise ValueError("Evidence must identify its implementation and report digests.")
        cases = value.get("cases")
        if not isinstance(cases, list) or not cases or len(cases) > 500 or any(not isinstance(c, dict) or not isinstance(c.get("name"), str) or type(c.get("passed")) is not bool for c in cases):
            raise ValueError("Evidence requires named, explicit test results.")
        payload = {key: copy.deepcopy(value[key]) for key in ("ref", "level", "implementationDigest", "reportDigest", "cases")}
        payload.update(issuer=actor, trusted=bool(admin), passed=all(c["passed"] for c in cases))
        if len(canonical(payload).encode()) > MAX_BYTES:
            raise ValueError("Evidence exceeds 512 KB.")
        key = digest(payload)
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO resource_evidence VALUES(?,?,?,?,?,0)", (key, ref["id"], ref["version"], actor, canonical(payload)))
            self._audit(db, actor, "attest", ref["id"], ref["version"])
        return {"id": key, **payload, "revoked": False}

    def evidence(self, id, ver, actor=""):
        self.get(id, ver, actor)
        with self.connect() as db:
            return [{"id": r["id"], **json.loads(r["payload"]), "revoked": bool(r["revoked"])}
                    for r in db.execute("SELECT * FROM resource_evidence WHERE resource_id=? AND version=?", (id, ver))]

    def revoke_evidence(self, id, actor, *, admin=False):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM resource_evidence WHERE id=?", (id,)).fetchone()
            if not row:
                raise KeyError("Evidence unavailable.")
            if row["issuer"] != actor and not admin:
                raise PermissionError("Only its issuer or an administrator may revoke evidence.")
            db.execute("UPDATE resource_evidence SET revoked=1 WHERE id=?", (id,))
            self._audit(db, actor, "revoke-evidence", row["resource_id"], row["version"])
        return {"id": id, "revoked": True}
