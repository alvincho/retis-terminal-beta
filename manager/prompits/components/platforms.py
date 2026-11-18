"""Platform names used in parts lists: `<system>-<machine>`, e.g. `macos-arm64` or `windows-x86_64`."""
from __future__ import annotations

import platform as _platform

_SYSTEMS = {"darwin": "macos", "windows": "windows", "linux": "linux"}
_MACHINES = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "x86_64", "amd64": "x86_64"}


def current_platform(system: str | None = None, machine: str | None = None) -> str:
    system_name = (system or _platform.system()).lower()
    machine_name = (machine or _platform.machine()).lower()
    return f"{_SYSTEMS.get(system_name, system_name)}-{_MACHINES.get(machine_name, machine_name)}"
