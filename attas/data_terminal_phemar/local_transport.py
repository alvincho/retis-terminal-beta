"""Explicit Mac-local source routes alongside a cloud/proxied Plaza connection."""
import os
from urllib.parse import urlsplit

import httpx


ENVIRONMENT_KEY = 'ATTAS_LOCAL_SOURCE_ORIGINS'


def local_source_origins(value=None, *, plaza_url=None):
    """Read host configuration, never a layout, directory card or Browser input.

    An explicit port is required: bypassing all localhost traffic would also
    bypass the SSH proxy for cloud agents advertised at loopback addresses.
    """
    if value is None:
        value = os.environ.get(ENVIRONMENT_KEY, '')
    items = value.split(',') if isinstance(value, str) else value
    origins = []
    for item in items:
        if not isinstance(item, str):
            raise ValueError('Local source origins must be loopback HTTP(S) origins with explicit ports.')
        origin = item.strip().rstrip('/')
        if not origin:
            continue
        parsed = urlsplit(origin)
        if (parsed.scheme not in ('http', 'https') or
                parsed.hostname not in ('127.0.0.1', 'localhost', '::1') or
                not parsed.port or parsed.username is not None or parsed.password is not None or
                parsed.path or parsed.query or parsed.fragment):
            raise ValueError('Local source origins must be loopback HTTP(S) origins with explicit ports.')
        # HTTPX normalizes :80/:443 to an unqualified host mount, which would
        # bypass the proxy on *every* port of that host. Require a dedicated API
        # port instead; unrelated cloud agents must retain their proxy route.
        if parsed.port == (80 if parsed.scheme == 'http' else 443):
            raise ValueError('A local source must use a dedicated, non-default API port.')
        if plaza_url:
            plaza = urlsplit(plaza_url)
            if plaza.hostname in ('127.0.0.1', 'localhost', '::1') and parsed.port == plaza.port:
                raise ValueError('A local source must not replace the Plaza connection.')
        # Normalization matches the registered source origin used for trust.
        host = '[::1]' if parsed.hostname == '::1' else parsed.hostname
        origin = f'{parsed.scheme}://{host}:{parsed.port}'
        if origin not in origins:
            origins.append(origin)
    return tuple(origins)


def create_http_client(*, plaza_url, base_url=None):
    origins = local_source_origins(plaza_url=plaza_url)
    from .public_transport import load_connection, PublicNetworkTransport
    connection = load_connection()
    if connection:
        transport = PublicNetworkTransport(connection,local_origins=origins)
        client = httpx.AsyncClient(base_url=base_url or '',timeout=httpx.Timeout(15),follow_redirects=False,trust_env=False,transport=transport)
        client.public_network = transport
        return client
    # Exact scheme/host/port mounts override environment proxies only for the
    # accepted local APIs. The same transport handles metadata, Pulse calls,
    # canonical fetches and streams, including callers outside Network.
    mounts = {origin: httpx.AsyncHTTPTransport(trust_env=False) for origin in origins}
    return httpx.AsyncClient(base_url=base_url or '',timeout=httpx.Timeout(15), follow_redirects=False, mounts=mounts)
