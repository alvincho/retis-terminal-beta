"""Data a processor asks its caller's host to fetch, and the host's answer.

A processor (an agent that only computes) never fetches market data itself.
When an operation needs data it answers with a data request instead of a
result. The user's host fetches every item for that user, then calls the same
operation again with the answers as `supplied_data`. Hosts know data models and
sources, never the processor's business.

Three kinds of item exist:
- `model-data`: call one source pulse with exact parameters (for example a
  canonical model request); the answer's `payload` is the source-shaped result.
- `model-sources`: list the sources this user can draw the named canonical
  models from; the answer's `sources` describes each one.
- `llm-generation`: run `messages` with the user's own key for one LLM provider
  an agent publishes (`agentId`, `provider`, `model`, optional `maxTokens`); the
  answer's `payload` carries the generated `text`. The key never leaves the
  host. A processor that wants the site's model instead names that agent's
  `llm_chat` pulse in a `model-data` item.

An item may carry `echo`, an object the host returns unchanged with its answer.
"""
from __future__ import annotations

import copy
import json
import re

REQUEST = 'phemacast:data-request/1'
SUPPLIED = 'supplied_data'
MODEL_DATA = 'model-data'
MODEL_SOURCES = 'model-sources'
LLM_GENERATION = 'llm-generation'
MAX_MESSAGES = 16
MAX_MESSAGE_BYTES = 100_000
MAX_GENERATION_TOKENS = 4096
ROLES = ('system', 'user', 'assistant')
MAX_ITEMS = 16
MAX_ROUNDS = 8
MAX_SUPPLIED_BYTES = 6_000_000
IDENTIFIER = re.compile(r'[A-Za-z0-9_.:-]{1,128}')
NAME = re.compile(r'[A-Za-z0-9_.-]{1,120}')


def is_request(value):
    return isinstance(value, dict) and value.get('schema') == REQUEST


def checked_request(value):
    """A bounded, well-formed data request; anything else is refused."""
    if not is_request(value) or not isinstance(value.get('requests'), list):
        raise ValueError('Malformed data request.')
    items = value['requests']
    if not 1 <= len(items) <= MAX_ITEMS:
        raise ValueError(f'A data request names 1 to {MAX_ITEMS} items.')
    seen = set()
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get('id'), str) or not IDENTIFIER.fullmatch(item['id']) or item['id'] in seen:
            raise ValueError('Every requested item needs its own identifier.')
        seen.add(item['id'])
        if 'echo' in item and not isinstance(item['echo'], dict):
            raise ValueError('An echo is an object.')
        if item.get('kind') == MODEL_DATA:
            if (set(item) - {'id', 'kind', 'agentId', 'pulse', 'parameters', 'echo'}
                    or not isinstance(item.get('agentId'), str) or not item['agentId']
                    or not isinstance(item.get('pulse'), str) or not NAME.fullmatch(item['pulse'])
                    or not isinstance(item.get('parameters'), dict)):
                raise ValueError('A model-data item names one source agent, its pulse and parameters.')
        elif item.get('kind') == MODEL_SOURCES:
            models = item.get('models')
            if (set(item) - {'id', 'kind', 'models', 'echo'} or not isinstance(models, list) or not 1 <= len(models) <= 16
                    or any(not isinstance(m, str) or not NAME.fullmatch(m) for m in models)):
                raise ValueError('A model-sources item names 1 to 16 canonical models.')
        elif item.get('kind') == LLM_GENERATION:
            messages = item.get('messages')
            tokens = item.get('maxTokens', MAX_GENERATION_TOKENS)
            if (set(item) - {'id', 'kind', 'agentId', 'provider', 'model', 'messages', 'maxTokens', 'echo'}
                    or not isinstance(item.get('agentId'), str) or not item['agentId']
                    or not isinstance(item.get('provider'), str) or not NAME.fullmatch(item['provider'])
                    or not isinstance(item.get('model'), str) or not NAME.fullmatch(item['model'])
                    or type(tokens) is not int or not 1 <= tokens <= MAX_GENERATION_TOKENS
                    or not isinstance(messages, list) or not 1 <= len(messages) <= MAX_MESSAGES
                    or any(not isinstance(m, dict) or set(m) != {'role', 'content'} or m['role'] not in ROLES
                           or not isinstance(m['content'], str) or not m['content'].strip() for m in messages)
                    or len(json.dumps(messages, ensure_ascii=False).encode()) > MAX_MESSAGE_BYTES):
                raise ValueError('An llm-generation item names one LLM provider and model, and 1 to 16 messages under 100 KB.')
        else:
            raise ValueError('Unknown data request item.')
    return copy.deepcopy(items)


def answer(item, **fields):
    """One supplied answer, carrying the item's echo back."""
    return {**fields, **({'echo': copy.deepcopy(item['echo'])} if 'echo' in item else {})}


def bounded(supplied):
    size = len(json.dumps(supplied, separators=(',', ':'), default=str))
    if size > MAX_SUPPLIED_BYTES:
        raise ValueError('Requested data exceeds 6 MB; narrow the time range or interval.')
    return supplied
