"""The one request every component answers about itself, and the manager's probe for it.

A component serves `GET <health_path>` (default `/health`) on 127.0.0.1 at its port and answers with
`health_payload(...)`::

    {"schema": "prompits.component-health/1", "up": true, "ready": false, "version": "1.2.0",
     "reason": "downloading data", "needs": ["data"]}

`needs` lists what the user still has to provide before the component is ready, using short codes the
manager understands: `data` (a download the component offers). Other codes are shown as reasons.
"""
from __future__ import annotations

import asyncio
import json
import socket
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Iterable

SCHEMA = "prompits.component-health/1"


def health_payload(*, ready: bool, version: str, up: bool = True, reason: str = "",
                   needs: Iterable[str] = ()) -> dict:
    return {"schema": SCHEMA, "up": bool(up), "ready": bool(ready), "version": str(version),
            "reason": str(reason), "needs": [str(item) for item in needs]}


@dataclass(frozen=True)
class HealthReport:
    reachable: bool
    up: bool = False
    ready: bool = False
    version: str = ""
    reason: str = ""
    needs: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict:
        return {"reachable": self.reachable, "up": self.up, "ready": self.ready, "version": self.version,
                "reason": self.reason, "needs": list(self.needs)}


def port_open(port: int, *, host: str = "127.0.0.1", timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


_LOCAL = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def probe(port: int, path: str = "/health", *, timeout: float = 2.0) -> HealthReport:
    """Ask the component on 127.0.0.1:`port` how it is. A closed port or a bad answer is a report, not an error."""
    if not port_open(port, timeout=min(timeout, 0.5)):
        return HealthReport(reachable=False, reason="port closed")
    try:
        with _LOCAL.open(f"http://127.0.0.1:{port}{path}", timeout=timeout) as response:
            body = json.loads(response.read(64 * 1024).decode("utf-8"))
    except Exception as error:  # an unanswered or malformed check is reported, never raised
        return HealthReport(reachable=True, reason=f"health check failed: {type(error).__name__}")
    if not isinstance(body, dict) or body.get("schema") != SCHEMA:
        return HealthReport(reachable=True, reason="health check answered in an unknown format")
    needs = body.get("needs") if isinstance(body.get("needs"), list) else []
    return HealthReport(reachable=True, up=bool(body.get("up")), ready=bool(body.get("ready")),
                        version=str(body.get("version") or ""), reason=str(body.get("reason") or ""),
                        needs=tuple(str(item) for item in needs))


async def serve_health(port: int, report: Callable[[], dict], *, path: str = "/health", host: str = "127.0.0.1"):
    """Answer `GET <path>` on 127.0.0.1:`port` with `report()` (a `health_payload`); return the asyncio server.

    It reads one request line and its headers, runs `report` off the event loop, answers and closes.
    Anything else gets 404. It listens on loopback only, for the manager on this computer.
    """
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            line = await asyncio.wait_for(reader.readline(), 5)
            for _ in range(100):
                header = await asyncio.wait_for(reader.readline(), 5)
                if header in (b"\r\n", b"\n", b""):
                    break
            parts = line.decode("latin-1").split()
            if len(parts) >= 2 and parts[0] == "GET" and parts[1] == path:
                try:
                    body, status = json.dumps(await asyncio.to_thread(report)).encode(), "200 OK"
                except Exception as error:  # a failing report is itself the health answer
                    body, status = json.dumps(health_payload(ready=False, version="", up=False, reason=type(error).__name__)).encode(), "200 OK"
            else:
                body, status = b"{}", "404 Not Found"
            writer.write(f"HTTP/1.1 {status}\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\n"
                         f"Cache-Control: no-store\r\nConnection: close\r\n\r\n".encode() + body)
            await writer.drain()
        except (asyncio.TimeoutError, ConnectionError):
            pass
        finally:
            writer.close()

    return await asyncio.start_server(handle, host, port)
