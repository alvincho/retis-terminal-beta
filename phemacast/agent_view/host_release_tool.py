"""Capture and activate one explicitly approved generic host release.

Activation changes only the default for subsequent explicit mounts. It never
restarts a process, broadcasts reloads, or changes a Phema's application state.
"""
import argparse,json,os,tempfile
from pathlib import Path
from prompits.core.capabilities import digest
from prompits.core.file_lock import exclusive
from .host_releases import capture,HostReleases

def activate(config_path,record,*,expected_digest):
    path=Path(config_path).resolve()
    with exclusive(path.with_suffix(path.suffix+'.host-release.lock')):
        before=path.read_bytes();value=json.loads(before)
        if digest(value)!=expected_digest:raise ValueError('Container configuration changed; review the current version before activating.')
        proposed={**value,'host_releases':{**value.get('host_releases',{}),
            'default':record['release'],'releases':list(value.get('host_releases',{}).get('releases',[]))}}
        rows=proposed['host_releases']['releases'];previous=next((r for r in rows if r['release']==record['release']),None)
        if previous and previous!=record:raise ValueError('Approved host release registration conflicts.')
        if previous is None:rows.append(record)
        # Validate the complete selected record before replacing any config.
        candidate=None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent,prefix='.host-release-',delete=False) as stream:
                candidate=Path(stream.name);stream.write(json.dumps(proposed,indent=2).encode());os.fchmod(stream.fileno(),0o600)
            HostReleases(candidate).load(record['release'])
            if path.read_bytes()!=before:raise ValueError('Container configuration changed during activation.')
            candidate.replace(path);candidate=None
        finally:
            if candidate:candidate.unlink(missing_ok=True)
    return {'hostRelease':record['release'],'activatedForNewMounts':True,'processRestarted':False,'activeViewsReloaded':False}

def main():
    parser=argparse.ArgumentParser(description=__doc__);commands=parser.add_subparsers(dest='command',required=True)
    build=commands.add_parser('capture');build.add_argument('--source-root',required=True);build.add_argument('--dependencies',required=True);build.add_argument('--runtime',required=True);build.add_argument('--output',required=True);build.add_argument('--record',required=True)
    install=commands.add_parser('activate');install.add_argument('--config',required=True);install.add_argument('--record',required=True);install.add_argument('--expected-config-digest',required=True);install.add_argument('--approved-client-update',action='store_true')
    args=parser.parse_args()
    if args.command=='capture':
        record=capture(source_root=args.source_root,dependencies=args.dependencies,runtime_directory=args.runtime,destination=args.output)
        path=Path(args.record).resolve()
        if path.is_relative_to(Path(args.source_root).resolve()):raise ValueError('Keep release registration state outside the source checkout.')
        path.write_text(json.dumps(record,indent=2));path.chmod(0o600)
        print('Generic host release captured. Activation requires specific human approval.')
    else:
        if not args.approved_client_update:parser.error('Obtain approval for this specific client update before activation.')
        record=json.loads(Path(args.record).read_text());activate(args.config,record,expected_digest=args.expected_config_digest)
        print('Approved generic host selected for new mounts. No process or active view restarted.')

if __name__=='__main__':main()
