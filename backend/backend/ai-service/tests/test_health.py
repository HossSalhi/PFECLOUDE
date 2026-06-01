from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_health() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"


def test_ready() -> None:
    response = client.get("/ready")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ready"
    assert "model_loaded" in payload


def test_predict_contract() -> None:
    response = client.post(
        "/predict",
        json={"namespace": "pfe", "app": "backend", "window_minutes": 5},
    )
    assert response.status_code == 200
    payload = response.json()
    for required in (
        "incident",
        "noise",
        "incident_type",
        "severity",
        "recommended_action",
        "final_executable_action",
        "execution_mode",
        "recommendation_only_reason",
        "confidence",
        "summary",
        "decision_explanation",
        "top_recommended_plans",
        "evidence",
        "features",
        "decision_context",
        "window_start",
        "window_end",
    ):
        assert required in payload
