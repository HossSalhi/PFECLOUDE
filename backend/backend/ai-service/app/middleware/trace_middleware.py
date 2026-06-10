"""
Propagate X-Request-ID across every FastAPI request.

Reads the header from the incoming request (or generates a UUID if absent),
stores it in a context variable so any log line or outbound call can attach it,
and echoes it back in the response header.
"""
from __future__ import annotations

import uuid
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

_HEADER = "X-Request-ID"
request_id_var: ContextVar[str] = ContextVar("request_id", default="")


class TraceIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        trace_id = request.headers.get(_HEADER) or str(uuid.uuid4())
        token = request_id_var.set(trace_id)
        try:
            response: Response = await call_next(request)
            response.headers[_HEADER] = trace_id
            return response
        finally:
            request_id_var.reset(token)
