"""Boot smoke test for the real FastAPI app, including Prometheus instrumentation.

Regression guard: prometheus-fastapi-instrumentator 7.x raised
``AttributeError: '_IncludedRouter' object has no attribute 'path'`` on *every*
request once FastAPI's router internals changed (seen with FastAPI 0.165).
The soft import in api/main.py only protects against a *missing* package, not a
broken one, so this test exercises the full middleware stack end to end.

No database is needed: /, /health and /metrics do not touch Postgres, the
routed-endpoint test mocks the DB dependency, and the cache layer degrades
gracefully when Redis is unreachable.
"""

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from api.main import _PROMETHEUS_AVAILABLE, app

client = TestClient(app)


def test_root_returns_200():
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_health_returns_200():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "healthy"


def test_metrics_endpoint_when_instrumentator_installed():
    resp = client.get("/metrics")
    if _PROMETHEUS_AVAILABLE:
        assert resp.status_code == 200
        assert "http_request" in resp.text
    else:
        assert resp.status_code == 404


def test_included_router_endpoint_passes_through_middleware():
    """A route registered via include_router must reach its handler.

    App-level routes (/, /health) did not trigger the instrumentator bug; only
    requests that resolve through an included router did, which is every data
    endpoint.  The DB is mocked so the request exercises routing + middleware only.
    """
    from datetime import date

    from api.deps import get_db

    app.dependency_overrides[get_db] = lambda: MagicMock()
    try:
        with (
            patch("api.routers.risk.queries.fetch_latest_snapshot_date", return_value=date(2026, 1, 1)),
            patch("api.routers.risk.queries.fetch_risk_scores", return_value=[]),
        ):
            resp = client.get("/risk/scores")
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 200
