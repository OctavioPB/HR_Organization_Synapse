"""Regression tests for the math-model corrections (MATH_MODEL.md §12 ledger).

Each test pins one defect that was found by reading the formulas against the code:
  - channel × direction weights were documented but never applied
  - the silo ratio was biased by department size (and could invert the ranking)
  - the SPOF robustness bracket was narrower than specified
  - KNOWLEDGE_LAMBDA (documented) was ignored in favour of KNOWLEDGE_DELTA
  - the scenario planner counted silos with its own threshold
  - the entropy SQL could not run on Postgres, so trends were always 0
"""

from __future__ import annotations

import importlib
import itertools
from datetime import date
from unittest.mock import MagicMock

import networkx as nx
import pytest

# ─── Channel × direction weighting ────────────────────────────────────────────


class TestChannelWeights:
    def test_documented_defaults(self):
        from graph import edge_weights as ew

        assert ew.CHANNEL_WEIGHTS["github"] == 3.0
        assert ew.CHANNEL_WEIGHTS["calendar"] == 2.5
        assert ew.DIRECTION_WEIGHTS["reviewed"] == 1.5
        assert ew.DIRECTION_WEIGHTS["sent"] == 0.7

    def test_code_review_outweighs_slack_message(self):
        from graph.edge_weights import omega

        assert omega("github", "reviewed") == pytest.approx(4.5)
        assert omega("slack", "sent") == pytest.approx(0.7)
        assert omega("github", "reviewed") > omega("slack", "sent")

    def test_unknown_values_are_neutral(self):
        from graph.edge_weights import omega

        assert omega("carrier_pigeon", "cooed") == 1.0
        assert omega(None, None) == 1.0

    def test_sql_expression_applies_both_multipliers(self):
        from graph.edge_weights import weighted_edge_sql

        sql = weighted_edge_sql("re")
        assert sql.startswith("(re.weight * (CASE re.channel")
        assert "CASE re.direction" in sql
        assert "WHEN 'github' THEN 3.0" in sql
        assert "WHEN 'reviewed' THEN 1.5" in sql

    def test_sql_rejects_unsafe_alias(self):
        from graph.edge_weights import weighted_edge_sql

        with pytest.raises(ValueError):
            weighted_edge_sql("re; DROP TABLE raw_events")

    def test_negative_weight_rejected(self, monkeypatch):
        from graph import edge_weights as ew

        monkeypatch.setenv("CHANNEL_WEIGHT_GITHUB", "-1")
        with pytest.raises(ValueError):
            ew._read("CHANNEL_WEIGHT_", {"github": 3.0})

    def test_off_switch_restores_raw_weights(self, monkeypatch):
        from graph import edge_weights as ew

        monkeypatch.setenv("CHANNEL_WEIGHTING", "off")
        mod = importlib.reload(ew)
        try:
            assert mod.weighted_edge_sql("re") == "re.weight"
            assert mod.omega("github", "reviewed") == 1.0
        finally:
            monkeypatch.delenv("CHANNEL_WEIGHTING")
            importlib.reload(ew)

    def test_every_graph_loader_uses_the_shared_weighting(self):
        """All SQL paths that turn raw_events into graph edges must weight the same way."""
        import inspect

        import api.db
        import api.nl.tools
        import graph.builder
        import graph.scenario_simulator
        import graph.succession

        for mod in (graph.builder, graph.scenario_simulator, graph.succession, api.nl.tools, api.db):
            assert "weighted_edge_sql" in inspect.getsource(mod), f"{mod.__name__} loads raw weights"


# ─── Silo detection: size-corrected homophily ─────────────────────────────────


def _mixed_org(sizes: dict[str, int], p_internal: dict[str, float], seed: int = 0) -> nx.DiGraph:
    """Each person sends 40 ties; a share p goes inside their department, the rest outside."""
    import random

    rng = random.Random(seed)
    G = nx.DiGraph()
    people = {d: [f"{d}{i}" for i in range(n)] for d, n in sizes.items()}
    everyone = [p for ps in people.values() for p in ps]
    for d, ps in people.items():
        for p in ps:
            G.add_node(p, department=d)
    for d, ps in people.items():
        outsiders = [p for p in everyone if p not in set(ps)]
        for p in ps:
            for _ in range(40):
                pool = [x for x in ps if x != p] if rng.random() < p_internal[d] else outsiders
                t = rng.choice(pool)
                # accumulate volume like graph.builder.build_graph does
                w = G[p][t]["weight"] + 1.0 if G.has_edge(p, t) else 1.0
                G.add_edge(p, t, weight=w)
    return G


class TestHomophilyIndex:
    def test_formula(self):
        from graph.silo_detector import homophily_index

        # 60 of 120 people, 80 % of ties internal: q = 59/119, H = (0.8 − q)/(1 − q)
        q = 59 / 119
        assert homophily_index(80, 20, 60, 120) == pytest.approx((0.8 - q) / (1 - q))

    def test_random_mixing_is_zero_for_any_size(self):
        from graph.silo_detector import homophily_index

        for n in (5, 30, 60):
            q = (n - 1) / 119
            internal = round(1000 * q)
            assert homophily_index(internal, 1000 - internal, n, 120) == pytest.approx(0.0, abs=0.01)

    def test_bounds_and_undefined(self):
        from graph.silo_detector import homophily_index

        assert homophily_index(100, 0, 10, 100) == pytest.approx(1.0)  # fully closed
        assert homophily_index(0, 100, 10, 100) < 0  # outward-looking
        assert homophily_index(0, 0, 10, 100) is None  # no activity
        assert homophily_index(10, 0, 100, 100) is None  # dept is the whole org

    def test_regression_large_department_not_flagged_for_its_size(self):
        """Old ratio: random mixing in a half-the-org department ≈ 1.0, in a small one ≈ 0.2.
        Both departments here mix at random, so neither may be a silo."""
        from graph.silo_detector import department_homophily, detect_silos

        sizes = {"Big": 60, "Small": 20, "Other": 40}
        G = _mixed_org(sizes, {d: (n - 1) / 119 for d, n in sizes.items()})
        stats = department_homophily(G)
        assert stats["Big"]["isolation_ratio"] > 3 * stats["Small"]["isolation_ratio"]  # the old bias
        assert abs(stats["Big"]["homophily"]) < 0.1 and abs(stats["Small"]["homophily"]) < 0.1
        assert detect_silos(G) == []

    def test_regression_ranking_follows_inwardness_not_size(self):
        """A small department that is far more inward than chance must outrank a big one that isn't."""
        from graph.silo_detector import detect_silos

        G = _mixed_org({"Big": 60, "Small": 15, "Other": 45}, {"Big": 0.75, "Small": 0.75, "Other": 0.3})
        by_dept = {next(iter(a.departments)): a for a in detect_silos(G)}
        assert "Small" in by_dept
        assert by_dept["Small"].homophily > by_dept.get("Big", by_dept["Small"]).homophily or "Big" not in by_dept
        # the legacy ratio ranks them the other way round
        if "Big" in by_dept:
            assert by_dept["Big"].isolation_ratio > by_dept["Small"].isolation_ratio

    def test_severity_bands(self):
        from graph.silo_detector import detect_silos

        G = _mixed_org({"A": 20, "B": 20, "C": 20}, {"A": 0.97, "B": 0.75, "C": 0.2})
        sev = {next(iter(a.departments)): a.severity for a in detect_silos(G)}
        assert sev.get("A") == "critical"
        assert sev.get("B") == "high"
        assert "C" not in sev

    def test_tiny_departments_never_flagged(self):
        from graph.silo_detector import detect_silos

        G = _mixed_org({"Pair": 2, "Rest": 40}, {"Pair": 1.0, "Rest": 0.5})
        assert all(next(iter(a.departments)) != "Pair" for a in detect_silos(G))

    def test_scenario_planner_counts_silos_like_the_detector(self):
        from graph.scenario_simulator import _count_silos
        from graph.silo_detector import detect_silos

        G = _mixed_org({"A": 20, "B": 20, "C": 20}, {"A": 0.97, "B": 0.75, "C": 0.2})
        assert _count_silos(G) == len(detect_silos(G)) == 2


# ─── SPOF robustness bracket ──────────────────────────────────────────────────


class TestPerturbationCorners:
    def test_hi_corner_matches_spec(self):
        from graph.risk_scorer import _WEIGHTS_HI

        assert [round(_WEIGHTS_HI[k], 3) for k in ("alpha", "beta", "gamma", "delta")] == [0.5, 0.3, 0.15, 0.05]

    def test_lo_corner_keeps_strict_ordering(self):
        """The spec's lo (0.35, 0.25, 0.25, 0.15) ties β and γ; ours keeps α > β > γ > δ."""
        from graph.risk_scorer import _WEIGHTS_LO

        w = [round(_WEIGHTS_LO[k], 3) for k in ("alpha", "beta", "gamma", "delta")]
        assert w == [0.35, 0.26, 0.24, 0.15]
        assert all(a > b for a, b in itertools.pairwise(w))

    def test_bracket_wider_than_old_proportional_one(self):
        from graph.risk_scorer import _WEIGHTS_HI, _WEIGHTS_LO

        assert _WEIGHTS_HI["alpha"] - _WEIGHTS_LO["alpha"] >= 0.15 - 1e-9  # old ±15 % gave 0.12


# ─── Config names ─────────────────────────────────────────────────────────────


def test_knowledge_lambda_is_honoured_as_alias(monkeypatch):
    import graph.knowledge_risk as kr

    monkeypatch.delenv("KNOWLEDGE_DELTA", raising=False)
    monkeypatch.setenv("KNOWLEDGE_LAMBDA", "0.45")
    try:
        assert importlib.reload(kr)._DELTA_K == pytest.approx(0.45)
        monkeypatch.setenv("KNOWLEDGE_DELTA", "0.2")  # canonical name wins
        assert importlib.reload(kr)._DELTA_K == pytest.approx(0.2)
    finally:
        monkeypatch.delenv("KNOWLEDGE_LAMBDA")
        monkeypatch.delenv("KNOWLEDGE_DELTA", raising=False)
        importlib.reload(kr)


# ─── Entropy weekly buckets ───────────────────────────────────────────────────


class TestEntropyBuckets:
    def _run(self, rows):
        from ml.features.feature_extractor import _load_weekly_interactions

        cur = MagicMock()
        cur.fetchall.return_value = rows
        out = _load_weekly_interactions(date(2026, 5, 26), date(2026, 6, 25), cur)
        return cur.execute.call_args.args, out

    def test_sql_is_postgres_valid_integer_day_bucketing(self):
        """date − date is an integer in Postgres; EXTRACT(EPOCH FROM integer) does not exist."""
        (sql, params), _ = self._run([])
        assert "EXTRACT" not in sql.upper()
        assert "/ 7" in sql
        assert params == (3, date(2026, 6, 25), date(2026, 5, 26), date(2026, 6, 25), 27)

    def test_equal_length_weeks_counted_back_from_snapshot(self):
        (sql, _), _ = self._run([])
        assert "BETWEEN 0 AND %s" in sql  # 4 × 7 = 28 days, no oversized final bucket

    def test_consent_filter_applied(self):
        (sql, _), _ = self._run([])
        assert "es.consent = true AND et.consent = true" in sql

    def test_rows_grouped_by_week(self):
        _, out = self._run([("a", 3, "b", 4), ("a", 0, "c", 1)])
        assert out == {"a": {3: {"b": 4}, 0: {"c": 1}}}
