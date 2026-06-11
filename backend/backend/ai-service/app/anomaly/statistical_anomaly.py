"""Statistical anomaly detector (Z-score on a rolling baseline).

NOTE: This is a STANDALONE, OPTIONAL diagnostic loop (`python -m app.anomaly.statistical_anomaly`),
NOT wired into the FastAPI service or the autonomous monitor. The live detection path is
PredictorService → InferenceEngine (ML + LLM); this module is a lightweight side-channel
useful for manual debugging of a single namespace.
"""
import math
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Iterable

import httpx


def _default_prometheus_url() -> str:
    """Read the Prometheus base URL from settings, falling back to the monitoring VM."""
    try:
        from app.core.config import get_settings
        return get_settings().prometheus_base_url
    except Exception:
        return "http://192.168.148.160:9090"


WINDOW_SIZE = 30  # last 30 samples (30 minutes if polled every 60s)
Z_THRESHOLD = 3.0  # 3 standard deviations
MIN_SAMPLES = 10

# Metrics to watch — one PromQL per signal
WATCHED_METRICS = {
    "backend_cpu": 'sum(rate(container_cpu_usage_seconds_total{namespace="pfe",pod=~"backend-.*",image!=""}[2m]))',
    "backend_memory_gb": 'sum(container_memory_working_set_bytes{namespace="pfe",pod=~"backend-.*",image!=""}) / 1e9',
    "backend_http_5xx_ratio": '''
        sum(rate(http_server_requests_seconds_count{application="backend",status=~"5.."}[2m]))
        / clamp_min(sum(rate(http_server_requests_seconds_count{application="backend"}[2m])), 0.001)
    ''',
    "backend_http_p95_seconds": '''
        histogram_quantile(0.95,
          sum(rate(http_server_requests_seconds_bucket{application="backend"}[2m])) by (le))
    ''',
    "demo_cpu": 'sum(rate(container_cpu_usage_seconds_total{namespace="pfe",pod=~"demo-app.*",container="demo"}[2m]))',
    "demo_memory_gb": 'sum(container_memory_working_set_bytes{namespace="pfe",pod=~"demo-app.*",container="demo"}) / 1e9',
}


@dataclass
class Baseline:
    name: str
    samples: deque[float] = field(default_factory=lambda: deque(maxlen=WINDOW_SIZE))

    def push(self, value: float) -> None:
        if math.isfinite(value):
            self.samples.append(value)

    def stats(self) -> tuple[float, float] | None:
        if len(self.samples) < MIN_SAMPLES:
            return None
        mu = sum(self.samples) / len(self.samples)
        var = sum((x - mu) ** 2 for x in self.samples) / len(self.samples)
        sigma = math.sqrt(var)
        return mu, sigma

    def zscore(self, value: float) -> float | None:
        s = self.stats()
        if s is None:
            return None
        mu, sigma = s
        if sigma == 0:
            return 0.0 if value == mu else float("inf") if value > mu else float("-inf")
        return (value - mu) / sigma


def query_prometheus(client: httpx.Client, expr: str, base_url: str) -> float | None:
    try:
        r = client.get(f"{base_url}/api/v1/query", params={"query": expr}, timeout=10.0)
        data = r.json()
        if data.get("status") != "success":
            return None
        results = data["data"]["result"]
        if not results:
            return None
        val_str = results[0]["value"][1]
        v = float(val_str)
        return v if math.isfinite(v) else None
    except Exception:
        return None


class StatisticalAnomalyDetector:
    def __init__(self, base_url: str | None = None) -> None:
        self.baselines: dict[str, Baseline] = {name: Baseline(name=name) for name in WATCHED_METRICS}
        self._base_url = base_url or _default_prometheus_url()
        # Persistent client — one connection pool reused across ticks (no per-tick handshake).
        self._client = httpx.Client(timeout=10.0)

    def close(self) -> None:
        self._client.close()

    def tick(self) -> list[dict]:
        anomalies: list[dict] = []
        for name, expr in WATCHED_METRICS.items():
            v = query_prometheus(self._client, expr, self._base_url)
            if v is None:
                continue
            z = self.baselines[name].zscore(v)
            # Detect BEFORE pushing so a freshly anomalous value doesn't poison the baseline
            if z is not None and abs(z) >= Z_THRESHOLD:
                mu, sigma = self.baselines[name].stats()
                anomalies.append({
                    "metric": name,
                    "value": v,
                    "mean": mu,
                    "sigma": sigma,
                    "z_score": z,
                    "ts": time.time(),
                })
            self.baselines[name].push(v)
        return anomalies


def run_loop(interval_seconds: int = 60) -> None:
    detector = StatisticalAnomalyDetector()
    print(f"[stat-anomaly] starting, polling every {interval_seconds}s, threshold |z|>={Z_THRESHOLD}")
    try:
        while True:
            try:
                anomalies = detector.tick()
                if anomalies:
                    for a in anomalies:
                        print(
                            f"[ANOMALY] {a['metric']}={a['value']:.4f} "
                            f"(baseline mu={a['mean']:.4f} sigma={a['sigma']:.4f}, z={a['z_score']:.2f})"
                        )
                else:
                    print(f"[stat-anomaly] tick OK, no anomalies")
            except Exception as exc:
                print(f"[stat-anomaly] tick error: {exc}")
            time.sleep(interval_seconds)
    finally:
        detector.close()


if __name__ == "__main__":
    run_loop()
