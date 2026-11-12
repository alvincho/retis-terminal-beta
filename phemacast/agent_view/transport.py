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
