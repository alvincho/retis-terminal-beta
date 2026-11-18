"""Yahoo live quotes via yfinance, exposed by the existing source over SSE.

The wire fields are an explicit vendor mapping. Sequence numbers count adapter
events, not exchange ticks; Yahoo offers no replay or completeness guarantee.
"""
import asyncio
import uuid
import hashlib
import json
import math
import re
import ssl
import time
from datetime import datetime, timezone

from attas.data_agent_network.canonical.stream import model_stream_event

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse
from attas.data_agent_network.scaffold.sources import YFinanceDataSource
from .stream_hub import StreamHub

SCHEMA = 'market.quotes.v1'
PATH = '/api/streams/yfinance/quotes'
FIELDS = ['ticker', 'last', 'changePercent', 'volume', 'observedAt', 'currency']
CAPABILITY = {'path': PATH, 'schemas': [SCHEMA], 'cost': 0, 'currency': 'plaza_point',
              'transport': 'sse', 'fields': FIELDS, 'replay': False,
              'license': 'Yahoo Finance personal use', 'maxSymbols': 100}
ROW_SCHEMA = {'type': 'object', 'properties': {
    key: {'type': ['number', 'null'] if key in ('last', 'changePercent', 'volume') else ['string', 'null']}
    for key in FIELDS}}
SPEC = {'endpoint_id': 'yfinance.live.quotes', 'operation': 'yfinance.live.quotes',
        'name': 'Yahoo live price stream', 'description': 'Continuous Yahoo price messages via yfinance. No tick replay; missing fields remain unknown.',
        'semantic_schema_id': SCHEMA, 'canonical_response_schema': {'type': 'array', 'items': ROW_SCHEMA},
        'mode': 'stream', 'transport': 'sse', 'executable': True,
        'authentication': {'required': False, 'plaza_login_required': True},
        'cost': {'amount': 0, 'currency': 'plaza_point', 'unit': 'subscription'},
        'license': CAPABILITY['license'], 'input_schema': {'type': 'object', 'additionalProperties': False},
        'documentation_url': 'https://ranaroussi.github.io/yfinance/reference/api/yfinance.AsyncWebSocket.html'}


def symbols(value):
    values = value.split(',') if isinstance(value, str) else value
    if not isinstance(values, list) or not 1 <= len(values) <= 100:
        raise HTTPException(422, 'Subscribe to between 1 and 100 Yahoo tickers.')
    if any(not isinstance(v, str) or not re.fullmatch(r'[A-Za-z0-9^][A-Za-z0-9.^=_-]{0,39}', v) for v in values):
        raise HTTPException(422, 'Invalid Yahoo ticker.')
    return sorted(set(v.upper() for v in values))


def quote(message):
    def number(key):
        value = message.get(key)
        try:
            return float(value) if not isinstance(value, bool) and value is not None and math.isfinite(float(value)) else None
        except (ValueError, TypeError):
            return None
    timestamp = number('time')
    try:
        observed = datetime.fromtimestamp(timestamp / 1000, timezone.utc).isoformat() if timestamp is not None else None
    except (ValueError, OverflowError, OSError):
        observed = None
    return {'ticker': message.get('id'), 'last': number('price'), 'priceKind':'provider-price', 'volumeUnit':'provider-unspecified', 'volumeScope':'session-cumulative', 'changePercent': number('change_percent'),
            'volume': number('day_volume'), 'observedAt': observed, 'currency': message.get('currency') or None,
            'exchange': message.get('exchange') or None, 'marketHours': message.get('market_hours')}


async def yahoo_events(tickers, sequence=None, factory=None):
    if factory is None:
        import certifi
        from yfinance import AsyncWebSocket
        from websockets.asyncio.client import connect
        class VerifiedWebSocket(AsyncWebSocket):
            async def _connect(self):
                if self._ws is None:
                    self._ws = await connect(self.url, ssl=ssl.create_default_context(cafile=certifi.where()), max_size=500_000, open_timeout=15)
            async def close(self):
                await super().close()
                if self._heartbeat_task:
                    await asyncio.gather(self._heartbeat_task, return_exceptions=True)
        factory = VerifiedWebSocket
    tickers = symbols(tickers)
    rows = {ticker: {'ticker': ticker, **{key: None for key in FIELDS if key != 'ticker'}} for ticker in tickers}
    count = sequence or 0
    stream_id = uuid.uuid4().hex
    async with factory(verbose=False) as socket:
        await socket.subscribe(tickers)
        # yfinance 1.2 listen() reuses a closed socket on reconnect. Read the
        # subscribed transport here so EOF/errors unwind and StreamHub creates
        # a fresh client. The library still owns subscription/protobuf decoding.
        count += 1
        yield model_stream_event('yfinance', {'sequence': count, 'rows': list(rows.values()), 'status': 'waiting', 'snapshot': True}, SCHEMA, stream_id=stream_id)
        async for raw in socket._ws:
            if len(raw) > 500_000:
                raise ValueError('Oversized Yahoo message.')
            envelope = json.loads(raw)
            message = socket._decode_message(envelope.get('message', ''))
            if not isinstance(message, dict) or message.get('id') not in rows:
                continue
            row = quote(message)
            previous = rows[row['ticker']]
            if previous.get('observedAt') and row.get('observedAt') and row['observedAt'] < previous['observedAt']:
                continue
            rows[row['ticker']] = row
            count += 1
            yield model_stream_event('yfinance', {'sequence': count, 'rows': list(rows.values()), 'status': 'live', 'snapshot': True}, SCHEMA, stream_id=stream_id)


def install_stream_route(app, plaza_url, hub, connector=yahoo_events, client_factory=httpx.AsyncClient, auth_interval=30, agent=None):
    @app.get(PATH)
    async def stream(request: Request, schema: str = '', symbols: str = ''):
        if schema != SCHEMA:
            raise HTTPException(422, 'Unsupported streaming schema.')
        tickers = globals()['symbols'](symbols)
        token = request.headers.get('authorization', '').removeprefix('Bearer ')
        if not token:
            raise HTTPException(401, 'Plaza login required.')
        async def authorize():
            from prompits.core.call_grants import PREFIX,stream_user
            if token.startswith(PREFIX):
                if not agent:raise HTTPException(403,'Scoped stream transport is not installed.')
                return (await stream_user(agent,token))['id']
            async with client_factory(timeout=15) as client:
                response = await client.get(str(plaza_url).rstrip('/') + '/api/ui_auth/me', headers={'Authorization': 'Bearer ' + token})
            if not response.is_success:
                raise HTTPException(401, 'Plaza login expired.')
            owner = response.json().get('user', {}).get('id')
            if not owner:
                raise HTTPException(401, 'Plaza login required.')
            return owner
        owner = await authorize()
        entitlement = hashlib.sha256(token.encode()).hexdigest()
        binding = tuple(tickers)
        if (owner, entitlement, binding) not in hub.channels and len(hub.channels) >= 64:
            raise HTTPException(429, 'Live subscription limit reached.')
        async def events():
            checked = time.monotonic()
            async with hub.subscribe(owner, entitlement, binding, lambda last: connector(tickers, last)) as queue:
                while True:
                    if time.monotonic() - checked >= auth_interval:
                        try:
                            if await authorize() != owner:
                                return
                        except Exception:
                            return
                        checked = time.monotonic()
                    try:
                        event = await asyncio.wait_for(queue.get(), 10)
                        yield 'data: ' + json.dumps(event, allow_nan=False) + '\n\n'
                    except asyncio.TimeoutError:
                        yield ': heartbeat\n\n'
        return StreamingResponse(events(), media_type='text/event-stream', headers={'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'})


class StreamingYFinanceDataSource(YFinanceDataSource):
    def additional_catalog_endpoints(self):
        return [dict(SPEC)]

    def source_profile(self):
        return {**super().source_profile(), 'streaming': dict(CAPABILITY)}

    def _setup_data_agent_routes(self):
        super()._setup_data_agent_routes()
        self.quote_streams = StreamHub(retry_seconds=3)
        install_stream_route(self.app, self.plaza_url, self.quote_streams,agent=self)
        self.app.router.add_event_handler('shutdown', self.quote_streams.close)

    def data_spec(self, input_data):
        if input_data.get('endpoint_id') == SPEC['endpoint_id']:
            return {'endpoints':[dict(SPEC)],'count':1,'total':1}
        # The base source returns only a limited first page. This source owns
        # offset pagination over its complete catalog plus the live contract.
        result = super().data_spec({**input_data, 'limit':500, 'offset':0})
        while len(result['endpoints']) < result.get('total', 0):
            page = super().data_spec({**input_data, 'limit':500, 'offset':len(result['endpoints'])})
            if not page.get('endpoints'):break
            result['endpoints'].extend(page['endpoints'])
        offset = int(input_data.get('offset') or 0)
        limit = max(1, min(int(input_data.get('limit') or 20),500))
        query = str(input_data.get('query') or '').lower()
        selected = input_data.get('endpoint_id')
        if not selected and (not query or query in json.dumps(SPEC).lower()):
            result['endpoints'] = [*result.get('endpoints', []), dict(SPEC)]
        result['total'] = len(result['endpoints'])
        result['endpoints'] = result['endpoints'][max(0,offset):max(0,offset)+limit]
        result['count'] = len(result['endpoints'])
        return result
