"""What Retis Terminal's problem report keeps from the Terminal's log, and what it removes.

The same rules as the Terminal package's own report command, kept beside the app so the app ships only what
it uses. tests/test_retis_desktop.py checks that both stay the same.
"""
from pathlib import Path
import re

SECRET = re.compile(r'(?i)((?:access|refresh|id|session|bearer|api|auth)?[_-]?(?:token|secret|password|cookie|key|authorization)'
                    r'["\']?\s*[:=]\s*["\']?)([^\s"\',;]+)')
LONG = re.compile(r'\b(eyJ[\w-]+\.[\w-]+\.[\w-]+|[A-Za-z0-9+_-]{40,}={0,2})\b')  # no '/', so file paths survive
# Sign-in values a URL carries by name, whatever their length: the Plaza sign-in callback's ?state=…&ticket=…,
# an OAuth ?code=…, and the open address's #local-access=…, also when URL-encoded inside another URL.
PARAMETER = re.compile(r'(?i)((?:[?&#;]|%3F|%26|%23)(?:ticket|state|code|code_verifier|local[-_]access)(?:=|%3D))'
                       r'([^\s&#"\'<>]+)')
IDENTIFIER = re.compile(r'\b(?:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|[0-9a-f]{32})\b', re.I)
# Requests an open Terminal repeats every few seconds; they crowd out what happened.
POLLING = re.compile(r'"GET /(?:api/ui-version|api/instances/[0-9a-f]{32}|health)(?:\?\S*)? HTTP/')
# Successful requests for the page's own files: opening the Terminal writes hundreds of them.
STATIC = re.compile(r'"GET /\S+\.(?:js|mjs|css|map|svg|png|jpe?g|gif|webp|ico|woff2?|ttf)(?:\?\S*)? HTTP/[\d.]+" (?:200|304)\b')
# What a report must never lose: warnings, errors and failed requests.
PROBLEM = re.compile(r'\b(?:WARNING|ERROR|CRITICAL|Traceback)\b|" [45]\d\d\b')


def redact(text):
    """Remove credentials from log lines: values after token/secret/password/cookie/key fields, sign-in URL
    parameters (ticket, state, code, local-access), JWTs and long opaque strings.

    Window, workspace and preview ids become short labels (id-1, id-2, ...), the same label for the same id,
    so lines about one window can still be followed.
    """
    text = re.sub(r'(?i)(bearer\s+)\S+', r'\1[redacted]', text)
    text = SECRET.sub(lambda m: m.group(1)+'[redacted]', text)
    text = PARAMETER.sub(lambda m: m.group(1)+'[redacted]', text)
    text = LONG.sub('[redacted]', text)
    labels = {}
    return IDENTIFIER.sub(lambda m: labels.setdefault(m.group(0).lower(), f'id-{len(labels) + 1}'), text)


def log_tail(path, count=400, earlier=100):
    """A log's last `count` lines without routine requests (polling, static files), how many routine lines
    were left out, and the warnings, errors and failed requests from before those lines (at most `earlier`)."""
    lines = Path(path).read_text(errors='replace').splitlines()
    kept = [line for line in lines if not POLLING.search(line) and not STATIC.search(line)]
    before = [line for line in kept[:-count] if PROBLEM.search(line)][-earlier:] if len(kept) > count else []
    return kept[-count:], len(lines) - len(kept), before


def state_directories(base):
    """The Terminal's state folder and the folder it keeps for each Plaza it has used."""
    base = Path(base)
    found = [base] if base.is_dir() else []
    if (base / 'plazas').is_dir():
        found += [p for p in (base / 'plazas').iterdir() if p.is_dir()]
    return found
