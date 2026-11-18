"""The manager's one pulse, its operations and their exact parameter schemas.

The view sends every call as one envelope, `{operation, parameters, request_key}`, so the Phema never changes
when an operation is added; the agent checks each operation's own schema.
"""
from copy import deepcopy

from jsonschema import Draft202012Validator

NAME = 'mas_manager_dispatch'
VIEW_ID = 'mas-manager'

COMPONENT = {'type': 'string', 'pattern': '^[a-z0-9][a-z0-9-]{0,62}$'}
TERMS = {'type': 'array', 'maxItems': 20, 'uniqueItems': True,
         'items': {'type': 'string', 'pattern': '^[A-Za-z0-9_.-]{1,64}@[A-Za-z0-9_.-]{1,32}$'}}
LANGUAGE = {'enum': ['en', 'zh-Hant', 'zh-Hans', 'ja', 'es', 'fr']}
# The Phema's read-only `host` parameter: a manager manages only the computer it runs on.
THIS_COMPUTER = 'this-computer'
HOST = {'const': THIS_COMPUTER}


def record(properties, required=()):
    return {'type': 'object', 'additionalProperties': False, 'properties': deepcopy(properties), 'required': list(required)}


_OPERATIONS = {
    # Everything the page shows: this computer, the accepted parts list, every component's row and recent events.
    'mas_status': record({'language': LANGUAGE}),
    # Read the publisher's parts list again now (it is also read once a day).
    'mas_check_updates': record({}),
    'mas_install': record({'component': COMPONENT, 'accept_terms': TERMS}, ['component']),
    # `restart: false` switches at the component's next start; the page asks before restarting a component the
    # viewer depends on, such as the Terminal.
    'mas_update': record({'component': COMPONENT, 'restart': {'type': 'boolean'}, 'accept_terms': TERMS}, ['component']),
    'mas_start': record({'component': COMPONENT}, ['component']),
    'mas_stop': record({'component': COMPONENT}, ['component']),
    'mas_restart': record({'component': COMPONENT}, ['component']),
    'mas_rollback': record({'component': COMPONENT}, ['component']),
    'mas_remove': record({'component': COMPONENT, 'keep_data': {'type': 'boolean'}}, ['component']),
    'mas_logs': record({'component': COMPONENT, 'lines': {'type': 'integer', 'minimum': 1, 'maximum': 1000}}, ['component']),
    # Fetch one of the data sets a component offers (its parts list names the download), or stop that download.
    'mas_download_data': record({'component': COMPONENT, 'data': COMPONENT}, ['component', 'data']),
    'mas_stop_download': record({'component': COMPONENT}, ['component']),
    # The third-party notices of what a component installs (its THIRD_PARTY_NOTICES.txt), one part at a time from
    # `offset` (a character index; the answer names the next part's).
    'mas_notices': record({'component': COMPONENT, 'offset': {'type': 'integer', 'minimum': 0, 'maximum': 100_000_000}},
                          ['component']),
    # A problem report: records, recent events and every log's end, without credentials, saved on this computer.
    'mas_report': record({}),
}
# The page may name this computer as the host; no manager answers for another computer.
SCHEMAS = {name: {**schema, 'properties': {**schema['properties'], 'host': HOST}} for name, schema in _OPERATIONS.items()}

ID = {'type': 'string', 'minLength': 1, 'maxLength': 128, 'pattern': '^[A-Za-z0-9_.:-]+$'}
ENVELOPE = record({'operation': {'type': 'string', 'pattern': '^mas_[a-z_]{1,32}$'}, 'parameters': {'type': 'object'},
                   'request_key': ID}, ['operation', 'parameters', 'request_key'])


def check(schema, value):
    errors = sorted(Draft202012Validator(schema).iter_errors(value), key=lambda e: list(e.absolute_path))
    if errors:
        where = '/'.join(str(part) for part in errors[0].absolute_path)
        raise ValueError(('Invalid ' + where + ': ' if where else 'Invalid request: ') + errors[0].message[:200])
    return deepcopy(value)


def definition():
    return {'name': NAME, 'pulse_name': NAME, 'pulse_address': 'attas:' + NAME,
            'description': 'Free: installs, updates, starts, stops and watches Retis components on this computer.',
            'input_schema': deepcopy(ENVELOPE), 'output_schema': {'type': 'object'}, 'cost': 0, 'cost_points': 0}
