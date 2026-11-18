"""A problem report: one zip with what someone helping the user needs, and no credentials.

It holds the manager's records (installed versions, processes, the accepted parts list), its recent events, the
end of every component's log and data-download log, and this computer's platform. Lines are passed through
`redact` first, so a token, key or password a component printed never leaves in the report.
"""
from __future__ import annotations

import json
import platform
import re
import time
import zipfile
from pathlib import Path
from typing import Any

from prompits.components.logs import tail

LOG_LINES = 2000
SECRET_NAMES = r'(?:api[_-]?key|token|secret|password|passwd|authorization|cookie|session|private[_-]?key|access[_-]?key)'
_PATTERNS = (
    re.compile(r'(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]{8,}'),
    re.compile(r'(?i)(["\']?' + SECRET_NAMES + r'["\']?\s*[:=]\s*["\']?)([^\s"\',;]{4,})'),
    re.compile(r'\b(?:sk|pk|rk)-[A-Za-z0-9_-]{16,}\b'),
    re.compile(r'\b(?:lc|pc|cs)-(?:access|refresh|call)-[A-Za-z0-9_-]{8,}'),
    re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----'),
)


def redact(text: str) -> str:
    text = _PATTERNS[0].sub(r'\1 [removed]', text)
    text = _PATTERNS[1].sub(lambda m: m.group(1) + '[removed]', text)
    for pattern in _PATTERNS[2:]:
        text = pattern.sub('[removed]', text)
    return text


def write_report(root: Path, state: dict[str, Any], events: list[dict[str, Any]], logs: dict[str, list[Path]],
                 *, extra: dict[str, Any] | None = None, clock=time.time) -> Path:
    """Write reports/report-<time>.zip under `root`; `logs` maps a component to its log files."""
    folder = Path(root) / 'reports'
    folder.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime('%Y%m%d-%H%M%S', time.gmtime(clock()))
    path = folder / f'report-{stamp}.zip'
    about = {'created_at': clock(), 'platform': platform.platform(), 'python': platform.python_version(), **(extra or {})}
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr('about.json', json.dumps(about, indent=2))
        bundle.writestr('state.json', redact(json.dumps(state, indent=1, sort_keys=True)))
        bundle.writestr('events.jsonl', redact(''.join(json.dumps(event) + '\n' for event in events)))
        for component, files in sorted(logs.items()):
            for file in files:
                lines = tail(file, LOG_LINES)
                if lines:
                    bundle.writestr(f'logs/{component}/{file.name}', redact('\n'.join(lines) + '\n'))
    path.chmod(0o600)
    return path
