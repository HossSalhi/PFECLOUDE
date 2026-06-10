"""
Service-to-service API key authentication for the AI service.

When AIOPS_API_KEY is set, every request must carry either:
  X-API-Key: <key>          (preferred)
  Authorization: ApiKey <key>   (alternative)

Paths in _OPEN_PATHS bypass the check so Prometheus scrapes and
health probes always succeed without credentials.
"""
from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

_OPEN_PATHS = frozenset({"/health", "/ready", "/metrics"})


class ApiKeyMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, api_key: str) -> None:
        super().__init__(app)
        self._api_key = api_key.strip() if api_key else ""

    async def dispatch(self, request: Request, call_next):
        if not self._api_key or request.url.path in _OPEN_PATHS:
            return await call_next(request)

        incoming = (
            request.headers.get("X-API-Key")
            or _extract_api_key_from_auth(request.headers.get("Authorization", ""))
        )
        if not incoming or incoming != self._api_key:
            return JSONResponse(
                {"detail": "Invalid or missing API key. Set X-API-Key header."},
                status_code=401,
            )
        return await call_next(request)


def _extract_api_key_from_auth(authorization: str) -> str:
    prefix = "ApiKey "
    if authorization.startswith(prefix):
        return authorization[len(prefix):].strip()
    return ""
