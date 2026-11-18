#!/usr/bin/env python3
"""Local yfinance source on this computer; the user's Terminal finds it by Bonjour and uses it at once."""
import argparse
import asyncio
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def prepare(directory, port):
    from phemacast.network.configure import configure, upgrade, write
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory/'source.json'
    if not path.exists():
        configure(path, port=port, name='Local yfinance', kind='data-source', execute=True)
    document = json.loads(path.read_text())
    if document['local_network']['port'] != port:
        raise ValueError('The existing local source uses another port. Stop it before changing configuration.')
    # A source set up before local resources dropped pairing keeps its id; its TLS and pairing settings go.
    upgrade(document['local_network'])
    write(path, document)
    return path


SHIPPED_CATALOG = ROOT/'attas/data_agent_network/catalogs/yfinance'


def tree_digest(folder):
    """One SHA-256 over every file of a catalog folder (relative path and bytes), bytecode and dot files left out."""
    import hashlib
    digest = hashlib.sha256()
    for path in sorted(p for p in Path(folder).rglob('*') if p.is_file()):
        relative = path.relative_to(folder)
        if '__pycache__' in relative.parts or any(part.startswith('.') for part in relative.parts):
            continue
        digest.update(relative.as_posix().encode() + b'\0' + hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def refresh_catalog(shipped, copy):
    """Keep the state folder's catalog the one this version ships (G8: a copy kept from an older version would
    publish an older data specification). Local yfinance never refreshes its spec at run time, so the copy holds
    only what was shipped and is replaced whole. Returns True when it was (re)written."""
    import os
    import shutil
    shipped, copy = Path(shipped), Path(copy)
    if copy.is_dir() and (copy/'manifest.json').is_file() and tree_digest(copy) == tree_digest(shipped):
        return False
    staging, previous = copy.with_name(copy.name+'.updating'), copy.with_name(copy.name+'.previous')
    shutil.rmtree(staging, ignore_errors=True)
    shutil.copytree(shipped, staging, ignore=shutil.ignore_patterns('__pycache__', '.*'))
    shutil.rmtree(previous, ignore_errors=True)
    if copy.exists():
        os.replace(copy, previous)
    os.replace(staging, copy)
    shutil.rmtree(previous, ignore_errors=True)
    return True


def build(config):
    from attas.data_agent_network.scaffold.sources import YFinanceDataSource
    from attas.data_agent_network.local_resources import source_local
    catalog = Path(config['stateDir'])/'catalogs'
    refresh_catalog(SHIPPED_CATALOG, catalog/'yfinance')
    profile = {'cost': {'amount':0, 'model':'free', 'provider_cost':'Public Yahoo Finance endpoint; provider limits apply'}}
    source = YFinanceDataSource(config={'name': 'Local yfinance', 'host': '127.0.0.1', 'port': config['port'],
        'plaza_url': '', 'local_network': config, 'source_profile': profile, 'spec_catalog_root': str(catalog)}, auto_register=False)
    # The base source installs local resources only when configured. No Plaza
    # registration loop or unauthenticated legacy source API is ever started.
    if not getattr(source, '_local_resource', None): source_local(source)
    return source._local_resource


def health(resource):
    """The component health check: ready while it serves."""
    from prompits.components.health import health_payload
    return health_payload(ready=True, version='')


async def serve(path, health_port=None):
    config = json.loads(path.read_text())['local_network']
    resource = build(config)
    await resource.start()
    checker = None
    if health_port:
        from prompits.components.health import serve_health
        checker = await serve_health(health_port, lambda: health(resource))
    try: await resource.task
    finally:
        if checker: checker.close()
        await resource.close()


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, help='A prepared source.json.')
    # How a component manager runs it: a state folder it prepares once, and the component health check.
    parser.add_argument('--state', type=Path, help='Prepare source.json in this folder (first run) and use it.')
    parser.add_argument('--port', type=int, default=28490, help='With --state: the port to listen on (127.0.0.1).')
    parser.add_argument('--health-port', type=int, help='Answer the component health check on 127.0.0.1 at this port.')
    args=parser.parse_args()
    if (args.config is None) == (args.state is None):
        parser.error('Give --config or --state.')
    path = args.config or prepare(args.state.expanduser().resolve(), args.port)
    asyncio.run(serve(path, args.health_port))
