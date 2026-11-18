"""Retis MAS Manager: installs, updates, runs and watches Retis components on the user's computer.

It runs in the background from login as a local resource on this computer: it listens on 127.0.0.1, Bonjour
announces it, and this computer's Terminal uses it at once, with no sign-in or pairing. It manages this computer
only. It keeps only its own records: installed versions, running processes and a few days of events. It never sees
the user's Terminal data, vendor keys or Plaza sign-in.
"""
from __future__ import annotations

import base64
import json
import platform
import sys
import threading
import time
from pathlib import Path

from fastapi import HTTPException
from phemacast.agents.pulser import Pulser
from prompits.components.installer import https_opener
from prompits.components.manager import ComponentManager, ManagerError
from .contracts import ENVELOPE, NAME, SCHEMAS, check, definition

TICK_SECONDS = 3.0
CHECK_EVERY_SECONDS = 86400.0
PARTS_LIST_LIMIT = 2 * 1024 * 1024
# Operations that change what runs; they take turns with the supervisor's own checks.
RUN_OPERATIONS = {'mas_start', 'mas_stop', 'mas_restart', 'mas_rollback', 'mas_remove'}


def pip_command(folder) -> list[str]:
    """Run the pip a host app keeps in `folder` (its `pip` package) with this Python, isolated from the environment.

    `-B`: no bytecode is written beside pip. The folder is inside the signed app, and `-I` ignores
    PYTHONDONTWRITEBYTECODE; a file added there breaks the app's signature."""
    return [sys.executable, '-I', '-B', str(Path(folder).expanduser() / 'pip')]


def offer_included(manager: ComponentManager, folder) -> list[str]:
    """Offer every component the host app ships in `folder` (each subfolder's component.json `offer`)."""
    offered = []
    if not folder:
        return offered
    root = Path(folder).expanduser()
    for item in sorted(root.iterdir()) if root.is_dir() else ():
        description = item / 'component.json'
        if not description.is_file():
            continue
        try:
            entry = json.loads(description.read_text(encoding='utf-8'))['offer']
            manager.offer_included(entry, item)
            offered.append(entry['id'])
        except (OSError, ValueError, KeyError, TypeError, ManagerError) as error:
            manager.state.record_event('included_refused', None, folder=item.name, reason=str(error)[:300])
    return offered


class MasManager(Pulser):
    PIT_TYPE = 'Agent'

    def __init__(self, config, *, manager: ComponentManager | None = None, **kwargs):
        config = dict(config)
        kwargs.setdefault('name', config.get('name') or 'Retis MAS Manager')
        kwargs.setdefault('supported_pulses', [definition()])
        super().__init__(config={**config, 'plaza_url': ''}, auto_register=False, **kwargs)
        self.raw_config = {**self.raw_config, **config}
        parts = config.get('parts_list') or {}
        self.parts_source = {key: parts[key] for key in ('url', 'file') if parts.get(key)}
        keys = {key_id: base64.b64decode(value) for key_id, value in (parts.get('keys') or {}).items()}
        # `package_folder` (tests, offline installs): packages come from a folder; signature and checksums still hold.
        from prompits.components.installer import folder_opener
        options = {'opener': folder_opener(Path(parts['package_folder']))} if parts.get('package_folder') else {}
        # A desktop app keeps pip outside its Python's import path (`pip_folder`): only the manager runs it.
        if config.get('pip_folder'):
            options['pip'] = pip_command(config['pip_folder'])
        self.manager = manager or ComponentManager(Path(config['components_root']).expanduser(), keys, **options)
        self.included = offer_included(self.manager, config.get('included_components'))
        self.lock = threading.RLock()
        self.jobs: dict[str, dict] = {}
        self.parts_check = {'checked_at': None, 'error': ''}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.resource = None   # this manager's local-resource listener, once it runs

    # ----- background work
    def run_background(self):
        """Adopt or start components, then check them every few seconds and the parts list once a day."""
        with self.lock:
            self.manager.resume()
        self._thread = threading.Thread(target=self._loop, name='mas-manager', daemon=True)
        self._thread.start()

    def stop_background(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=10)

    def _loop(self):
        last_check = 0.0
        while not self._stop.is_set():
            if self.parts_source and time.time() - last_check >= CHECK_EVERY_SECONDS:
                last_check = time.time()
                self.check_updates()
            with self.lock:
                try:
                    self.manager.tick()
                except Exception as error:  # one bad tick never stops the supervisor
                    self.manager.state.record_event('tick_failed', None, reason=str(error)[:300])
            self._stop.wait(TICK_SECONDS)

    def check_updates(self) -> dict:
        try:
            if 'file' in self.parts_source:
                envelope = json.loads(Path(self.parts_source['file']).expanduser().read_text())
            elif 'url' in self.parts_source:
                with https_opener(self.parts_source['url']) as response:
                    data = response.read(PARTS_LIST_LIMIT + 1)
                if len(data) > PARTS_LIST_LIMIT:
                    raise ManagerError('The parts list is larger than allowed.')
                envelope = json.loads(data)
            else:
                raise ManagerError('No parts list is configured.')
            result = self.manager.accept_parts_list(envelope)
            self.parts_check = {'checked_at': time.time(), 'error': ''}
            return result
        except Exception as error:
            self.parts_check = {'checked_at': time.time(), 'error': str(error)[:300]}
            return {'error': self.parts_check['error']}

    def _job(self, component_id: str, kind: str, work):
        """Downloads run beside everything else; only the switch takes the run lock."""
        if (self.jobs.get(component_id) or {}).get('state') == 'running':
            return {'error': f'{component_id} is already being {kind}ed.'}
        job = self.jobs[component_id] = {'kind': kind, 'state': 'running', 'started_at': time.time(), 'error': ''}

        def run():
            try:
                work()
                job['state'] = 'done'
            except Exception as error:
                job.update(state='failed', error=str(error)[:300])
            job['ended_at'] = time.time()
        threading.Thread(target=run, name=f'mas-{kind}-{component_id}', daemon=True).start()
        return {'accepted': True, 'component': component_id, 'job': dict(job)}

    # ----- the pulse
    def get_pulse_data(self, input_data=None, pulse_name=None, pulse_address=None, output_schema=None):
        name = pulse_name or (pulse_address.removeprefix('attas:') if pulse_address else NAME)
        if name != NAME or not isinstance(input_data, dict):
            raise HTTPException(422, 'Choose the manager operation with object input.')
        try:
            value = check(ENVELOPE, {key: item for key, item in input_data.items() if key != '_caller'})
            operation = value['operation']
            if operation not in SCHEMAS:
                raise ValueError('Unknown operation ' + operation)
            parameters = check(SCHEMAS[operation], value['parameters'])
        except ValueError as error:
            raise HTTPException(422, str(error))
        parameters.pop('host', None)
        # The Terminal's view channel matches each answer to its call by `channel_receipt`.
        receipt = {'request_key': value['request_key'], 'channel_receipt': value['request_key']}
        try:
            return {'operation': operation, 'response': self.dispatch(operation, parameters), **receipt}
        except ManagerError as error:
            return {'operation': operation, 'error': str(error), **receipt}
        except HTTPException as error:
            return {'operation': operation, 'error': str(error.detail), **receipt}

    def dispatch(self, operation: str, parameters: dict):
        component = parameters.get('component')
        if operation == 'mas_status':
            return self.status(parameters.get('language', 'en'))
        if operation == 'mas_check_updates':
            result = self.check_updates()
            if result.get('error'):
                raise ManagerError(result['error'])
            return result
        if operation == 'mas_logs':
            count = parameters.get('lines', 200)
            return {'component': component, 'lines': self.manager.logs(component, count),
                    'data_lines': self.manager.data_logs(component, count)}
        if operation == 'mas_download_data':
            with self.lock:
                return self.manager.download_data(component, parameters['data'])
        if operation == 'mas_stop_download':
            with self.lock:
                return self.manager.stop_download(component)
        if operation == 'mas_notices':
            return {'component': component, 'text': self.manager.notices_text(component)}
        if operation == 'mas_report':
            return self.manager.report(extra={'manager': self.name, 'parts_list': self.parts_check})
        terms = parameters.get('accept_terms', [])
        if operation == 'mas_install':
            def install():
                self.manager.download(component, accept_terms=terms)
                with self.lock:
                    self.manager.install(component, accept_terms=terms)
                    # Installing is asking for it: a program the manager runs starts at once.
                    if self.manager.supervisor.component(component).run is not None:
                        self.manager.start(component)
            return self._job(component, 'install', install)
        if operation == 'mas_update':
            def update():
                self.manager.download(component, accept_terms=terms)
                with self.lock:
                    self.manager.update(component, restart=parameters.get('restart', True), accept_terms=terms)
            return self._job(component, 'update', update)
        if operation in RUN_OPERATIONS:
            with self.lock:
                if operation == 'mas_remove':
                    return self.manager.remove(component, keep_data=parameters.get('keep_data', True))
                return getattr(self.manager, operation.removeprefix('mas_'))(component)
        raise ManagerError('Unknown operation ' + operation)

    def status(self, language: str = 'en') -> dict:
        parts = self.manager.state.read().get('parts_list') or {}
        overview = self.manager.overview(language=language)
        rows = overview['components']
        for row in rows:
            job = self.jobs.get(row['id'])
            if job:
                row['job'] = dict(job)
        return {
            'host': {'name': platform.node(), 'platform': self.manager.platform},
            'parts_list': {'sequence': parts.get('sequence'), 'issued_at': parts.get('issued_at'),
                           'publisher': parts.get('publisher'), **self.parts_check, 'configured': bool(self.parts_source)},
            'components': rows,
            'disk': overview['disk'],
            'alerts': overview['alerts'],
            'events': self.manager.events(since=time.time() - 3 * 86400)[-30:],
        }


MasManagerAgent = MasManager
