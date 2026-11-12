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
    primary_consultant = kind == 'app-consultant' and agent_id == 'research-studio'
    if primary_consultant:
        name = 'App Consultant'
    route_id = 'app-consultant' if primary_consultant else agent_id
    in_data_network = kind == 'data-terminal' or primary_consultant
    description = ('Find, customize, and save market, company, and portfolio layouts to use in Data Terminal.'
                   if primary_consultant else f'{name}: source-independent data layouts and research workspaces.')
    definition = HostDefinition(
        slug=kind, feature_id=agent_id, name=name, logical_agent_id=agent_id,
        description=description,
        sections=(
            {'id': agent_id, 'label': name, 'kind': 'workspace', 'placement': 'main', 'visibility': 'public', 'surface_id': agent_id, 'order': 120},
            *(({'id': agent_id + '-navigation', 'label': name, 'kind': 'navigation', 'placement': 'sidebar', 'visibility': 'public', 'surface_id': agent_id, 'order': 190 if primary_consultant else 120},) if in_data_network else ()),
        ),
        surfaces=({'id': agent_id, 'kind': 'iframe', 'placement': 'main', 'label': name, 'title': name, 'path': '/app-consultant.html' if kind == 'app-consultant' else '/', 'visibility': 'public', 'presentation': {'chrome': 'none', 'border': 'none', 'sizing': 'viewport', 'disclosure': 'none'}}, {'id':agent_id+'-settings','kind':'iframe','placement':'main','label':name+' settings','path':'/settings.html','visibility':'public'}),
        portable_items=(
            {'id': agent_id + '-route', 'slot': 'catalog.routes', 'kind': 'route', 'label': name, 'route_id': route_id, 'surface_id': agent_id, 'order': 120},
            # Old studio bookmarks resolve to the retained editor; aliases do
            # not advertise extra agents, menus, or product cards.
            *(tuple({'id': alias + '-legacy-route', 'slot': 'catalog.routes', 'kind': 'route', 'label': name, 'route_id': alias, 'surface_id': agent_id, 'order': 191 + index}
                    for index, alias in enumerate(('research-studio', 'market-studio', 'portfolio-studio'))) if primary_consultant else ()),
            {'id': agent_id + '-menu', 'slot': 'shell.catalog-menu', 'kind': 'link', 'label': name, 'route_id': route_id, 'order': 190 if primary_consultant else 180, **({'parent_route_id': 'data-agent-network'} if in_data_network else {})},
            {'id': agent_id + '-card', 'slot': 'catalog.cards', 'kind': 'card', 'label': name, 'route_id': route_id, 'description': description, 'order': 120},
            {'id':agent_id+'-settings-panel','slot':'settings.panels','kind':'panel','label':name+' settings','surface_id':agent_id+'-settings','order':120},
            {'id':agent_id+'-keys','slot':'keys.actions','kind':'link','label':name+' API connections','surface_id':agent_id+'-settings','order':120},
        ),
        attas_items=({'id': agent_id + '-product', 'slot': 'top-menu.products', 'kind': 'link', 'label': name, 'surface_id': agent_id, 'order': 120},),
    )
    return definition
