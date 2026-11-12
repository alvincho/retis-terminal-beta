"""Stable installed implementation identity for local conformance evidence."""
from functools import lru_cache
from pathlib import Path
import hashlib

@lru_cache(maxsize=1)
def _base_hash():
    root=Path(__file__).resolve().parents[2];digest=hashlib.sha256()
    for folder in ['attas/data_terminal_phemar','phemacast/core','phemacast/web','phemacast/schemas/phema']:
        for path in sorted((root/folder).rglob('*')):
            if path.is_file() and path.suffix in {'.py','.js','.css','.html','.json'} and not set(path.relative_to(root).parts)&{'tests','__pycache__'}:
                digest.update(str(path.relative_to(root)).encode());digest.update(path.read_bytes())
    return digest.hexdigest()

def implementation_hash():
    from phemacast.core.renderer_plugins import installed_renderers
    import json
    digest=hashlib.sha256(_base_hash().encode())
    for renderer in installed_renderers():
        digest.update(json.dumps({k:renderer[k] for k in ('capability','inputSchema','sha256')},sort_keys=True,separators=(',',':')).encode())
    return digest.hexdigest()
