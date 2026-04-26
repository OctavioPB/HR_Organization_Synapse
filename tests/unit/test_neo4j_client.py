"""Unit tests for graph.neo4j_client — all Neo4j driver calls are mocked.

No running Neo4j instance is required.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _make_mock_driver(session_records=None):
    """Return a mock Neo4j driver whose session().run() yields given records."""
    mock_driver = MagicMock()
    mock_session = MagicMock()

    # Support context-manager protocol for `with driver.session() as session:`
    mock_driver.session.return_value.__enter__ = lambda s: mock_session
    mock_driver.session.return_value.__exit__ = MagicMock(return_value=False)

    if session_records is not None:
        mock_result = MagicMock()
        mock_result.__iter__ = MagicMock(return_value=iter(session_records))
        mock_result.single = MagicMock(
            return_value=session_records[0] if session_records else None
        )
        mock_session.run.return_value = mock_result

    return mock_driver, mock_session


# ─── neo4j_available ──────────────────────────────────────────────────────────


def test_neo4j_available_true_when_connectivity_succeeds():
    mock_driver = MagicMock()
    mock_driver.verify_connectivity.return_value = None

    with patch("graph.neo4j_client.get_driver", return_value=mock_driver):
        from graph.neo4j_client import neo4j_available
        assert neo4j_available() is True


def test_neo4j_available_false_when_connectivity_fails():
    mock_driver = MagicMock()
    mock_driver.verify_connectivity.side_effect = Exception("Connection refused")

    with patch("graph.neo4j_client.get_driver", return_value=mock_driver):
        from graph.neo4j_client import neo4j_available
        assert neo4j_available() is False


# ─── upsert_graph ─────────────────────────────────────────────────────────────


def test_upsert_graph_empty_input_returns_zeros():
    mock_driver, mock_session = _make_mock_driver()

    with patch("graph.neo4j_client.get_driver", return_value=mock_driver):
        from graph.neo4j_client import upsert_graph
        result = upsert_graph("2025-04-25", [], [])

    assert result == {"nodes_upserted": 0, "edges_upserted": 0}


def test_upsert_graph_counts_match_inputs():
    mock_driver, mock_session = _make_mock_driver()

    nodes = [
        {"employee_id": "a", "name": "Alice", "department": "Eng", "spof_score": 0.8},
        {"employee_id": "b", "name": "Bob",   "department": "HR",  "spof_score": 0.3},
    ]
    edges = [{"source_id": "a", "target_id": "b", "weight": 5.0}]

    with patch("graph.neo4j_client.get_driver", return_value=mock_driver):
        from graph.neo4j_client import upsert_graph
        result = upsert_graph("2025-04-25", nodes, edges)

    assert result["nodes_upserted"] == 2
    assert result["edges_upserted"] == 1


def test_upsert_graph_calls_session_run_twice():
    """upsert_graph must issue exactly two Cypher statements: one for nodes, one for edges."""
    mock_driver, mock_session = _make_mock_driver()

    with patch("graph.neo4j_client.get_driver", return_value=mock_driver):
        from graph.neo4j_client import upsert_graph
        upsert_graph("2025-04-25", [{"employee_id": "x", "name": "X", "department": "A", "spof_score": 0.0}], [])

    assert mock_session.run.call_count == 2


# ─── query_shortest_path ──────────────────────────────────────────────────────


def test_query_shortest_path_returns_none_when_no_path():
    mock_driver, mock_session = _make_mock_driver(session_records=[])
    mock_session.run.return_value.single.return_value = None

    with patch("graph.neo4j_client.get_driver", return_value=mock_driver):
        from graph.neo4j_client import query_shortest_path
        result = query_shortest_path("emp-a", "emp-b")

    assert result is None


def test_query_shortest_path_parses_record():
    record = {
        "node_ids":    ["emp-a", "emp-mid", "emp-b"],
        "names":       ["Alice", "Midway",  "Bob"],
        "departments": ["Eng",   "Sales",   "HR"],
        "hops":        2,
    }
    mock_record = MagicMock()
    mock_record.__getitem__ = lambda self, k: record[k]

    mock_driver, mock_session = _make_mock_driver()
    mock_session.run.return_value.single.return_value = mock_record

    with patch("graph.neo4j_client.get_driver", return_value=mock_driver):
        from graph.neo4j_client import query_shortest_path
        result = query_shortest_path("emp-a", "emp-b")

    assert result is not None
    assert result["hops"] == 2
    assert len(result["path"]) == 3
    assert result["path"][0]["employee_id"] == "emp-a"
    assert result["path"][2]["name"] == "Bob"


# ─── query_reachability ───────────────────────────────────────────────────────


def test_query_reachability_returns_list():
    rows = [
        {"employee_id": "emp-b", "name": "Bob", "department": "Sales", "spof_score": 0.3},
        {"employee_id": "emp-c", "name": "Carol", "department": "HR",  "spof_score": 0.5},
    ]
    mock_records = [MagicMock(**{"keys.return_value": r.keys(), **{k: v for k, v in r.items()}}) for r in rows]

    mock_driver, mock_session = _make_mock_driver()
    # Override __iter__ to return row dicts
    mock_session.run.return_value.__iter__ = MagicMock(return_value=iter(rows))

    with patch("graph.neo4j_client.get_driver", return_value=mock_driver):
        from graph.neo4j_client import query_reachability
        result = query_reachability("emp-a", hops=2)

    assert isinstance(result, list)
    assert len(result) == 2


def test_query_reachability_empty_when_no_neighbors():
    mock_driver, mock_session = _make_mock_driver()
    mock_session.run.return_value.__iter__ = MagicMock(return_value=iter([]))

    with patch("graph.neo4j_client.get_driver", return_value=mock_driver):
        from graph.neo4j_client import query_reachability
        result = query_reachability("lonely-emp", hops=2)

    assert result == []


# ─── query_knowledge_islands ──────────────────────────────────────────────────


def test_query_knowledge_islands_returns_list():
    rows = [
        {"employee_id": "emp-x", "name": "Xavier", "department": "HR", "connection_count": 0},
    ]
    mock_driver, mock_session = _make_mock_driver()
    mock_session.run.return_value.__iter__ = MagicMock(return_value=iter(rows))

    with patch("graph.neo4j_client.get_driver", return_value=mock_driver):
        from graph.neo4j_client import query_knowledge_islands
        result = query_knowledge_islands(max_size=2)

    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["connection_count"] == 0


def test_query_knowledge_islands_empty_when_none():
    mock_driver, mock_session = _make_mock_driver()
    mock_session.run.return_value.__iter__ = MagicMock(return_value=iter([]))

    with patch("graph.neo4j_client.get_driver", return_value=mock_driver):
        from graph.neo4j_client import query_knowledge_islands
        assert query_knowledge_islands() == []


# ─── query_betweenness_gds ────────────────────────────────────────────────────


def test_query_betweenness_gds_returns_empty_on_failure():
    mock_driver, mock_session = _make_mock_driver()
    mock_session.run.side_effect = Exception("GDS not installed")

    with patch("graph.neo4j_client.get_driver", return_value=mock_driver):
        from graph.neo4j_client import query_betweenness_gds
        result = query_betweenness_gds()

    assert result == []


def test_query_betweenness_gds_drops_graph_on_success():
    """GDS must drop the projected graph even after a successful run."""
    rows = [{"employee_id": "emp-a", "betweenness": 0.9}]

    mock_driver, mock_session = _make_mock_driver()
    call_results = [
        MagicMock(),                              # project
        MagicMock(**{"__iter__": lambda s: iter(rows)}),  # stream
        MagicMock(),                              # drop (separate session)
    ]
    mock_session.run.side_effect = call_results

    # Second session call (drop) also needs the context manager protocol
    mock_driver.session.return_value.__enter__ = lambda s: mock_session
    mock_driver.session.return_value.__exit__ = MagicMock(return_value=False)

    with patch("graph.neo4j_client.get_driver", return_value=mock_driver):
        from graph.neo4j_client import query_betweenness_gds
        query_betweenness_gds()

    # session.run was called at least twice (project + stream) + once more for drop
    assert mock_session.run.call_count >= 2


# ─── ensure_indexes ───────────────────────────────────────────────────────────


def test_ensure_indexes_calls_create_constraint():
    mock_driver, mock_session = _make_mock_driver()

    with patch("graph.neo4j_client.get_driver", return_value=mock_driver):
        from graph.neo4j_client import ensure_indexes
        ensure_indexes()

    assert mock_session.run.call_count == 2  # constraint + index
    first_call_cypher = mock_session.run.call_args_list[0][0][0]
    assert "CONSTRAINT" in first_call_cypher
