"""Generic quote practice for cascading agent cost estimates."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException
from starlette.concurrency import run_in_threadpool

from prompits.core.message import Message
from prompits.core.practice import Practice


class QuoteRequestPractice(Practice):
    """Expose `agent.quote_request()` as a mounted callable practice."""

    def __init__(self):
        super().__init__(
            name="Quote Request",
            description="Quote Plaza point cost for this agent request, including downstream dependencies.",
            id="quote_request",
            tags=["quote", "billing", "cascade"],
            examples=[
                "POST /quote_request {'practice_id': 'get_pulse_data', 'pulse_name': 'llm_chat', 'params': {}, 'ttl_seconds': 86400}"
            ],
            inputModes=["http-post", "json"],
            outputModes=["json"],
            parameters={
                "practice_id": {"type": "string", "description": "Practice to quote."},
                "pulse_name": {"type": "string", "description": "Optional pulse name for Pulser-compatible agents."},
                "pulse_address": {"type": "string", "description": "Optional pulse address for Pulser-compatible agents."},
                "params": {"type": "object", "description": "Request parameters."},
                "ttl_seconds": {"type": "integer", "description": "Caller-defined quote lifetime in seconds."},
            },
        )

    def mount(self, app):
        router = APIRouter()

        @router.post(self.path)
        async def quote_request(message: Message):
            content = message.content or {}
            if not isinstance(content, dict):
                raise HTTPException(status_code=400, detail="Quote content must be a JSON object.")
            return await run_in_threadpool(self.execute, **content)

        app.include_router(router)

    def execute(self, **kwargs: Any) -> Dict[str, Any]:
        if not self.agent:
            raise RuntimeError("QuoteRequestPractice is not bound to an agent.")
        quote_request = getattr(self.agent, "quote_request", None)
        if not callable(quote_request):
            raise RuntimeError("This agent does not implement quote_request().")
        return quote_request(**kwargs)
