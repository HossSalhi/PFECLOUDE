from app.clients.prometheus_client import PrometheusClient


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self._payload


class _Client:
    def __init__(self, payload):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def get(self, url, params=None):
        return _Response(self._payload)


def test_batch_query_ignores_missing_series(monkeypatch) -> None:
    payload = {
        "data": {
            "result": [],
        }
    }

    monkeypatch.setattr(
        "app.clients.prometheus_client.httpx.Client",
        lambda timeout: _Client(payload),
    )

    client = PrometheusClient(base_url="http://prometheus", timeout_seconds=5.0)

    result = client.batch_query({"monitoring_vm_up": "up"})

    assert result == {}


def test_batch_query_keeps_numeric_results(monkeypatch) -> None:
    payload = {
        "data": {
            "result": [
                {
                    "value": [1710000000, "1"],
                }
            ],
        }
    }

    monkeypatch.setattr(
        "app.clients.prometheus_client.httpx.Client",
        lambda timeout: _Client(payload),
    )

    client = PrometheusClient(base_url="http://prometheus", timeout_seconds=5.0)

    result = client.batch_query({"ai_vm_up": "up"})

    assert result == {"ai_vm_up": 1.0}


def test_batch_query_ignores_nan_results(monkeypatch) -> None:
    payload = {
        "data": {
            "result": [
                {
                    "value": [1710000000, "NaN"],
                }
            ],
        }
    }

    monkeypatch.setattr(
        "app.clients.prometheus_client.httpx.Client",
        lambda timeout: _Client(payload),
    )

    client = PrometheusClient(base_url="http://prometheus", timeout_seconds=5.0)

    result = client.batch_query({"http_latency_p95_seconds": "histogram_quantile(...)"} )

    assert result == {}
