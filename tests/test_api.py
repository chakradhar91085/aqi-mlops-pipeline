import pandas as pd
import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

import api.main as m  # noqa: E402


class StubModel:
    def predict(self, X):
        return [187.4]


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(m, "_log_request", lambda *a, **k: None)
    monkeypatch.setattr(m, "load_champion", lambda: None)
    m.ModelHolder.model, m.ModelHolder.version = StubModel(), "7"
    with TestClient(m.app) as c:
        yield c
    m.ModelHolder.model = m.ModelHolder.version = None


def test_health_ok(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["model_version"] == "7"


def test_health_503_without_model(client):
    m.ModelHolder.model = None
    assert client.get("/health").status_code == 503


def test_predict_returns_forecast_and_category(client, monkeypatch):
    monkeypatch.setattr(m, "latest_feature_row", lambda city, as_of=None: (pd.DataFrame({"x": [1]}), pd.Timestamp("2026-09-20").date(), 30))
    body = client.get("/predict", params={"city": "Delhi"}).json()
    assert body["forecast_date"] == "2026-09-21"
    assert body["predicted_aqi"] == 187.4
    assert body["predicted_category"] == "Unhealthy"
    assert body["model_version"] == "7"


def test_unknown_city_404(client, monkeypatch):
    def boom(city, as_of=None):
        raise LookupError("No data")
    monkeypatch.setattr(m, "latest_feature_row", boom)
    assert client.get("/predict", params={"city": "Atlantis"}).status_code == 404


def test_short_history_422(client, monkeypatch):
    monkeypatch.setattr(m, "latest_feature_row", lambda city, as_of=None: (pd.DataFrame({"x": [1]}), pd.Timestamp("2026-09-20").date(), 5))
    assert client.get("/predict", params={"city": "Delhi"}).status_code == 422
