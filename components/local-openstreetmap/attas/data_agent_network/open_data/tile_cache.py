"""A map tile cache on this computer, for tile servers that ask their users to cache and to go easy on them.

OpenStreetMap's tile servers are donated, and its Tile Usage Policy (https://operations.osmfoundation.org/policies/tiles/)
asks an application to honour their caching headers, to revalidate an expired tile with a conditional request, never
to send no-cache headers, and never to fetch tiles nobody is looking at. `TileCache` stands in for the HTTP client the
provider executor calls (`request(method, url, **options)`), so a source keeps the executor's own request and reply
checks and gains these rules:

- A tile kept here is answered from this computer while it is fresh: `Cache-Control: max-age` (less its `Age`), else
  `Expires`, else seven days, the policy's floor for a reply whose headers can't be read.
- An expired tile is asked for again with `If-None-Match` / `If-Modified-Since`; a 304 keeps the copy.
- It fetches only the tile asked for: nothing ahead, nothing around it. At most two downloads run at once and at most
  `per_minute` start in any minute; past that it answers an expired copy if it has one, or 429 without reaching the
  server.
- When the server fails or can't be reached, an expired copy is answered rather than nothing.
- The cache stays under `max_bytes`, dropping the tiles used least recently.

Only GET replies with an image type are kept; anything else passes through as the server sent it.
"""
from __future__ import annotations

import collections
import email.utils
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import time
from typing import Any, Dict, Mapping, Optional

WEEK = 7 * 24 * 3600
MAX_BYTES = 512 * 1024 * 1024
PER_MINUTE = 300
DOWNLOADS = 2
MAX_TILE = 2_000_000
# Request headers that would ask the server to skip its own cache; never sent (the policy forbids it).
NO_CACHE = ('cache-control', 'pragma')


class Reply:
    """A reply as the executor reads it: `status_code`, lower-case `headers`, `content`, `text` and `json()`."""

    def __init__(self, status_code: int, headers: Mapping[str, str], content: bytes = b''):
        self.status_code = int(status_code)
        self.headers = {str(k).lower(): str(v) for k, v in dict(headers).items()}
        self.content = bytes(content)

    @property
    def text(self) -> str:
        return self.content.decode('utf-8', 'replace')

    def json(self) -> Any:
        return json.loads(self.content)


def _parse_date(value: str) -> Optional[float]:
    try:
        return email.utils.parsedate_to_datetime(value).timestamp()
    except (TypeError, ValueError, IndexError, OverflowError):
        return None


def lifetime(headers: Mapping[str, str], now: float) -> Optional[int]:
    """Seconds a reply stays fresh from `now`, by its headers; None when it must not be kept."""
    headers = {str(k).lower(): str(v) for k, v in dict(headers).items()}
    control = {}
    for part in headers.get('cache-control', '').split(','):
        name, _, value = part.partition('=')
        if name.strip():
            control[name.strip().lower()] = value.strip().strip('"')
    if 'no-store' in control:
        return None
    if 'no-cache' in control:
        return 0  # kept, but asked for again every time
    if 'max-age' in control:
        try:
            age = int(headers.get('age', '0') or 0)
        except ValueError:
            age = 0
        try:
            return max(0, int(control['max-age']) - max(0, age))
        except ValueError:
            pass
    if 'expires' in headers:
        expires = _parse_date(headers['expires'])
        if expires is not None:
            date = _parse_date(headers.get('date', '')) or now
            return max(0, int(expires - date))
    return WEEK


class TileCache:
    """See the module's description. Safe to call from several threads at once (the source runs fetches in a pool)."""

    def __init__(self, directory, *, max_bytes: int = MAX_BYTES, per_minute: int = PER_MINUTE,
                 downloads: int = DOWNLOADS, client: Any = None, clock=time.time, monotonic=time.monotonic):
        self.directory = Path(directory).expanduser()
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.max_bytes, self.per_minute = int(max_bytes), int(per_minute)
        if self.max_bytes < MAX_TILE or self.per_minute < 1 or downloads < 1:
            raise ValueError('A tile cache needs room for a tile, and at least one download a minute.')
        self._client, self._owns_client = client, client is None
        self._clock, self._monotonic = clock, monotonic
        self._lock = threading.Lock()
        self._downloads = threading.BoundedSemaphore(int(downloads))
        self._started: collections.deque = collections.deque()  # when each download in the last minute started
        self._index: 'collections.OrderedDict[str, int]' = collections.OrderedDict()  # key -> bytes, least used first
        self._size = 0
        self.counts = {'hits': 0, 'downloads': 0, 'revalidated': 0, 'stale': 0, 'refused': 0}
        self._load()

    # -- the client the executor calls ---------------------------------------------------------------------------------

    def request(self, method, url, params=None, headers=None, **options):
        send = {k: v for k, v in dict(headers or {}).items() if str(k).lower() not in NO_CACHE}
        if str(method).upper() != 'GET' or params or options.get('json') is not None:
            return self.client().request(method, url, params=params, headers=send, **options)
        key = hashlib.sha256(str(url).encode('utf-8')).hexdigest()
        kept = self._read(key)
        if kept is not None and kept[0]['expires'] > self._clock():
            return self._answer(key, kept, 'hits')
        if not self._may_download():
            if kept is not None:
                return self._answer(key, kept, 'stale')
            self._count('refused')
            return Reply(429, {'content-type': 'text/plain', 'retry-after': '60'},
                         b'Too many new map tiles were asked for in the last minute.')
        with self._downloads:
            again = self._read(key)  # another request may have fetched it while this one waited
            if again is not None and again[0]['expires'] > self._clock():
                return self._answer(key, again, 'hits')
            kept = again or kept
            if kept is not None:
                if kept[0].get('etag'):
                    send['If-None-Match'] = kept[0]['etag']
                if kept[0].get('last_modified'):
                    send['If-Modified-Since'] = kept[0]['last_modified']
            try:
                reply = self.client().request('GET', url, params=None, headers=send, **options)
            except Exception:
                if kept is not None:
                    return self._answer(key, kept, 'stale')
                raise
        status = int(getattr(reply, 'status_code', 0) or 0)
        received = {str(k).lower(): str(v) for k, v in dict(getattr(reply, 'headers', {}) or {}).items()}
        if status == 304 and kept is not None:
            fresh = lifetime({**kept[0].get('headers', {}), **received}, self._clock())
            meta = {**kept[0], 'expires': self._clock() + (fresh or 0)}
            meta.update({name: received[header] for name, header in (('etag', 'etag'), ('last_modified', 'last-modified'))
                         if received.get(header)})
            self._write_meta(key, meta)
            return self._answer(key, (meta, kept[1]), 'revalidated')
        if 200 <= status < 300:
            content = bytes(getattr(reply, 'content', b'') or b'')
            self._keep(key, url, received, content)
            self._count('downloads')
            return Reply(status, received, content)
        if kept is not None and (status >= 500 or status == 429):
            return self._answer(key, kept, 'stale')
        return reply

    def client(self):
        if self._client is None:
            import httpx
            self._client = httpx.Client(timeout=30, follow_redirects=False)
        return self._client

    def close(self) -> None:
        if self._owns_client and self._client is not None:
            self._client.close()
            self._client = None

    def status(self) -> Dict[str, Any]:
        with self._lock:
            return {'tiles': len(self._index), 'bytes': self._size, 'max_bytes': self.max_bytes, **self.counts}

    # -- the budget ----------------------------------------------------------------------------------------------------

    def _may_download(self) -> bool:
        now = self._monotonic()
        with self._lock:
            while self._started and now - self._started[0] >= 60:
                self._started.popleft()
            if len(self._started) >= self.per_minute:
                return False
            self._started.append(now)
            return True

    def _count(self, name):
        with self._lock:
            self.counts[name] += 1

    # -- the files -----------------------------------------------------------------------------------------------------

    def _paths(self, key):
        folder = self.directory / key[:2]
        return folder / key, folder / (key + '.json')

    def _load(self):
        """Index the tiles kept by earlier runs, least recently used first; drop what a stop left half written."""
        found = []
        for folder in self.directory.iterdir():
            if not folder.is_dir() or len(folder.name) != 2:
                continue
            files = list(folder.iterdir())
            described = {path.stem for path in files if path.suffix == '.json'}
            for path in files:
                if path.suffix != '.json' and path.name not in described:
                    path.unlink(missing_ok=True)  # an unfinished write, or a tile whose description was lost
            for meta_path in (path for path in files if path.suffix == '.json'):
                try:
                    meta = json.loads(meta_path.read_text(encoding='utf-8'))
                    if not (folder / meta_path.stem).is_file():
                        raise ValueError('no tile')
                    found.append((meta_path.stat().st_mtime, meta_path.stem, int(meta['size'])))
                except (OSError, ValueError, KeyError, TypeError):
                    self._remove(meta_path.stem)
        for _, key, size in sorted(found):
            self._index[key] = size
            self._size += size
        self._evict()

    def _read(self, key):
        """(meta, content) of a kept tile, or None; a damaged copy is dropped."""
        tile, meta_path = self._paths(key)
        try:
            meta = json.loads(meta_path.read_text(encoding='utf-8'))
            content = tile.read_bytes()
            if hashlib.sha256(content).hexdigest() != meta['sha256'] or not isinstance(meta.get('expires'), (int, float)):
                raise ValueError('damaged')
            return meta, content
        except FileNotFoundError:
            return None
        except (OSError, ValueError, KeyError, TypeError):
            self._remove(key)
            return None

    def _answer(self, key, kept, counted):
        meta, content = kept
        self._count(counted)
        with self._lock:
            if key in self._index:
                self._index.move_to_end(key)
        try:
            os.utime(self._paths(key)[1])  # when it was last used, for the next start's order
        except OSError:
            pass
        return Reply(200, {'content-type': meta['content_type']}, content)

    def _keep(self, key, url, headers, content):
        fresh = lifetime(headers, self._clock())
        content_type = headers.get('content-type', '').split(';', 1)[0].strip().lower()
        if fresh is None or not content or len(content) > MAX_TILE or not content_type.startswith('image/'):
            self._remove(key)  # an older copy is not kept past a reply that may not be
            return
        kept = {name: headers[header] for name, header in (('etag', 'etag'), ('last_modified', 'last-modified'),
                                                            ('cache-control', 'cache-control'), ('expires', 'expires'))
                if headers.get(header)}
        meta = {'url': str(url), 'content_type': content_type, 'size': len(content),
                'sha256': hashlib.sha256(content).hexdigest(), 'stored': self._clock(), 'expires': self._clock() + fresh,
                'headers': {k: v for k, v in kept.items() if k in ('cache-control', 'expires')},
                **{k: v for k, v in kept.items() if k in ('etag', 'last_modified')}}
        tile, _ = self._paths(key)
        tile.parent.mkdir(exist_ok=True, mode=0o700)
        self._atomic(tile, content)
        self._write_meta(key, meta)
        with self._lock:
            self._size += len(content) - self._index.pop(key, 0)
            self._index[key] = len(content)
        self._evict()

    def _write_meta(self, key, meta):
        self._atomic(self._paths(key)[1], json.dumps(meta, sort_keys=True).encode('utf-8'))

    @staticmethod
    def _atomic(path, data):
        handle, temporary = tempfile.mkstemp(dir=path.parent, prefix='.', suffix='.part')
        try:
            with os.fdopen(handle, 'wb') as out:
                out.write(data)
            os.replace(temporary, path)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise

    def _evict(self):
        dropped = []
        with self._lock:
            while self._size > self.max_bytes and len(self._index) > 1:
                key, size = self._index.popitem(last=False)
                self._size -= size
                dropped.append(key)
        for key in dropped:
            self._remove(key, indexed=False)

    def _remove(self, key, indexed=True):
        if indexed:
            with self._lock:
                self._size -= self._index.pop(key, 0)
        for path in self._paths(key):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
