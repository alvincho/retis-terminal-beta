"""DBL is an application profile; publisher mappings remain outside it."""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

from fastapi import HTTPException

WEB = Path(__file__).parent / 'web'
ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$')


def identifier(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise HTTPException(422, 'Invalid identifier.')
    return value


def validate_provided_entry(entry):
    from phemacast.core.layout import validate_layout
    from .layout_document import to_document
    validate_layout(to_document(entry))
    return entry


async def layout_operation(operation, **payload):
    """Container structure only; no templates, recommendations or binding policy."""
    try:
        if operation == 'validate':
            return {'entry': validate_provided_entry(payload.get('entry'))}
        if operation == 'workspace':
            from phemacast.browser.workspace_documents import workspace_document
            return {'document': workspace_document(payload.get('document'), validate_provided_entry)}
        raise ValueError('Application layout operations belong to their owning agents.')
    except (ValueError, TypeError, KeyError) as exc:
        raise HTTPException(422, str(exc)) from exc


def public_descriptor(kind, name, origin, agent_id):
    # Existing Phemacast host contract; ordinary browsers are scoped instances,
    # separate from the service attachment registered in Plaza.
    from .host_descriptor import HostDefinition
    # The Terminal hosts only itself (create_app refuses any other kind); App Consultants are agents of their own,
    # all used at once, so none is special here.
    route_id = agent_id
    in_data_network = kind == 'data-terminal'
    description = f'{name}: source-independent data layouts and research workspaces.'
    definition = HostDefinition(
        slug=kind, feature_id=agent_id, name=name, logical_agent_id=agent_id,
        description=description,
        sections=(
            {'id': agent_id, 'label': name, 'kind': 'workspace', 'placement': 'main', 'visibility': 'public', 'surface_id': agent_id, 'order': 120},
            *(({'id': agent_id + '-navigation', 'label': name, 'kind': 'navigation', 'placement': 'sidebar', 'visibility': 'public', 'surface_id': agent_id, 'order': 120},) if in_data_network else ()),
        ),
        surfaces=({'id': agent_id, 'kind': 'iframe', 'placement': 'main', 'label': name, 'title': name, 'path': '/', 'visibility': 'public', 'presentation': {'chrome': 'none', 'border': 'none', 'sizing': 'viewport', 'disclosure': 'none'}}, {'id':agent_id+'-settings','kind':'iframe','placement':'main','label':name+' settings','path':'/settings.html','visibility':'public'}),
        portable_items=(
            {'id': agent_id + '-route', 'slot': 'catalog.routes', 'kind': 'route', 'label': name, 'route_id': route_id, 'surface_id': agent_id, 'order': 120},
            {'id': agent_id + '-menu', 'slot': 'shell.catalog-menu', 'kind': 'link', 'label': name, 'route_id': route_id, 'order': 180, **({'parent_route_id': 'data-agent-network'} if in_data_network else {})},
            {'id': agent_id + '-card', 'slot': 'catalog.cards', 'kind': 'card', 'label': name, 'route_id': route_id, 'description': description, 'order': 120},
            {'id':agent_id+'-settings-panel','slot':'settings.panels','kind':'panel','label':name+' settings','surface_id':agent_id+'-settings','order':120},
            {'id':agent_id+'-keys','slot':'keys.actions','kind':'link','label':name+' API connections','surface_id':agent_id+'-settings','order':120},
        ),
        attas_items=({'id': agent_id + '-product', 'slot': 'top-menu.products', 'kind': 'link', 'label': name, 'surface_id': agent_id, 'order': 120},),
    )
    return definition
