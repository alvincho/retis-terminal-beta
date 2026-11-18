"""Fugle WebSocket channels relayed as Plaza-scoped SSE, for operator-owned data only.

Users' Fugle keys stay in their own Terminal, which calls Fugle directly; this
source never accepts one. The relay runs only with the operator's own key, and
only when the source's operator owns the data it serves. The key is never
included in a Pulse, event, URL, log, or channel identity.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
import hashlib
import json
import os
import re
import secrets
import ssl
import time
from typing import Any, Mapping

from attas.data_agent_network.canonical.stream import model_stream_event

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse

from attas.data_agent_network.scaffold.taiwan import FUGLE_STREAM_CAPABILITY, FUGLE_STREAM_SCHEMAS


PATH = str(FUGLE_STREAM_CAPABILITY["path"])
CAPABILITY = dict(FUGLE_STREAM_CAPABILITY)


def stream_symbols(value: Any, *, maximum: int = 5) -> list[str]:
    values = value.split(",") if isinstance(value, str) else value
    if not isinstance(values, list) or not 1 <= len(values) <= maximum:
        raise HTTPException(422, f"Subscribe to between 1 and {maximum} Fugle symbols.")
    if any(not isinstance(item, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+:-]{0,39}", item) for item in values):
        raise HTTPException(422, "Invalid Fugle symbol.")
    return list(dict.fromkeys(item.upper() for item in values))


def _observed_at(value: Any) -> str | None:
    try:
        timestamp = float(value)
        if timestamp > 10_000_000_000_000:
            timestamp /= 1_000_000
        elif timestamp > 10_000_000_000:
            timestamp /= 1_000
        return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def normalize_fugle_row(data: Mapping[str, Any], schema: str) -> dict[str, Any]:
    """Map aggregate quotes canonically and preserve documented channel fields."""
    symbol = str(data.get("symbol") or "")
    last_trade = data.get("lastTrade") if isinstance(data.get("lastTrade"), Mapping) else {}
    observed = _observed_at(data.get("lastUpdated") or data.get("time") or last_trade.get("time"))
    if schema == "market.quotes.v1":
        total = data.get("total") if isinstance(data.get("total"), Mapping) else {}
        return {
            "ticker": symbol,
            "last": data.get("lastPrice", data.get("closePrice")),
            "priceKind": "trial" if data.get("isTrial") else "close" if data.get("isClose") else "trade",
            "volumeUnit":"provider-unspecified", "volumeScope":"session-cumulative",
            "sourceUpdatedAt":observed,
            "changePercent": data.get("changePercent"),
            "volume": total.get("tradeVolume", data.get("volume")),
            "observedAt": _observed_at(((data.get("lastTrial") if data.get("isTrial") else last_trade) or {}).get("time")),
            "currency": "TWD",
            "exchange": data.get("exchange"),
            "market": data.get("market"),
        }
    return {"ticker": symbol, "observedAt": _observed_at(((data.get("lastTrial") if data.get("isTrial") else last_trade) or {}).get("time")), **dict(data)}


async def fugle_events(
    symbols: list[str],
    schema: str,
    api_key: str,
    sequence: int | None = None,
    connector: Any = None,
):
    """Yield normalized snapshots from one documented Fugle channel."""
    definition = FUGLE_STREAM_SCHEMAS.get(schema)
    if not definition:
        raise ValueError("Unsupported Fugle streaming schema.")
    product = definition["product"]
    channel = definition["channel"]
    symbols = stream_symbols(symbols)
    aliases = {re.sub(r'\.(TW|TWO)$','',symbol) if product=='stock' else symbol: symbol for symbol in symbols}
    if len(aliases)!=len(symbols):
        raise ValueError('Ambiguous Fugle symbols after exchange suffix mapping.')
    version = "v1.1" if product == "futopt" else "v1.0"
    url = f"wss://api.fugle.tw/marketdata/{version}/{product}/streaming"
    if connector is None:
        import certifi
        from websockets.asyncio.client import connect
        class PinnedConnect(connect):
            def process_redirect(self, exc):
                return exc
        def connector(url, **options):
            return PinnedConnect(url, ssl=ssl.create_default_context(cafile=certifi.where()), proxy=None, **options)
    rows: dict[str, dict[str, Any]] = {symbol:{'ticker':symbol,'last':None,'observedAt':None} for symbol in symbols}
    def public_value(value):
        if isinstance(value,dict):
            return {key:public_value(item) for key,item in value.items() if str(key).lower() not in ('api_key','apikey','token','authorization','secret','password')}
        if isinstance(value,list):return [public_value(item) for item in value]
        if isinstance(value,str) and api_key:return value.replace(api_key,'[redacted]')
        return value
    count = sequence or 0
    stream_id = uuid.uuid4().hex
    async with connector(url, max_size=500_000, open_timeout=15, ping_interval=20, ping_timeout=20) as socket:
        await socket.send(json.dumps({"event": "auth", "data": {"apikey": api_key}}))
        authenticated = False
        for _ in range(20):
            raw = await asyncio.wait_for(socket.recv(), 15)
            if len(raw) > 500_000:
                raise ValueError("Oversized Fugle message.")
            message = json.loads(raw)
            if message.get("event") == "authenticated":
                authenticated = True
                break
            if message.get("event") == "error":
                raise PermissionError("Fugle rejected the API key or streaming entitlement.")
        if not authenticated:
            raise TimeoutError("Fugle streaming authentication timed out.")
        for symbol in aliases:
            await socket.send(json.dumps({"event": "subscribe", "data": {"channel": channel, "symbol": symbol}}))
        count += 1
        yield model_stream_event('fugle', {"sequence": count, "rows": list(rows.values()), "status": "waiting", "snapshot": True}, schema, stream_id=stream_id)
        async for raw in socket:
            if len(raw) > 500_000:
                raise ValueError("Oversized Fugle message.")
            message = json.loads(raw)
            if message.get("event") == "error":
                raise ConnectionError("Fugle rejected the subscription or disconnected the channel.")
            if message.get("event") != "data" or message.get("channel") != channel:
                continue
            data = message.get("data")
            if not isinstance(data, Mapping) or str(data.get("symbol") or "").upper() not in aliases:
                continue
            row = public_value(normalize_fugle_row(data, schema))
            requested = aliases[str(data['symbol']).upper()]
            row['ticker'] = requested
            rows[requested] = row
            count += 1
            yield model_stream_event('fugle', {"sequence": count, "rows": [rows[key] for key in symbols if key in rows], "status": "live", "snapshot": True}, schema, stream_id=stream_id)


def install_fugle_stream_route(
    app,
    plaza_url: str,
    hub,
    connector=fugle_events,
    client_factory=httpx.AsyncClient,
    auth_interval: int = 30,
    agent=None,
):
    """Install the trusted SSE relay on a Fugle Data Source agent."""

    async def authenticated_owner(token):
        from prompits.core.call_grants import PREFIX,stream_user
        if token.startswith(PREFIX):
            if agent is None:raise HTTPException(403,'Scoped stream transport is not installed.')
            return (await stream_user(agent,token))['id']
        # A slow attached Plaza must not prevent a valid primary Plaza login.
        # Race only deployment-trusted origins; credentials never go elsewhere.
        origins=list(dict.fromkeys(p.strip().rstrip('/') for p in [plaza_url,*os.getenv('PROMPITS_TRUSTED_PLAZA_URLS','').split(',')] if p.strip()))
        async with client_factory(timeout=5) as client:
            async def check(origin):
                try:
                    response=await client.get(origin+'/api/ui_auth/me',headers={'Authorization':'Bearer '+token})
                    owner=response.json().get('user',{}).get('id') if response.is_success else None
                    return owner,response.status_code>=500
                except (httpx.HTTPError,ValueError):return None,True
            tasks=[asyncio.create_task(check(origin)) for origin in origins]
            unavailable=False
            try:
                for task in asyncio.as_completed(tasks):
                    owner,failed=await task;unavailable=unavailable or failed
                    if owner:return str(owner)
            finally:
                for task in tasks:
                    if not task.done():task.cancel()
                await asyncio.gather(*tasks,return_exceptions=True)
        if unavailable:raise HTTPException(503,'Plaza login verification is temporarily unavailable; retry later.')
        raise HTTPException(401,'Plaza login expired.')

    @app.get(PATH)
    async def stream(request: Request, schema: str = "", symbols: str = ""):
        if schema not in FUGLE_STREAM_SCHEMAS:
            raise HTTPException(422, "Unsupported Fugle streaming schema.")
        tickers = stream_symbols(symbols, maximum=int(CAPABILITY["maxSymbols"]))
        token = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        if not token:
            raise HTTPException(401, "Plaza login required.")
        if request.headers.get("x-attas-vendor-key"):
            raise HTTPException(400, "Users' keys stay in their Terminal, which calls Fugle directly.")
        operator_owned = bool(agent is not None and getattr(agent, "_operator_owned_data", lambda: False)())
        api_key = os.getenv("FUGLE_MARKETDATA_API_KEY", "").strip() if operator_owned else ""
        if len(api_key) < 4 or len(api_key) > 4096:
            raise HTTPException(409, "Connect your own Fugle key in your Terminal; it calls Fugle directly.")

        async def authorize() -> str:
            return await authenticated_owner(token)

        owner = await authorize()
        scope = request.headers.get('x-attas-credential-scope') or secrets.token_hex(16)
        if not re.fullmatch(r'[A-Za-z0-9:_-]{1,128}',scope):
            raise HTTPException(422,'Invalid opaque credential scope.')
        entitlement = (hashlib.sha256(token.encode()).hexdigest(), scope)
        binding = (schema, *tickers)
        active_symbols = {symbol for (account, credentials, subscribed) in hub.channels if account==owner and credentials==entitlement for symbol in subscribed[1:]}
        if len(active_symbols | set(tickers)) > int(CAPABILITY['maxSymbols']):
            raise HTTPException(429,'This Fugle connection already uses the five-symbol allowance. Close another subscription first.')
        if (owner, entitlement, binding) not in hub.channels and len(hub.channels) >= 64:
            raise HTTPException(429, "Live subscription limit reached.")

        async def events():
            checked = time.monotonic()
            async with hub.subscribe(
                owner,
                entitlement,
                binding,
                lambda last: connector(tickers, schema, api_key, last),
            ) as queue:
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
                        yield "data: " + json.dumps(event, allow_nan=False) + "\n\n"
                    except asyncio.TimeoutError:
                        yield ": heartbeat\n\n"

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )


__all__ = [
    "CAPABILITY",
    "PATH",
    "fugle_events",
    "install_fugle_stream_route",
    "normalize_fugle_row",
    "stream_symbols",
]
