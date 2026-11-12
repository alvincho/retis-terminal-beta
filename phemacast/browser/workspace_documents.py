"""Bounded container documents. Application content is supplied by publishers."""
from copy import deepcopy
import json
import math


def workspace_document(value, validate_entry):
    if not isinstance(value, dict) or value.get('version') != 2:
        raise ValueError('Expected a versioned workspace document.')
    workspaces = value.get('workspaces')
    if not isinstance(workspaces, list) or not 1 <= len(workspaces) <= 32:
        raise ValueError('Provide 1–32 workspaces.')
    if len(json.dumps(value, allow_nan=False).encode()) > 5_000_000:
        raise ValueError('Workspace exceeds the document size limit.')
    result = deepcopy(value)
    identifiers = set()
    for workspace in result['workspaces']:
        if not isinstance(workspace, dict) or not isinstance(workspace.get('id'), str):
            raise ValueError('A workspace identity is required.')
        if workspace['id'] in identifiers:
            raise ValueError('Workspace identities must be unique.')
        identifiers.add(workspace['id'])
        browsers = workspace.get('browsers')
        if not isinstance(browsers, list) or len(browsers) > 12:
            raise ValueError('Each workspace supports up to 12 browsers.')
        for browser in browsers:
            if not isinstance(browser, dict):
                raise ValueError('A browser must be a container object.')
            provided = browser.get('customLayout')
            if provided is not None:
                validate_entry(provided)
            for key in ('contentOverrides','refreshOverrides','fieldBindings',
                        'instrumentOverrides','parameterValues','interactionDrafts',
                        'discussionDrafts','researchView'):
                browser.setdefault(key,{})
            browser.setdefault('sourceMode','parameters')
            frame = browser.get('frame')
            if frame is not None and (not isinstance(frame, dict) or
                    any(type(frame.get(k)) not in (float, int) or
                        not math.isfinite(frame[k]) for k in ('x','y','width','height'))):
                raise ValueError('Browser geometry must be finite.')
    return result
