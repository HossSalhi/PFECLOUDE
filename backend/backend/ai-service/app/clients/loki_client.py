import logging
from datetime import datetime

import httpx


logger = logging.getLogger(__name__)


def _to_epoch_ns(value: datetime) -> int:
    return int(value.timestamp() * 1_000_000_000)


class LokiClient:
    def __init__(self, base_url: str, timeout_seconds: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def query_range(
        self,
        query: str,
        start: datetime,
        end: datetime,
        limit: int = 5000,
    ) -> list[str]:
        url = f"{self.base_url}/loki/api/v1/query_range"
        params = {
            "query": query,
            "start": _to_epoch_ns(start),
            "end": _to_epoch_ns(end),
            "limit": str(limit),
            "direction": "forward",
        }

        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                response = client.get(url, params=params)
                response.raise_for_status()
                payload = response.json()
        except Exception as exc:
            logger.warning("Loki query failed: %s", exc)
            return []

        data = payload.get("data", {})
        result = data.get("result", [])

        lines: list[str] = []
        for stream in result:
            values = stream.get("values", [])
            for value in values:
                if len(value) >= 2:
                    lines.append(str(value[1]))

        return lines
