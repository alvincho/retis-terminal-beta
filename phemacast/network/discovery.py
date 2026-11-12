"""Bonjour announces availability, never trust or permission to execute."""
import asyncio
import re
import time

from .tls import interface_networks, lan_endpoint, pinned_context

SERVICE = '_phemacast._tcp.local.'
PROTOCOL = 'phemacast.local/1'


def decode_service(info, networks=None):
    properties = {k.decode('utf-8', 'strict'): v.decode('utf-8', 'strict') for k, v in info.properties.items() if isinstance(k, bytes) and isinstance(v, bytes)}
    if properties.get('protocol') != PROTOCOL or not re.fullmatch(r'[a-zA-Z0-9_-]{8,80}', properties.get('id', '')):
        raise ValueError('Unknown local resource protocol or identity.')
    label = properties.get('name', '')
    if not 1 <= len(label) <= 80 or any(ord(c) < 32 for c in label):
        raise ValueError('Invalid local resource name.')
    addresses = info.parsed_addresses()
    endpoints = []
    for address in addresses[:8]:
        host = '[' + address + ']' if ':' in address else address
        endpoint = lan_endpoint({'url': f'https://{host}:{info.port}', 'tlsFingerprintSha256': properties.get('pin', '')}, networks if networks is not None else interface_networks())
        if endpoint:endpoints.append(endpoint)
    if not endpoints:raise ValueError('Resource is not on an attached private subnet.')
    return {'id': properties['id'], 'name': label, 'kind': properties.get('kind', '')[:48], 'protocol': PROTOCOL, 'endpoints': endpoints}


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
                if len(self.rows) < 256 or name in self.rows:
                    self.rows[name] = (time.monotonic() + 120, row)
        except (ValueError, UnicodeError, OSError):
            self.rows.pop(name, None)

    def resources(self):
        now = time.monotonic()
        self.rows = {k: v for k, v in self.rows.items() if v[0] > now}
        return [row for _, row in self.rows.values()]

    async def rescan(self):
        # Browsing stays active. Resolve known services again to refresh TTLs, and
        # retry listed services that were unreachable so a restarted one returns.
        names = list(dict.fromkeys([*self.rows, *self.names]))[:32]
        await asyncio.gather(*(self.resolve(name) for name in names))
        async def reachable(name):
            row = self.rows.get(name)
            if not row:return
            for endpoint in row[1]['endpoints']:
                try:
                    await pinned_context(endpoint)  # No credentials are sent by the availability probe.
                    return
                except (OSError, ValueError, asyncio.TimeoutError):pass
            self.rows.pop(name, None)
        await asyncio.gather(*(reachable(name) for name in names))
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


class Advertisement:
    def __init__(self, config, endpoint):
        self.config, self.endpoint = config, endpoint
        self.zc = self.info = None

    async def start(self):
        from zeroconf.asyncio import AsyncZeroconf, AsyncServiceInfo
        config = self.config
        self.zc = AsyncZeroconf(interfaces=[config['address']])
        self.info = AsyncServiceInfo(SERVICE, config['resourceId'] + '.' + SERVICE,
            parsed_addresses=[config['address']], port=config['port'], server=config['resourceId'] + '.local.',
            properties={'id': config['resourceId'], 'name': config['name'], 'kind': config['kind'],
                        'protocol': PROTOCOL, 'pin': self.endpoint['tlsFingerprintSha256']})
        await self.zc.async_register_service(self.info)

    async def close(self):
        if self.zc:
            if self.info:await self.zc.async_unregister_service(self.info)
            await self.zc.async_close()
            self.zc = None
