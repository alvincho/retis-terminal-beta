#!/usr/bin/env python3
"""Start an independent User Terminal using a public Plaza URL, without SSH."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import sys
import time
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value,indent=2)+'\n');temporary.chmod(0o600);temporary.replace(path)


def stop_child(process):
    """Reap a child before reporting shutdown or propagating startup errors."""
    if process is None:return
    if process.poll() is None:process.terminate()
    try:process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill();process.wait(timeout=5)


def command_line(pid):
    """A running process's command line, or '' when there is none; psutil where it is installed, else ps."""
    try:
        import psutil
    except ImportError:
        return subprocess.run(['ps','-p',str(pid),'-o','command='],capture_output=True,text=True).stdout
    try:return ' '.join(psutil.Process(pid).cmdline())
    except (psutil.Error,OSError):return ''


def process_list():
    """(pid, command line) of every process this user can see."""
    try:
        import psutil
    except ImportError:
        listing=subprocess.run(['ps','-ax','-o','pid=,command='],capture_output=True,text=True).stdout
        rows=(line.strip().partition(' ') for line in listing.splitlines())
        return [(int(pid),command) for pid,_,command in rows if pid.isdigit()]
    rows=[]
    for process in psutil.process_iter(['pid','cmdline']):
        try:rows.append((process.info['pid'],' '.join(process.info['cmdline'] or [])))
        except (psutil.Error,OSError):continue
    return rows


def end(pid,force=False):
    """Ask a process to stop (SIGTERM), or with force kill it (SIGKILL).

    Windows has neither signal to catch: there both end the process and every process it started, such as the
    Terminal's Browser workers.
    """
    if os.name=='nt':
        import psutil
        try:
            process=psutil.Process(pid)
            for child in process.children(recursive=True):
                try:child.kill()
                except psutil.Error:pass
            process.kill()
        except psutil.Error:pass
        return
    os.kill(pid,signal.SIGKILL if force else signal.SIGTERM)


def stop_recorded(pid, expected):
    def matches():
        command=command_line(pid)
        return all(part in command for part in expected)
    if not matches():return
    end(pid)
    deadline=time.monotonic()+15
    while matches() and time.monotonic()<deadline:time.sleep(.2)
    if matches():
        end(pid,force=True)
        deadline=time.monotonic()+5
        while matches() and time.monotonic()<deadline:time.sleep(.1)
    if matches():raise RuntimeError('A local source process did not stop.')


LOCAL_YFINANCE=str(ROOT/'scripts/run_local_yfinance.py')


def local_yfinance_pids(config):
    """Every running local yfinance started from this config: the recorded one, and any a killed Terminal left behind."""
    marker=f'--config {config}'
    pids=[]
    for pid,command in process_list():
        at=command.find(marker)
        if (pid!=os.getpid() and LOCAL_YFINANCE in command and at>=0
                and command[at+len(marker):at+len(marker)+1] in ('',' ')):
            pids.append(pid)
    return pids


def stop_local_yfinance(config):
    """Stop every local yfinance started from this config; returns how many were running."""
    pids=local_yfinance_pids(config)
    for pid in pids:stop_recorded(pid,[LOCAL_YFINANCE,str(config)])
    return len(pids)


def address_available(host,port):
    """Whether a listener can bind this address, as uvicorn does (address reuse on)."""
    with socket.socket(socket.AF_INET6 if ':' in host else socket.AF_INET) as check:
        check.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
        try:check.bind((host,port))
        except OSError:return False
    return True


def port_available(port):
    """Whether the Terminal can listen on this loopback port, as uvicorn binds it (address reuse on).

    Reuse lets the port through while a browser tab still holds a connection to the last run; a live listener
    is still refused.
    """
    with socket.socket() as check:
        check.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
        try:check.bind(('127.0.0.1',port))
        except OSError:return False
    return True


def prepare_connection(plaza,state,*,fetch=None):
    import httpx
    from attas.data_terminal_phemar.public_transport import load_connection
    origin=urlsplit(plaza)
    if origin.scheme!='https' or not origin.hostname or origin.username or origin.password or origin.path not in ('','/') or origin.query or origin.fragment:
        raise ValueError('Enter a public Plaza HTTPS origin, such as https://attas-b-031.retis.ai.')
    plaza=plaza.rstrip('/')
    if fetch is None:
        from attas.data_terminal_phemar.tls import verify
        with httpx.Client(trust_env=False,verify=verify(),follow_redirects=False,timeout=20) as client:
            response=client.get(plaza+'/.well-known/attas-network.json')
    else:response=fetch(plaza+'/.well-known/attas-network.json')
    response.raise_for_status()
    if len(response.content)>512000:raise ValueError('Public connection metadata is too large.')
    connection=response.json()
    if connection.get('publicOrigin')!=plaza:raise ValueError('The returned connection belongs to a different Plaza.')
    state.mkdir(parents=True,exist_ok=True,mode=0o700)
    staging=state/'network-candidate.json'
    write(staging,connection)
    try:load_connection(staging)
    except BaseException:staging.unlink(missing_ok=True);raise
    staging.replace(state/'public-network.json')
    identity_path=state/'installation.json'
    if identity_path.is_file():
        identity=json.loads(identity_path.read_text())
        if identity['plaza']!=plaza:raise ValueError('Use a separate state directory for another Plaza.')
    else:
        identity={'id':'user-terminal-'+secrets.token_hex(8),'plaza':plaza}
        write(identity_path,identity)
    agents=connection['agents']
    manifest={'plaza':connection['plazaOrigin'],'sourceOrigins':[r['origin'] for r in agents if r['kind']=='source'],
              'executionOrigins':[r['origin'] for r in agents if r['kind']!='source'],'sourcePolicy':connection.get('sourcePolicy',{}),
              'dependencies':{'dataConsultant':next((r['origin'] for r in agents if r['kind']=='data-consultant'),'')},'services':[]}
    write(state/'deployment.json',manifest)
    return identity,connection


def forget_registration(url,state):
    """Ask the running Terminal to stop heartbeating and leave its Plaza's directory; False when it cannot."""
    import httpx
    try:
        with httpx.Client(base_url=url,trust_env=False,timeout=30) as client:
            secret=state/'local-access.json'
            if secret.exists():  # a launch that prints an access address needs it for every API call
                from attas.data_terminal_phemar.local_access import read_secret
                client.post('/api/local-access',json={'token':read_secret(secret)})
            response=client.post('/api/plaza-registration/forget',headers={'Origin':url.rstrip('/')})
            return response.status_code==200 and response.json().get('forgotten') is True
    except (httpx.HTTPError,ValueError,OSError,ImportError):
        return False


def main():
    # A wrapper script names itself in the usage line.
    parser=argparse.ArgumentParser(prog=os.environ.get('TERMINAL_LAUNCHER_PROG') or None,description=__doc__)
    parser.add_argument('action',nargs='?',choices=['up','down','status','open'],default='up')
    parser.add_argument('--local-only',action='store_true',help='Use owner-approved local resources without any Plaza connection.')
    parser.add_argument('--plaza',help='Public Plaza HTTPS origin; no AWS access or SSH key required.')
    parser.add_argument('--state-dir',type=Path)
    parser.add_argument('--port',type=int,default=28410)
    parser.add_argument('--forget',action='store_true',help='With down: this Terminal is being removed, so it also leaves its Plaza\'s directory.')
    parser.add_argument('--with-local-yfinance', action='store_true', help='Install and run an independent local yfinance source beside this Terminal.')
    parser.add_argument('--local-yfinance-port', type=int, default=28413)
    parser.add_argument('--local-source-origin',action='append',default=[])
    args=parser.parse_args()
    if args.local_only and args.plaza:parser.error('Choose a Plaza URL or local-only mode.')
    if args.local_only and not args.state_dir:args.state_dir=Path.home()/'.local/state/finmas/user-terminals/local'
    if not args.plaza and not args.state_dir:parser.error('Provide --plaza or an existing --state-dir.')
    state=args.state_dir or Path.home()/'.local/state/finmas/user-terminals'/hashlib.sha256(args.plaza.rstrip('/').encode()).hexdigest()[:16]
    state=state.expanduser().resolve()
    record_path=state/'process.json'
    record=json.loads(record_path.read_text()) if record_path.exists() else {}
    def running():
        if not record.get('pid'):return False
        command=command_line(record['pid'])
        return str(ROOT/'scripts/serve_data_terminal.py') in command and record.get('id','') in command
    if args.action=='status':print(json.dumps({'running':running(),'url':record.get('url'),'transport':record.get('transport','direct-https')}));return
    if args.action=='open':
        from attas.data_terminal_phemar.local_access import read_secret
        token=read_secret(state/'local-access.json') if running() else None
        if not token:raise SystemExit('This Terminal is not running. Start it with up; it prints the address to open.')
        address=record['url']+'#local-access='+token
        import webbrowser
        try:opened=webbrowser.open(address)
        except webbrowser.Error:opened=False
        print(json.dumps({'open':address,'browserOpened':bool(opened)}));return
    if args.action=='down':
        # Asked before stopping: only the running Terminal holds the session Plaza recognizes.
        forgotten=forget_registration(record['url'],state) if args.forget and running() else None
        try:
            if running():
                end(record['pid'])
                deadline=time.monotonic()+30
                while running() and time.monotonic()<deadline:time.sleep(.2)
                if running():raise SystemExit('Terminal is still stopping; inspect '+str(state/'terminal.log'))
        finally:
            # Every local yfinance of this Terminal stops, including one a killed Terminal left running.
            local_record=record.get('localYfinance') or {}
            local_config=local_record.get('config') or str(state/'local-yfinance'/'source.json')
            if local_record.get('pid'):stop_recorded(local_record['pid'],[LOCAL_YFINANCE,local_config])
            stop_local_yfinance(local_config)
        if args.forget and not forgotten:
            print('Plaza could not be told; it drops this Terminal from its directory after 14 days without a heartbeat.',file=sys.stderr)
        print(json.dumps({'stopped':True,'statePreserved':str(state),**({'forgotten':bool(forgotten)} if args.forget else {})}));return
    if running():raise SystemExit('This Terminal is already running.')
    if not 1024<=args.port<=65535:parser.error('Choose an unprivileged local port.')
    if not port_available(args.port):
        raise SystemExit(f'Port {args.port} is in use. Stop the other Terminal, or choose another port with --port.')
    host_state=state
    from attas.data_terminal_phemar.trusted_plazas import TrustedPlazas, plaza_key
    registry=TrustedPlazas(host_state/'trusted-plazas.json')
    if args.plaza:
        known=registry.read()['plazas'].get(plaza_key(args.plaza.rstrip('/')))
        if known:state=Path(known['stateDir'])
        elif (state/'installation.json').exists() and json.loads((state/'installation.json').read_text()).get('plaza')!=args.plaza.rstrip('/'):
            state=host_state/'plazas'/plaza_key(args.plaza.rstrip('/'))
    elif registry.read().get('active'):
        state=Path(registry.read()['plazas'][registry.read()['active']]['stateDir'])
    existing=json.loads((state/'installation.json').read_text()) if (state/'installation.json').is_file() else {}
    local_only=args.local_only or existing.get('plaza')=='local-only'
    if not (local_only or args.plaza or existing.get('plaza')):
        parser.error('This state directory has no Plaza yet: name it with --plaza https://…')
    plaza='local-only' if local_only else args.plaza or existing['plaza']
    if local_only:
        if existing and existing.get('plaza')!='local-only':raise ValueError('Use a separate state directory for local-only mode.')
        identity=existing or {'id':'user-terminal-'+secrets.token_hex(8),'plaza':'local-only'}
        write(state/'installation.json',identity)
        connection={'plazaOrigin':'http://127.0.0.1:1','agents':[]}
        write(state/'deployment.json',{'plaza':connection['plazaOrigin'],'sourceOrigins':[],'executionOrigins':[]})
    else:
        import httpx
        try:identity,connection=prepare_connection(plaza,state)
        except httpx.HTTPError as exc:
            raise SystemExit(f"Can't reach the Plaza at {plaza} ({type(exc).__name__}). If the site is off, turn it on "
                             "and run this again; otherwise check your network.") from None
        registry.remember(plaza,identity['id'],state)
    from attas.data_terminal_phemar.local_transport import local_source_origins
    from attas.data_terminal_phemar.launch_settings import terminal_version
    local=local_source_origins(args.local_source_origin,plaza_url=connection['plazaOrigin'])
    if any(origin in [r['origin'] for r in connection['agents']] for origin in local):raise ValueError('A local source cannot shadow a published cloud route.')
    env={k:v for k,v in os.environ.items() if k.upper() not in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','NO_PROXY')}
    env.update(PYTHONPATH=str(ROOT),PYTHON_DOTENV_DISABLED='1',ATTAS_PLAZA_URL=connection['plazaOrigin'],
               ATTAS_PUBLIC_NETWORK_FILE=str(state/'public-network.json'),ATTAS_DEPLOYMENT_MANIFEST=str(state/'deployment.json'),
               ATTAS_TERMINAL_STATE=str(state/'state'),ATTAS_SESSION_COOKIE='attas_user_terminal',ATTAS_CANONICAL_MODELS='1',
               ATTAS_PUBLIC_ORIGIN=f'http://127.0.0.1:{args.port}',ATTAS_RELEASE_VERSION=terminal_version(),
               ATTAS_B028_TRUSTED_ORIGINS=','.join(r['origin'] for r in connection['agents']),ATTAS_LOCAL_SOURCE_ORIGINS=','.join(local),
               PROMPITS_PLAZA_OWNER_KEY='',PLAZA_OWNER_KEY='',PROMPITS_USE_OWNER_KEY_AS_CALLER_USER_TOKEN='0')
    env['ATTAS_TRUSTED_PLAZAS_FILE']=str(registry.path)
    from attas.data_terminal_phemar.launch_settings import terminal_channel
    # The desktop app ships no local sources to find, and listening for them would bring up a firewall prompt on Windows.
    env['PHEMACAST_LOCAL_DISCOVERY']='0' if terminal_channel()=='public' else '1'
    env['ATTAS_LOCAL_ONLY']='1' if local_only else '0'
    if local_only:env.pop('ATTAS_PUBLIC_NETWORK_FILE',None)
    # The one launch setting; agent views live beside the Terminal's state, as in a packaged Terminal.
    from attas.data_terminal_phemar.launch_settings import terminal_env, prebuilt_folder, prebuilt_env, prebuilt_ui
    # A desktop app brings its UI and agent views built: they are used as they are, nothing is built at start.
    prebuilt=prebuilt_folder()
    try:env.update(prebuilt_env(prebuilt,host_state/'agent-view',agent_views=not local_only) if prebuilt
                   else terminal_env(host_state/'agent-view',agent_views=not local_only))
    except (RuntimeError,OSError,ValueError,subprocess.CalledProcessError) as exc:
        detail=getattr(exc,'stderr','') or str(exc)
        print(f'Agent views are unavailable this time ({detail.strip().splitlines()[-1] if detail.strip() else exc}); '
              'the Terminal starts without them.',file=sys.stderr)
        env.update(terminal_env(host_state/'agent-view',agent_views=False))
    local_process=None
    local_config=host_state/'local-yfinance'/'source.json'
    if args.with_local_yfinance or local_config.exists():
        from scripts.run_local_yfinance import prepare
        local_config=prepare(local_config.parent,args.local_yfinance_port)
        # A Terminal that was killed leaves its local yfinance running on the same address; stop it first.
        left=stop_local_yfinance(local_config)
        if left:print(f'Stopped the local yfinance an earlier run left running ({left}); starting it again.',file=sys.stderr)
        listen=json.loads(local_config.read_text())['local_network']
        if not address_available(listen['address'],int(listen['port'])):
            raise SystemExit(f"Local yfinance cannot listen on {listen['address']}:{listen['port']}: another program is using it. "
                             'Stop that program, or start with --local-yfinance-port and a free port.')
        env['ATTAS_BUNDLED_YFINANCE_CONFIG']=str(local_config)
        with (host_state/'local-yfinance.log').open('ab') as log:
            local_process=subprocess.Popen([sys.executable,str(ROOT/'scripts/run_local_yfinance.py'),'--config',str(local_config)],cwd=ROOT,env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
        # Verify TLS identity before starting the client, with a bounded wait.
        from phemacast.network.service import descriptor
        from phemacast.network.tls import pinned_context
        import asyncio
        endpoint=descriptor(json.loads(local_config.read_text())['local_network'])
        deadline=time.monotonic()+30
        try:
            while True:
                if local_process.poll() is not None:raise RuntimeError('Local yfinance failed. Inspect '+str(host_state/'local-yfinance.log'))
                try:asyncio.run(pinned_context(endpoint))
                except (OSError,ValueError,asyncio.TimeoutError):
                    if time.monotonic()>deadline:raise RuntimeError('Local yfinance startup timed out.')
                    time.sleep(.2);continue
                # The answer must come from the process just started, not from another one on the same address.
                time.sleep(.5)
                if local_process.poll() is not None:raise RuntimeError('Local yfinance failed. Inspect '+str(host_state/'local-yfinance.log'))
                break
        except BaseException:
            stop_child(local_process);raise
    # A new secret for each launch proves sign-in requests come from this computer's user (local_access.py).
    from attas.data_terminal_phemar.local_access import write_secret, ENV as LOCAL_ACCESS_ENV
    access_token=write_secret(host_state/'local-access.json')
    env[LOCAL_ACCESS_ENV]=str(host_state/'local-access.json')
    try:
        with (state/'terminal.log').open('ab') as log:
            process=subprocess.Popen([sys.executable,str(ROOT/'scripts/serve_data_terminal.py'),'--kind','data-terminal','--id',identity['id'],'--port',str(args.port),*(['--web-dir',str(prebuilt_ui(prebuilt))] if prebuilt else [])],cwd=ROOT,env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
    except BaseException:
        stop_child(local_process);raise
    record={'pid':process.pid,'id':identity['id'],'url':f'http://127.0.0.1:{args.port}/','plaza':plaza,'transport':'local-only' if local_only else 'direct-https'}
    if local_process:record['localYfinance']={'pid':local_process.pid,'config':str(local_config)}
    write(record_path,record)
    import httpx
    deadline=time.monotonic()+30
    with httpx.Client(trust_env=False,timeout=2) as health:
        while time.monotonic()<deadline:
            if process.poll() is not None:
                stop_child(local_process)
                raise SystemExit('Terminal failed to start. Inspect '+str(state/'terminal.log'))
            try:
                response=health.get(record['url']+'health')
                if response.status_code==200:break
            except httpx.HTTPError:pass
            time.sleep(.25)
        else:
            stop_child(process)
            stop_child(local_process)
            raise SystemExit('Terminal did not become healthy. Inspect '+str(state/'terminal.log'))
    # Only the printed address carries the secret; process.json and the logs never do.
    print(json.dumps({**record,'open':record['url']+'#local-access='+access_token}))


if __name__=='__main__':main()
