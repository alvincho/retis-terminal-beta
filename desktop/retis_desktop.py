#!/usr/bin/env python3
"""Retis Terminal's controller: what the Mac menu-bar app and the Windows tray app ask for, one command at a time.

    python retis_desktop.py start                start the Terminal, or find it running; prints {"open": address}
    python retis_desktop.py address              the address to open in the browser while it runs
    python retis_desktop.py status               whether it runs, and on which port
    python retis_desktop.py stop                 stop it; the user's data stays
    python retis_desktop.py report               write a problem report without credentials; prints {"report": path}
    python retis_desktop.py check-update [--force]
                                                 compare this version with the update feed, at most once a day

Each command prints one JSON line on stdout; a failure prints {"error": ...} and exits with 1.

The native shell keeps the vault key (macOS Keychain, Windows DPAPI) and passes it as ATTAS_TERMINAL_VAULT_KEY.
It only opens the printed address in the default browser; the local-access secret in it is never shown.
Everything else is the Terminal's own launcher, scripts/run_user_terminal.py, run with this app's Python.
"""
import json
import os
from pathlib import Path
import re
import ssl
import subprocess
import sys
import time
import urllib.request

KIT = Path(__file__).resolve().parent
RUNTIME = KIT / 'runtime'
LAUNCHER = RUNTIME / 'scripts' / 'run_user_terminal.py'
PRODUCT = 'Retis Terminal'
# Its own port, so it can run beside another Terminal on this computer. The Terminal's sign-in cookie names its port,
# so the app keeps the port it last used and moves only when that one is taken.
DEFAULT_PORT = 28430
PORTS = range(DEFAULT_PORT, DEFAULT_PORT + 10)
UPDATE_INTERVAL = 24 * 3600
FEED_LIMIT = 65536
VERSION = re.compile(r'^(\d+)\.(\d+)\.(\d+)(?:-beta(?:\.(\d+))?)?$')


class Failure(Exception):
    """A problem the app shows the user in one sentence."""


def manifest():
    path = KIT / 'package-manifest.json'
    value = json.loads(path.read_text()) if path.is_file() else {}
    value.pop('files', None)
    return value


def app_state():
    """The app's folder: kept when the app is removed or updated."""
    if os.environ.get('RETIS_TERMINAL_STATE'):
        return Path(os.environ['RETIS_TERMINAL_STATE'])
    if sys.platform == 'darwin':
        return Path.home() / 'Library' / 'Application Support' / PRODUCT
    if os.name == 'nt':
        return Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData' / 'Local') / PRODUCT
    return Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local' / 'state') / 'retis-terminal'


def terminal_state():
    """The Terminal's own state folder (run_user_terminal.py --state-dir)."""
    return app_state() / 'terminal'


def settings_path():
    return app_state() / 'desktop.json'


def load_settings():
    try:
        value = json.loads(settings_path().read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def save_settings(value):
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.replace(path)


def log(message):
    """One line in the app's own log, which problem reports include."""
    path = app_state() / 'desktop.log'
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if path.is_file() and path.stat().st_size > 1_000_000:
            path.replace(path.with_suffix('.log.1'))
        with path.open('a', encoding='utf-8') as out:
            out.write(time.strftime('%Y-%m-%d %H:%M:%S%z ') + message + '\n')
    except OSError:
        pass


def environment():
    """The launcher's environment: this app's code and Python only, no user site-packages or .env files."""
    env = {k: v for k, v in os.environ.items() if k not in ('PYTHONHOME', 'PYTHONPATH', 'PYTHONSTARTUP')}
    env.update(PYTHONPATH=str(RUNTIME), PYTHON_DOTENV_DISABLED='1', PROMPITS_ENV_FILE='none', PYTHONNOUSERSITE='1',
               # The app's files are signed and may be read-only; their bytecode is compiled when the app is built.
               PYTHONDONTWRITEBYTECODE='1',
               PROMPITS_OBSERVABILITY_STATE_DIR=str(app_state() / 'observability'),
               TERMINAL_LAUNCHER_PROG=PRODUCT)
    return env


def launcher(action, *extra, kit=None):
    """Run the Terminal's launcher with this app's state folder; the first start names the app's site."""
    kit = manifest() if kit is None else kit
    args = [action, '--state-dir', str(terminal_state()), *extra]
    # RETIS_TERMINAL_SITE starts a new installation on another site, such as a staging copy, for tests.
    site = os.environ.get('RETIS_TERMINAL_SITE') or kit.get('site')
    if action == 'up' and site and not (terminal_state() / 'installation.json').is_file():
        args += ['--plaza', site]
    terminal_state().mkdir(parents=True, exist_ok=True, mode=0o700)
    return subprocess.run([sys.executable, str(LAUNCHER), *args], cwd=str(app_state()), env=environment(),
                          capture_output=True, text=True, stdin=subprocess.DEVNULL)


def last_json(text):
    for line in reversed((text or '').strip().splitlines()):
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    return None


def problem(result):
    """The launcher's last message, for one line in the app."""
    lines = [line.strip() for line in (result.stderr or '').splitlines() if line.strip()]
    return lines[-1] if lines else f'The Terminal launcher stopped with code {result.returncode}.'


def status():
    result = launcher('status')
    value = last_json(result.stdout)
    if result.returncode or value is None:
        raise Failure(problem(result))
    return value


def port_free(port):
    sys.path.insert(0, str(RUNTIME))
    from scripts.run_user_terminal import port_available
    return port_available(port)


def choose_port(settings):
    preferred = settings.get('port') if settings.get('port') in PORTS else DEFAULT_PORT
    for port in (preferred, *[p for p in PORTS if p != preferred]):
        if port_free(port):
            return port
    raise Failure(f'Ports {PORTS.start}-{PORTS.stop - 1} are all in use. Close the program using them and try again.')


def address():
    """The running Terminal's address with this launch's local-access secret, for the browser only."""
    current = status()
    if not current.get('running'):
        raise Failure('Retis Terminal is not running.')
    sys.path.insert(0, str(RUNTIME))
    from attas.data_terminal_phemar.local_access import read_secret
    token = read_secret(terminal_state() / 'local-access.json')
    if not token:
        raise Failure('Retis Terminal is not running.')
    return {'open': current['url'] + '#local-access=' + token, 'url': current['url']}


def start():
    current = status()
    if current.get('running'):
        return {**address(), 'alreadyRunning': True}
    settings = load_settings()
    port = choose_port(settings)
    log(f'starting on port {port}')
    result = launcher('up', '--port', str(port))
    record = last_json(result.stdout)
    if result.returncode or not record or 'open' not in record:
        message = problem(result)
        log('start failed: ' + message)
        raise Failure(message)
    settings['port'] = port
    save_settings(settings)
    log(f'started on {record["url"]}')
    return {'open': record['open'], 'url': record['url']}


def stop():
    result = launcher('down')
    value = last_json(result.stdout)
    if result.returncode or value is None:
        raise Failure(problem(result))
    log('stopped')
    return {'stopped': True}


def version_key(text):
    match = VERSION.match(text or '')
    if not match:
        raise ValueError(f'Not a Retis Terminal version: {text!r}')
    major, minor, patch, beta = match.groups()
    # 0.1.0-beta < 0.1.0-beta.1 < 0.1.0
    prerelease = '-beta' in text
    return (int(major), int(minor), int(patch), 0 if prerelease else 1, int(beta or 0))


def platform_key():
    return 'mac' if sys.platform == 'darwin' else 'windows' if os.name == 'nt' else 'linux'


def fetch_feed(url):
    if not url.startswith('https://'):
        raise Failure('The update feed must be an HTTPS address.')
    import certifi
    context = ssl.create_default_context(cafile=certifi.where())
    request = urllib.request.Request(url, headers={'User-Agent': f'{PRODUCT} update check', 'Accept': 'application/json'})
    with urllib.request.urlopen(request, timeout=15, context=context) as response:
        body = response.read(FEED_LIMIT + 1)
    if len(body) > FEED_LIMIT:
        raise Failure('The update feed is too large.')
    return json.loads(body)


def compare(feed, current, where=None):
    """What the feed says about this version: whether a newer one exists, and whether this one is still supported."""
    latest, minimum = feed.get('latest'), feed.get('minimumVersion')
    newer = version_key(latest) > version_key(current)
    unsupported = bool(minimum) and version_key(minimum) > version_key(current)
    downloads = feed.get('downloads') if isinstance(feed.get('downloads'), dict) else {}
    link = downloads.get(where or platform_key()) or feed.get('url') or ''
    if not str(link).startswith('https://'):
        link = ''
    return {'current': current, 'latest': latest, 'minimumVersion': minimum, 'updateAvailable': newer or unsupported,
            'required': unsupported, 'download': link, 'notes': str(feed.get('notes') or '')[:2000]}


def check_update(force=False, *, now=time.time, fetch=fetch_feed):
    """At most once a day unless forced; nothing is downloaded or installed, the app only shows the result."""
    kit = manifest()
    settings = load_settings()
    last = settings.get('updateCheck') or {}
    if not force and now() - float(last.get('at', 0)) < UPDATE_INTERVAL and last.get('result'):
        return {**last['result'], 'cached': True}
    feed_url = os.environ.get('RETIS_TERMINAL_UPDATE_FEED') or kit.get('updateFeed')
    current = kit.get('version', '')
    if not feed_url or not VERSION.match(current):
        return {'current': current, 'updateAvailable': False, 'skipped': 'This build has no update feed.'}
    try:
        result = compare(fetch(feed_url), current)
    except Failure:
        raise
    except Exception as exc:  # offline, the repository not public yet, a malformed feed
        log(f'update check failed: {type(exc).__name__}: {exc}')
        raise Failure("Can't check for updates right now. Check your network and try again later.") from None
    settings['updateCheck'] = {'at': now(), 'result': result}
    save_settings(settings)
    return result


def reports_folder():
    for folder in (Path.home() / 'Desktop', Path.home() / 'Downloads'):
        if folder.is_dir() and os.access(folder, os.W_OK):
            return folder
    return app_state() / 'reports'


def report(folder=None):
    """The app's problem report: status and recent log lines, credentials removed, ids labelled."""
    sys.path[:0] = [str(KIT)]  # report_tools.py sits beside this file, in the app and in a checkout
    import report_tools
    kit = manifest()
    header = [f'{PRODUCT} report {time.strftime("%Y-%m-%dT%H:%M:%S%z")}',
              'app: ' + json.dumps({k: kit.get(k) for k in ('version', 'site')}),
              f'system: {__import__("platform").platform()} python {__import__("platform").python_version()}']
    lines = ['', '## status']
    try:
        lines.append(json.dumps(status()))
    except Failure as exc:
        lines.append(f'status unavailable: {exc}')
    settings = load_settings()
    lines.append('settings: ' + json.dumps({'port': settings.get('port'), 'updateCheck': settings.get('updateCheck')}))
    for directory in report_tools.state_directories(terminal_state()):
        log_file = directory / 'terminal.log'
        if log_file.is_file():
            tail, routine, before = report_tools.log_tail(log_file)
            left_out = f'; {routine} routine lines (polling, static files) left out' if routine else ''
            if before:
                lines += ['', f'## terminal.log: earlier warnings and errors ({len(before)} lines)', *before]
            lines += ['', f'## terminal.log (last {len(tail)} lines{left_out})', *tail]
    own = app_state() / 'desktop.log'
    if own.is_file():
        lines += ['', '## desktop.log', *own.read_text(errors='replace').splitlines()[-200:]]
    target = Path(folder) if folder else reports_folder()
    target.mkdir(parents=True, exist_ok=True)
    output = target / f'Retis-Terminal-report-{time.strftime("%Y%m%d-%H%M%S")}.txt'
    # The header is this app's own version and site; the status and logs are redacted.
    output.write_text('\n'.join(header) + '\n' + report_tools.redact('\n'.join(lines)) + '\n', encoding='utf-8')
    return {'report': str(output)}


COMMANDS = {'start': start, 'address': address, 'status': status, 'stop': stop, 'report': report}


def main(argv):
    if not argv or argv[0] not in (*COMMANDS, 'check-update'):
        print(__doc__, file=sys.stderr)
        return 2
    try:
        if argv[0] == 'check-update':
            value = check_update(force='--force' in argv[1:])
        else:
            value = COMMANDS[argv[0]]()
    except Failure as exc:
        print(json.dumps({'error': str(exc), 'log': str(app_state() / 'desktop.log')}))
        return 1
    except Exception as exc:
        log(f'{argv[0]} failed: {type(exc).__name__}: {exc}')
        print(json.dumps({'error': f'{type(exc).__name__}: {exc}', 'log': str(app_state() / 'desktop.log')}))
        return 1
    print(json.dumps(value))
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
