#!/usr/bin/env python3
"""Build Retis Terminal, the public desktop app, for macOS or Windows.

    build_desktop_app.py --platform mac --tag T --version 0.1.0-beta            Retis-Terminal-0.1.0-beta.dmg
    build_desktop_app.py --platform windows --tag T --version 0.1.0-beta        Retis-Terminal-Setup-0.1.0-beta.exe
    build_desktop_app.py ... --untagged                                          a local dry build of HEAD
    build_desktop_app.py --payload-only --output DIR ...                         only the platform-independent kit

In the public retis-terminal-beta repository this file is desktop/build_desktop_app.py and builds the app from that
repository's own source, with the version, site and update feed in its VERSION.json (BUILDING.md has the steps):

    python desktop/build_desktop_app.py --platform mac                             dist/Retis-Terminal-<version>.dmg
    python desktop/build_desktop_app.py --platform windows                         the installer (on Windows)

What the app carries, so a user installs with a double-click and never needs a command line, Node or Python:
  kit/       the public package (build_user_terminal_package.py --profile public), its captured UI and agent views
             prebuilt here (kit/prebuilt), and the controller both native shells call (packaging/desktop)
  python/    a relocatable CPython 3.13 (python-build-standalone, pinned in config/releases/desktop-python.json) with
             only the libraries the Terminal imports, installed from config/releases/user-terminal-requirements.lock
  a native shell: a menu-bar app on macOS (packaging/desktop/mac), a tray app on Windows (packaging/desktop/windows)

macOS: one universal .app (arm64 + x86_64) in a .dmg. Without --sign-identity it is signed ad hoc, which runs but
shows Gatekeeper's "unidentified developer" screen; --sign-identity "Developer ID Application: ..." signs it for
distribution and --notary-profile (an xcrun notarytool keychain profile) notarizes and staples the .dmg.
Windows: one per-user Inno Setup installer that needs no administrator rights, in the Terminal's six languages
(English, Traditional and Simplified Chinese, Japanese, Spanish, French) as Windows' display language says. The
staging folder is built anywhere; the tray app and the installer compile on Windows (csc from .NET Framework 4.8 and
Inno Setup's iscc), as the Windows workflow does. --windows-sign-command signs each executable, e.g.
'signtool sign /fd SHA256 ... {file}'.

Every download is pinned by SHA-256 (Python, the installer's translations) or by the lock's hashes (libraries).
Nothing in the checkout changes.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import ssl
import subprocess
import sys
import tarfile
import tempfile
import urllib.request

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
# Where each file this script reads sits in the public repository (left) and in the source it is generated from
# (right). The publisher copies them; in the public repository, PUBLIC, this script reads its own layout.
PUBLIC_LAYOUT = {
    'desktop/build_desktop_app.py': 'scripts/build_desktop_app.py',
    'desktop/python.json': 'config/releases/desktop-python.json',
    'desktop/mac-overrides.lock': 'config/releases/desktop-mac-overrides.lock',
    'desktop/icon.png': 'apps/phemacast-terminal-ios/Resources/Assets.xcassets/AppIcon.appiconset/AppIcon.png',
    'desktop/agent-view-tools/package.json': 'packaging/data-terminal/agent-view-tools/package.json',
    'desktop/agent-view-tools/package-lock.json': 'packaging/data-terminal/agent-view-tools/package-lock.json',
}
PUBLIC = HERE.name == 'desktop'
here = (lambda public, source: ROOT / public) if PUBLIC else (lambda public, source: ROOT / source)
if not PUBLIC:
    sys.path.insert(0, str(ROOT / 'scripts'))
DESKTOP = ROOT / 'desktop' if PUBLIC else ROOT / 'packaging' / 'desktop'
# The package's record of the release tag and commit it came from (build_user_terminal_package.PROVENANCE): read here,
# never copied into an app. A build from the public repository has none.
PROVENANCE = 'build-provenance.json'
PYTHON_SPEC = here('desktop/python.json', 'config/releases/desktop-python.json')
# The public repository's requirements.lock already holds only DESKTOP_PACKAGES.
LOCK = here('requirements.lock', 'config/releases/user-terminal-requirements.lock')
MAC_OVERRIDES = here('desktop/mac-overrides.lock', 'config/releases/desktop-mac-overrides.lock')
AGENT_VIEW_TOOLS = here('desktop/agent-view-tools', 'packaging/data-terminal/agent-view-tools')
LICENSE = here('LICENSE', 'packaging/retis-terminal-beta/LICENSE')
# The Terminal's own folders in a package's runtime/, which the public repository keeps at its top level.
RUNTIME = ('attas', 'phemacast', 'prompits', 'scripts')
PUBLIC_REPOSITORY = 'alvincho/retis-terminal-beta'
PUBLIC_VERSION = re.compile(r'^\d+\.\d+\.\d+(?:-[0-9A-Za-z.]+)?$')
# Libraries with no Intel Mac wheel whose compiled modules are optional accelerators of their own Python source:
# the Mac app installs the arm64 wheel for both architectures and keeps only the Python source.
MAC_PURE_PYTHON = ('zeroconf',)
ICON = here('desktop/icon.png', 'apps/phemacast-terminal-ios/Resources/Assets.xcassets/AppIcon.appiconset/AppIcon.png')
PRODUCT = 'Retis Terminal'
BUNDLE_ID = 'ai.retis.terminal'
MAC_MINIMUM = '12.0'
# The libraries the Terminal imports (checked against its modules in check_imports), with what they need on any OS.
# The full lock it is cut from also carries what Retis's other agents use (boto3, paramiko, supabase, playwright,
# psycopg, pandas, yfinance, pytest, ...); none of it is imported by the Terminal, so the app leaves it out.
DESKTOP_PACKAGES = ('annotated-doc', 'annotated-types', 'anyio', 'attrs', 'certifi', 'cffi', 'charset-normalizer',
                    'click', 'cryptography', 'fastapi', 'h11', 'httpcore', 'httpx', 'idna', 'ifaddr', 'jsonschema',
                    'jsonschema-specifications', 'pillow', 'psutil', 'pycparser', 'pydantic', 'pydantic-core',
                    'referencing', 'requests', 'rpds-py', 'sniffio', 'starlette', 'typing-extensions',
                    'typing-inspection', 'urllib3', 'uvicorn', 'websockets', 'zeroconf')
# Import names that differ from their distribution's name.
IMPORT_NAMES = {'PIL': 'pillow', 'pydantic_core': 'pydantic-core', 'typing_extensions': 'typing-extensions',
                'jsonschema_specifications': 'jsonschema-specifications', 'rpds': 'rpds-py',
                'charset_normalizer': 'charset-normalizer', 'typing_inspection': 'typing-inspection',
                'annotated_types': 'annotated-types', 'annotated_doc': 'annotated-doc'}
# Parts of the standard library the Terminal never uses: the Tk GUI toolkit, IDLE, the test suite.
STDLIB_UNUSED = ('idlelib', 'tkinter', 'turtledemo', 'turtle.py', 'test', 'ensurepip', 'pydoc_data', 'lib2to3')
MACHO = {b'\xfe\xed\xfa\xce', b'\xfe\xed\xfa\xcf', b'\xce\xfa\xed\xfe', b'\xcf\xfa\xed\xfe', b'\xca\xfe\xba\xbe'}
WHEEL_PLATFORMS = {'mac-arm64': ['macosx_12_0_arm64'], 'mac-x86_64': ['macosx_12_0_x86_64'], 'windows-x86_64': ['win_amd64']}


def run(*argv, **kwargs):
    kwargs.setdefault('check', True)
    return subprocess.run([str(a) for a in argv], **kwargs)


def normalized(name):
    return re.sub(r'[-_.]+', '-', name).lower()


# -- the platform-independent kit ------------------------------------------------------------------------------------

def check_imports():
    """Every third-party library the shipped Terminal imports must be one the app installs."""
    import ast
    if PUBLIC:
        terminal = sorted(p for name in RUNTIME for p in (ROOT / name).rglob('*.py') if '__pycache__' not in p.parts)
        first_party = set(RUNTIME)
    else:
        import build_user_terminal_package as builder
        terminal, _ = builder.tester_closure(local_yfinance=False)
        first_party = set(builder.PACKAGES)
    wanted = {}
    for path in terminal:
        for node in ast.walk(ast.parse(path.read_text(), str(path))):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                [node.module] if isinstance(node, ast.ImportFrom) and not node.level and node.module else []
            for name in names:
                top = name.split('.')[0]
                if top not in first_party and top not in sys.stdlib_module_names and top not in ('scripts', '__future__'):
                    wanted.setdefault(top, str(path.relative_to(ROOT)))
    missing = {top: where for top, where in wanted.items() if normalized(IMPORT_NAMES.get(top, top)) not in DESKTOP_PACKAGES}
    if missing:
        raise SystemExit('The Terminal imports libraries the app would not install:\n  ' +
                         '\n  '.join(f'{name} (imported by {where})' for name, where in sorted(missing.items())))
    return sorted(wanted)


def lock_blocks(path):
    """Each requirement of a hashed lock file, with its hash lines, by normalized name."""
    blocks, current = {}, None
    for line in path.read_text().splitlines():
        match = re.match(r'^([A-Za-z0-9_.-]+)==\S+', line)
        if match:
            current = normalized(match.group(1))
            blocks[current] = [line]
        elif current and line.startswith((' ', '\t')):
            blocks[current].append(line)
        else:
            current = None
    return blocks


def desktop_lock(target, overrides=None, names=DESKTOP_PACKAGES):
    """LOCK with only `names` (default DESKTOP_PACKAGES), hashes kept; fails when one is missing.

    `overrides` is a lock file whose pins replace LOCK's (the Mac app's, for libraries that no longer publish Intel
    wheels)."""
    blocks = lock_blocks(LOCK)
    if overrides:
        blocks.update(lock_blocks(overrides))
    absent = [name for name in names if name not in blocks]
    if absent:
        raise SystemExit(f'{LOCK.name} has no pin for: {", ".join(absent)}')
    text = ['# Retis Terminal: the libraries the Terminal imports, with their hashes (build_desktop_app.py).']
    for name in names:
        block = blocks[name]
        block[-1] = block[-1].rstrip(' \\')  # the last hash line of each block ends the requirement
        text += block
    target.write_text('\n'.join(text) + '\n')
    return target


def build_payload(kit, *, tag, version, site, feed, tools=None):
    """The public package plus its prebuilt UI and agent views and the desktop controller, sealed by one manifest."""
    import build_user_terminal_package as builder
    check_imports()
    if kit.exists():
        shutil.rmtree(kit)
    builder.build(kit, profile='public', tag=tag, site=site, public_version=version, update_feed=feed)
    shutil.copyfile(DESKTOP / 'retis_desktop.py', kit / 'retis_desktop.py')
    shutil.copyfile(DESKTOP / 'report_tools.py', kit / 'report_tools.py')
    desktop_lock(kit / 'requirements.lock')
    prebuild(kit, tools=tools)
    details = json.loads((kit / 'package-manifest.json').read_text())
    details.pop('files', None)
    details['prebuilt'] = True
    builder.seal(kit, details)
    return details


def public_payload(kit, *, version, site, feed, tools=None, cache=None):
    """The public repository's own source as the app's kit: runtime/, the controller, the lock, the prebuilt UI and
    agent views, sealed by one manifest, as a release's kit is (without the record of where a release came from)."""
    if not PUBLIC_VERSION.match(version):
        raise SystemExit(f'A version looks like 0.1.0-beta, not {version!r}.')
    check_imports()
    if kit.exists():
        shutil.rmtree(kit)
    ignore = shutil.ignore_patterns('__pycache__', '*.pyc')
    for name in RUNTIME:
        shutil.copytree(ROOT / name, kit / 'runtime' / name, ignore=ignore)
    shutil.copyfile(DESKTOP / 'retis_desktop.py', kit / 'retis_desktop.py')
    shutil.copyfile(DESKTOP / 'report_tools.py', kit / 'report_tools.py')
    desktop_lock(kit / 'requirements.lock')
    prebuild(kit, tools=tools or agent_view_tools(cache))
    details = {'package': 'retis-terminal', 'product': PRODUCT, 'version': version, 'profile': 'container',
               'site': site, 'updateFeed': feed, 'repository': PUBLIC_REPOSITORY, 'prebuilt': True}
    seal(kit, details)
    return details


def seal(kit, details):
    """package-manifest.json: the details and every file's SHA-256 (build_user_terminal_package.seal's rule)."""
    files = {p.relative_to(kit).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted(kit.rglob('*')) if p.is_file() and p.name not in ('package-manifest.json', PROVENANCE)
             and not {'.venv', '.runtime', '__pycache__', '.git'} & set(p.relative_to(kit).parts)}
    (kit / 'package-manifest.json').write_text(json.dumps({**details, 'files': files}, indent=2) + '\n')
    return files


def agent_view_tools(cache):
    """esbuild and QuickJS for prebuilding agent views, installed once into the cache from their lock (npm ci)."""
    tools = Path(cache or Path.home() / '.cache' / 'retis-terminal-build') / 'agent-view-tools'
    lock = (AGENT_VIEW_TOOLS / 'package-lock.json').read_bytes()
    if not ((tools / 'node_modules' / '.bin' / 'esbuild').exists() and (tools / 'package-lock.json').is_file()
            and (tools / 'package-lock.json').read_bytes() == lock):
        npm = shutil.which('npm')
        if not npm:
            raise SystemExit('Building agent views needs Node.js with npm (https://nodejs.org); install it and run again.')
        shutil.rmtree(tools, ignore_errors=True)
        tools.mkdir(parents=True)
        for name in ('package.json', 'package-lock.json'):
            shutil.copyfile(AGENT_VIEW_TOOLS / name, tools / name)
        run(npm, 'ci', '--no-audit', '--no-fund', '--loglevel=error', cwd=tools)
    return tools


def prebuild(kit, *, tools=None):
    """Capture the Terminal's UI and build the agent-view runtime and host release now, so users need no Node.

    The Terminal's launcher finds kit/prebuilt and serves these instead of building them at every start; the
    agent-view configuration names its folders by absolute path, so prebuilt.json records where they were built
    and the launcher writes a copy with this installation's path into the Terminal's state folder.
    """
    sys.path.insert(0, str(ROOT))
    from attas.data_terminal_phemar.instance_assets import capture_ui
    from attas.data_terminal_phemar.launch_settings import prepare_agent_views
    prebuilt = kit / 'prebuilt'
    # From the package's own web files, so a build from the public repository captures the same UI.
    capture_ui(prebuilt / 'terminal-ui', source=kit / 'runtime' / 'attas' / 'data_terminal_phemar' / 'web')
    views = prebuilt / 'agent-view'
    prepare_agent_views(views, tools=tools)
    for leftover in [*views.glob('*.lock'), *views.glob('*.before-*')]:
        leftover.unlink()
    (prebuilt / 'prebuilt.json').write_text(json.dumps({'ui': 'terminal-ui', 'agentView': 'agent-view',
                                                         'buildRoot': str(views)}, indent=2) + '\n')
    return prebuilt


# -- Python and libraries --------------------------------------------------------------------------------------------

def download(url, sha256, cache):
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / url.rsplit('/', 1)[1].replace('%2B', '+')
    if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != sha256:
        try:
            import certifi  # python.org's macOS Python has no certificates until Install Certificates runs
            context = ssl.create_default_context(cafile=certifi.where())
        except ImportError:
            context = ssl.create_default_context()
        with urllib.request.urlopen(url, timeout=300, context=context) as response, open(target.with_suffix('.part'), 'wb') as out:
            shutil.copyfileobj(response, out)
        target.with_suffix('.part').replace(target)
    if hashlib.sha256(target.read_bytes()).hexdigest() != sha256:
        target.unlink()
        raise SystemExit(f'Checksum failed for {url}')
    return target


def site_packages(python, key):
    return python / 'Lib' / 'site-packages' if key.startswith('windows') else python / 'lib' / 'python3.13' / 'site-packages'


def python_tree(key, target, lock, cache, pure=None):
    """An unpacked python-build-standalone for one platform, with the app's libraries and without unused stdlib.

    `pure` is a lock of MAC_PURE_PYTHON libraries: installed from their arm64 wheels, compiled modules removed."""
    spec = json.loads(PYTHON_SPEC.read_text())['archives'][key]
    archive = download(spec['url'], spec['sha256'], cache)
    if target.exists():
        shutil.rmtree(target)
    with tempfile.TemporaryDirectory(dir=target.parent) as staging:
        with tarfile.open(archive) as bundle:
            bundle.extractall(staging, filter='data')
        shutil.move(str(Path(staging) / 'python'), target)
    stdlib = target / 'Lib' if key.startswith('windows') else target / 'lib' / 'python3.13'
    for name in STDLIB_UNUSED:
        path = stdlib / name
        shutil.rmtree(path) if path.is_dir() else path.unlink(missing_ok=True)
    for pattern in ('lib-dynload/_tkinter*', 'tcl*', 'tk*'):
        for path in [*stdlib.glob(pattern), *(target / 'lib').glob(pattern), *(target / 'tcl').glob('*')]:
            shutil.rmtree(path) if path.is_dir() else path.unlink(missing_ok=True)
    shutil.rmtree(target / 'tcl', ignore_errors=True)
    # Headers, import libraries, build configuration and the tools only developers run.
    for path in [target / 'include', target / 'libs', target / 'Scripts', target / 'share', target / 'lib' / 'pkgconfig',
                 *stdlib.glob('config-3.13-*'), *(target / 'bin').glob('pip*'), *(target / 'bin').glob('idle*'),
                 *(target / 'bin').glob('pydoc*'), *(target / 'bin').glob('*-config')]:
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)
    for name in ('_tkinter.pyd', 'tcl86t.dll', 'tk86t.dll'):
        (target / 'DLLs' / name).unlink(missing_ok=True)
    # Wheels for this platform, by the lock's hashes, whatever platform builds the app. --no-compile: the building
    # Python's bytecode is for its own version; compile_bytecode writes the app's.
    platforms = [arg for p in WHEEL_PLATFORMS[key] for arg in ('--platform', p)]
    run(sys.executable, '-m', 'pip', 'install', '--quiet', '--disable-pip-version-check', '--isolated', '--no-deps', '--no-compile',
        '--require-hashes', '--only-binary=:all:', '--python-version', '3.13', '--implementation', 'cp', '--abi', 'cp313',
        *platforms, '--index-url', 'https://pypi.org/simple', '--target', site_packages(target, key), '-r', lock)
    packages = site_packages(target, key)
    if pure:
        run(sys.executable, '-m', 'pip', 'install', '--quiet', '--disable-pip-version-check', '--isolated', '--no-deps', '--no-compile',
            '--require-hashes', '--only-binary=:all:', '--python-version', '3.13', '--implementation', 'cp', '--abi', 'cp313',
            '--platform', WHEEL_PLATFORMS['mac-arm64'][0], '--index-url', 'https://pypi.org/simple', '--target', packages, '-r', pure)
        for name in MAC_PURE_PYTHON:
            for module in (packages / name).rglob('*.so'):
                if module.with_name(module.name.split('.')[0] + '.py').is_file():
                    module.unlink()
                else:
                    raise SystemExit(f'{module.relative_to(packages)} has no Python source to fall back on.')
    # Console scripts written for the building machine's Python, and pip, which the app never runs.
    for leftover in [*packages.glob('bin'), *packages.glob('pip'), *packages.glob('pip-*.dist-info'), packages / 'README.txt']:
        shutil.rmtree(leftover) if leftover.is_dir() else leftover.unlink(missing_ok=True)
    return target


def notices(python, key, target):
    """THIRD_PARTY_NOTICES: each bundled library's name, version, license and license text; then Python's."""
    sections = [f'{PRODUCT} includes the following third-party software, unmodified, under their own licenses.\n']
    for info in sorted(site_packages(python, key).glob('*.dist-info')):
        meta = (info / 'METADATA').read_text(errors='replace')
        field = lambda name: next((l.split(':', 1)[1].strip() for l in meta.splitlines() if l.startswith(name + ':')), '')
        license_name = (field('License-Expression') or field('License'))[:120]
        if not license_name:
            license_name = ', '.join(l.split('::')[-1].strip() for l in meta.splitlines() if l.startswith('Classifier: License ::'))
        texts = sorted(p for p in info.rglob('*') if p.is_file() and re.match(r'(?i)(licen[cs]e|copying|notice|authors)', p.name))
        sections.append('=' * 78 + f'\n{field("Name")} {field("Version")} — {license_name or "see license text"}\n' +
                        (field('Home-page') or '') + '\n')
        for text in texts:
            sections.append(f'--- {text.relative_to(info)} ---\n' + text.read_text(errors='replace').strip() + '\n')
    license_file = next((p for p in (python / 'lib' / 'python3.13' / 'LICENSE.txt', python / 'Lib' / 'LICENSE.txt',
                                     python / 'LICENSE.txt') if p.is_file()), None)
    sections.append('=' * 78 + '\nPython 3.13 (python-build-standalone) — PSF-2.0\n' +
                    (license_file.read_text(errors='replace') if license_file else 'https://docs.python.org/3/license.html') + '\n')
    target.write_text('\n'.join(sections), encoding='utf-8')
    return target


def compile_bytecode(python_for_compiling, *folders):
    """Compile once at build time: the app's files may be read-only, and the Terminal never writes bytecode."""
    for folder in folders:
        run(python_for_compiling, '-m', 'compileall', '-q', '-j', '0', '--invalidation-mode', 'unchecked-hash',
            '-s', folder, folder, stdout=subprocess.DEVNULL)


# -- macOS ------------------------------------------------------------------------------------------------------------

def is_macho(path):
    try:
        with open(path, 'rb') as handle:
            head = handle.read(8)
    except OSError:
        return False
    # 0xcafebabe is also a Java class file; a fat Mach-O has a small architecture count where Java has its version.
    return head[:4] in MACHO and not (head[:4] == b'\xca\xfe\xba\xbe' and int.from_bytes(head[4:8], 'big') > 30)


def architectures(path):
    return set(run('lipo', '-archs', path, capture_output=True, text=True).stdout.split())


def fuse(arm, intel, target):
    """One universal tree from the arm64 and x86_64 trees: Mach-O files merged with lipo, everything else from arm64."""
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(arm, target, symlinks=True)
    merged = 0
    for path in sorted(target.rglob('*')):
        if path.is_symlink() or not path.is_file() or not is_macho(path):
            continue
        other = intel / path.relative_to(target)
        if 'x86_64' in architectures(path):
            continue
        if not (other.is_file() and is_macho(other)):
            # An accelerator built only for arm64 (zeroconf's Cython modules) beside its pure-Python source: both
            # architectures use the Python source, since Python would try, and fail, to load an arm64-only module.
            source = path.with_name(path.name.split('.')[0] + '.py')
            if path.suffix == '.so' and source.is_file():
                path.unlink()
                continue
            raise SystemExit(f'No x86_64 counterpart for {path.relative_to(target)}')
        run('lipo', '-create', path, other, '-output', path.with_name(path.name + '.universal'))
        path.with_name(path.name + '.universal').replace(path)
        merged += 1
    for path in sorted(intel.rglob('*')):
        destination = target / path.relative_to(intel)
        if not (destination.exists() or destination.is_symlink()):
            destination.parent.mkdir(parents=True, exist_ok=True)
            if path.is_symlink():
                destination.symlink_to(os.readlink(path))
            elif path.is_file():
                shutil.copy2(path, destination)
    return merged


def mac_icon(resources, work):
    """AppIcon.icns from the Retis Terminal icon, inset on a rounded square as macOS icons are."""
    from PIL import Image, ImageDraw
    source = Image.open(ICON).convert('RGBA').resize((824, 824), Image.LANCZOS)
    mask = Image.new('L', (824, 824), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, 823, 823), radius=185, fill=255)
    canvas = Image.new('RGBA', (1024, 1024), (0, 0, 0, 0))
    canvas.paste(source, (100, 100), mask)
    iconset = work / 'AppIcon.iconset'
    iconset.mkdir(parents=True, exist_ok=True)
    for size in (16, 32, 128, 256, 512):
        canvas.resize((size, size), Image.LANCZOS).save(iconset / f'icon_{size}x{size}.png')
        canvas.resize((size * 2, size * 2), Image.LANCZOS).save(iconset / f'icon_{size}x{size}@2x.png')
    run('iconutil', '-c', 'icns', iconset, '-o', resources / 'AppIcon.icns')


def info_plist(version):
    numeric = version.split('-')[0]
    entries = {'CFBundleName': PRODUCT, 'CFBundleDisplayName': PRODUCT, 'CFBundleIdentifier': BUNDLE_ID,
               'CFBundleExecutable': 'RetisTerminal', 'CFBundlePackageType': 'APPL', 'CFBundleIconFile': 'AppIcon',
               'CFBundleShortVersionString': numeric, 'CFBundleVersion': numeric, 'RetisTerminalVersion': version,
               'LSMinimumSystemVersion': MAC_MINIMUM, 'LSUIElement': True, 'NSHighResolutionCapable': True,
               'LSApplicationCategoryType': 'public.app-category.finance',
               'CFBundleDevelopmentRegion': 'zh_TW', 'CFBundleLocalizations': ['zh_TW', 'zh-Hans', 'en'],
               'NSHumanReadableCopyright': 'Copyright © 2026 Retis. MIT License.'}
    import plistlib
    return plistlib.dumps(entries)


def sign_mac(app, identity, entitlements):
    """Sign every Mach-O inside out, then the bundle. Ad hoc ('-') without an identity: it runs, but isn't notarized."""
    options = ['--force', '--sign', identity or '-']
    if identity:
        options += ['--options', 'runtime', '--timestamp']
    nested = sorted((p for p in app.rglob('*') if p.is_file() and not p.is_symlink() and is_macho(p)
                     and p != app / 'Contents' / 'MacOS' / 'RetisTerminal'), key=lambda p: -len(p.parts))
    for path in nested:
        extra = ['--entitlements', entitlements] if identity and path.name.startswith('python3') else []
        run('codesign', *options, *extra, path, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    run('codesign', *options, *(['--entitlements', entitlements] if identity else []), app)
    run('codesign', '--verify', '--deep', '--strict', app)


def build_mac(kit, out, *, version, cache, identity=None, notary_profile=None):
    if platform.system() != 'Darwin':
        raise SystemExit('The macOS app is built on a Mac (lipo, codesign, iconutil, hdiutil).')
    work = out / 'mac-work'
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    lock = desktop_lock(work / 'requirements-mac.lock', overrides=MAC_OVERRIDES,
                        names=[n for n in DESKTOP_PACKAGES if n not in MAC_PURE_PYTHON])
    pure = desktop_lock(work / 'requirements-mac-pure.lock', names=MAC_PURE_PYTHON)
    trees = {key: python_tree(key, work / key, lock, cache, pure=pure) for key in ('mac-arm64', 'mac-x86_64')}
    app = out / f'{PRODUCT}.app'
    shutil.rmtree(app, ignore_errors=True)
    contents = app / 'Contents'
    resources = contents / 'Resources'
    (contents / 'MacOS').mkdir(parents=True)
    resources.mkdir()
    fuse(trees['mac-arm64'], trees['mac-x86_64'], resources / 'python')
    shutil.copytree(kit, resources / 'kit', ignore=shutil.ignore_patterns(PROVENANCE))
    native = 'mac-arm64' if platform.machine() == 'arm64' else 'mac-x86_64'
    host_python = trees[native] / 'bin' / 'python3'
    compile_bytecode(host_python, resources / 'kit', resources / 'python' / 'lib' / 'python3.13')
    smoke_imports(resources / 'python' / 'bin' / 'python3', resources / 'kit')
    notices(resources / 'python', 'mac', resources / 'THIRD_PARTY_NOTICES.txt')
    shutil.copyfile(LICENSE, resources / 'LICENSE.txt')
    # The menu-bar shell, for both architectures.
    slices = []
    for arch in ('arm64', 'x86_64'):
        binary = work / f'RetisTerminal-{arch}'
        run('xcrun', 'swiftc', '-O', '-target', f'{arch}-apple-macos{MAC_MINIMUM}', DESKTOP / 'mac' / 'RetisTerminal.swift',
            '-o', binary)
        slices.append(binary)
    run('lipo', '-create', *slices, '-output', contents / 'MacOS' / 'RetisTerminal')
    (contents / 'Info.plist').write_bytes(info_plist(version))
    (contents / 'PkgInfo').write_text('APPL????')
    mac_icon(resources, work)
    sign_mac(app, identity, DESKTOP / 'mac' / 'entitlements.plist')
    dmg = out / f'Retis-Terminal-{version}.dmg'
    root = work / 'dmg'
    root.mkdir()
    shutil.move(str(app), root / app.name)
    (root / 'Applications').symlink_to('/Applications')
    dmg.unlink(missing_ok=True)
    run('hdiutil', 'create', '-quiet', '-volname', f'{PRODUCT} {version}', '-srcfolder', root, '-fs', 'HFS+',
        '-format', 'UDZO', '-imagekey', 'zlib-level=9', '-ov', dmg)
    shutil.move(str(root / app.name), app)
    if identity:
        run('codesign', '--force', '--sign', identity, '--timestamp', dmg)
    if notary_profile:
        run('xcrun', 'notarytool', 'submit', dmg, '--keychain-profile', notary_profile, '--wait')
        run('xcrun', 'stapler', 'staple', dmg)
    shutil.rmtree(work)
    return dmg


def smoke_imports(python, kit):
    """Import the Terminal and the controller with the bundled Python alone (this machine's architecture)."""
    probe = ('import sys; sys.path[:0] = [sys.argv[1] + "/runtime", sys.argv[1]]\n'
             'from attas.data_terminal_phemar.runtime import create_app\n'
             'import retis_desktop, report_tools, certifi, cryptography, uvicorn, zeroconf, psutil, PIL\n'
             'print("imports ok")\n')
    env = {k: v for k, v in os.environ.items() if not k.startswith('PYTHON')}
    env.update(PYTHONDONTWRITEBYTECODE='1', PYTHON_DOTENV_DISABLED='1', PROMPITS_ENV_FILE='none')
    result = run(python, '-I', '-c', probe, kit, capture_output=True, text=True, env=env, check=False)
    if result.returncode:
        raise SystemExit('The bundled Python cannot import the Terminal:\n' + result.stderr[-3000:])


# -- Windows ----------------------------------------------------------------------------------------------------------

def windows_icon(target):
    from PIL import Image
    Image.open(ICON).convert('RGBA').save(target, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])


# The installer's translations besides English (Inno Setup's own Default.isl, which has to match the compiler):
# Inno Setup 6.7's files, pinned by SHA-256 as they are at its 6.7.4 release commit (the same files since 6.7.1).
# Inno Setup installs Japanese, Spanish and French; a copy with the pinned checksum is used as is. The two Chinese
# files are unofficial in Inno Setup 6 (official only from 7) and not installed, so they, and any copy that differs,
# come from that commit. Without all five the build stops: RetisTerminal.iss reads them from beside itself.
ISSRC_LANGUAGES = 'https://raw.githubusercontent.com/jrsoftware/issrc/c2913c993858a781fcb455bac8b8949c1c366cb8/Files/Languages/'
INSTALLER_MESSAGES = {
    'ChineseTraditional.isl': ('Unofficial/ChineseTraditional.isl', 'ab22b0ebf82969d1c9a7208e0e9f60e5119c0c95b9dacc00eb8e5582c7d3c604'),
    'ChineseSimplified.isl': ('Unofficial/ChineseSimplified.isl', '7d544b9bb1d142cfa11f2e5d3cc8abe2e55f8e066c5124e3772675aa236e1278'),
    'Japanese.isl': ('Japanese.isl', 'd5450537bb128112347bf86a4bdc3a4be0605414df0bc4fc90f55aaef1ba369b'),
    'Spanish.isl': ('Spanish.isl', 'c61ee6287d6a10db186cce43b556422e0a8bc631784132468a9cebca4f2df0d5'),
    'French.isl': ('French.isl', '145bf4ea34eaa79c52e4fcb77ce3eee9720b0d424c69458cb99592bc52121fba'),
}


def inno_setup_folders():
    """Where Inno Setup 6 may be installed: beside PATH's iscc (Chocolatey's shim has no Languages), then its defaults."""
    found = shutil.which('iscc')
    folders = [Path(found).parent] if found else []
    for base in (os.environ.get('ProgramFiles(x86)'), os.environ.get('ProgramFiles'), os.environ.get('LOCALAPPDATA')):
        if base:
            folders += [Path(base) / 'Inno Setup 6', Path(base) / 'Programs' / 'Inno Setup 6']
    return folders


def installer_messages(target, cache):
    """Put the installer's five translations in `target`; returns where each came from."""
    sources = {}
    for name, (path, sha256) in INSTALLER_MESSAGES.items():
        bundled = next((f / 'Languages' / name for f in inno_setup_folders() if (f / 'Languages' / name).is_file()
                        and hashlib.sha256((f / 'Languages' / name).read_bytes()).hexdigest() == sha256), None)
        if bundled:
            shutil.copyfile(bundled, target / name)
            sources[name] = f'Inno Setup ({bundled})'
            continue
        try:
            shutil.copyfile(download(ISSRC_LANGUAGES + path, sha256, cache), target / name)
        except OSError as error:  # urllib's errors, offline included
            raise SystemExit(f"The installer's {name} could not be downloaded ({error}); the build stops rather "
                             'than make an installer without that language.') from error
        sources[name] = 'pinned download'
    return sources


def find_csc():
    root = Path(os.environ.get('WINDIR', r'C:\Windows')) / 'Microsoft.NET'
    return next((p for p in (root / 'Framework64' / 'v4.0.30319' / 'csc.exe', root / 'Framework' / 'v4.0.30319' / 'csc.exe') if p.is_file()), None)


def find_iscc():
    found = shutil.which('iscc')
    if found:
        return Path(found)
    for base in (os.environ.get('ProgramFiles(x86)'), os.environ.get('ProgramFiles'), os.environ.get('LOCALAPPDATA')):
        for candidate in (Path(base or '') / 'Inno Setup 6' / 'ISCC.exe', Path(base or '') / 'Programs' / 'Inno Setup 6' / 'ISCC.exe'):
            if base and candidate.is_file():
                return candidate
    return None


def sign_windows(command, *paths):
    """Sign with the given command template ({file} is each path); unsigned builds pass none."""
    if not command:
        return
    for path in paths:
        run(*[part.replace('{file}', str(path)) for part in command.split()])


def build_windows(kit, out, *, version, cache, sign_command=None):
    stage = out / 'windows' / PRODUCT
    shutil.rmtree(stage.parent, ignore_errors=True)
    stage.mkdir(parents=True)
    python = python_tree('windows-x86_64', stage / 'python', kit / 'requirements.lock', cache)
    shutil.copytree(kit, stage / 'kit', ignore=shutil.ignore_patterns(PROVENANCE))
    # Bytecode for 3.13 is the same on every platform: the bundled Python compiles it on Windows, a 3.13 elsewhere.
    compiler = (python / 'python.exe' if os.name == 'nt' else None) or shutil.which('python3.13') or \
        (sys.executable if sys.version_info[:2] == (3, 13) else None)
    if compiler:
        compile_bytecode(compiler, stage / 'kit', python / 'Lib')
    notices(python, 'windows', stage / 'THIRD_PARTY_NOTICES.txt')
    shutil.copyfile(LICENSE, stage / 'LICENSE.txt')
    windows_icon(stage / 'RetisTerminal.ico')
    source = out / 'windows' / 'shell'
    source.mkdir()
    for name in ('RetisTerminal.cs', 'RetisTerminal.manifest', 'RetisTerminal.iss'):
        shutil.copyfile(DESKTOP / 'windows' / name, source / name)
    result = {'staging': str(stage), 'installer': None, 'installerMessages': installer_messages(source, cache)}
    csc, iscc = find_csc(), find_iscc()
    if not (csc and iscc):
        result['next'] = ('Compile on Windows: csc for the tray app and iscc for the installer '
                          '(.github/workflows/retis-terminal-desktop.yml does both).')
        return result
    run(csc, '/nologo', '/optimize+', '/target:winexe', f'/win32icon:{stage / "RetisTerminal.ico"}',
        f'/win32manifest:{source / "RetisTerminal.manifest"}', '/r:System.Windows.Forms.dll', '/r:System.Drawing.dll',
        '/r:System.Security.dll', '/r:System.Web.Extensions.dll', f'/out:{stage / "RetisTerminal.exe"}', source / 'RetisTerminal.cs')
    sign_windows(sign_command, stage / 'RetisTerminal.exe', *sorted((stage / 'python').glob('*.exe')))
    defines = [f'/DAppVersion={version}', f'/DSourceDir={stage}', f'/DOutputDir={out}']
    signing = [f'/Sretis={sign_command.replace("{file}", "$f")}', '/DSignTool=retis'] if sign_command else []
    run(iscc, '/Q', *defines, *signing, source / 'RetisTerminal.iss')
    result['installer'] = str(out / f'Retis-Terminal-Setup-{version}.exe')
    return result


# -- checksums and the update feed ------------------------------------------------------------------------------------

def checksum(path):
    digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    Path(str(path) + '.sha256').write_text(f'{digest}  {Path(path).name}\n')
    return digest


def compare(manifest, other):
    """Print each file the two manifests disagree on (present in one only, or with another SHA-256); 1 if any."""
    files = [json.loads(Path(path).read_text(encoding='utf-8'))['files'] for path in (manifest, other)]
    differ = sorted(name for name in files[0].keys() | files[1].keys() if files[0].get(name) != files[1].get(name))
    for name in differ:
        state = 'only in the first' if name not in files[1] else 'only in the second' if name not in files[0] else 'differs'
        print(f'{state}: {name}')
    print(f'{len(files[0])} and {len(files[1])} files; ' + (f'{len(differ)} differ.' if differ else 'all the same.'))
    return 1 if differ else 0


def main():
    # The public repository names its version, site and update feed in VERSION.json.
    published = json.loads((ROOT / 'VERSION.json').read_text()) if PUBLIC and (ROOT / 'VERSION.json').is_file() else {}
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--platform', choices=('mac', 'windows'))
    parser.add_argument('--version', default=published.get('version'),
                        help='The Retis Terminal version, such as 0.1.0-beta' + (' (default: VERSION.json).' if PUBLIC else '.'))
    if not PUBLIC:
        parser.add_argument('--tag', help='The release tag of the source; HEAD must be exactly this tag.')
        parser.add_argument('--untagged', action='store_true', help='A local dry build of HEAD, marked untagged.')
    parser.add_argument('--output', type=Path, default=ROOT / 'dist' / ('' if PUBLIC else 'retis-terminal'))
    parser.add_argument('--site', default=published.get('site') or 'https://tw-001-olive-canyon.retis.ai')
    parser.add_argument('--update-feed', default=published.get('updateFeed'),
                        help='The latest.json address the app checks (default: the public repository).')
    parser.add_argument('--payload', type=Path, help='Use this already built kit (from --payload-only) instead of building one.')
    parser.add_argument('--payload-only', action='store_true', help='Build only the platform-independent kit into --output/kit.')
    parser.add_argument('--agent-view-tools', type=Path, help="esbuild and QuickJS for prebuilding agent views (default: the Terminal's own)")
    parser.add_argument('--cache', type=Path, default=Path.home() / '.cache' / 'retis-terminal-build')
    parser.add_argument('--sign-identity', help='macOS: a "Developer ID Application: ..." identity; without it, ad hoc.')
    parser.add_argument('--notary-profile', help='macOS: an xcrun notarytool keychain profile; notarizes and staples the .dmg.')
    parser.add_argument('--windows-sign-command', help="Windows: a signing command with {file}, e.g. 'signtool sign /fd SHA256 ... {file}'.")
    parser.add_argument('--compare', nargs=2, type=Path, metavar=('MANIFEST', 'OTHER'),
                        help="List the files two apps' kit/package-manifest.json disagree on, and stop.")
    args = parser.parse_args()
    if args.compare:
        sys.exit(compare(*args.compare))
    if not args.version:
        parser.error('Name the --version, such as 0.1.0-beta.')
    if not PUBLIC and not args.payload and not args.tag and not args.untagged:
        parser.error('Name the release --tag, or --untagged for a local dry build.')
    if not (args.platform or args.payload_only):
        parser.error('Choose --platform mac or windows, or --payload-only.')
    out = args.output.expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    if args.payload:
        kit = args.payload.expanduser().resolve()
        details = json.loads((kit / 'package-manifest.json').read_text())
        if details.get('version') != args.version or not details.get('prebuilt'):
            raise SystemExit(f'{kit} is version {details.get("version")} (prebuilt: {details.get("prebuilt")}), not {args.version}.')
    elif PUBLIC:
        kit = out / 'kit'
        details = public_payload(kit, version=args.version, site=args.site, tools=args.agent_view_tools, cache=args.cache,
                                 feed=args.update_feed or f'https://github.com/{PUBLIC_REPOSITORY}/releases/latest/download/latest.json')
    else:
        import build_user_terminal_package as builder
        kit = out / 'kit'
        details = build_payload(kit, tag=None if args.untagged else args.tag, version=args.version, site=args.site,
                                feed=args.update_feed or builder.PUBLIC_UPDATE_FEED, tools=args.agent_view_tools)
    provenance = json.loads((kit / PROVENANCE).read_text()) if (kit / PROVENANCE).is_file() else {}
    report = {'version': args.version, 'sourceTag': provenance.get('sourceTag'), 'sourceCommit': provenance.get('sourceCommit'),
              'kit': str(kit)}
    if args.payload_only:
        print(json.dumps(report, indent=2))
        return
    if args.platform == 'mac':
        dmg = build_mac(kit, out, version=args.version, cache=args.cache, identity=args.sign_identity,
                        notary_profile=args.notary_profile)
        report.update(dmg=str(dmg), sha256=checksum(dmg), megabytes=round(dmg.stat().st_size / 1e6, 1),
                      signed=bool(args.sign_identity), notarized=bool(args.notary_profile))
    else:
        result = build_windows(kit, out, version=args.version, cache=args.cache, sign_command=args.windows_sign_command)
        report.update(result)
        if result.get('installer'):
            report['sha256'] = checksum(result['installer'])
        report['signed'] = bool(args.windows_sign_command)
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
