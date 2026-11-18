#!/usr/bin/env python3
"""Local OpenStreetMap map tiles on this computer; the user's Terminal finds them by Bonjour and uses them at once.

It serves the standard OpenStreetMap map and OpenTopoMap as tiles, fetched by this computer from their public tile
servers with no key. Those servers are donated, so they run on users' own computers only, never on a shared site, and
as OpenStreetMap's Tile Usage Policy asks: only the tiles a map shows, kept in `<state>/tile-cache` while their HTTP
caching headers allow (seven days when a server sends none), revalidated when they expire, and never fetched in bulk.

    python3 scripts/run_local_openstreetmap.py --state <folder> --port 28510 [--health-port 28511]
"""
import argparse
import asyncio
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
NAME = 'Local OpenStreetMap'


def prepare(state, port):
    from phemacast.network.configure import configure, upgrade, write
    state = Path(state).expanduser().resolve()
    if state.is_relative_to(ROOT):
        raise ValueError('Keep runtime state outside the source checkout.')
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = state / 'source.json'
    if not path.exists():
        configure(path, port=port, name=NAME, kind='data-source', execute=True)
    document = json.loads(path.read_text())
    if document['local_network']['port'] != port:
        raise ValueError('The local OpenStreetMap source already uses another port. Stop it before changing its configuration.')
    # A source set up before local resources dropped pairing keeps its id; its TLS and pairing settings go.
    upgrade(document['local_network'])
    write(path, document)
    return path


def tiles_folder(state):
    return Path(state).expanduser().resolve() / 'tile-cache'


def build(path):
    from attas.data_agent_network.open_data.source import OpenStreetMapDataSource
    config = json.loads(Path(path).read_text())['local_network']
    source = OpenStreetMapDataSource(config={'name': NAME, 'host': '127.0.0.1', 'port': config['port'], 'plaza_url': '',
                                             'local_network': config,
                                             'tile_cache': {'directory': str(tiles_folder(Path(path).parent))}},
                                     auto_register=False)
    return source, source._local_resource


def health(source, resource):
    from prompits.components.health import health_payload
    return health_payload(ready=True, version='')


async def serve(path, health_port=None):
    from prompits.components.health import serve_health
    source, resource = build(path)
    await resource.start()
    checker = await serve_health(health_port, lambda: health(source, resource)) if health_port else None
    try:
        await resource.task
    finally:
        if checker:
            checker.close()
        await resource.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--port', type=int, default=28510)
    parser.add_argument('--health-port', type=int, help='Answer the component health check on 127.0.0.1 at this port.')
    args = parser.parse_args()
    asyncio.run(serve(prepare(args.state, args.port), args.health_port))
