"""The Data Terminal's one launch setting.

A container Terminal without the obsolete application routes, agents' operation and job forms (Agent
workspaces), and agent views. Every launcher applies it: run_user_terminal.py (and so every packaged Terminal),
run_terminal_instance.py, and a hosted site's Terminal. Only a Terminal's site address and
state folder differ between environments.
"""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SETTING = {'ATTAS_TERMINAL_PROFILE': 'container', 'ATTAS_NATIVE_OPERATION_FORMS': '1', 'ATTAS_NATIVE_JOB_FORMS': '1'}
# Where `npm ci` of packaging/data-terminal/agent-view-tools put the build tools: beside an installed runtime
# (a packaged Terminal's .runtime folder), else this computer's state folder (a checkout).
TOOL_HOMES = (ROOT.parent / '.runtime/agent-view-tools', Path.home() / '.local/state/finmas/agent-view-tools')


def terminal_version(root=ROOT):
    """This Terminal's own release, never a Plaza's or a site's: the package's version, else the checkout's tag.

    A commit between tags reads untagged-<commit>, as an untagged package's manifest does.
    """
    manifest = Path(root).parent / 'package-manifest.json'
    if manifest.is_file() and json.loads(manifest.read_text()).get('version'):
        return str(json.loads(manifest.read_text())['version'])
    git = lambda *a: subprocess.run(['git', '-C', str(root), *a], capture_output=True, text=True, timeout=5).stdout.strip()
    try:
        tag = git('describe', '--tags', '--exact-match', 'HEAD')
        commit = '' if tag else git('rev-parse', '--short=12', 'HEAD')
    except (OSError, subprocess.SubprocessError):
        tag = commit = ''
    return tag or ('untagged-' + commit if commit else 'untagged')


def package_manifest(root=ROOT):
    """The manifest of the package this Terminal runs from, without its file list; {} for a checkout."""
    manifest = Path(root).parent / 'package-manifest.json'
    if not manifest.is_file():
        return {}
    value = json.loads(manifest.read_text())
    value.pop('files', None)
    return value


def terminal_channel(root=ROOT):
    """Which line of builds this Terminal belongs to, for its badge, /health and Plaza card.

    A package names its channel, and the desktop app (a package with a product name) is public; a checkout is dev.
    """
    manifest = package_manifest(root)
    if not manifest:
        return 'dev'
    return str(manifest.get('channel') or ('public' if manifest.get('product') else ''))


def terminal_product(root=ROOT):
    """The product name a package gives the Terminal (Retis Terminal for the desktop app), else None."""
    return package_manifest(root).get('product') or None


def prebuilt_folder(root=ROOT):
    """The captured UI and agent views a desktop app was built with (scripts/build_desktop_app.py), else None."""
    folder = Path(root).parent / 'prebuilt'
    return folder if (folder / 'prebuilt.json').is_file() else None


def prebuilt_ui(prebuilt):
    """The captured UI release the app was built with; the Terminal serves it as is."""
    return Path(prebuilt) / json.loads((Path(prebuilt) / 'prebuilt.json').read_text())['ui']


def prebuilt_env(prebuilt, views, *, agent_views=True):
    """The setting for a Terminal whose agent views were built with its app: nothing is built when it starts.

    The agent-view configuration names its runtime and host releases by absolute path, as they were where the app
    was built. Each start writes a copy with this installation's path into `views`, in the Terminal's state folder:
    the app may have moved or been updated, and its own files stay read-only.
    """
    if not agent_views:
        return dict(SETTING)
    prebuilt = Path(prebuilt)
    record = json.loads((prebuilt / 'prebuilt.json').read_text())
    built, here = record['buildRoot'].rstrip('/'), prebuilt / record['agentView']

    def moved(value):
        if isinstance(value, dict):
            return {key: moved(item) for key, item in value.items()}
        if isinstance(value, list):
            return [moved(item) for item in value]
        if isinstance(value, str) and (value == built or value.startswith(built + '/')):
            return str(here.joinpath(*[part for part in value[len(built):].split('/') if part]))
        return value

    config = moved(json.loads(agent_view_config(here).read_text()))
    views = Path(views)
    views.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = agent_view_config(views)
    temporary = target.with_suffix('.tmp')
    temporary.write_text(json.dumps(config, indent=2))
    temporary.chmod(0o600)
    temporary.replace(target)
    return {**SETTING, 'ATTAS_AGENT_VIEW_CONFIGURATION': str(target)}


def agent_view_tools():
    return next((home for home in TOOL_HOMES if (home / 'node_modules/.bin/esbuild').exists()), TOOL_HOMES[-1])


def runtime_key(tools):
    """Names a confined runtime by what it is built from: its source, as the build records it, and the tools' lock file."""
    source = ROOT / 'phemacast/agent_view/confined'
    hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in source.iterdir() if p.suffix in ('.js', '.mjs', '.json', '.py')}
    lock = Path(tools) / 'package-lock.json'
    hashes['tools'] = hashlib.sha256(lock.read_bytes()).hexdigest() if lock.is_file() else ''
    return hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()


def agent_view_config(views):
    """The agent-view configuration a Terminal reads, once prepare_agent_views has written it."""
    return Path(views) / 'container-config.json'


def terminal_env(views, *, agent_views=True, tools=None):
    """The setting for one Terminal; its agent views are prepared in `views` (none for a local-only Terminal)."""
    if not agent_views:
        return dict(SETTING)
    return {**SETTING, 'ATTAS_AGENT_VIEW_CONFIGURATION': str(prepare_agent_views(views, tools=tools))}


def prepare_agent_views(views, *, tools=None, path=None):
    """Build the confined runtime and capture the generic host from this code; return the config path.

    Packaged and local dev Terminals capture and activate the
    host release at the first start. Later starts reuse the runtime and activate a new host release only
    when the host code changed. The runtime is rebuilt whenever its source or tools change, into its own folder
    under runtimes/; earlier ones stay for the host releases that use them. `path` prefixes PATH (a host's own node).
    """
    views, tools = Path(views), Path(tools or agent_view_tools())
    if not (tools / 'node_modules/.bin/esbuild').exists():
        raise RuntimeError(f'Agent-view build tools are missing at {tools}: in an installed Terminal, install it '
                           'again; in a checkout, npm ci there with packaging/data-terminal/agent-view-tools/package*.json')
    runtime, config, record = views / 'runtimes' / runtime_key(tools), agent_view_config(views), views / 'host-release.json'
    views.mkdir(parents=True, exist_ok=True, mode=0o700)
    env = {**os.environ, 'PYTHONPATH': str(ROOT), 'PYTHON_DOTENV_DISABLED': '1', 'NODE_PATH': str(tools / 'node_modules')}
    if path:
        env['PATH'] = str(path) + os.pathsep + env.get('PATH', '')
    step = lambda *argv: subprocess.run([sys.executable, *map(str, argv)], cwd=ROOT, env=env, check=True,
                                        capture_output=True, text=True)
    if not (runtime / 'runtime.json').is_file():
        step('-m', 'phemacast.agent_view.confined.build', '--dependencies', tools, '--output', runtime)
    if not config.is_file():
        config.write_text(json.dumps({'runtime_directory': str(runtime), 'host_releases': {'releases': []}, 'views': []}, indent=2))
        config.chmod(0o600)
    step('-m', 'phemacast.agent_view.host_release_tool', 'capture', '--source-root', ROOT, '--dependencies', tools,
         '--runtime', runtime, '--output', views / 'host-releases', '--record', record)
    captured = json.loads(record.read_text())['release']
    if json.loads(config.read_text()).get('host_releases', {}).get('default') != captured:
        from prompits.core.capabilities import digest
        step('-m', 'phemacast.agent_view.host_release_tool', 'activate', '--config', config, '--record', record,
             '--expected-config-digest', digest(json.loads(config.read_text())), '--approved-client-update')
    return config
