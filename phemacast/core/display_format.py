"""Display formats: the viewer's global style and a Phema field's declared kind.

Presentation preferences only; they never change values, units, requests or execution policy.
phemacast/web/value-format.js applies them.
"""

# percent is in percentage points (1 = 1%); ratio is a fraction shown as a percentage (0.1 = 10%).
KINDS = ('datetime', 'date', 'time', 'amount', 'percent', 'ratio', 'count', 'number', 'text')
DEFAULT = {'dateTime': 'compact', 'clock': '24h', 'decimals': 2, 'largeNumbers': 'abbreviated', 'signed': True}
EXTENSION = 'phemacast:fieldFormats'


def validate_display_format(value):
    choices = {'dateTime': ('compact', 'full'), 'clock': ('24h', '12h'), 'largeNumbers': ('full', 'abbreviated')}
    if not isinstance(value, dict) or set(value) - set(DEFAULT):
        raise ValueError('Invalid display format.')
    for key, allowed in choices.items():
        if key in value and value[key] not in allowed:
            raise ValueError('Invalid display format.')
    if 'decimals' in value and (type(value['decimals']) is not int or not 0 <= value['decimals'] <= 4):
        raise ValueError('Decimals must be 0–4.')
    if 'signed' in value and type(value['signed']) is not bool:
        raise ValueError('Invalid display format.')


def validate_field_formats(value):
    """A panel's formats by field: {field: {kind, decimals?, signed?, dateTime?}}."""
    if not isinstance(value, dict) or len(value) > 100:
        raise ValueError('Field formats name at most 100 fields.')
    for field, spec in value.items():
        if not isinstance(field, str) or not 1 <= len(field) <= 200 or not isinstance(spec, dict) \
                or set(spec) - {'kind', 'decimals', 'signed', 'dateTime'} or spec.get('kind') not in KINDS:
            raise ValueError('Each field format names a kind: ' + ', '.join(KINDS) + '.')
        if 'decimals' in spec and (type(spec['decimals']) is not int or not 0 <= spec['decimals'] <= 8):
            raise ValueError('Field decimals must be 0–8.')
        if 'signed' in spec and type(spec['signed']) is not bool:
            raise ValueError('A field format sign is true or false.')
        if 'dateTime' in spec and spec['dateTime'] not in ('compact', 'full'):
            raise ValueError('A field date style is compact or full.')
