"""Component logs: the child writes straight to its file (so it keeps running if the manager stops), and the
manager rotates by copy-and-truncate. Children open the file in append mode, so truncation is safe."""
from __future__ import annotations

import os
import re
import shutil
from collections import deque
from pathlib import Path

_ERROR = re.compile(r"\b(ERROR|CRITICAL|FATAL|Traceback|Exception|Error:)")


def rotate_if_needed(path: Path, *, max_bytes: int = 5 * 1024 * 1024, keep: int = 3) -> bool:
    try:
        if path.stat().st_size <= max_bytes:
            return False
    except FileNotFoundError:
        return False
    for index in range(keep - 1, 0, -1):
        older = path.with_name(f"{path.name}.{index}")
        if older.exists():
            os.replace(older, path.with_name(f"{path.name}.{index + 1}"))
    shutil.copyfile(path, path.with_name(f"{path.name}.1"))
    with open(path, "r+b") as handle:
        handle.truncate(0)
    return True


def tail(path: Path, lines: int = 200) -> list[str]:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return [line.rstrip("\n") for line in deque(handle, maxlen=max(lines, 0))]
    except FileNotFoundError:
        return []


def last_error(path: Path, *, scan: int = 400) -> str:
    for line in reversed(tail(path, scan)):
        if _ERROR.search(line):
            return line.strip()[:300]
    return ""
