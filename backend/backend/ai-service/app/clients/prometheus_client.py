import concurrent.futures
import json
import logging
import math
from datetime import datetime
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class PrometheusClient:
    def __init__(self, base_url: str, timeout_seconds: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        # Persistent client — one TCP connection pool shared across all queries.
        self._client = httpx.Client(timeout=timeout_seconds)

    def query(self, expression: str, at_time: datetime | None = None) -> float | None:
        url = f"{self.base_url}/api/v1/query"
        params: dict[str, str] = {"query": expression}
        if at_time:
            params["time"] = str(at_time.timestamp())

        try:
            response = self._client.get(url, params=params)
            response.raise_for_status()
            try:
                payload = response.json()
            except json.JSONDecodeError as exc:
                logger.warning(
                    "Prometheus query returned non-JSON response (status=%d, url=%s): %s",
                    response.status_code, url, exc,
                )
                return None
        except json.JSONDecodeError:
            return None
        except Exception as exc:
            logger.warning("Prometheus query failed: %s", exc)
            return None

        data = payload.get("data", {})
        result = data.get("result", [])
        if not result:
            return None

        first = result[0]
        value = first.get("value")
        if not isinstance(value, list) or len(value) < 2:
            return None

        try:
            numeric = float(value[1])
        except (TypeError, ValueError):
            return None

        if not math.isfinite(numeric):
            return None

        return numeric

    def batch_query(
        self,
        expressions: dict[str, str],
        at_time: datetime | None = None,
    ) -> dict[str, float]:
        if not expressions:
            return {}
        # Run all queries in parallel — eliminates N×round-trip serial latency.
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(expressions), 8)) as executor:
            futures = {
                name: executor.submit(self.query, expr, at_time)
                for name, expr in expressions.items()
            }
        results: dict[str, float] = {}
        for name, future in futures.items():
            try:
                value = future.result()
                if value is not None:
                    results[name] = value
            except Exception:
                pass
        return results

    def query_raw(self, expression: str, at_time: datetime | None = None) -> list[dict[str, Any]]:
        """Return the full list of Prometheus result vectors (each has 'metric' and 'value')."""
        url = f"{self.base_url}/api/v1/query"
        params: dict[str, str] = {"query": expression}
        if at_time:
            params["time"] = str(at_time.timestamp())

        try:
            response = self._client.get(url, params=params)
            response.raise_for_status()
            try:
                payload = response.json()
            except json.JSONDecodeError as exc:
                logger.warning(
                    "Prometheus query_raw returned non-JSON (status=%d): %s",
                    response.status_code, exc,
                )
                return []
        except json.JSONDecodeError:
            return []
        except Exception as exc:
            logger.warning("Prometheus query_raw failed: %s", exc)
            return []

        return payload.get("data", {}).get("result", [])

    def query_scalar(self, expression: str, at_time: datetime | None = None) -> float | None:
        """Return a single scalar from an instant query (alias for query())."""
        return self.query(expression, at_time=at_time)

    def query_range(
        self,
        expression: str,
        start: datetime,
        end: datetime,
        step: int = 60,
    ) -> list[tuple[float, str]]:
        """Return a list of (timestamp, value_str) pairs from a range query.

        Uses the first time-series returned. Returns an empty list on failure.
        """
        url = f"{self.base_url}/api/v1/query_range"
        params: dict[str, str] = {
            "query": expression,
            "start": str(start.timestamp()),
            "end": str(end.timestamp()),
            "step": str(step),
        }

        try:
            response = self._client.get(url, params=params)
            response.raise_for_status()
            try:
                payload = response.json()
            except json.JSONDecodeError as exc:
                logger.warning(
                    "Prometheus query_range returned non-JSON (status=%d): %s",
                    response.status_code, exc,
                )
                return []
        except json.JSONDecodeError:
            return []
        except Exception as exc:
            logger.warning("Prometheus query_range failed: %s", exc)
            return []

        results = payload.get("data", {}).get("result", [])
        if not results:
            return []

        values: list[tuple[float, str]] = results[0].get("values", [])
        return [(float(ts), str(val)) for ts, val in values]
