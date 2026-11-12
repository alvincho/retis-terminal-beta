"""Window selectors are not credentials; authentication requires their HttpOnly cookie."""
import re
from fastapi import HTTPException

WINDOW = re.compile(r'^[a-f0-9]{32}$')


def window_context(request):
    header = request.headers.get('x-terminal-window')
    query = request.query_params.get('terminalWindow')
    if header and query and header != query:
        raise HTTPException(403, 'Conflicting Terminal window context.')
    value = header or query or ''
    if value and not WINDOW.fullmatch(value):
        raise HTTPException(422, 'Invalid Terminal window context.')
    return value


def window_scope(scope, window):
    return scope + '|window|' + window if window else scope


def window_cookie(cookie, window):
    return cookie + '_w_' + window if window else cookie


def state_key(user):
    return 'window.' + user['terminalWindow'] if user.get('terminalWindow') else 'default'


def preferences(store, user):
    key = state_key(user) if user.get('terminalWindow') else 'preferences'
    try:
        return store.get(user['id'], 'settings', key)
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
    try:
        legacy = store.get(user['id'], 'settings', 'preferences')
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
        legacy = {'theme':'dark'}
    value = {k:v for k,v in legacy.items() if k not in ('id','revision','updatedAt')}
    # Snapshot defaults once. Later preference changes in another window cannot
    # silently change this window's presentation.
    if user.get('terminalWindow'):
        try:
            return store.put(user['id'], 'settings', key, value, 0)
        except HTTPException as exc:
            if exc.status_code != 409:
                raise
            return store.get(user['id'], 'settings', key)
    return value


def saved_tabs(store, user):
    """This account's Terminal tabs, most recently used first, so a closed one can be opened again."""
    tabs = {}
    for kind in ('settings', 'workspace-state'):
        for record in store.list(user['id'], kind):
            window = record['id'].removeprefix('window.')
            if record['id'] == window or not WINDOW.fullmatch(window):
                continue
            tab = tabs.setdefault(window, {'window':window, 'name':'', 'lastUsed':0, 'workspaces':[], 'scheduledRuns':0,
                                           'current':window == user.get('terminalWindow')})
            tab['lastUsed'] = max(tab['lastUsed'], record['updatedAt'])
            if kind == 'settings':
                tab['name'] = record.get('sessionName') if isinstance(record.get('sessionName'), str) else ''
            else:
                tab['workspaces'] = [{'name':w.get('name', ''), 'browsers':len(w.get('browsers') or [])}
                                     for w in ((record.get('document') or {}).get('workspaces') or [])]
    for run in store.list(user['id'], 'scheduled-run'):
        if run.get('window') in tabs:
            tabs[run['window']]['scheduledRuns'] += 1
    return sorted(tabs.values(), key=lambda tab: tab['lastUsed'], reverse=True)


def forget_tabs(store, owner, windows):
    """Remove what these saved windows kept: settings and keys, workspace, revisions, previews and scheduled runs."""
    keys = {'window.' + window for window in windows}
    prefixes = tuple(key + ':' for key in keys)
    def gone(kind, record):
        if kind in ('settings', 'workspace-state'):
            return record['id'] in keys
        if kind in ('workspace-revision', 'workspace-preview'):
            return record['id'].startswith(prefixes)
        if kind == 'workspace-conflict':
            return record.get('windowKey') in keys
        if kind == 'connection':
            return record.get('terminalWindow') in windows
        if kind == 'phema-permission':
            return record['id'].split(':', 1)[0] in windows
        return kind == 'scheduled-run' and record.get('window') in windows
    for kind in ('settings', 'workspace-state', 'workspace-revision', 'workspace-preview', 'workspace-conflict',
                 'connection', 'phema-permission', 'scheduled-run'):
        for record in store.list(owner, kind):
            if gone(kind, record):
                store.delete(owner, kind, record['id'])
                if kind == 'scheduled-run':
                    store.delete(owner, 'scheduled-run-result', record['id'])
