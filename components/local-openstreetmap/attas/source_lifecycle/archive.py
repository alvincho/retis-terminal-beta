"""Full specifications retained by the Data Consultant, independently of sources."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from pathlib import Path


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def validate_catalog(payload):
    """Validate completeness of the available catalog, not licensed coverage."""
    if not isinstance(payload, dict) or not payload.get("source_id"):
        raise ValueError("Source identity is required.")
    snapshot = payload.get("snapshot") or {}
    endpoints = snapshot.get("endpoints")
    datasets = payload.get("datasets")
    if not isinstance(endpoints, list) or not endpoints or not isinstance(datasets, list):
        raise ValueError("A complete available specification and catalog are required.")
    ids = [item["endpoint_id"] for item in endpoints]
    dataset_ids = [item["dataset_id"] for item in datasets]
    if len(ids) != len(set(ids)) or len(dataset_ids) != len(set(dataset_ids)):
        raise ValueError("Duplicate catalog identifiers.")
    if set(ids) != set(dataset_ids) or payload.get("endpoint_count") != len(ids):
        raise ValueError("Specification handoff is incomplete.")
    if "endpoint_records" in payload and {item["endpoint_id"] for item in payload["endpoint_records"]} != set(ids):
        raise ValueError("Executable discovery metadata is incomplete.")
    extra_ids = [item["endpoint_id"] for item in payload.get("additional_endpoint_records", [])]
    if len(extra_ids) != len(set(extra_ids)) or set(extra_ids) & set(ids):
        raise ValueError("Duplicate additional endpoint identifiers.")
    if snapshot.get("vendor_id") != payload["source_id"] or not snapshot.get("sources"):
        raise ValueError("Specification identity or provenance is missing.")
    if not isinstance(snapshot.get("warnings"), list):
        raise ValueError("Specification coverage warnings must be retained.")
    return digest(payload)


class ConsultantArchive:
    def __init__(self, path):
        path = Path(path).resolve()
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(path, timeout=15, check_same_thread=False)
        path.chmod(0o600)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("CREATE TABLE IF NOT EXISTS catalogs (source_id TEXT PRIMARY KEY, revision TEXT NOT NULL, payload TEXT NOT NULL, availability TEXT NOT NULL DEFAULT 'running')")
        self.db.commit()
        self.lock = threading.RLock()

    def retain(self, payload, expected_revision):
        revision = validate_catalog(payload)
        if revision != expected_revision:
            raise ValueError("Specification digest mismatch.")
        with self.lock, self.db:
            self.db.execute("INSERT INTO catalogs(source_id,revision,payload) VALUES(?,?,?) ON CONFLICT(source_id) DO UPDATE SET revision=excluded.revision,payload=excluded.payload", (payload["source_id"], revision, encode(payload)))
        # Read back through SQLite after COMMIT; never acknowledge a cache write.
        saved = self.read(payload["source_id"])
        if not saved or saved["revision"] != revision or digest(saved["catalog"]) != revision:
            raise RuntimeError("Consultant specification readback failed.")
        return {"source_id": payload["source_id"], "revision": revision, "durable": True,
                "endpoint_count": payload["endpoint_count"], "warnings": payload["snapshot"]["warnings"]}

    def read(self, source_id):
        with self.lock:
            row = self.db.execute("SELECT revision,payload,availability FROM catalogs WHERE source_id=?", (source_id,)).fetchone()
        if row is None:
            return None
        payload = json.loads(row[1])
        if validate_catalog(payload) != row[0]:
            raise ValueError("Retained specification failed integrity verification.")
        return {"revision": row[0], "catalog": payload, "availability": row[2]}

    def availability(self, source_id, state):
        if state not in {"running", "sleeping", "starting", "unavailable"}:
            raise ValueError("Invalid source availability.")
        with self.lock, self.db:
            cursor = self.db.execute("UPDATE catalogs SET availability=? WHERE source_id=?", (state, source_id))
        if not cursor.rowcount:
            raise ValueError("Source has no retained specification.")

    def metadata(self, source_id):
        with self.lock:
            return self.db.execute("SELECT revision,availability FROM catalogs WHERE source_id=?", (source_id,)).fetchone()

    def close(self):
        self.db.close()
