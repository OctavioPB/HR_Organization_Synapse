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
        "component_scores": json.dumps(
            {
                "silo": 0.2,
                "spof": 0.42,
                "entropy": 0.2,
                "frag": 0.01,
            }
        ),
        **kwargs,
    }


def _trend_rows(n=4) -> list[dict]:
    base = 70.0
    rows = []
    for i in range(n):
        rows.append(
            {
                "computed_at": date(2025, 4, 7 + i * 7),
                "score": base + i * 2,
                "tier": "caution",
                "silo_count": 2,
                "avg_spof_score": 0.42,
                "avg_entropy_trend": -0.01,
                "wcc_count": 3,
                "node_count": 150,
            }
        )
    return rows


# ─── Health components (pure functions) ───────────────────────────────────────


def _h(**kw):
    """compute_org_health with neutral defaults; override any component."""
    from graph.org_health import compute_org_health

    args = {"spof_risk": 0.0, "silo_risk": 0.0, "entropy_risk": 0.0, "wcc_count": 1, "node_count": 100}
    args.update(kw)
    return compute_org_health(**args)


class TestComputeOrgHealth:
    def test_perfect_org_scores_100(self):
        result = _h()
        assert result["score"] == 100.0
        assert result["tier"] == "healthy"

    def test_worst_case_approaches_zero(self):
        result = _h(spof_risk=1.0, silo_risk=1.0, entropy_risk=1.0, wcc_count=100)
        assert result["score"] <= 1.0

    def test_score_and_components_bounded(self):
        for kw in [
            {"spof_risk": 5.0, "silo_risk": -1.0, "entropy_risk": 2.0, "wcc_count": 200},
            {"spof_risk": 0.5, "silo_risk": 0.5, "entropy_risk": 0.5, "wcc_count": 2},
        ]:
            result = _h(**kw)
            assert 0.0 <= result["score"] <= 100.0
            assert all(0.0 <= v <= 1.0 for v in result["component_scores"].values())

    def test_weights_apply_linearly(self):
        # spof weight 0.35 → full spof risk costs 35 points
        assert _h(spof_risk=1.0)["score"] == pytest.approx(65.0)
        assert _h(silo_risk=1.0)["score"] == pytest.approx(80.0)

    def test_fragmentation_convex_first_split_is_severe(self):
        """MODEL.md §12.2: first disconnection (WCC 1→2) ≈ 0.78 risk."""
        assert _h(wcc_count=2)["component_scores"]["frag"] == pytest.approx(0.777, abs=0.01)

    def test_fragmentation_increases_with_components(self):
        assert _h(wcc_count=10)["component_scores"]["frag"] > _h(wcc_count=2)["component_scores"]["frag"]

    def test_reporting_fields_pass_through(self):
        result = _h(silo_count=3, avg_spof_score=0.43, avg_entropy_trend=-0.02)
        for key in ("score", "tier", "silo_count", "avg_spof_score", "avg_entropy_trend", "wcc_count", "node_count"):
            assert key in result
        assert result["silo_count"] == 3
        assert set(result["component_scores"]) == {"silo", "spof", "entropy", "frag"}


class TestBrokerageConcentration:
    """spof_risk: absolute concentration of brokerage, not mean rank-percentile SPOF."""

    def test_even_spread_is_zero(self):
        from graph.org_health import brokerage_concentration

        assert brokerage_concentration([0.1] * 40) == pytest.approx(0.0, abs=1e-9)

    def test_single_broker_is_one(self):
        from graph.org_health import brokerage_concentration

        assert brokerage_concentration([0.9] + [0.0] * 39) == pytest.approx(1.0)

    def test_more_concentration_means_more_risk(self):
        from graph.org_health import brokerage_concentration

        spread = brokerage_concentration([0.3, 0.3] + [0.1] * 38)
        concentrated = brokerage_concentration([0.9, 0.8] + [0.01] * 38)
        assert concentrated > spread

    def test_no_brokerage_is_zero(self):
        from graph.org_health import brokerage_concentration

        assert brokerage_concentration([]) == 0.0
        assert brokerage_concentration([0.0] * 10) == 0.0

    def test_regression_not_constant_like_mean_spof(self):
        """Mean of rank-percentile SPOF is ~0.4 for ANY org; this metric must separate orgs."""
        from graph.org_health import brokerage_concentration

        star = brokerage_concentration([1.0] + [0.0] * 49)
        flat = brokerage_concentration([0.02] * 50)
        assert star - flat > 0.9


class TestSiloExposure:
    def test_random_mixing_is_zero(self):
        from graph.org_health import silo_exposure

        assert silo_exposure([(10, 0.0), (20, 0.0)]) == 0.0

    def test_size_weighted(self):
        from graph.org_health import silo_exposure

        # 30 people closed (H=1), 10 people random (H=0) → 0.75
        assert silo_exposure([(30, 1.0), (10, 0.0)]) == pytest.approx(0.75)

    def test_outward_departments_do_not_cancel_closed_ones(self):
        from graph.org_health import silo_exposure

        assert silo_exposure([(10, 0.8), (10, -0.8)]) == pytest.approx(0.4)

    def test_undefined_departments_skipped(self):
        from graph.org_health import silo_exposure

        assert silo_exposure([(10, 0.6), (5, None)]) == pytest.approx(0.6)
        assert silo_exposure([]) == 0.0


class TestWithdrawalShare:
    def test_fraction_below_threshold(self):
        from graph.org_health import withdrawal_share

        assert withdrawal_share([-0.2, -0.06, 0.0, 0.1], threshold=-0.05) == pytest.approx(0.5)

    def test_few_strong_withdrawers_are_not_averaged_away(self):
        """Old form used -mean(trend): one strong withdrawer + many mild engagers ≈ 0 risk."""
        from graph.org_health import withdrawal_share

        assert withdrawal_share([-1.0] + [0.05] * 9, threshold=-0.05) == pytest.approx(0.1)

    def test_no_data_is_neutral(self):
        from graph.org_health import withdrawal_share

        assert withdrawal_share([]) == 0.0
        assert withdrawal_share([None, None]) == 0.0


class TestOrgHealthFromGraph:
    def _two_dept_graph(self, cross_links: int):
        import networkx as nx

        G = nx.DiGraph()
        a = [f"a{i}" for i in range(6)]
        b = [f"b{i}" for i in range(6)]
        for n in a:
            G.add_node(n, department="A")
        for n in b:
            G.add_node(n, department="B")
        for grp in (a, b):
            for u in grp:
                for v in grp:
                    if u != v:
                        G.add_edge(u, v, weight=1.0)
        for i in range(cross_links):
            G.add_edge(a[i % 6], b[(i * 5) % 6], weight=1.0)
            G.add_edge(b[(i * 5) % 6], a[i % 6], weight=1.0)
        return G

    def test_more_cross_links_lower_silo_risk(self):
        from graph.org_health import org_health_from_graph

        closed = org_health_from_graph(self._two_dept_graph(1))
        open_ = org_health_from_graph(self._two_dept_graph(12))
        assert open_["component_scores"]["silo"] < closed["component_scores"]["silo"]
        assert open_["score"] > closed["score"]

    def test_single_bridge_concentrates_brokerage(self):
        from graph.org_health import org_health_from_graph

        bridged_once = org_health_from_graph(self._two_dept_graph(1))
        bridged_widely = org_health_from_graph(self._two_dept_graph(12))
        assert bridged_once["component_scores"]["spof"] > bridged_widely["component_scores"]["spof"]

    def test_empty_graph_is_safe(self):
        import networkx as nx

        from graph.org_health import org_health_from_graph

        result = org_health_from_graph(nx.DiGraph())
        assert result["score"] == 100.0


# ─── score_tier ───────────────────────────────────────────────────────────────


class TestScoreTier:
    @pytest.mark.parametrize(
        "score,expected",
        [
            (100.0, "healthy"),
            (80.0, "healthy"),
            (79.9, "caution"),
            (60.0, "caution"),
            (59.9, "at_risk"),
            (40.0, "at_risk"),
            (39.9, "critical"),
            (0.0, "critical"),
        ],
    )
    def test_tier_boundaries(self, score, expected):
        from graph.org_health import score_tier

        assert score_tier(score) == expected


# ─── generate_briefing ────────────────────────────────────────────────────────


class TestGenerateBriefing:
    def _call(self, current=None, trend=None):
        from graph.org_health import generate_briefing

        current = current or _health_row()
        trend = trend or _trend_rows(4)
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": ""}, clear=False):
            return generate_briefing(current, trend)

    def test_returns_required_keys(self):
        b = self._call()
        for key in (
            "score",
            "tier",
            "trend_delta",
            "trend_direction",
            "top_risks",
            "recommended_actions",
            "narrative",
            "computed_at",
        ):
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
        row["component_scores"] = json.dumps(
            {
                "silo": 0.8,
                "spof": 0.1,
                "entropy": 0.0,
                "frag": 0.0,
            }
        )
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
