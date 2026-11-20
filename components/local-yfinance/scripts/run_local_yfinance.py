#!/usr/bin/env python3
"""Local yfinance source on this computer; the user's Terminal finds it by Bonjour and uses it at once."""
import argparse
import asyncio
import json
import logging
from pathlib import Path
import sys
import time

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
LOG = logging.getLogger('local_yfinance')
# A request that fails on the network (a dropped connection, an HTTP/2 error such as curl (16)) is tried again after
# 1 s, then 2 s: such an error is usually gone a moment later. An answer such as "not found" or "too many requests" is
# not a network error and is never repeated.
NETWORK_RETRIES = 2


def configure_logging():
    """The component log (Retis MAS Manager → Logs): when it started and what each request did, with a time on every
    line. Libraries keep to warnings and errors."""
    logging.basicConfig(level=logging.WARNING, stream=sys.stdout, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    for name in ('local_yfinance', 'attas.local_source'):
        logging.getLogger(name).setLevel(logging.INFO)


def load_yfinance(retries=NETWORK_RETRIES):
    """Load yfinance before the source is announced, so the first request doesn't wait for it, and let it try a
    request again after a network error."""
    started = time.monotonic()
    import yfinance
    network = getattr(getattr(yfinance, 'config', None), 'network', None)
    if network is not None:
        network.retries = retries
    LOG.info('yfinance %s loaded in %.1f s; a request that fails on the network is tried %d more times',
             getattr(yfinance, '__version__', ''), time.monotonic() - started, retries if network is not None else 0)
    return yfinance


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


def health(state):
    """The component health check: up from the start, ready once the source serves."""
    from prompits.components.health import health_payload
    return health_payload(ready=state['ready'], version='', reason='' if state['ready'] else 'loading')


async def serve(path, health_port=None):
    config = json.loads(path.read_text())['local_network']
    state, checker = {'ready': False}, None
    if health_port:
        from prompits.components.health import serve_health
        checker = await serve_health(health_port, lambda: health(state))
    LOG.info('Local yfinance is starting on 127.0.0.1:%s', config['port'])
    await asyncio.to_thread(load_yfinance)
    resource = build(config)
    await resource.start()
    state['ready'] = True
    LOG.info('Local yfinance is ready')
    try: await resource.task
    finally:
        if checker: checker.close()
        await resource.close()
        LOG.info('Local yfinance stopped')


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
    configure_logging()
    path = args.config or prepare(args.state.expanduser().resolve(), args.port)
    asyncio.run(serve(path, args.health_port))
