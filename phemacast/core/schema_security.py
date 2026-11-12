"""Resource-bounded validation for schemas supplied by content publishers."""
import asyncio
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import hashlib
import threading
from collections import OrderedDict
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError, SchemaError

MAX_SCHEMA_BYTES = 256_000
MAX_VALUE_BYTES = 4_000_000
_successes = OrderedDict()
_cache_lock = threading.Lock()
_workers = threading.BoundedSemaphore(4)


def check_schema(schema):
    if not isinstance(schema, dict):
        raise ValueError('Expected a schema object.')
    if len(json.dumps(schema, allow_nan=False).encode()) > MAX_SCHEMA_BYTES:
        raise ValueError('Schema exceeds 256 KB.')
    nodes = 0
    def inspect(value, depth=0):
        nonlocal nodes
        nodes += 1
        if depth > 32 or nodes > 10_000:
            raise ValueError('Schema complexity exceeds limits.')
        if isinstance(value, dict):
            for key in ('$ref', '$dynamicRef', '$recursiveRef'):
                if key in value and (key != '$ref' or not str(value[key]).startswith('#')):
                    raise ValueError('External or dynamic schema references are not supported.')
            patterns = [value['pattern']] if 'pattern' in value else []
            patterns.extend(value.get('patternProperties', {}).keys())
            for pattern in patterns:
                if not isinstance(pattern, str) or len(pattern) > 256:
                    raise ValueError('Schema pattern exceeds limits.')
                # Restrict authored patterns to literals/classes and flat
                # quantifiers. Group repetition, backrefs and lookaround are
                # unnecessary for field contracts and prone to CPU attacks.
                if re.search(r'\)[+*{]|\(\?[^:]|\\[1-9]|\{\d{5,}', pattern):
                    raise ValueError('Complex schema patterns are not supported.')
                try: re.compile(pattern)
                except re.error as exc: raise ValueError('Invalid schema pattern.') from exc
            for child in value.values(): inspect(child, depth + 1)
        elif isinstance(value, list):
            for child in value: inspect(child, depth + 1)
    inspect(schema)
    try: Draft202012Validator.check_schema(schema)
    except SchemaError as exc: raise ValueError('Invalid composition schema.') from exc


def validate(schema, value, *, timeout=3):
    check_schema(schema)
    def inspect(item, depth=0):
        if depth > 32: raise ValueError('Validation input exceeds depth limit.')
        if isinstance(item, dict):
            for child in item.values(): inspect(child, depth+1)
        elif isinstance(item, list):
            for child in item: inspect(child, depth+1)
    inspect(value)
    payload = json.dumps({'schema': schema, 'value': value}, allow_nan=False).encode()
    if len(payload) > MAX_VALUE_BYTES + MAX_SCHEMA_BYTES:
        raise ValueError('Validation input exceeds 4 MB.')
    key=hashlib.sha256(payload).digest()
    with _cache_lock:
        if key in _successes:
            _successes.move_to_end(key)
            return
    # A disposable process is killed on timeout. Threads cannot interrupt a
    # pathological regex or recursive validator and must not host this work.
    try:
        with _workers:
            result = subprocess.run([sys.executable, '-I', str(Path(__file__).with_name('schema_worker.py'))],
                input=payload, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                env={name: os.environ[name] for name in ('PATH', 'SYSTEMROOT', 'WINDIR') if name in os.environ},
                timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise ValueError('Schema validation exceeded its time limit.') from exc
    if result.returncode or result.stdout != b'valid':
        raise ValidationError('Value does not match the bounded schema contract.')
    # Retain only hashes, never parameters, portfolio data or content.
    with _cache_lock:
        _successes[key]=True
        while len(_successes)>512:_successes.popitem(last=False)


async def validate_async(schema, value):
    return await asyncio.to_thread(validate, schema, value)
