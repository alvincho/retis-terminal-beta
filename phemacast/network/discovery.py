"""Bonjour finds the local resources running on this computer.

A resource announces `_phemacast._tcp` with its id, name, kind and this computer's addresses. A Terminal keeps an
advert only when one of those addresses is its own computer's, and always connects to `127.0.0.1:<port>`: a
resource listens on loopback only, so an advert from another computer (or a fake one) can point nowhere but at a
program on this computer, and the client checks that the program there answers with the advertised id.
"""
import asyncio
import ipaddress
import re
import time

SERVICE = '_phemacast._tcp.local.'
PROTOCOL = 'phemacast.local/2'
LOOPBACK = '127.0.0.1'


def own_addresses():
    from .service import host_addresses
    return host_addresses()


def decode_service(info, own=None):
    properties = {k.decode('utf-8', 'strict'): v.decode('utf-8', 'strict') for k, v in info.properties.items() if isinstance(k, bytes) and isinstance(v, bytes)}
    if properties.get('protocol') != PROTOCOL or not re.fullmatch(r'[a-zA-Z0-9_-]{8,80}', properties.get('id', '')):
        raise ValueError('Unknown local resource protocol or identity.')
    label = properties.get('name', '')
    if not 1 <= len(label) <= 80 or any(ord(c) < 32 for c in label):
        raise ValueError('Invalid local resource name.')
    if not 1024 <= int(info.port or 0) <= 65535:
        raise ValueError('Invalid local resource port.')
    own = own if own is not None else own_addresses()
    advertised = set()
    for address in info.parsed_addresses()[:16]:
        try:advertised.add(ipaddress.ip_address(address.split('%')[0]))
        except ValueError:pass
    if not advertised & set(own):
        raise ValueError('Resource runs on another computer.')
    return {'id': properties['id'], 'name': label, 'kind': properties.get('kind', '')[:48], 'protocol': PROTOCOL,
            'url': f'http://{LOOPBACK}:{int(info.port)}'}


async def answers(row, timeout=2.0):
    """The program at the row's address is the advertised resource (it answers its descriptor with the same id)."""
    import httpx
    try:
        async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=timeout) as client:
            response = await client.get(row['url'] + '/local/v2/resource')
            return response.status_code == 200 and response.json().get('id') == row['id']
    except (httpx.HTTPError, ValueError):
        return False


class Discovery:
    def __init__(self, *, interfaces=None):
        self.interfaces = interfaces
        self.rows, self.tasks = {}, set()
        # Every service Bonjour still lists. A row leaves `rows` while its resource
        # is unreachable, and Bonjour reports no change when an identical advert
        # returns, so rescans keep resolving these names to bring it back.
        self.names = set()
        self.status = 'stopped'
        self.zc = self.browser = self.maintenance = None

    async def start(self):
        try:
            from zeroconf import ServiceStateChange
            from zeroconf.asyncio import AsyncZeroconf, AsyncServiceBrowser
            self.zc = AsyncZeroconf(**({'interfaces': self.interfaces} if self.interfaces else {}))
            def change(zeroconf, service_type, name, state_change):
                if state_change == ServiceStateChange.Removed:
                    self.names.discard(name); self.rows.pop(name, None)
                elif len(self.tasks) < 32:
                    if len(self.names) < 256:self.names.add(name)
                    task = asyncio.create_task(self.resolve(name))
                    self.tasks.add(task); task.add_done_callback(self.tasks.discard)
            self.browser = AsyncServiceBrowser(self.zc.zeroconf, SERVICE, handlers=[change])
            self.status = 'discovering'
            async def maintain():
                while True:
                    await asyncio.sleep(30)
                    await self.rescan()
            self.maintenance = asyncio.create_task(maintain())
        except (ImportError, OSError):
            self.status = 'unavailable'
            await self.close()
            self.status = 'unavailable'

    async def resolve(self, name):
        try:
            info = await self.zc.async_get_service_info(SERVICE, name, timeout=2000)
            if info:
                row = decode_service(info)
                if (len(self.rows) < 256 or name in self.rows) and await answers(row):
                    self.rows[name] = (time.monotonic() + 120, row)
                else:
                    self.rows.pop(name, None)
        except (ValueError, UnicodeError, OSError):
            self.rows.pop(name, None)

    def resources(self):
        now = time.monotonic()
        self.rows = {k: v for k, v in self.rows.items() if v[0] > now}
        found = {}
        for _, row in self.rows.values():
            found.setdefault(row['id'], row)
        return list(found.values())

    async def rescan(self):
        # Browsing stays active. Resolve known services again to refresh TTLs; a resource that no longer answers
        # leaves the list, and one that returns is found again.
        names = list(dict.fromkeys([*self.rows, *self.names]))[:32]
        await asyncio.gather(*(self.resolve(name) for name in names))
        return self.resources()

    async def close(self):
        if self.maintenance:
            self.maintenance.cancel(); await asyncio.gather(self.maintenance, return_exceptions=True)
        if self.browser:await self.browser.async_cancel()
        for task in list(self.tasks):task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        if self.zc:await self.zc.async_close()
        self.browser = self.zc = None
        self.status = 'stopped'


def advertised_addresses():
    """This computer's addresses to announce: its private LAN addresses, or loopback when no network is up."""
    import socket
    import psutil
    found = []
    for values in psutil.net_if_addrs().values():
        for value in values:
            if value.family == socket.AF_INET:
                address = ipaddress.ip_address(value.address)
                if address.is_private and not address.is_loopback and not address.is_link_local:
                    found.append(value.address)
    return found or [LOOPBACK]


class Advertisement:
    def __init__(self, config):
        self.config = config
        self.zc = self.info = None

    async def start(self):
        from zeroconf.asyncio import AsyncZeroconf, AsyncServiceInfo
        config = self.config
        addresses = advertised_addresses()
        self.zc = AsyncZeroconf(interfaces=addresses)
        self.info = AsyncServiceInfo(SERVICE, config['resourceId'] + '.' + SERVICE,
            parsed_addresses=addresses, port=int(config['port']), server=config['resourceId'] + '.local.',
            properties={'id': config['resourceId'], 'name': config['name'], 'kind': config['kind'],
                        'protocol': PROTOCOL, 'scope': 'host'})
        await self.zc.async_register_service(self.info)

    async def close(self):
        if self.zc:
            if self.info:await self.zc.async_unregister_service(self.info)
            await self.zc.async_close()
            self.zc = None
