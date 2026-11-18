"""Validate portable UI catalogs without changing source content or contracts."""
import re


def validate_ui_catalog(catalog):
    if catalog is None:
        return
    for key, values in catalog['messages'].items():
        expected = sorted(re.findall(r'\{\d+\}', key))
        for text in values.values():
            if sorted(re.findall(r'\{\d+\}', text)) != expected:
                raise ValueError('Phema UI translation placeholders must match the message key.')
