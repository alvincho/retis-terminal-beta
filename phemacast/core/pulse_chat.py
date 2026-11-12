"""Portable, explicitly submitted interactions with registered Pulse contracts.

Discovery, trust, authentication and billing belong to the hosting application.
This module never resolves an address, supplies credentials or executes a Pulse.
"""
from copy import deepcopy
import json
import re
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry

CAPABILITY = {'id': 'phemacast:pulse-chat', 'version': '1.0.0'}
MESSAGE_FIELDS = ('incoming_text', 'message', 'query', 'prompt', 'text', 'question')
SECRET = re.compile(r'password|secret|(?:^|_)token(?:$|_)|api.?key|credential|connection.?id', re.I)
CONFIG_SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'properties': {
        'target': {'type': 'object', 'additionalProperties': False,
                   'properties': {'agentId': {'type': 'string', 'minLength': 1, 'maxLength': 200},
                                  'pulse': {'type': 'string', 'minLength': 1, 'maxLength': 200}},
                   'required': ['agentId', 'pulse']},
        'allowTargetSelection': {'type': 'boolean', 'default': True},
        'arguments': {'type': 'object'},
        **{key: {'type': 'string', 'minLength': 1, 'maxLength': 200}
           for key in ('messageField', 'historyField', 'contextField')},
    },
}


def validate_arguments(schema, arguments):
    if not isinstance(arguments, dict):
        raise ValueError('Pulse arguments must be an object.')
    try:
        from .schema_security import validate
        validate(schema,arguments)
    except Exception:
        raise ValueError('Pulse arguments do not match a valid, bounded publisher schema.') from None
    return arguments


def safe_arguments(value, depth=0):
    if depth > 12:
        raise ValueError('Pulse arguments are too deeply nested.')
    if isinstance(value, dict):
        for key, item in value.items():
            if SECRET.search(key):
                raise ValueError('Configure credentials through the host connection settings.')
            safe_arguments(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            safe_arguments(item, depth + 1)


def validate_config(config):
    config = deepcopy(config or {})
    validate_arguments(CONFIG_SCHEMA, config)
    if config.get('allowTargetSelection') is False and not config.get('target'):
        raise ValueError('A fixed Pulse chat requires a registered Pulser and Pulse.')
    safe_arguments(config.get('arguments', {}))
    return config


def message_field(schema, config=None):
    properties = schema.get('properties', {}) if isinstance(schema, dict) else {}
    field = (config or {}).get('messageField')
    if field:
        if field not in properties or properties[field].get('type') not in (None, 'string', 'array'):
            raise ValueError('The configured message field is not declared by this Pulse.')
        return field
    return next((key for key in MESSAGE_FIELDS if key in properties and properties[key].get('type') in (None, 'string')),
                'messages' if properties.get('messages', {}).get('type') == 'array' else None)


def prepare_parameters(schema, configured, submitted, text='', context=None, history=(), config=None):
    """Validate exact arguments before execution; never turn context into a query."""
    config = validate_config(config)
    if not isinstance(configured, dict) or not isinstance(submitted, dict):
        raise ValueError('Pulse arguments must be an object.')
    if not isinstance(text, str) or len(text) > 2000:
        raise ValueError('Message must contain at most 2,000 characters.')
    schema = schema or {'type': 'object'}
    properties = schema.get('properties', {}) if isinstance(schema, dict) else {}
    args = {**deepcopy(configured), **deepcopy(config.get('arguments', {})), **deepcopy(submitted)}
    for key, definition in properties.items():
        if key not in args and isinstance(definition, dict) and 'default' in definition and not SECRET.search(key):
            args[key] = deepcopy(definition['default'])
    messages = [{'role': m['role'], 'content': (m['text'] if isinstance(m['text'], str) else json.dumps(m['text'], ensure_ascii=False))[:8000]}
                for m in list(history)[-6:] if m.get('role') in ('user', 'assistant')]
    field = message_field(schema, config)
    if text.strip():
        if not field:
            raise ValueError('This Pulse has no declared message input; use its argument fields.')
        args[field] = messages + [{'role': 'user', 'content': text}] if properties[field].get('type') == 'array' else text
    for option, value in (('historyField', messages), ('contextField', context or {})):
        if config.get(option):
            if config[option] not in properties:
                raise ValueError('The configured ' + option + ' is not declared by this Pulse.')
            args[config[option]] = deepcopy(value)
    safe_arguments(args)
    if len(json.dumps(args, ensure_ascii=False, allow_nan=False)) > 20000:
        raise ValueError('Pulse arguments exceed 20,000 characters.')
    return validate_arguments(schema, args)


def validate_defaults(schema, arguments):
    """Validate supplied editor defaults; complete requests are checked on submit.

    Missing root inputs and cross-field alternatives are deliberately deferred.
    A configured property must still satisfy its declared value schema.
    """
    if not isinstance(arguments, dict):
        raise ValueError('Pulse arguments must be an object.')
    safe_arguments(arguments)
    if len(json.dumps(arguments, ensure_ascii=False, allow_nan=False)) > 20000:
        raise ValueError('Pulse arguments exceed 20,000 characters.')
    try:
        from .schema_security import check_schema
        check_schema(schema)
    except Exception:
        raise ValueError('Publisher input schema is invalid.') from None
    properties = schema.get('properties', {})
    for key, value in arguments.items():
        if key in properties:
            # Preserve local definitions without allowing network schema retrieval.
            contract = {'type':'object', 'properties':{key:properties[key]}, **{k:deepcopy(schema[k]) for k in ('$defs','definitions') if k in schema}}
            validate_arguments(contract, {key:value})
        elif schema.get('additionalProperties') is False:
            raise ValueError('Pulse arguments: undeclared field ' + key)
    return arguments
