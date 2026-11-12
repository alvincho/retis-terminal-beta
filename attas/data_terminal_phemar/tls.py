"""Certificate trust for the Terminal's own HTTPS clients.

They ignore the environment's proxy settings (trust_env=False), which also drops SSL_CERT_FILE. This keeps
the public certificate authorities and adds the one SSL_CERT_FILE names, so a computer can trust a site
whose certificate a local authority signed (the local dev site behind Caddy). Unset, nothing changes.
"""
import functools
import os
import ssl

import certifi


@functools.cache
def verify():
    context = ssl.create_default_context(cafile=certifi.where())
    if os.environ.get('SSL_CERT_FILE'):
        context.load_verify_locations(cafile=os.environ['SSL_CERT_FILE'])
    return context
