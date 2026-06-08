"""Unit tests for the MODEL.md v2/v3 mathematical corrections.

Covers the new pure-function building blocks introduced by the corrections:
  §5.1/§5.3  rank-percentile SPOF + weight-sensitivity bands
  §9.1       CS_overlap neighborhood community overlap
  §7.4.1     peer_churn_rate contagion signal
"""

from __future__ import annotations

import networkx as nx
import pytest


# ─── §5.1: rank-percentile transform ──────────────────────────────────────────


class TestPercentRank:
    def test_min_maps_to_zero_max_to_one(self):
        from graph.risk_scorer import _percent_rank

        pr = _percent_rank({"a": -5.0, "b": 0.0, "c": 100.0})
        assert pr["a"] == 0.0
        assert pr["c"] == 1.0

    def test_single_value_is_zero(self):
        from graph.risk_scorer import _percent_rank

        assert _percent_rank({"only": 0.9}) == {"only": 0.0}

    def test_ties_share_strictly_lower_fraction(self):
        from graph.risk_scorer import _percent_rank

        pr = _percent_rank({"a": 1.0, "b": 1.0, "c": 2.0})
        assert pr["a"] == pr["b"]  # ties get the same percentile


# ─── §5.3: weight perturbation bracket ────────────────────────────────────────


class TestWeightPerturbation:
    def test_perturbed_weights_sum_to_one(self):
        from graph.risk_scorer import _WEIGHTS_HI, _WEIGHTS_LO

        assert sum(_WEIGHTS_LO.values()) == pytest.approx(1.0)
        assert sum(_WEIGHTS_HI.values()) == pytest.approx(1.0)

    def test_ordering_preserved(self):
        from graph.risk_scorer import _WEIGHTS_HI, _WEIGHTS_LO

        for w in (_WEIGHTS_LO, _WEIGHTS_HI):
            assert w["alpha"] > w["beta"] > w["gamma"] > w["delta"]


# ─── §5.1/§5.3: banded SPOF scoring ───────────────────────────────────────────


def _two_dept_star(n_leaves: int = 8) -> nx.DiGraph:
    G = nx.DiGraph()
    for i in range(n_leaves):
        G.add_edge("center", f"l{i}")
        G.add_edge(f"l{i}", "center")
    for node in G.nodes():
        G.nodes[node]["department"] = "A" if node == "center" else "B"
    return G


class TestScoreAllWithBands:
    def test_returns_band_keys(self):
        from graph.metrics import compute_betweenness, compute_clustering
        from graph.risk_scorer import score_all_with_bands

        G = _two_dept_star()
        bands = score_all_with_bands(G, compute_betweenness(G), compute_clustering(G))
        for detail in bands.values():
            for key in ("score", "score_lo", "score_hi", "robust_critical", "weight_sensitive"):
                assert key in detail

    def test_scores_clamped_to_unit_interval(self):
        from graph.metrics import compute_betweenness, compute_clustering
        from graph.risk_scorer import score_all_with_bands

        G = _two_dept_star()
        bands = score_all_with_bands(G, compute_betweenness(G), compute_clustering(G))
        for d in bands.values():
            assert 0.0 <= d["score"] <= 1.0
            assert 0.0 <= d["score_lo"] <= 1.0
            assert 0.0 <= d["score_hi"] <= 1.0

    def test_robust_and_sensitive_are_mutually_exclusive(self):
        from graph.metrics import compute_betweenness, compute_clustering
        from graph.risk_scorer import score_all_with_bands

        G = _two_dept_star()
        bands = score_all_with_bands(G, compute_betweenness(G), compute_clustering(G))
        for d in bands.values():
            assert not (d["robust_critical"] and d["weight_sensitive"])

    def test_signed_entropy_keeps_withdrawer_riskier(self):
        """R_signed: a withdrawing employee should not get risk subtracted away."""
        from graph.risk_scorer import score_all_with_bands

        G = nx.DiGraph()
        for n in ("a", "b", "c"):
            G.add_node(n, department="X")
        G.add_edge("a", "b")
        G.add_edge("b", "c")
        betweenness = {"a": 0.5, "b": 0.5, "c": 0.5}
        clustering = {"a": 0.5, "b": 0.5, "c": 0.5}
        trends = {"a": -0.5, "b": 0.0, "c": 0.5}  # a withdrawing, c engaging
        bands = score_all_with_bands(G, betweenness, clustering, entropy_trends=trends)
        assert bands["a"]["score"] >= bands["c"]["score"]


# ─── §9.1: CS_overlap ─────────────────────────────────────────────────────────


class TestCSOverlap:
    def test_identical_membership_is_one(self):
        from etl.tasks.compute_onboarding import compute_cs_overlap

        curr = {0: {"a", "b", "c"}}
        prev = {7: {"a", "b", "c"}}  # different ID, same members
        assert compute_cs_overlap("a", 0, 7, curr, prev) == pytest.approx(1.0)

    def test_partial_membership_overlap(self):
        from etl.tasks.compute_onboarding import compute_cs_overlap

        curr = {0: {"a", "b", "c"}}
        prev = {5: {"a", "b", "x"}}
        # {a,b,c} ∩ {a,b,x} = 2 ; ∪ = 4 → 0.5
        assert compute_cs_overlap("a", 0, 5, curr, prev) == pytest.approx(0.5)

    def test_missing_community_is_zero(self):
        from etl.tasks.compute_onboarding import compute_cs_overlap

        assert compute_cs_overlap("a", None, 5, {}, {5: {"a"}}) == 0.0
        assert compute_cs_overlap("a", 0, None, {0: {"a"}}, {}) == 0.0

    def test_id_relabeling_does_not_break_overlap(self):
        """Arbitrary Louvain ID churn must not register as instability."""
        from etl.tasks.compute_onboarding import compute_cs_overlap

        # Same people, completely different integer labels week to week.
        curr = {3: {"a", "b", "c", "d"}}
        prev = {99: {"a", "b", "c", "d"}}
        assert compute_cs_overlap("a", 3, 99, curr, prev) == pytest.approx(1.0)


# ─── §7.4.1: peer churn rate ──────────────────────────────────────────────────


class TestPeerChurnRate:
    def test_fraction_of_departed_peers(self):
        from etl.tasks.compute_peer_contagion import peer_churn_rate

        assert peer_churn_rate({"u1", "u2", "u3"}, {"u1", "u2"}) == pytest.approx(2 / 3)

    def test_no_neighbors_is_zero(self):
        from etl.tasks.compute_peer_contagion import peer_churn_rate

        assert peer_churn_rate(set(), {"u1"}) == 0.0

    def test_no_departures_is_zero(self):
        from etl.tasks.compute_peer_contagion import peer_churn_rate

        assert peer_churn_rate({"u1", "u2"}, set()) == 0.0

    def test_crosses_default_threshold(self):
        from etl.tasks.compute_peer_contagion import peer_churn_rate

        # 2 of 5 peers departed = 0.4 > 0.30 default threshold
        assert peer_churn_rate({"a", "b", "c", "d", "e"}, {"a", "b"}) > 0.30
