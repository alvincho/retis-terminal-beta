"""Source-scoped stream ownership, credential leases and explicit selection."""
from contextlib import asynccontextmanager
import os

from prompits.core.file_lock import try_exclusive, unlock  # fcntl alone has no Windows build

from .stream_hub import StreamHub
from .vault import AccessDenied
from .service import reject_secrets
from ..scaffold.taiwan import FUGLE_STREAM_SCHEMAS


class ProviderStreams:
    def __init__(self, service, *, connectors=None):
        self.service = service
        self.hub = StreamHub(retry_seconds=3)
        self.connectors = dict(connectors or {})
        self.leases = {}

    @asynccontextmanager
    async def subscribe(self, owner, window, body):
        reject_secrets(body)
        if set(body)-{'source_id','source_mode','connection_id','connection_revision','schema','symbols'}:
            raise ValueError('Unsupported stream fields.')
        record=self.service._record(owner,window,body)
        schema=body.get('schema')
        source=self.service.source_id
        policy=record.get('policy',{})
        if schema not in policy.get('stream_schemas',[]):
            raise AccessDenied('Streaming schema is outside the connection scope.')
        if source=='fugle':
            from .fugle import stream_symbols, fugle_events
            definition=FUGLE_STREAM_SCHEMAS.get(schema)
            if not definition or definition['product'] not in policy.get('products',[]):
                raise AccessDenied('Fugle product entitlement is unavailable.')
            tickers=stream_symbols(body.get('symbols'),maximum=min(5,policy.get('max_symbols',5)))
            if definition['product']=='stock' and definition['channel']!='indices':
                import re
                if any(not re.fullmatch(r'[0-9]{4,6}[A-Z]?(?:\.(?:TW|TWO))?',s) for s in tickers):
                    raise ValueError('Fugle stock streaming requires Taiwan stock codes.')
                native=[re.sub(r'\.(TW|TWO)$','',s) for s in tickers]
                if len(native)!=len(set(native)):
                    raise ValueError('Ambiguous Fugle stock aliases.')
            subscriptions={(definition['channel'],s) for s in tickers}
            if len(subscriptions)>min(5,policy.get('max_subscriptions',5)):
                raise AccessDenied('Fugle channel/symbol subscription quota exceeded.')
            connector=self.connectors.get(source,fugle_events)
            producer=lambda last:connector(tickers,schema,record['secret'],last)
        elif source=='yfinance':
            from .yfinance import symbols, yahoo_events
            tickers=symbols(body.get('symbols'))
            if len(tickers)>policy.get('max_symbols',100):
                raise AccessDenied('Yahoo symbol limit exceeded.')
            connector=self.connectors.get(source,yahoo_events)
            producer=lambda last:connector(tickers,last)
        else:
            raise ValueError('No managed streaming adapter is installed for this source.')
        fingerprint=self.service.vault.fingerprint({'credential':record['secret']})
        binding=(window,record['id'],record['revision'],schema,*sorted(tickers))
        lease=self.leases.get(fingerprint)
        if lease and lease['binding'] != (owner,binding):
            raise AccessDenied('This credential already has an upstream binding; close it before switching.')
        if lease is None:
            # Cross-process lock prevents a second source worker from bypassing
            # the one-connection limit. No secret or plain key digest in path.
            lock=self.service.vault.root/('stream-'+fingerprint+'.lock')
            fd=os.open(lock,os.O_RDWR|os.O_CREAT,0o600)
            try:
                try_exclusive(fd)
            except BlockingIOError:
                os.close(fd)
                raise AccessDenied('Source credential stream is already leased by another worker.') from None
            lease={'binding':(owner,binding),'users':0,'fd':fd}
            self.leases[fingerprint]=lease
        lease['users']+=1
        try:
            async with self.hub.subscribe(owner,fingerprint,binding,producer) as queue:
                yield queue
        finally:
            lease['users']-=1
            if not lease['users']:
                self.leases.pop(fingerprint,None)
                unlock(lease['fd'])
                os.close(lease['fd'])

    async def close(self):
        await self.hub.close()
