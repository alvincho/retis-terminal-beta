"""Download, verify and unpack a component beside its older versions; switch, roll back and remove.

Installing never touches the running version: the package goes into `versions/<version>/` and only
`activate` switches the component's current version, keeping the previous one for `rollback`.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import stat
import subprocess
import tarfile
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from typing import IO, Callable, Sequence

from prompits.components.parts_list import Artifact, Component
from prompits.components.state import Layout, State

CHUNK = 256 * 1024

Opener = Callable[[str], IO[bytes]]
Progress = Callable[[int, int], None]
Libraries = Callable[..., None]  # (folder) or (folder, previous=<installed version folder>)
LOCK = "requirements.lock"
INDEX = "https://pypi.org/simple"
# Windows: the manager may run without a console; its pip and interpreter checks must not open a console window.
NO_WINDOW = {"creationflags": 0x08000000} if os.name == "nt" else {}


class InstallError(RuntimeError):
    """A download, checksum, unpack or switch failed; nothing was changed for the running version."""


class _HttpsOnlyRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not str(newurl).startswith("https://"):
            raise InstallError(f"refusing redirect to non-https {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def https_opener(url: str) -> IO[bytes]:
    """Open an https URL with no proxy and https-only redirects."""
    if not url.startswith("https://"):
        raise InstallError(f"refusing non-https download {url}")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _HttpsOnlyRedirects())
    return opener.open(urllib.request.Request(url, headers={"User-Agent": "prompits-component-manager"}), timeout=60)


def folder_opener(folder: Path) -> Opener:
    """For tests and offline installs: read each package by its file name from `folder` instead of over HTTPS.

    The signed parts list and every checksum are checked exactly as for a download."""
    folder = Path(folder).resolve()

    def open_package(url: str) -> IO[bytes]:
        name = url.rsplit('/', 1)[-1]
        if not name or name in ('.', '..') or '\\' in name or not (folder / name).is_file():
            raise InstallError(f'{name or url} is not in {folder}')
        return open(folder / name, 'rb')
    return open_package


def download(artifact: Artifact, destination: Path, *, opener: Opener = https_opener,
             progress: Progress | None = None) -> Path:
    """Fetch `artifact` to `destination`, stopping past its declared size; raise unless size and SHA-256 match."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".partial")
    digest = hashlib.sha256()
    received = 0
    try:
        with opener(artifact.url) as source, open(partial, "wb") as target:
            while True:
                chunk = source.read(CHUNK)
                if not chunk:
                    break
                received += len(chunk)
                if received > artifact.size:
                    raise InstallError(f"download is larger than the declared {artifact.size} bytes")
                digest.update(chunk)
                target.write(chunk)
                if progress:
                    progress(received, artifact.size)
        if received != artifact.size:
            raise InstallError(f"download has {received} bytes, expected {artifact.size}")
        if digest.hexdigest() != artifact.sha256:
            raise InstallError("download checksum does not match the parts list")
        os.replace(partial, destination)
        return destination
    except BaseException:
        partial.unlink(missing_ok=True)
        raise


def _safe_member(name: str) -> PurePosixPath:
    path = PurePosixPath(name.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts or (path.parts and ":" in path.parts[0]):
        raise InstallError(f"package entry escapes its folder: {name}")
    return path


def unpack(archive: Path, archive_format: str, target: Path):
    """Unpack only plain files and folders that stay inside `target`; links and devices are refused."""
    target.mkdir(parents=True, exist_ok=True)
    if archive_format == "zip":
        with zipfile.ZipFile(archive) as bundle:
            for info in bundle.infolist():
                relative = _safe_member(info.filename)
                mode = (info.external_attr >> 16) & 0xFFFF
                if stat.S_ISLNK(mode):
                    raise InstallError(f"package contains a link: {info.filename}")
                destination = target.joinpath(*relative.parts)
                if info.is_dir():
                    destination.mkdir(parents=True, exist_ok=True)
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(info) as source, open(destination, "wb") as output:
                    shutil.copyfileobj(source, output, CHUNK)
                if os.name != "nt" and mode & 0o111:
                    destination.chmod(0o755)
    elif archive_format == "tar.gz":
        with tarfile.open(archive, "r:gz") as bundle:
            for member in bundle.getmembers():
                relative = _safe_member(member.name)
                destination = target.joinpath(*relative.parts)
                if member.isdir():
                    destination.mkdir(parents=True, exist_ok=True)
                elif member.isfile():
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    source = bundle.extractfile(member)
                    assert source is not None
                    with source, open(destination, "wb") as output:
                        shutil.copyfileobj(source, output, CHUNK)
                    if os.name != "nt" and member.mode & 0o111:
                        destination.chmod(0o755)
                else:
                    raise InstallError(f"package contains a link or special file: {member.name}")
    else:
        raise InstallError(f"unsupported package format {archive_format}")


def python_tag(python: str) -> str:
    """The interpreter's bytecode tag and platform (e.g. `cpython-313 macosx-12.0-arm64`): compiled libraries match it."""
    try:
        result = subprocess.run([python, "-I", "-c", "import sys, sysconfig; print(sys.implementation.cache_tag, sysconfig.get_platform())"],
                                capture_output=True, text=True, timeout=60, **NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return ""
    return (getattr(result, "stdout", "") or "").strip() if getattr(result, "returncode", 1) == 0 else ""


def pip_libraries(python: str, *, index_url: str = INDEX, pip: Sequence[str] | None = None) -> Libraries:
    """Install a package's `requirements.lock` (every pin hashed, no other packages) into its own `lib/` folder.

    The libraries come from the package index as wheels only (nothing is built here, so no compiler and no unhashed
    build tools), checked against the lock's hashes; the lock itself came inside the package, whose checksum the
    signed parts list fixed, or inside the host app. Each component keeps its own `lib/`.

    `pip` is how to run pip (default `<python> -m pip`); a host app may keep pip outside its Python's import path.
    On an update, `previous` is the installed version's folder: when its lock is the same and its `lib/` was
    installed for the same interpreter, that `lib/` is copied instead of downloaded again.
    """
    command = list(pip) if pip else [python, "-m", "pip"]

    def install(folder: Path, previous: Path | None = None):
        lock = folder / LOCK
        if not lock.is_file():
            return
        if previous is not None and _same_libraries(lock, previous, python_tag(python)):
            shutil.copytree(previous / "lib", folder / "lib", symlinks=True)
            return
        result = subprocess.run([*command, "--isolated", "install", "--no-input", "--disable-pip-version-check",
                                 "--require-hashes", "--no-deps", "--only-binary=:all:", "--index-url", index_url,
                                 "--target", str(folder / "lib"), "-r", str(lock)], capture_output=True, text=True, **NO_WINDOW)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip().splitlines()[-1:] or ["pip failed"]
            raise InstallError("could not install the package's libraries: " + detail[0][:300])
        tag = python_tag(python)
        if tag and (folder / "lib").is_dir():
            (folder / "lib" / MARKER).write_text(tag + "\n" + hashlib.sha256(lock.read_bytes()).hexdigest() + "\n")
    return install


MARKER = ".installed-for"


def _same_libraries(lock: Path, previous: Path, tag: str) -> bool:
    if not tag:
        return False
    marker = previous / "lib" / MARKER
    if not marker.is_file() or not (previous / LOCK).is_file():
        return False
    if (previous / LOCK).read_bytes() != lock.read_bytes():
        return False
    return marker.read_text().splitlines()[:2] == [tag, hashlib.sha256(lock.read_bytes()).hexdigest()]


def copy_included(source: Path, target: Path):
    """Copy a component the host app ships: plain files and folders only, without bytecode or an old `lib/`."""
    if not source.is_dir():
        raise InstallError(f"{source} is not a folder")
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        if relative.parts[0] == "lib" or "__pycache__" in relative.parts or path.suffix == ".pyc":
            continue
        if path.is_symlink():
            raise InstallError(f"included component contains a link: {relative}")
        destination = target / relative
        if path.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
        elif path.is_file():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, destination)
    target.mkdir(parents=True, exist_ok=True)


class Installer:
    def __init__(self, state: State, layout: Layout, *, opener: Opener = https_opener, libraries: Libraries | None = None):
        self.state = state
        self.layout = layout
        self.opener = opener
        self.libraries = libraries

    def install(self, component: Component, platform: str, *, progress: Progress | None = None,
                folder: Path | None = None) -> Path:
        """Put `component.version` beside the installed versions. The current version is not changed.

        `folder` is a component the host app ships: its files are copied instead of downloaded (its libraries still
        install as for a download)."""
        artifact = None if folder is not None else component.artifact_for(platform)
        if folder is None and artifact is None:
            raise InstallError(f"{component.id} {component.version} has no package for {platform}")
        target = self.layout.version_dir(component.id, component.version)
        if target.exists():
            self._remember(component)
            return target
        archive = None
        if folder is None:
            suffix = ".zip" if artifact.format == "zip" else ".tar.gz"
            archive = self.layout.downloads / f"{component.id}-{component.version}{suffix}"
            download(artifact, archive, opener=self.opener, progress=progress)
        staging = target.with_name(target.name + ".unpacking")
        shutil.rmtree(staging, ignore_errors=True)
        try:
            if archive is not None:
                unpack(archive, artifact.format, staging)
            else:
                copy_included(Path(folder), staging)
            if self.libraries:
                previous = self._current_folder(component.id)
                if previous is not None:
                    self.libraries(staging, previous=previous)
                else:
                    self.libraries(staging)
            os.replace(staging, target)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        finally:
            if archive is not None:
                archive.unlink(missing_ok=True)
        self.layout.data_dir(component.id).mkdir(parents=True, exist_ok=True)
        self._remember(component)
        self.state.record_event("installed", component.id, version=component.version)
        return target

    def _current_folder(self, component_id: str) -> Path | None:
        """The installed current version's folder (not a bundled component's own folder), if any."""
        record = self.state.read()["components"].get(component_id) or {}
        current = record.get("current")
        if not current or record.get("location"):
            return None
        folder = self.layout.version_dir(component_id, current)
        return folder if folder.is_dir() else None

    def _remember(self, component: Component):
        with self.state.edit() as data:
            record = data["components"].setdefault(component.id, {"current": None, "previous": None, "desired": "stopped", "entries": {}})
            record.setdefault("entries", {})[component.version] = dict(component.raw)

    def installed_versions(self, component_id: str) -> list[str]:
        folder = self.layout.versions_dir(component_id)
        if not folder.exists():
            return []
        return sorted(item.name for item in folder.iterdir() if item.is_dir() and not item.name.endswith(".unpacking"))

    def activate(self, component_id: str, version: str):
        if version not in self.installed_versions(component_id):
            raise InstallError(f"{component_id} {version} is not installed")
        with self.state.edit() as data:
            record = data["components"].get(component_id)
            if record is None:
                raise InstallError(f"{component_id} is not installed")
            if record.get("current") != version:
                record["previous"] = record.get("current")
                record["current"] = version
        self.state.record_event("activated", component_id, version=version)

    def rollback(self, component_id: str) -> str:
        record = self.state.read()["components"].get(component_id) or {}
        previous = record.get("previous")
        if not previous or previous not in self.installed_versions(component_id):
            raise InstallError(f"{component_id} has no earlier version to go back to")
        self.activate(component_id, previous)
        self.state.record_event("rolled_back", component_id, version=previous)
        return previous

    def prune(self, component_id: str) -> list[str]:
        """Remove versions other than the current and previous ones."""
        record = self.state.read()["components"].get(component_id) or {}
        keep = {record.get("current"), record.get("previous")}
        removed = []
        for version in self.installed_versions(component_id):
            if version not in keep:
                shutil.rmtree(self.layout.version_dir(component_id, version), ignore_errors=True)
                removed.append(version)
        if removed:
            with self.state.edit() as data:
                entries = (data["components"].get(component_id) or {}).get("entries") or {}
                for version in removed:
                    entries.pop(version, None)
        return removed

    def remove(self, component_id: str, *, keep_data: bool = True):
        """Remove every version; the component's data folder stays unless `keep_data` is False."""
        shutil.rmtree(self.layout.versions_dir(component_id), ignore_errors=True)
        if not keep_data:
            shutil.rmtree(self.layout.component_dir(component_id), ignore_errors=True)
        with self.state.edit() as data:
            data["components"].pop(component_id, None)
        self.state.record_event("removed", component_id, kept_data=keep_data)
