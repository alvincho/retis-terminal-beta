"""Run Retis MAS Manager in the background, with its state outside the checkout.

    python3 -m attas.mas_manager --state ~/Library/Application\\ Support/Retis/mas-manager \\
        --port 28460 --parts-list-url https://.../parts-list.json --parts-list-key retis-2026=<base64 key>

A desktop app that ships components passes --included-components (a folder of components, each with its
component.json `offer`) and --pip-folder (the folder holding the pip package it keeps outside its Python's import
path): its components install from that folder, their libraries from the package index, with no parts list.

The first run writes `manager.json`: a local resource on 127.0.0.1 that Bonjour announces, used by this computer's
Terminal with no pairing. Later runs reuse it; the parts list options update it.
"""
import argparse
import asyncio
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NAME = 'Retis MAS Manager'


def prepare(state, port, *, parts_url=None, parts_file=None, keys=(), package_folder=None, included=None, pip_folder=None):
    from phemacast.network.configure import configure, upgrade, write
    from .contracts import NAME as PULSE
    state = Path(state).expanduser().resolve()
    if state.is_relative_to(ROOT):
        raise ValueError('Keep runtime state outside the source checkout.')
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = state / 'manager.json'
    if not path.exists():
        configure(path, port=port, name=NAME, kind='manager', execute=True, pulses=[PULSE])
    document = json.loads(path.read_text(encoding='utf-8'))
    if document['local_network']['port'] != port:
        raise ValueError('The manager already uses another port. Stop it before changing its configuration.')
    # A manager set up before local resources dropped pairing keeps its id; its TLS and pairing settings go.
    upgrade(document['local_network'])
    document.setdefault('name', NAME)
    document['components_root'] = str(state / 'components')
    document['view_releases'] = str(state / 'view-releases')
    parts = document.setdefault('parts_list', {'keys': {}})
    if parts_url or parts_file:
        parts.pop('url', None), parts.pop('file', None)
        parts.update({'url': parts_url} if parts_url else {'file': str(Path(parts_file).expanduser().resolve())})
    if package_folder:
        parts['package_folder'] = str(Path(package_folder).expanduser().resolve())
    # The app passes these at every start, so a moved or updated app is followed.
    if included:
        document['included_components'] = str(Path(included).expanduser().resolve())
    if pip_folder:
        folder = Path(pip_folder).expanduser().resolve()
        if not (folder / 'pip' / '__main__.py').is_file():
            raise ValueError(f'{folder} holds no pip package.')
        document['pip_folder'] = str(folder)
    for item in keys:
        key_id, _, value = item.partition('=')
        if not key_id or not value:
            raise ValueError('Give each parts list key as <key id>=<base64 public key>.')
        parts['keys'][key_id] = value
    write(path, document)
    return path


def build(path):
    from phemacast.network.pulser import install_pulser_local
    from phemacast.network.service import endpoint
    from .agent import MasManager
    from .release import PHEMA_VERSION, TITLE, advertise, capture, phema
    document = json.loads(Path(path).read_text(encoding='utf-8'))
    network = document['local_network']
    agent = MasManager({**document, 'host': '127.0.0.1', 'port': network['port']})
    # The Terminal lists a local resource as `local-<resource id>`; the view release and the Phema name that publisher.
    publisher = 'local-' + network['resourceId']
    directory, manifest = capture(publisher, endpoint(network)['url'], document['view_releases'])
    # The manager ships its own Phema; the Terminal keeps it in its library, so it opens with no Plaza or catalog.
    phemas = {'mas-manager': {'name': TITLE, 'revision': PHEMA_VERSION, 'phema': phema(publisher)}}
    resource = install_pulser_local(agent, views=advertise(agent, directory, manifest), phemas=phemas)
    agent.resource = resource
    return agent, resource


async def serve(path, runtime=None):
    from .control import Control
    agent, resource = build(path)
    agent.run_background()
    def stop():  # the channel's shutdown ends the listener, and with it this process
        if resource.server:
            resource.server.should_exit = True
    # The desktop app's private channel: it hands over the Terminal's key when the user opens the app.
    control = Control(agent, Path(path).parent, runtime, on_shutdown=stop)
    await control.start()
    await resource.start()
    try:
        await resource.task
    finally:
        control.close()
        agent.stop_background()
        await resource.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=NAME)
    parser.add_argument('--state', type=Path, required=True, help='Folder for the manager\'s records and components.')
    parser.add_argument('--port', type=int, default=28460)
    source = parser.add_mutually_exclusive_group()
    source.add_argument('--parts-list-url', help='HTTPS address of the signed parts list.')
    source.add_argument('--parts-list-file', help='A signed parts list file (development).')
    parser.add_argument('--parts-list-key', action='append', default=[], help='<key id>=<base64 Ed25519 public key>; repeatable.')
    parser.add_argument('--terminal-runtime', type=Path, help='The Terminal the desktop app bundles (its runtime folder).')
    parser.add_argument('--package-folder', type=Path, help='Tests and offline installs: take packages from this folder.')
    parser.add_argument('--included-components', type=Path,
                        help='Components the app ships (each a folder with its component.json), offered with no parts list.')
    parser.add_argument('--pip-folder', type=Path, help='The folder holding the pip package the app keeps for the manager.')
    args = parser.parse_args(argv)
    path = prepare(args.state, args.port, parts_url=args.parts_list_url, parts_file=args.parts_list_file,
                   keys=args.parts_list_key, package_folder=args.package_folder, included=args.included_components,
                   pip_folder=args.pip_folder)
    asyncio.run(serve(path, args.terminal_runtime.resolve() if args.terminal_runtime else None))


if __name__ == '__main__':
    main()
