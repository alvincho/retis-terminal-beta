"""Helpers for locating FinMAS runtime log files outside the repository."""

from __future__ import annotations

import os
import platform
from pathlib import Path
from typing import Mapping


FINMAS_LOG_DIR_ENV = "FINMAS_LOG_DIR"


def _path_is_relative_to(path: Path, parent: Path) -> bool:
    """Return whether path is inside parent without requiring either to exist."""
    try:
        path.resolve(strict=False).relative_to(parent.resolve(strict=False))
        return True
    except ValueError:
        return False


def _safe_log_part(value: str) -> str:
    """Return a filesystem-safe path segment for log directory names."""
    cleaned = "".join(char if char.isalnum() or char in {"-", "_", "."} else "-" for char in str(value or "").strip())
    cleaned = cleaned.strip(".-")
    return cleaned or "default"


def _default_system_log_root(
    *,
    env: Mapping[str, str],
    system_name: str,
    home: Path,
) -> Path:
    """Return the platform-appropriate FinMAS log root."""
    normalized_system = system_name.lower()
    if normalized_system == "darwin":
        return home / "Library" / "Logs" / "FinMAS"
    if normalized_system == "windows":
        base = env.get("ProgramData") or env.get("LOCALAPPDATA")
        if base:
            return Path(base).expanduser() / "FinMAS" / "Logs"
        return home / "AppData" / "Local" / "FinMAS" / "Logs"

    var_log = Path("/var/log")
    if var_log.exists() and os.access(var_log, os.W_OK):
        return var_log / "finmas"

    state_home = env.get("XDG_STATE_HOME")
    base = Path(state_home).expanduser() if state_home else home / ".local" / "state"
    return base / "finmas" / "log"


def finmas_log_dir(
    *parts: str,
    repo_root: Path | str | None = None,
    env: Mapping[str, str] | None = None,
    system_name: str | None = None,
    home: Path | str | None = None,
) -> Path:
    """Return an ensured FinMAS log directory outside the repository.

    ``FINMAS_LOG_DIR`` may override the root, but an override inside ``repo_root``
    is ignored so runtime logs do not get written back into the checkout.
    """
    environ = os.environ if env is None else env
    home_path = Path(home).expanduser() if home is not None else Path.home()
    current_system = system_name or platform.system()

    configured = str(environ.get(FINMAS_LOG_DIR_ENV) or "").strip()
    root = Path(configured).expanduser() if configured else _default_system_log_root(
        env=environ,
        system_name=current_system,
        home=home_path,
    )

    if repo_root is not None and _path_is_relative_to(root, Path(repo_root)):
        root = _default_system_log_root(
            env={key: value for key, value in environ.items() if key != FINMAS_LOG_DIR_ENV},
            system_name=current_system,
            home=home_path,
        )

    for part in parts:
        root = root / _safe_log_part(part)
    root.mkdir(parents=True, exist_ok=True)
    return root
