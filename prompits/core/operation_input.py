"""Explicit typed HTTP input projection for immutable operation resources."""
from copy import deepcopy
import re
from urllib.parse import urlencode

from jsonschema import Draft202012Validator


def project_operation_input(contract, value):
    """Only a resource declaring http-envelope may supply path/query selectors.

    Legacy body contracts retain their fixed-route body semantics. There is no
    inference from property names and no client-controlled mapping expression.
    """
    if list(Draft202012Validator(contract['inputSchema']).iter_errors(value)):
        raise ValueError('Operation input does not conform to its immutable schema.')
    path = contract['transport']['path']
    if contract.get('inputBinding', 'body') == 'body':
        if '{' in path or '}' in path:
            raise ValueError('Parameterized routes require explicit http-envelope binding.')
        return {'method': contract['transport']['method'], 'path': path, 'query': '', 'body': deepcopy(value)}
    if not isinstance(value, dict) or set(value) - {'path', 'query', 'body'}:
        raise ValueError('HTTP envelope contains only path, query and body.')
    parameters = value.get('path', {})
    if not isinstance(parameters, dict):
        raise ValueError('Path selectors must be a typed object.')
    names = re.findall(r'\{([A-Za-z][A-Za-z0-9_]*)\}', path)
    if set(names) != set(parameters):
        raise ValueError('Path selector set differs from the immutable route.')
    for name, parameter in parameters.items():
        if (type(parameter) not in (str, int) or not re.fullmatch(r'[A-Za-z0-9_.:@-]{1,200}', str(parameter))
                or str(parameter) in ('.', '..')):
            raise ValueError('Path selector is not a safe single segment.')
        path = path.replace('{' + name + '}', str(parameter))
    if '{' in path or '}' in path:
        raise ValueError('Unbound operation route.')
    query = value.get('query', {})
    if not isinstance(query, dict) or len(query) > 64:
        raise ValueError('Query selectors must be a bounded typed object.')
    for key, item in query.items():
        values = item if isinstance(item, list) else [item]
        if (not isinstance(key, str) or len(key) > 128 or len(values) > 64
                or any(type(v) not in (str, int, bool) for v in values)):
            raise ValueError('Query selectors must be scalars or bounded scalar lists.')
    encoded = urlencode(sorted(query.items()), doseq=True)
    if len(encoded) > 8192:
        raise ValueError('Query exceeds its limit.')
    body = value.get('body')
    if contract['transport']['method'] == 'GET' and body is not None:
        raise ValueError('GET envelope cannot send a body.')
    return {'method': contract['transport']['method'], 'path': path, 'query': encoded, 'body': deepcopy(body)}
