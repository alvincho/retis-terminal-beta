"""A Data Browser's choice of data source for its data panels.

A data panel asks a source for a canonical model (a `data_fetch` Pulse reference with
`inputs.target_model`). The viewer may pick any source this Terminal can use that serves
the same model: one on Plaza, or their own paired local copy. The choice is kept with the
Browser's other per-panel overrides; the layout itself is unchanged, so it needs no new review.
"""
import copy

FIELD = 'sourceAgentId'


def choosable(ref):
    """A data panel whose source the viewer may choose."""
    target = ref.get('target') if isinstance(ref, dict) else None
    model = ((ref.get('inputs') or {}).get('target_model') if isinstance(ref, dict) else None) or {}
    return (ref.get('kind') == 'pulse' and isinstance(target, dict) and target.get('pulse') == 'data_fetch'
            and isinstance(model, dict) and isinstance(model.get('id'), str))


def model_of(entry, target_model):
    """The version of the requested model this source serves (the same version when it has it), or None."""
    support = ((entry.get('meta') or {}).get('data_source') or {}).get('modelSupport') or []
    rows = [row['modelRef'] for row in support if isinstance(row, dict) and isinstance(row.get('modelRef'), dict)
            and row['modelRef'].get('id') == (target_model or {}).get('id')]
    return next((row for row in rows if row == target_model), rows[0] if rows else None)


def fetches(entry):
    meta = entry.get('meta') or {}
    names = {p.get('pulse_name') or p.get('name') for p in meta.get('supported_pulses') or [] if isinstance(p, dict)}
    access = ((meta.get('data_source') or {}).get('connectivity') or {}).get('data_access') or {}
    return 'data_fetch' in names and access.get('attempt_ready') is not False


def usable(entry):
    """A source this viewer may call now: trusted on Plaza, or their own paired local copy."""
    return isinstance(entry.get('id'), str) and (entry.get('_plazaTrusted') is True or bool(entry.get('localId')))


def panels(refs):
    return {path: ref['inputs']['target_model'] for path, ref in refs.items() if choosable(ref)}


def candidates(directory, refs):
    """The sources the viewer can choose for this Browser's data panels, those serving the most panels first."""
    wanted = panels(refs)
    rows = []
    for entry in directory if wanted else []:
        if not isinstance(entry, dict) or not usable(entry) or not fetches(entry):
            continue
        served = sum(1 for model in wanted.values() if model_of(entry, model))
        if served:
            rows.append({'id': entry['id'], 'name': str(entry.get('name') or entry['id'])[:120],
                         'local': bool(entry.get('localId')), 'panels': served})
    rows.sort(key=lambda row: (-row['panels'], row['name'].lower(), row['id']))
    return {'panels': len(wanted), 'sources': rows[:40]}


def chosen(overrides, refs):
    """The source chosen for this Browser's data panels, or None for the automatic choice."""
    picks = {(overrides.get(path) or {}).get(FIELD) for path in panels(refs)}
    picks.discard(None)
    return picks.pop() if len(picks) == 1 else None


def apply(overrides, refs, entry):
    """Set (or, with no entry, clear) the source of every data panel the entry serves; returns the changed paths."""
    result, changed = copy.deepcopy(overrides or {}), []
    for path, model in panels(refs).items():
        current = dict(result.get(path) or {})
        before = current.pop(FIELD, None)
        if entry is not None and model_of(entry, model):
            current[FIELD] = entry['id']
        if current:
            result[path] = current
        else:
            result.pop(path, None)
        if current.get(FIELD) != before:
            changed.append(path)
    return result, changed


def split(ref):
    """The reference as reviewed, and the viewer's source choice carried with it (or None)."""
    choice = ref.get(FIELD)
    if choice is not None and (not choosable(ref) or not isinstance(choice, str) or not 1 <= len(choice) <= 200):
        raise ValueError('A data source applies only to a data panel.')
    return {k: v for k, v in ref.items() if k != FIELD}, choice
