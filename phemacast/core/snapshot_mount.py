"""Mount already resolved, inert Phemar content without resolving it again.

This conveys no source, operation or publisher authority. Authentication and
publication provenance remain the owning service's responsibility. The container
stores only the user's selected view and never queries a provider in this mode.
"""
from copy import deepcopy
from .composition import validate_composition,walk_compositions,composition,content_hash


def resolved_snapshot(phema):
    value=validate_composition(phema)
    if composition(value).get('extensions',{}).get('phemacast:source_mode')!='snapshot':
        raise ValueError('Select an explicitly resolved snapshot Phema.')
    for _,current in walk_compositions(value):
        node=composition(current)
        if node.get('references'):
            raise ValueError('Resolved snapshots cannot contain deferred source references.')
        document=node['presentation'].get('document',{})
        design=document.get('design',{})
        if design.get('dataRequirements'):
            raise ValueError('Resolved snapshots cannot contain data requests.')
        for surface in design.get('layout',{}).get('children',[]):
            renderer=surface['renderer']['id']
            if renderer in ('phemacast:media.interactive','phemacast:pulse-chat'):
                raise ValueError('Resolved snapshots cannot contain active controls.')
            if surface.get('referenceId') or surface.get('requirementId'):
                raise ValueError('Every snapshot surface must already contain its content.')
    return {'schema':'phemacast:resolved-composition/1','phemaId':value['id'],
        'contentHash':content_hash(value),'status':'ready','sourceMode':'snapshot',
        'results':{},'components':{},'provenance':deepcopy(composition(value).get('extensions',{}))}
