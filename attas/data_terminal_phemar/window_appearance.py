"""A Terminal tab's own look: its custom theme colors and its session name."""
import re

from fastapi import HTTPException

COLOR = re.compile(r'#[0-9a-fA-F]{6}')
COLORS = ('accent', 'background', 'surface', 'text')


def validate_custom_theme(value):
    if (not isinstance(value, dict) or set(value) != {'base', *COLORS} or value['base'] not in ('dark', 'light')
            or not all(isinstance(value[key], str) and COLOR.fullmatch(value[key]) for key in COLORS)):
        raise HTTPException(422, 'Choose a dark or light base and four #rrggbb colors.')


def session_name(value):
    """The name shown in this tab's title bar and browser tab; empty clears it."""
    if not isinstance(value, str):
        raise HTTPException(422, 'Enter a session name.')
    value = value.strip()
    if len(value) > 60 or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise HTTPException(422, 'Use up to 60 characters for the session name.')
    return value
