"""Locally installed renderer bundles. Documents cannot install executable code."""
import hashlib
import json
import os
from pathlib import Path
from prompits.core.capabilities import validate_requirement


def installed_renderers():
    directory=os.environ.get('PHEMACAST_RENDERER_DIRECTORY')
    if not directory:return []
    root=Path(directory).resolve();result=[]
    for manifest in sorted(root.glob('*/renderer.json'))[:32]:
        if manifest.stat().st_size>128000:raise ValueError('Renderer manifest exceeds 128 KB.')
        value=json.loads(manifest.read_text());validate_requirement(value['capability'])
        if value['capability']['id'].split(':',1)[0] in {'phemacast','plaza','prompits'}:
            raise ValueError('Renderer extensions must use their own namespace.')
        filename=value.get('module','')
        if not filename.endswith('.js') or '/' in filename or '\\' in filename:raise ValueError('Install a single JavaScript renderer bundle.')
        module=(manifest.parent/filename).resolve()
        if not module.is_relative_to(root) or module.stat().st_size>2000000:raise ValueError('Renderer module exceeds its installation boundary.')
        if hashlib.sha256(module.read_bytes()).hexdigest()!=value.get('sha256'):raise ValueError('Installed renderer integrity mismatch.')
        from .composition import schema_check
        schema_check(value['inputSchema'])
        result.append({**value,'path':module,'key':manifest.parent.name})
    ids=[r['capability']['id'] for r in result]
    if len(ids)!=len(set(ids)):raise ValueError('Duplicate installed renderer capability.')
    return result


def public_renderers():
    return [{'capability':r['capability'],'sha256':r['sha256'],'module':'/composition-renderers/'+r['key']+'/'+r['sha256']+'.js'} for r in installed_renderers()]
