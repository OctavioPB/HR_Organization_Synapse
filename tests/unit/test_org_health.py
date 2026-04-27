"""Unit tests for F9 — Org Health Score & Executive Briefing.

Tests cover:
  - compute_org_health: score bounds, tier thresholds, component math, edge cases
  - score_tier: boundary conditions
  - generate_briefing: deterministic template path, trend_delta, recommendation logic
  - API endpoints: GET /org-health/score, GET /org-health/trend, GET /org-health/briefing
"""

from __future__ import annotations

import json
from datetime import date
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


# ─── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture()
def api_client():
    from api.main import app
    from api.deps import get_db

    mock_conn = MagicMock()
    app.dependency_overrides[get_db] = lambda: mock_conn
    yield TestClient(app, raise_server_exceptions=True)
    app.dependency_overrides.clear()


def _health_row(score=75.0, tier="caution", **kwargs) -> dict:
    return {
        "computed_at": date(2025, 5, 5),
        "score": score,
        "tier": tier,
        "silo_count": 2,
        "avg_spof_score": 0.42,
        "avg_entropy_trend": -0.01,
        "wcc_count": 3,
        "node_count": 150,
        "component_scores": json.dumps({
            "silo": 0.2, "spof": 0.42, "entropy": 0.2, "frag": 0.01,
        }),
        **kwargs,
    }


def _trend_rows(n=4) -> list[dict]:
    base = 70.0
    rows = []
    for i in range(n):
        rows.append({
            "computed_at": date(2025, 4, 7 + i * 7),
            "score": base + i * 2,
            "tier": "caution",
            "silo_count": 2,
            "avg_spof_score": 0.42,
            "avg_entropy_trend": -0.01,
            "wcc_count": 3,
            "node_count": 150,
        })
    return rows


# ─── compute_org_health (pure function) ───────────────────────────────────────


class TestComputeOrgHealth:
    def test_perfect_org_scores_100(self):
        from graph.org_health import compute_org_health

        result = compute_org_health(
            silo_count=0,
            avg_spof_score=0.0,
            avg_entropy_trend=0.0,
            wcc_count=1,
            node_count=100,
        )
        assert result["score"] == 100.0
        assert result["tier"] == "healthy"

    def test_worst_case_approaches_zero(self):
        from graph.org_health import compute_org_health

        result = compute_org_health(
            silo_count=100,
            avg_spof_score=1.0,
            avg_entropy_trend=-1.0,
            wcc_count=100,
            node_count=10,
        )
        assert result["score"] <= 10.0

    def test_score_bounded_0_to_100(self):
        from graph.org_health import compute_org_health

        for silo, spof, entropy, wcc, n in [
            (0, 0.0, None, 1, 0),
            (50, 2.0, -5.0, 200, 1),
            (0, 0.5, 0.1, 2, 50),
        ]:
            result = compute_org_health(silo, spof, entropy, wcc, n)
            assert 0.0 <= result["score"] <= 100.0, f"score out of range: {result['score']}"

    def test_no_entropy_data_is_neutral(self):
        from graph.org_health import compute_org_health

        with_none = compute_org_health(0, 0.3, None, 1, 100)
        with_zero = compute_org_health(0, 0.3, 0.0, 1, 100)
        assert with_none["score"] == with_zero["score"]

    def test_positive_entropy_has_no_penalty(self):
        from graph.org_health import compute_org_health

        # Positive slope = employees engaging more = no entropy risk
        result = compute_org_health(0, 0.0, 0.1, 1, 100)
        assert result["component_scores"]["entropy"] == 0.0
        assert result["score"] == 100.0

    def test_silo_risk_capped_at_1(self):
        from graph.org_health import compute_org_health

        result = compute_org_health(9999, 0.0, None, 1, 100)
        assert result["component_scores"]["silo"] == 1.0

    def test_fragmentation_zero_when_fully_connected(self):
        from graph.org_health import compute_org_health

        result = compute_org_health(0, 0.0, None, 1, 100)
        assert result["component_scores"]["frag"] == 0.0

    def test_fragmentation_increases_with_components(self):
        from graph.org_health import compute_org_health

        low  = compute_org_health(0, 0.0, None, 2, 100)
        high = compute_org_health(0, 0.0, None, 10, 100)
        assert high["component_scores"]["frag"] > low["component_scores"]["frag"]

    def test_returns_correct_keys(self):
        from graph.org_health import compute_org_health

        result = compute_org_health(1, 0.4, -0.01, 2, 80)
        for key in ("score", "tier", "silo_count", "avg_spof_score",
                    "avg_entropy_trend", "wcc_count", "node_count", "component_scores"):
            assert key in result

    def test_component_scores_are_0_to_1(self):
        from graph.org_health import compute_org_health

        result = compute_org_health(2, 0.6, -0.03, 4, 100)
        for k, v in result["component_scores"].items():
            assert 0.0 <= v <= 1.0, f"{k}={v} out of [0,1]"


# ─── score_tier ───────────────────────────────────────────────────────────────


class TestScoreTier:
    @pytest.mark.parametrize("score,expected", [
        (100.0, "healthy"),
        (80.0,  "healthy"),
        (79.9,  "caution"),
        (60.0,  "caution"),
        (59.9,  "at_risk"),
        (40.0,  "at_risk"),
        (39.9,  "critical"),
        (0.0,   "critical"),
    ])
    def test_tier_boundaries(self, score, expected):
        from graph.org_health import score_tier

        assert score_tier(score) == expected


# ─── generate_briefing ────────────────────────────────────────────────────────


class TestGenerateBriefing:
    def _call(self, current=None, trend=None):
        from graph.org_health import generate_briefing

        current = current or _health_row()
        trend   = trend   or _trend_rows(4)
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": ""}, clear=False):
            return generate_briefing(current, trend)

    def test_returns_required_keys(self):
        b = self._call()
        for key in ("score", "tier", "trend_delta", "trend_direction",
                    "top_risks", "recommended_actions", "narrative", "computed_at"):
            assert key in b

    def test_trend_delta_computed_correctly(self):
        trend = _trend_rows(3)
        # Last two scores: 72 and 74 → delta = 74 - 72 = 2.0
        b = self._call(current=_health_row(score=74.0), trend=trend)
        assert b["trend_delta"] == pytest.approx(2.0, abs=1.5)

    def test_improving_direction(self):
        trend = _trend_rows(2)
        # trend scores: 70 and 72; current score 76 → delta > 0
        b = self._call(current=_health_row(score=76.0), trend=trend)
        assert b["trend_direction"] in ("improving", "stable")

    def test_declining_direction(self):
        trend = [
            {**_trend_rows(2)[0], "score": 80.0},
            {**_trend_rows(2)[1], "score": 75.0},
        ]
        b = self._call(current=_health_row(score=70.0), trend=trend)
        assert b["trend_direction"] == "declining"

    def test_top_risks_sorted_descending(self):
        b = self._call()
        levels = [r["risk_level"] for r in b["top_risks"]]
        assert levels == sorted(levels, reverse=True)

    def test_narrative_is_non_empty(self):
        b = self._call()
        assert len(b["narrative"]) > 20

    def test_recommendations_non_empty(self):
        b = self._call()
        assert len(b["recommended_actions"]) >= 1

    def test_silo_recommendation_fires(self):
        row = _health_row()
        row["component_scores"] = json.dumps({
            "silo": 0.8, "spof": 0.1, "entropy": 0.0, "frag": 0.0,
        })
        b = self._call(current=row)
        assert any("silo" in a.lower() for a in b["recommended_actions"])

    def test_single_trend_point_delta_zero(self):
        b = self._call(trend=[_trend_rows(1)[0]])
        assert b["trend_delta"] == 0.0

    def test_component_scores_as_string_parsed(self):
        row = _health_row()
        row["component_scores"] = '{"silo":0.5,"spof":0.3,"entropy":0.0,"frag":0.1}'
        b = self._call(current=row)
        assert len(b["top_risks"]) > 0


# ─── GET /org-health/score ────────────────────────────────────────────────────


class TestOrgHealthScoreEndpoint:
    def test_returns_200_with_data(self, api_client):
        with patch("api.db.fetch_latest_org_health", return_value=_health_row()):
            resp = api_client.get("/org-health/score")
        assert resp.status_code == 200
        body = resp.json()
        assert body["score"] == 75.0
        assert body["tier"] == "caution"

    def test_returns_404_when_no_data(self, api_client):
        with patch("api.db.fetch_latest_org_health", return_value=None):
            resp = api_client.get("/org-health/score")
        assert resp.status_code == 404

    def test_component_scores_in_response(self, api_client):
        with patch("api.db.fetch_latest_org_health", return_value=_health_row()):
            resp = api_client.get("/org-health/score")
        body = resp.json()
        assert "component_scores" in body
        for k in ("silo", "spof", "entropy", "frag"):
            assert k in body["component_scores"]


# ─── GET /org-health/trend ────────────────────────────────────────────────────


class TestOrgHealthTrendEndpoint:
    def test_returns_200_with_points(self, api_client):
        with patch("api.db.fetch_org_health_trend", return_value=_trend_rows(4)):
            resp = api_client.get("/org-health/trend?weeks=4")
        assert resp.status_code == 200
        body = resp.json()
        assert body["weeks"] == 4
        assert len(body["points"]) == 4

    def test_returns_404_when_empty(self, api_client):
        with patch("api.db.fetch_org_health_trend", return_value=[]):
            resp = api_client.get("/org-health/trend")
        assert resp.status_code == 404

    def test_weeks_query_param_validated(self, api_client):
        resp = api_client.get("/org-health/trend?weeks=0")
        assert resp.status_code == 422


# ─── GET /org-health/briefing ─────────────────────────────────────────────────


class TestOrgHealthBriefingEndpoint:
    def _mock_briefing(self):
        return {
            "computed_at": "2025-05-05",
            "score": 75.0,
            "tier": "caution",
            "trend_delta": 2.0,
            "trend_direction": "improving",
            "top_risks": [{"factor": "spof", "risk_level": 0.42}],
            "recommended_actions": ["Initiate cross-training."],
            "narrative": "Org health improved marginally this week.",
        }

    def test_returns_200_with_narrative(self, api_client):
        with (
            patch("api.db.fetch_latest_org_health", return_value=_health_row()),
            patch("api.db.fetch_org_health_trend", return_value=_trend_rows(4)),
            patch("graph.org_health.generate_briefing", return_value=self._mock_briefing()),
        ):
            resp = api_client.get("/org-health/briefing")
        assert resp.status_code == 200
        body = resp.json()
        assert "narrative" in body
        assert body["tier"] == "caution"

    def test_returns_404_when_no_score(self, api_client):
        with patch("api.db.fetch_latest_org_health", return_value=None):
            resp = api_client.get("/org-health/briefing")
        assert resp.status_code == 404

    def test_top_risks_present(self, api_client):
        with (
            patch("api.db.fetch_latest_org_health", return_value=_health_row()),
            patch("api.db.fetch_org_health_trend", return_value=_trend_rows(4)),
            patch("graph.org_health.generate_briefing", return_value=self._mock_briefing()),
        ):
            resp = api_client.get("/org-health/briefing")
        body = resp.json()
        assert len(body["top_risks"]) >= 1
        assert "factor" in body["top_risks"][0]

    def test_502_on_generate_failure(self, api_client):
        with (
            patch("api.db.fetch_latest_org_health", return_value=_health_row()),
            patch("api.db.fetch_org_health_trend", return_value=_trend_rows(4)),
            patch("graph.org_health.generate_briefing", side_effect=RuntimeError("API down")),
        ):
            resp = api_client.get("/org-health/briefing")
        assert resp.status_code == 502
