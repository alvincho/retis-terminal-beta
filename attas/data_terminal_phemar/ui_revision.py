"""Content identity of installed UI assets; no runtime or user state is exposed."""
import hashlib
from pathlib import Path
import threading


class UIRevision:
    def __init__(self, directory):
        self.directory = Path(directory)
        self._manifest = None
        self._revision = None
        self._lock = threading.Lock()

    def read(self):
        with self._lock:
            files = sorted(p for p in self.directory.iterdir()
                           if p.is_file() and p.suffix in ('.html', '.js', '.css'))
            manifest = tuple((p.name, p.stat().st_mtime_ns, p.stat().st_size) for p in files)
            if manifest != self._manifest:
                digest = hashlib.sha256()
                for path in files:
                    digest.update(path.name.encode())
                    digest.update(b'\0')
                    digest.update(path.read_bytes())
                    digest.update(b'\0')
                self._revision = digest.hexdigest()
                self._manifest = manifest
            return {'revision': self._revision}
