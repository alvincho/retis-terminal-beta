"""Bind a catalog Phema's unfilled agent-view needs to this account's paired local resources.

A catalog Phema names what it needs (`{"<need>": {"viewId": "..."}}` under `attas:app-consultant-agents`) and
carries a placeholder agent (`app-consultant-agent:<need>`) until something fills it. The App Consultant fills a
need from the agents Plaza lists. A view served by a program on the viewer's own computer is never on Plaza; the
Terminal finds it by Bonjour. When a placeholder is still unfilled, this binds it to the local resource on this
computer that advertises that view (in its local resource description).

It only replaces placeholders: an agent the App Consultant already bound is never changed, and nothing is bound
to anything but a local resource running on this computer. The view then mounts from that program; the Terminal
still reviews it as any other view. The placeholder format is the App Consultant's published convention, used here as data.
"""
KEY = 'attas:app-consultant-agents'
PREFIX = 'app-consultant-agent:'


def _walk(value):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)


def _contains(value, text):
    if isinstance(value, str):
        return value == text
    if isinstance(value, dict):
        return any(_contains(item, text) for item in value.values())
    if isinstance(value, list):
        return any(_contains(item, text) for item in value)
    return False


def _replace(value, mapping):
    if isinstance(value, str):
        return mapping.get(value, value)
    if isinstance(value, dict):
        return {key: _replace(item, mapping) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace(item, mapping) for item in value]
    return value


def view_needs(value):
    """Every unfilled placeholder that needs a view: {placeholder: viewId}."""
    found = {}
    for node in _walk(value):
        declared = node.get(KEY)
        if not isinstance(declared, dict) or declared.get('version') != 1 or not isinstance(declared.get('needs'), dict):
            continue
        for name, need in declared['needs'].items():
            if isinstance(name, str) and isinstance(need, dict) and isinstance(need.get('viewId'), str):
                found[PREFIX + name] = need['viewId']
    return {slot: view for slot, view in found.items() if _contains(value, slot)}


def advertises(row, view_id):
    views = ((row.get('meta') or {}).get('phemacast_agent_views') or {}).get('views') or []
    return any(isinstance(view, dict) and view.get('viewId') == view_id for view in views)


def choose(directory, view_id):
    """The local resource on this computer that advertises the view; the first by name when several do."""
    rows = [row for row in directory if row.get('localId') and advertises(row, view_id)]
    return sorted(rows, key=lambda row: (str(row.get('name') or ''), row['id']))[0]['id'] if rows else None


def mapping(value, directory):
    """{placeholder: local resource} for the value's unfilled needs that resources on this computer fill."""
    return {slot: chosen for slot, view in view_needs(value).items() if (chosen := choose(directory, view))}


def bound(value, directory):
    """The value with its unfilled needs filled as `bind` fills them."""
    return _replace(value, mapping(value, directory))


async def bind(body, network, user):
    """Fill unfilled view placeholders in an instance request (its `phema` and `entry`) in place; return the mapping."""
    target = body.get('phema') if body.get('phema') is not None else body.get('entry')
    needs = view_needs(target) if isinstance(target, dict) else {}
    if not needs or not getattr(network, 'local_clients', None):
        return {}
    directory = await network.directory(user.get('token', ''))
    mapping = {slot: chosen for slot, view in needs.items() if (chosen := choose(directory, view))}
    for key in ('phema', 'entry'):
        if body.get(key) is not None and mapping:
            body[key] = _replace(body[key], mapping)
    return mapping
