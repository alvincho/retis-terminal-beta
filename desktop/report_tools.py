"""What Retis Terminal's problem report keeps from the Terminal's log, and what it removes.

The same rules as the Terminal package's own report command, kept beside the app so the app ships only what
it uses. tests/test_retis_desktop.py checks that both stay the same.
"""
from datetime import datetime, timedelta, timezone
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
POLLING = re.compile(r'"GET /(?:api/ui-version|api/instances/[0-9a-f]{32}(?:/runs)?|health)(?:\?\S*)? HTTP/')
# Successful requests for the page's own files: opening the Terminal writes hundreds of them.
STATIC = re.compile(r'"GET /\S+\.(?:js|mjs|css|map|svg|png|jpe?g|gif|webp|ico|woff2?|ttf)(?:\?\S*)? HTTP/[\d.]+" (?:200|304)\b')
# What a report must never lose: warnings, errors and failed requests.
PROBLEM = re.compile(r'\b(?:WARNING|ERROR|CRITICAL|Traceback)\b|" [45]\d\d\b')
# Failed requests the Terminal answers as designed while a window has not signed in: local resources and layouts
# answer 401 until it signs in, and a new tab asks for windows to take up before it exchanges the launch secret,
# so that answers 403. Only these INFO request lines match, never a warning or an error; earlier ones are counted,
# not listed, and every other failed request is listed.
PRE_SIGN_IN = re.compile(r'^(?:\S+ \S+ )?INFO:\s+\S+ - "(?:GET /api/(?:local-resources|catalog)(?:\?\S*)? HTTP/[\d.]+" 401'
                         r'|GET /api/auth/resumable-windows(?:\?\S*)? HTTP/[\d.]+" 403)\b')
# The Terminal's log starts each line with the time it was written (scripts/serve_data_terminal.py).
TIME = re.compile(r'(\d{4})-(\d\d)-(\d\d) (\d\d):(\d\d):(\d\d)([+-])(\d\d)(\d\d)\b')
# How far the earlier lines reach back from the log's last line with a time.
EARLIER_HOURS = 24
# The Terminal's note that a Plaza trust check was slow, written up to once a minute per layout.
TRUST = re.compile(r'\bWARNING:?\s+Plaza trust: ')


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


def line_time(line):
    """When a log line was written, from the time at its start; None for a line without one, such as a traceback's."""
    match = TIME.match(line)
    if not match:
        return None
    *fields, sign, hours, minutes = match.groups()
    try:
        offset = timedelta(hours=int(hours), minutes=int(minutes))
        return datetime(*map(int, fields), tzinfo=timezone(offset if sign == '+' else -offset))
    except ValueError:
        return None


def log_tail(path, count=400, earlier=100, hours=EARLIER_HOURS):
    """A log's last `count` lines without routine requests (polling, static files), how many routine lines
    were left out, the warnings, errors and failed requests from before those lines, and what those cover.

    The earlier lines reach back `hours` from the log's last line with a time, so a log kept for weeks shows its
    latest day. A line without a time counts as written when the next line with one was. Normal pre-sign-in
    responses (PRE_SIGN_IN) are counted instead of listed, each layout's repeated Plaza trust lines are folded
    into its latest, and the latest `earlier` lines are kept. What they cover: 'since', the time they start from
    (None for a log without times), 'found', how many there were before that limit, and 'preSignIn', how many
    normal pre-sign-in responses were left out.
    """
    # The Terminal writes its log in UTF-8; Windows would otherwise read it in its own code page.
    lines = Path(path).read_text(encoding='utf-8', errors='replace').splitlines()
    times = [line_time(line) for line in lines]
    newest = next((written for written in reversed(times) if written is not None), None)
    cutoff = newest - timedelta(hours=hours) if newest is not None else None
    recent, after = [], None
    for written in reversed(times):
        after = written if written is not None else after
        recent.append(cutoff is None or after is None or after >= cutoff)
    recent.reverse()
    kept = [(line, fresh) for line, fresh in zip(lines, recent) if not POLLING.search(line) and not STATIC.search(line)]
    problems = [line for line, fresh in kept[:-count] if fresh and PROBLEM.search(line)] if len(kept) > count else []
    pre_sign_in = sum(1 for line in problems if PRE_SIGN_IN.search(line))
    problems = [line for line in problems if not PRE_SIGN_IN.search(line)]
    # Slow Plaza trust checks repeat all day: each layout's latest stands for its others, so other warnings and errors fit.
    layouts = {}
    for index, line in enumerate(problems):
        match = TRUST.search(line)
        if match:
            layouts.setdefault(line[match.end():].split(' \u2014 ', 1)[0], []).append(index)
    folded = set()
    for indexes in layouts.values():
        if len(indexes) > 1:
            since = problems[indexes[0]].split(' WARNING', 1)[0]
            problems[indexes[-1]] += f' (and {len(indexes) - 1} earlier Plaza trust lines for it since {since})'
            folded.update(indexes[:-1])
    problems = [line for index, line in enumerate(problems) if index not in folded]
    covered = {'since': cutoff.strftime('%Y-%m-%d %H:%M:%S%z') if cutoff else None, 'found': len(problems),
               'preSignIn': pre_sign_in}
    return [line for line, _ in kept[-count:]], len(lines) - len(kept), problems[-earlier:], covered


def log_report(name, path):
    """A report's sections for one log: its earlier warnings, errors and failed requests, then its last lines."""
    tail, routine, before, covered = log_tail(path)
    lines = []
    if before:
        shown = (f'{len(before)} line{"s" * (len(before) != 1)}' if len(before) == covered['found']
                 else f'the latest {len(before)} of {covered["found"]} lines')
        span = (f"in the log's last {EARLIER_HOURS} hours ({shown} since {covered['since']}" if covered['since']
                else f'in the whole log ({shown}')
        left_out = f"; {covered['preSignIn']} normal pre-sign-in responses (401, 403) left out" if covered['preSignIn'] else ''
        lines += ['', f'## {name}: earlier warnings, errors and failed requests {span}{left_out})', *before]
    left_out = f'; {routine} routine lines (polling, static files) left out' if routine else ''
    return lines + ['', f'## {name} (last {len(tail)} lines{left_out})', *tail]


def state_directories(base):
    """The Terminal's state folder and the folder it keeps for each Plaza it has used."""
    base = Path(base)
    found = [base] if base.is_dir() else []
    if (base / 'plazas').is_dir():
        found += [p for p in (base / 'plazas').iterdir() if p.is_dir()]
    return found
