"""Targeted coverage tests for the four core graph modules.

Each test class corresponds to one module and covers a specific branch or
pure function that the existing unit tests do not reach.  Tests are kept
minimal — they test the logic, not just the lines.

Modules targeted:
    graph/builder.py       — graph_to_adjacency, dict-row branch, edge accumulation
    graph/metrics.py       — empty-graph community, nx<3.0 fallback, approx betweenness,
                             write_snapshot (mocked DB)
    graph/risk_scorer.py   — all flag tiers in write_scores (mocked DB), score_all wrapper
    graph/succession.py    — empty-source domain overlap, source-not-in-graph early return,
                             load_node_metrics + write_succession (mocked DB)
    graph/silo_detector.py — write_alerts empty path, write_alerts mocked DB path
"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, call, patch

import networkx as nx
import pytest

# ─── builder ──────────────────────────────────────────────────────────────────

from graph.builder import build_graph, graph_to_adjacency


class TestGraphToAdjacency:
    def _weighted_graph(self) -> nx.DiGraph:
        G = nx.DiGraph()
        G.add_node("A", department="Eng")
        G.add_node("B", department="Sales")
        G.add_edge("A", "B", weight=3.0)
        G.add_edge("B", "A", weight=1.5)
        return G

    def test_nodes_serialised(self):
        G = self._weighted_graph()
        adj = graph_to_adjacency(G)
        node_ids = {n["id"] for n in adj["nodes"]}
        assert node_ids == {"A", "B"}

    def test_node_department_included(self):
        G = self._weighted_graph()
        adj = graph_to_adjacency(G)
        dept_map = {n["id"]: n["department"] for n in adj["nodes"]}
        assert dept_map["A"] == "Eng"
        assert dept_map["B"] == "Sales"

    def test_edges_serialised_with_weight(self):
        G = self._weighted_graph()
        adj = graph_to_adjacency(G)
        edge_set = {(e["source"], e["target"]): e["weight"] for e in adj["edges"]}
        assert edge_set[("A", "B")] == pytest.approx(3.0)
        assert edge_set[("B", "A")] == pytest.approx(1.5)

    def test_empty_graph(self):
        adj = graph_to_adjacency(nx.DiGraph())
        assert adj["nodes"] == []
        assert adj["edges"] == []

    def test_node_missing_department_defaults_to_empty_string(self):
        G = nx.DiGraph()
        G.add_node("X")   # no department attribute
        G.add_edge("X", "X", weight=1.0)
        adj = graph_to_adjacency(G)
        assert adj["nodes"][0]["department"] == ""


class TestBuildGraphDictRow:
    """build_graph() accepts both plain tuples and mapping rows (psycopg2 RealDictRow)."""

    def test_dict_row_branch(self):
        """Rows with .keys() should be handled via tuple(row.values())[:5]."""

        class DictRow(dict):
            def keys(self):
                return super().keys()

        rows = [
            DictRow(
                source_id="E1",
                target_id="E2",
                weight=2.0,
                source_dept="Eng",
                target_dept="Sales",
            )
        ]
        G = build_graph(rows)
        assert G.has_node("E1")
        assert G.has_node("E2")
        assert G["E1"]["E2"]["weight"] == pytest.approx(2.0)

    def test_edge_weight_accumulates(self):
        """Multiple interactions between the same pair accumulate weight."""
        rows = [
            ("E1", "E2", 1.0, "Eng", "Sales"),
            ("E1", "E2", 1.0, "Eng", "Sales"),
            ("E1", "E2", 1.0, "Eng", "Sales"),
        ]
        G = build_graph(rows)
        assert G["E1"]["E2"]["weight"] == pytest.approx(3.0)

    def test_department_set_on_nodes(self):
        rows = [("E1", "E2", 1.0, "Engineering", "HR")]
        G = build_graph(rows)
        assert G.nodes["E1"]["department"] == "Engineering"
        assert G.nodes["E2"]["department"] == "HR"


# ─── metrics ──────────────────────────────────────────────────────────────────

from graph.metrics import (
    BETWEENNESS_EXACT_THRESHOLD,
    compute_betweenness,
    compute_community,
    write_snapshot,
)


class TestComputeCommunityEdgeCases:
    def test_empty_graph_returns_empty_dict(self):
        assert compute_community(nx.DiGraph()) == {}

    def test_single_node_graph(self):
        G = nx.DiGraph()
        G.add_node("solo")
        result = compute_community(G)
        assert "solo" in result

    def test_networkx_louvain_attribute_error_fallback(self):
        """When nx.community.louvain_communities raises AttributeError,
        the function falls back to connected components."""
        G = nx.DiGraph()
        G.add_edge("A", "B", weight=1.0)
        G.add_edge("B", "A", weight=1.0)
        G.add_edge("C", "D", weight=1.0)
        G.add_edge("D", "C", weight=1.0)

        import graph.metrics as metrics_mod

        original = metrics_mod._LOUVAIN_AVAILABLE
        try:
            metrics_mod._LOUVAIN_AVAILABLE = False
            with patch("graph.metrics.nx.community.louvain_communities",
                       side_effect=AttributeError):
                result = compute_community(G)

            # Two connected components → two distinct community IDs
            assert result["A"] == result["B"]
            assert result["C"] == result["D"]
            assert result["A"] != result["C"]
        finally:
            metrics_mod._LOUVAIN_AVAILABLE = original


class TestComputeBetweennessApproximate:
    def test_approximate_path_used_for_large_graphs(self):
        """Graphs above BETWEENNESS_EXACT_THRESHOLD use k-pivot approximation."""
        import graph.metrics as metrics_mod

        original = metrics_mod.BETWEENNESS_EXACT_THRESHOLD
        try:
            # Lower threshold so the current small graph triggers approximate path
            metrics_mod.BETWEENNESS_EXACT_THRESHOLD = 3
            G = nx.DiGraph()
            for i in range(5):
                G.add_edge(str(i), str((i + 1) % 5), weight=1.0)
                G.add_edge(str((i + 1) % 5), str(i), weight=1.0)

            result = compute_betweenness(G)
            assert len(result) == 5
            for v in result.values():
                assert 0.0 <= v <= 1.0
        finally:
            metrics_mod.BETWEENNESS_EXACT_THRESHOLD = original


class TestWriteSnapshot:
    def _mock_conn(self):
        cur = MagicMock()
        conn = MagicMock()
        conn.__enter__ = MagicMock(return_value=conn)
        conn.__exit__ = MagicMock(return_value=False)
        conn.cursor.return_value.__enter__ = MagicMock(return_value=cur)
        conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        return conn, cur

    def test_write_snapshot_calls_execute_batch(self):
        conn, _cur = self._mock_conn()
        with patch("graph.metrics.get_conn", return_value=conn):
            with patch("psycopg2.extras.execute_batch") as eb:
                write_snapshot(
                    snapshot_date=date(2025, 1, 1),
                    betweenness={"E1": 0.5},
                    degree_in={"E1": 0.3},
                    degree_out={"E1": 0.4},
                    clustering={"E1": 0.2},
                    communities={"E1": 0},
                )
                assert eb.called
                rows = eb.call_args[0][2]
                assert len(rows) == 1
                assert rows[0][1] == "E1"

    def test_write_snapshot_union_of_keys(self):
        """All nodes appearing in any metric dict are written."""
        conn, cur = self._mock_conn()
        with patch("graph.metrics.get_conn", return_value=conn):
            with patch("psycopg2.extras.execute_batch") as eb:
                write_snapshot(
                    snapshot_date=date(2025, 1, 1),
                    betweenness={"E1": 0.5, "E2": 0.1},
                    degree_in={"E1": 0.3, "E3": 0.0},
                    degree_out={},
                    clustering={},
                    communities={"E1": 0},
                )
                rows = eb.call_args[0][2]
                node_ids = {r[1] for r in rows}
                assert node_ids == {"E1", "E2", "E3"}


# ─── risk_scorer ──────────────────────────────────────────────────────────────

from graph.risk_scorer import score_all, write_scores


class TestWriteScoresFlagTiers:
    """write_scores() maps scores to flag tiers — test every branch of _flag()."""

    def _mock_conn(self):
        conn = MagicMock()
        conn.__enter__ = MagicMock(return_value=conn)
        conn.__exit__ = MagicMock(return_value=False)
        conn.cursor.return_value.__enter__ = MagicMock(
            return_value=MagicMock()
        )
        conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        return conn

    def _run(self, scores, entropy_trends=None, bands=None):
        conn = self._mock_conn()
        written_rows = []

        def fake_execute_batch(cur, sql, rows, page_size=500):
            written_rows.extend(rows)

        with patch("graph.risk_scorer.get_conn", return_value=conn):
            with patch("psycopg2.extras.execute_batch", side_effect=fake_execute_batch):
                write_scores(
                    scores=scores,
                    entropy_trends=entropy_trends or {},
                    snapshot_date=date(2025, 1, 1),
                    bands=bands,
                )
        # row layout: (id, date, emp_id, score, entropy, flag, lo, hi, robust)
        return {r[2]: r[5] for r in written_rows}

    def test_flag_critical_robust(self):
        """score >= 0.7 with robust_critical=True → 'critical'."""
        flags = self._run(
            scores={"E1": 0.8},
            bands={"E1": {"robust_critical": True, "weight_sensitive": False,
                          "score_lo": 0.75, "score_hi": 0.85}},
        )
        assert flags["E1"] == "critical"

    def test_flag_critical_uncertain(self):
        """score >= 0.7 with robust_critical=False → 'critical_uncertain'."""
        flags = self._run(
            scores={"E1": 0.72},
            bands={"E1": {"robust_critical": False, "weight_sensitive": True,
                          "score_lo": 0.65, "score_hi": 0.75}},
        )
        assert flags["E1"] == "critical_uncertain"

    def test_flag_warning(self):
        flags = self._run(scores={"E1": 0.60})
        assert flags["E1"] == "warning"

    def test_flag_elevated(self):
        flags = self._run(scores={"E1": 0.45})
        assert flags["E1"] == "elevated"

    def test_flag_withdrawing(self):
        """score < 0.4 AND negative entropy_trend → 'withdrawing'."""
        flags = self._run(
            scores={"E1": 0.20},
            entropy_trends={"E1": -0.05},
        )
        assert flags["E1"] == "withdrawing"

    def test_flag_normal(self):
        """score < 0.4, entropy >= 0 → 'normal'."""
        flags = self._run(scores={"E1": 0.20})
        assert flags["E1"] == "normal"

    def test_multiple_employees_in_one_call(self):
        scores = {"A": 0.85, "B": 0.55, "C": 0.42, "D": 0.10}
        flags = self._run(
            scores=scores,
            entropy_trends={"D": -0.1},
            bands={
                "A": {"robust_critical": True, "weight_sensitive": False,
                      "score_lo": 0.80, "score_hi": 0.90},
            },
        )
        assert flags["A"] == "critical"
        assert flags["B"] == "warning"
        assert flags["C"] == "elevated"
        assert flags["D"] == "withdrawing"


class TestScoreAll:
    """score_all() is a thin wrapper over score_all_with_bands()."""

    def test_returns_float_per_node(self):
        G = nx.DiGraph()
        G.add_edge("A", "B", weight=1.0)
        G.add_edge("B", "A", weight=1.0)
        G.nodes["A"]["department"] = "Eng"
        G.nodes["B"]["department"] = "Sales"

        from graph.metrics import compute_betweenness, compute_clustering

        betweenness = compute_betweenness(G)
        clustering = compute_clustering(G)

        scores = score_all(G, betweenness, clustering)
        assert set(scores.keys()) == {"A", "B"}
        for v in scores.values():
            assert isinstance(v, float)
            assert 0.0 <= v <= 1.0

    def test_custom_weights_accepted(self):
        """score_all accepts a weights dict and produces scores in [0, 1]."""
        # Bridge graph: two cliques connected by a single bridge pair.
        # The bridge nodes have very different betweenness from non-bridge nodes,
        # so any consistent weight set produces valid (though possibly equal) scores.
        G = nx.DiGraph()
        for u, v in [("A1","A2"),("A2","A3"),("A3","A1"),
                     ("B1","B2"),("B2","B3"),("B3","B1")]:
            G.add_edge(u, v, weight=1.0)
            G.add_edge(v, u, weight=1.0)
        G.add_edge("A1", "B1", weight=1.0)
        G.add_edge("B1", "A1", weight=1.0)
        for n in G.nodes():
            G.nodes[n]["department"] = "Eng" if n.startswith("A") else "Sales"

        from graph.metrics import compute_betweenness, compute_clustering

        scores = score_all(
            G,
            compute_betweenness(G),
            compute_clustering(G),
            weights={"alpha": 0.7, "beta": 0.1, "gamma": 0.1, "delta": 0.1},
        )
        assert set(scores.keys()) == set(G.nodes())
        for v in scores.values():
            assert 0.0 <= v <= 1.0


# ─── succession ───────────────────────────────────────────────────────────────

from graph.succession import (
    compute_domain_overlap,
    find_border_employees,
    score_candidates,
)


class TestComputeDomainOverlapEdgeCases:
    def test_empty_source_domains_returns_zero(self):
        assert compute_domain_overlap(set(), {"python", "ml"}) == pytest.approx(0.0)

    def test_both_empty(self):
        assert compute_domain_overlap(set(), set()) == pytest.approx(0.0)

    def test_no_overlap(self):
        assert compute_domain_overlap({"a", "b"}, {"c", "d"}) == pytest.approx(0.0)

    def test_full_overlap(self):
        assert compute_domain_overlap({"a", "b"}, {"a", "b", "c"}) == pytest.approx(1.0)

    def test_partial_overlap(self):
        result = compute_domain_overlap({"a", "b", "c"}, {"a", "b"})
        assert result == pytest.approx(2 / 3)


class TestFindBorderEmployees:
    def test_no_source_community_treats_source_as_singleton(self):
        """source_id not in community_map → its community is just itself."""
        G = nx.DiGraph()
        G.add_edge("S", "X", weight=1.0)
        G.add_edge("Y", "S", weight=1.0)

        community_map: dict[str, int | None] = {"S": None, "X": 1, "Y": 2}
        result = find_border_employees("S", community_map, G)
        # X and Y border S (successor and predecessor)
        assert "X" in result
        assert "Y" in result
        assert "S" not in result

    def test_source_not_in_graph_returns_empty(self):
        G = nx.DiGraph()
        G.add_node("X")
        community_map = {"ghost": 0, "X": 0}
        result = find_border_employees("ghost", community_map, G)
        # ghost is not in G, so no edges to traverse
        assert result == set()


class TestScoreCandidatesEdgeCases:
    def test_source_not_in_graph_returns_empty(self):
        G = nx.DiGraph()
        G.add_node("C1")
        result = score_candidates("MISSING", G, {}, {}, ["C1"])
        assert result == []

    def test_candidate_equal_to_source_skipped(self):
        G = nx.DiGraph()
        G.add_edge("S", "C1", weight=1.0)
        result = score_candidates("S", G, {"C1": {"clustering": 0.5}}, {}, ["S", "C1"])
        assert all(r["candidate_employee_id"] != "S" for r in result)

    def test_candidate_not_in_graph_skipped(self):
        G = nx.DiGraph()
        G.add_node("S")
        result = score_candidates("S", G, {}, {}, ["GHOST"])
        assert result == []


class TestLoadNodeMetricsMock:
    def test_returns_dict_keyed_by_employee_id(self):
        from graph.succession import load_node_metrics

        row1 = ("emp-uuid-1", 0.5, 0.3, 2)
        row2 = ("emp-uuid-2", 0.1, 0.8, 1)

        cur = MagicMock()
        cur.fetchall.return_value = [row1, row2]
        conn = MagicMock()
        conn.cursor.return_value.__enter__ = MagicMock(return_value=cur)
        conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        result = load_node_metrics(date(2025, 1, 1), conn)
        assert "emp-uuid-1" in result
        assert result["emp-uuid-1"]["betweenness"] == pytest.approx(0.5)
        assert result["emp-uuid-1"]["clustering"] == pytest.approx(0.3)
        assert result["emp-uuid-1"]["community_id"] == 2


# ─── silo_detector ────────────────────────────────────────────────────────────

from graph.silo_detector import SiloAlert, write_alerts


class TestWriteAlerts:
    def test_empty_alerts_returns_without_db_call(self):
        with patch("graph.silo_detector.get_conn") as mock_conn:
            write_alerts([], snapshot_date=date(2025, 1, 1))
            mock_conn.assert_not_called()

    def test_writes_one_row_per_alert(self):
        alerts = [
            SiloAlert(
                community_id=1,
                members=["E1", "E2"],
                departments={"HR"},
                isolation_ratio=5.5,
                severity="high",
            ),
            SiloAlert(
                community_id=2,
                members=["E3"],
                departments={"Sales"},
                isolation_ratio=7.0,
                severity="critical",
            ),
        ]

        written_rows = []

        def fake_execute_batch(cur, sql, rows, **kw):
            written_rows.extend(rows)

        conn = MagicMock()
        conn.__enter__ = MagicMock(return_value=conn)
        conn.__exit__ = MagicMock(return_value=False)
        conn.cursor.return_value.__enter__ = MagicMock(return_value=MagicMock())
        conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        with patch("graph.silo_detector.get_conn", return_value=conn):
            with patch("psycopg2.extras.execute_batch", side_effect=fake_execute_batch):
                write_alerts(alerts, date(2025, 1, 1))

        assert len(written_rows) == 2
        alert_types = {r[1] for r in written_rows}
        assert alert_types == {"silo"}
        severities = {r[2] for r in written_rows}
        assert severities == {"high", "critical"}
