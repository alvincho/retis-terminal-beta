"""Opt-in pinned-origin transport. No registry declarations become URL authority."""
from urllib.parse import urlsplit
import hashlib
import httpx
from .contracts import origin, verify_assets


class PinnedPublisherTransport:
    """Construct from authenticated discovery/pairing, never the mounted frame.

    DNS/TLS peer policy for Internet or private LAN targets belongs to the existing
    generic discovery transport. A deployment must supply and audit that policy;
    HTTP fixture tests cannot establish certificate or DNS rebinding acceptance.
    """
    def __init__(self, agent_id, accepted_origin, *, client=None, allow_local=False):
        if not agent_id:raise PermissionError('Verified publisher identity required.')
        self.agent_id=agent_id;self.origin=origin(accepted_origin,allow_local=allow_local)
        self.owned_client=client is None
        self.client=client or httpx.AsyncClient(timeout=15,follow_redirects=False,trust_env=False)

    async def manifest(self,path,limit):
        """The manifest bytes at an owner-relative path, at most `limit` bytes."""
        response=await self.client.get(self.target(path),timeout=10,follow_redirects=False);response.raise_for_status()
        if len(response.content)>limit:raise PermissionError('Manifest exceeds its limit.')
        return response.content

    def target(self,path):
        if not isinstance(path,str) or not path.startswith('/') or path.startswith('//') or any(x in path for x in ('..','\\','%','#','?')):
            raise ValueError('Pinned owner-relative path required.')
        return self.origin+path

    async def assets(self,manifest):
        if manifest['publisher']['agentId']!=self.agent_id or manifest['origin']!=self.origin:raise PermissionError('Manifest origin does not match authenticated discovery.')
        assets={}
        for name,descriptor in manifest['assets'].items():
            data=bytearray()
            async with self.client.stream('GET',self.target(descriptor['path']),headers={'Accept':'application/octet-stream'},follow_redirects=False) as response:
                if response.status_code!=200:raise ValueError('Publisher asset failed without redirect/fallback.')
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data)>descriptor['bytes']:raise ValueError('Owner asset exceeds its declared byte limit.')
            assets[name]=bytes(data)
        verify_assets(manifest,assets);return assets

    async def invoke(self,accepted_origin,wire,grant):
        if accepted_origin!=self.origin:raise PermissionError('Publisher transport origin mismatch.')
        token=grant.get('token','')
        if not isinstance(token,str) or not token.startswith('pc-call-'):raise PermissionError('Only scoped call grants are accepted.')
        params=wire.get('query','');url=self.target(wire['path'])+('?' + params if params else '')
        headers={'Authorization':'Bearer '+token,'Accept':'application/json'}
        # Revision/idempotency are bound in schema-declared input fields; broad
        # user/session/vendor credentials are never accepted by this adapter.
        data=bytearray()
        async with self.client.stream(wire['method'],url,headers=headers,
            **({'json':wire['body']} if wire['body'] is not None else {}),follow_redirects=False) as response:
            if not 200<=response.status_code<300:raise ValueError('Owner operation rejected; inspect status without replay.')
            async for chunk in response.aiter_bytes():
                data.extend(chunk)
                if len(data)>300_000:raise ValueError('Owner result exceeds its limit.')
        import json
        return json.loads(data)

    async def close(self):
        if self.owned_client:await self.client.aclose()


class LocalPublisherTransport:
    """A local resource's view: manifest and assets read from the program on this computer (127.0.0.1).

    `clients` is the Terminal's local-resource client; `origin` the resource's loopback origin, which the manifest
    must name. A local publisher's view reaches its agent only through composition references, so it has no
    operations and no Plaza call grants; `invoke` refuses.
    """
    def __init__(self,agent_id,local_id,clients,accepted_origin):
        if not agent_id or not local_id:raise PermissionError('A local resource on this computer is required.')
        self.agent_id,self.local_id,self.clients=agent_id,local_id,clients
        self.origin=origin(accepted_origin,allow_local=True)

    def _path(self,path):
        if not isinstance(path,str) or not path.startswith('/local/v2/views/'):raise ValueError('Local view files live under /local/v2/views/.')
        return path

    async def manifest(self,path,limit):
        return await self.clients.view_file(self.local_id,self._path(path),limit)

    async def assets(self,manifest):
        if manifest['publisher']['agentId']!=self.agent_id or manifest['origin']!=self.origin:raise PermissionError('Manifest origin does not match the local resource.')
        assets={name:await self.clients.view_file(self.local_id,self._path(descriptor['path']),descriptor['bytes'])
                for name,descriptor in manifest['assets'].items()}
        verify_assets(manifest,assets);return assets

    async def invoke(self,accepted_origin,wire,grant):
        raise PermissionError('A local publisher\'s view calls its agent only through composition references.')

    async def close(self):
        pass
