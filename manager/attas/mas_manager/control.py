"""The desktop app's private channel to the manager on this computer, and the Terminal as a bundled component.

The Retis Terminal app keeps the Terminal's vault key in the macOS Keychain or Windows DPAPI. When the user opens
the app, it hands that key to the manager here; the manager keeps it in memory only, starts the Terminal with it,
and restarts the Terminal with it if it stops. A manager that restarts no longer has it, so the Terminal then waits
until the app is opened again. The key is never written to the manager's records or logs.

The channel listens on 127.0.0.1 only. Its port and a fresh secret are written to `control.json` in the manager's
state folder, readable by this user only; every request carries the secret.
"""
from __future__ import annotations

import asyncio
import hmac
import json
import os
import secrets
import sys
from pathlib import Path

TERMINAL = 'terminal'
FILE = 'control.json'
LIMIT = 64 * 1024


def terminal_entry(runtime: Path, version: str) -> dict:
    """The Terminal as the app bundles it: its own launcher, kept attached so the manager sees it stop."""
    return {'id': TERMINAL, 'version': version or 'bundled', 'optional': False,
            'names': {'en': 'Retis Terminal', 'zh-Hant': 'Retis 終端'},
            'descriptions': {'en': 'Your Terminal: workspaces, Phemas and your keys stay here.',
                             'zh-Hant': '您的終端：工作區、Phema 與金鑰都保存在這裡。'},
            'run': {'command': ['{python}', str(Path(runtime) / 'scripts' / 'run_user_terminal.py'), 'up', '--foreground'],
                    'env': {'PYTHONPATH': str(runtime), 'PYTHON_DOTENV_DISABLED': '1', 'PROMPITS_ENV_FILE': 'none',
                            'PYTHONDONTWRITEBYTECODE': '1', 'RETIS_MAS_MANAGER': '1', 'TERMINAL_LAUNCHER_PROG': 'Retis Terminal'}}}


def start_terminal(manager, runtime: Path, request: dict) -> dict:
    """Register the bundled Terminal for this state folder and port, and start it with the key handed over."""
    key = request.get('vault_key')
    state = request.get('state_dir')
    port = request.get('port')
    if not isinstance(key, str) or not key or not isinstance(state, str) or type(port) is not int or not 1024 <= port <= 65535:
        raise ValueError('Give the vault key, the Terminal state folder and its port.')
    entry = terminal_entry(runtime, str(request.get('version') or 'bundled'))
    entry['run']['command'] += ['--state-dir', state, '--port', str(port)]
    site = request.get('site')
    if isinstance(site, str) and site.startswith('https://') and not (Path(state) / 'installation.json').is_file():
        entry['run']['command'] += ['--plaza', site]
    manager.register_bundled(entry, runtime)
    if manager.supervisor.is_running(TERMINAL):
        manager.stop(TERMINAL)
    return manager.start(TERMINAL, env={'ATTAS_TERMINAL_VAULT_KEY': key})


class Control:
    """POST {"action": ...} with header X-Retis-Control: <secret>. Actions: terminal-start, terminal-stop, status,
    shutdown (stop every component and then the manager, as an uninstaller asks)."""

    def __init__(self, agent, state: Path, runtime: Path | None, on_shutdown=None):
        self.agent, self.state, self.runtime = agent, Path(state), Path(runtime) if runtime else None
        self.secret = secrets.token_urlsafe(32)
        self.server = None
        self.on_shutdown = on_shutdown  # ends the manager once the answer is sent

    async def start(self, port: int = 0):
        self.server = await asyncio.start_server(self.handle, '127.0.0.1', port)
        bound = self.server.sockets[0].getsockname()[1]
        path = self.state / FILE
        temporary = path.with_suffix('.tmp')
        with os.fdopen(os.open(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600), 'w') as handle:
            json.dump({'port': bound, 'secret': self.secret, 'pid': os.getpid()}, handle)
        os.replace(temporary, path)
        return bound

    def close(self):
        if self.server:
            self.server.close()
        try:
            (self.state / FILE).unlink()
        except OSError:
            pass

    def act(self, request: dict) -> dict:
        action = request.get('action')
        manager = self.agent.manager
        if action == 'status':
            row = next((r for r in manager.status(probe=False) if r['id'] == TERMINAL), None)
            return {'manager': True, 'terminal': row and {'state': row['state'], 'running': bool((row.get('process') or {}).get('running'))}}
        if action == 'terminal-start':
            if self.runtime is None:
                raise ValueError('This manager was started without the Terminal it runs.')
            with self.agent.lock:
                row = start_terminal(manager, self.runtime, request)
            return {'started': True, 'state': row['state']}
        if action == 'terminal-stop':
            with self.agent.lock:
                row = manager.stop(TERMINAL) if manager.state.read()['components'].get(TERMINAL) else {'state': 'not_installed'}
            return {'stopped': True, 'state': row['state']}
        if action == 'shutdown':
            # No more supervising, so nothing is restarted; each component's wanted state stays in the records, so
            # a manager that starts again (after an update, say) runs what ran before.
            self.agent.stop_background()
            with self.agent.lock:
                stopped = [component_id for component_id in manager.state.read()['components']
                           if manager.supervisor.is_running(component_id)]
                manager.supervisor.shutdown(stop_components=True)
            return {'stopped': stopped}
        raise ValueError('Unknown action.')

    async def handle(self, reader, writer):
        status, body, action = 400, {'error': 'Bad request.'}, None
        try:
            line = (await asyncio.wait_for(reader.readline(), 5)).decode('latin-1').split()
            headers = {}
            while True:
                raw = await asyncio.wait_for(reader.readline(), 5)
                if raw in (b'\r\n', b'\n', b''):
                    break
                name, _, value = raw.decode('latin-1').partition(':')
                headers[name.strip().lower()] = value.strip()
            length = int(headers.get('content-length') or 0)
            if len(line) < 2 or line[0] != 'POST' or line[1] != '/control' or not 0 < length <= LIMIT:
                status, body = 404, {'error': 'Not found.'}
            elif not hmac.compare_digest(headers.get('x-retis-control', ''), self.secret) or headers.get('origin'):
                status, body = 403, {'error': 'Not allowed.'}
            else:
                request = json.loads(await asyncio.wait_for(reader.readexactly(length), 5))
                request = request if isinstance(request, dict) else {}
                action = request.get('action')
                body = await asyncio.to_thread(self.act, request)
                status = 200
        except (ValueError, KeyError) as error:
            status, body = 422, {'error': str(error)[:300]}
        except Exception as error:  # every answer is one JSON line; details stay in the manager's log
            print(f'control: {type(error).__name__}: {error}', file=sys.stderr, flush=True)
            status, body = 500, {'error': type(error).__name__}
        data = json.dumps(body).encode()
        reason = {200: 'OK', 400: 'Bad Request', 403: 'Forbidden', 404: 'Not Found', 422: 'Unprocessable', 500: 'Error'}[status]
        writer.write(f'HTTP/1.1 {status} {reason}\r\nContent-Type: application/json\r\nContent-Length: {len(data)}\r\n'
                     f'Connection: close\r\n\r\n'.encode() + data)
        try:
            await writer.drain()
        finally:
            writer.close()
        # After the answer is sent: the listener ends and the process exits with 0, which a login item (launchd's
        # KeepAlive SuccessfulExit=false) does not restart.
        if status == 200 and action == 'shutdown' and self.on_shutdown:
            self.on_shutdown()


def call(state: Path, request: dict, *, timeout: float = 30.0) -> dict:
    """Send one request to the manager whose state folder is `state` (used by the desktop app's controller)."""
    import http.client
    info = json.loads((Path(state) / FILE).read_text())
    connection = http.client.HTTPConnection('127.0.0.1', int(info['port']), timeout=timeout)
    try:
        payload = json.dumps(request).encode()
        connection.request('POST', '/control', body=payload, headers={'Content-Type': 'application/json', 'X-Retis-Control': info['secret']})
        response = connection.getresponse()
        value = json.loads(response.read(LIMIT) or b'{}')
        if response.status != 200:
            raise RuntimeError(value.get('error') or f'Manager answered {response.status}.')
        return value
    finally:
        connection.close()
