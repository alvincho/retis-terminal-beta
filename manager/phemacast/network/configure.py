"""Owner CLI: make a program a local resource on this computer, and turn it on or off.

A local resource listens on 127.0.0.1 and is announced by Bonjour; this computer's Terminal uses it at once, with no
pairing, token or certificate (see `service.py`).
"""
import argparse
import json
import os
from pathlib import Path
import secrets
from .service import endpoint

KINDS = ('pulser', 'source', 'data-source', 'data-consultant', 'app-consultant', 'phemar', 'manager')


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_suffix('.tmp')
    with open(temp, 'w', opener=lambda p, flags: os.open(p, flags, 0o600)) as file:
        json.dump(value, file, indent=2); file.write('\n')
    temp.chmod(0o600); temp.replace(path)


def configure(path, *, port, name, kind, execute=False, pulses=()):
    """Write the `local_network` block for a program at `path` (its config document) and return its endpoint."""
    path = Path(path).resolve()
    document = json.loads(path.read_text()) if path.exists() else {}
    if document.get('local_network'):raise ValueError('Already configured; use settings to change acceptance or discovery.')
    if not 1 <= len(name) <= 80 or any(ord(c) < 32 for c in name):raise ValueError('Provide a short resource name.')
    if any(not isinstance(p, str) or not p or len(p)>100 or any(ord(c)<32 for c in p) for p in pulses):
        raise ValueError('Provide valid shared pulse names.')
    resource_id = 'resource-' + secrets.token_hex(12)
    config = {'acceptLocalConnections': True, 'advertiseBonjour': True, 'allowExecution': execute,
              'port': int(port), 'name': name, 'kind': kind, 'resourceId': resource_id,
              'configurationFile': str(path), 'stateDir': str(path.parent / (resource_id + '-local'))}
    endpoint(config)  # refuses a privileged port
    Path(config['stateDir']).mkdir(parents=True, exist_ok=True, mode=0o700)
    if pulses:config['pulses'] = sorted(set(pulses))
    document['local_network'] = config
    write(path, document)
    return {'id': resource_id, **endpoint(config), 'config': str(path)}


def upgrade(config):
    """A `local_network` block from the paired protocol, as this one uses it: its TLS and pairing settings are dropped."""
    for key in ('address', 'certificate', 'private_key', 'sameHostOnly', 'pairingApproval'):
        config.pop(key, None)
    return config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    sub = parser.add_subparsers(dest='action', required=True)
    setup = sub.add_parser('setup')
    setup.add_argument('--port', type=int, required=True)
    setup.add_argument('--name', required=True)
    setup.add_argument('--kind', choices=KINDS, required=True)
    setup.add_argument('--pulse', action='append', default=[], help='Pulser operation to share; repeat for each allowed pulse.')
    setup.add_argument('--allow-execution', action='store_true', help='Let this computer\'s Terminal execute using this resource\'s configured vendor entitlement. No Plaza points are charged.')
    sub.add_parser('status')
    settings = sub.add_parser('settings')
    settings.add_argument('--accept', choices=['on', 'off']); settings.add_argument('--bonjour', choices=['on', 'off'])
    args = parser.parse_args()
    if args.action == 'setup':
        print(json.dumps(configure(args.config, port=args.port, name=args.name, kind=args.kind, execute=args.allow_execution, pulses=args.pulse))); return
    document = json.loads(args.config.read_text()); config = upgrade(document['local_network'])
    if args.action == 'settings':
        if args.accept:config['acceptLocalConnections'] = args.accept == 'on'
        if args.bonjour:config['advertiseBonjour'] = args.bonjour == 'on'
        write(args.config, document)
        result = {'restartRequired': args.accept == 'on', 'acceptLocalConnections': config['acceptLocalConnections'], 'advertiseBonjour': config['advertiseBonjour']}
    else:result = {**endpoint(config), **{k: config[k] for k in ('resourceId', 'name', 'kind', 'acceptLocalConnections', 'advertiseBonjour', 'allowExecution')}}
    print(json.dumps(result, indent=2))


if __name__ == '__main__':main()
