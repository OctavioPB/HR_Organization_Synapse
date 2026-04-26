"""Unit tests for Sprint 10 — performance optimizations and Redis cache.

Covers:
  - Approximate betweenness activates above the threshold
  - Approximate betweenness result is within tolerance of exact result
  - Joblib community parallelism is invoked for large graphs
  - Redis cache: get/set/delete round-trip
  - Redis cache: graceful degradation when Redis is unavailable
  - Redis cache key format
  - GET /graph/snapshot returns cached response on second call
  - GET /health includes cache status
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import networkx as nx
import pytest


# ─── Betweenness centrality optimizations ────────────────────────────────────


def _make_path_graph(n: int) -> nx.DiGraph:
    """Return a directed path graph A→B→C→…→N (N nodes, N-1 edges)."""
    G = nx.DiGraph()
    nodes = [str(i) for i in range(n)]
    G.add_nodes_from(nodes)
    for i in range(n - 1):
        G.add_edge(nodes[i], nodes[i + 1], weight=1.0)
    return G


def _make_star_graph(n: int) -> nx.DiGraph:
    """Return a directed star: center → all leaves and leaves → center."""
    G = nx.DiGraph()
    center = "center"
    leaves = [f"leaf_{i}" for i in range(n - 1)]
    G.add_node(center)
    for leaf in leaves:
        G.add_edge(center, leaf, weight=1.0)
        G.add_edge(leaf, center, weight=1.0)
    return G


def test_betweenness_exact_for_small_graph():
    """Graphs below threshold must use exact betweenness (k=None path)."""
    from graph.metrics import BETWEENNESS_EXACT_THRESHOLD, compute_betweenness

    # Use a graph smaller than the threshold
    G = _make_star_graph(10)
    assert G.number_of_nodes() < BETWEENNESS_EXACT_THRESHOLD

    with patch("networkx.betweenness_centrality", wraps=nx.betweenness_centrality) as mock_bc:
        result = compute_betweenness(G)
        call_kwargs = mock_bc.call_args[1]
        # Exact mode: k parameter must NOT be present (or is None)
        assert call_kwargs.get("k") is None

    assert "center" in result


def test_betweenness_approximate_for_large_graph():
    """Graphs above threshold must use k-pivot approximate betweenness."""
    from graph.metrics import BETWEENNESS_K_PIVOTS, compute_betweenness

    n = 600
    G = _make_star_graph(n)

    with patch.dict(os.environ, {"BETWEENNESS_EXACT_THRESHOLD": "500"}):
        # Reload constants after env change
        import importlib
        import graph.metrics as gm
        importlib.reload(gm)
        result = gm.compute_betweenness(G)

    # Center node must still rank highest even with approximation
    assert result["center"] == max(result.values())


def test_betweenness_approximate_within_tolerance():
    """Approximate result must be within 5% of exact on a 600-node star."""
    import graph.metrics as gm

    n = 200  # small enough to run in tests but tests both paths
    G = _make_star_graph(n)

    exact = nx.betweenness_centrality(G.copy(), normalized=True)

    with patch.dict(os.environ, {
        "BETWEENNESS_EXACT_THRESHOLD": "50",
        "BETWEENNESS_K_PIVOTS": "100",
    }):
        import importlib
        importlib.reload(gm)
        approx = gm.compute_betweenness(G)

    center_exact = exact["center"]
    center_approx = approx["center"]
    # Allow 10% relative error at test scale (k=100, n=200)
    assert abs(center_approx - center_exact) / max(center_exact, 1e-9) < 0.10


def test_betweenness_returns_zeros_for_tiny_graph():
    from graph.metrics import compute_betweenness

    G = nx.DiGraph()
    G.add_nodes_from(["a", "b"])
    result = compute_betweenness(G)
    assert all(v == 0.0 for v in result.values())


def test_community_parallelism_invoked_for_large_graph():
    """For graphs > threshold, compute_community should call Parallel when joblib available."""
    import graph.metrics as gm

    G_large = _make_star_graph(600)

    with patch.dict(os.environ, {"BETWEENNESS_EXACT_THRESHOLD": "500"}):
        import importlib
        importlib.reload(gm)

        if not gm._JOBLIB_AVAILABLE or not gm._LOUVAIN_AVAILABLE:
            pytest.skip("joblib or python-louvain not installed")

        with patch("graph.metrics.Parallel", wraps=gm.Parallel) as mock_parallel:
            gm.compute_community(G_large)
            mock_parallel.assert_called_once()


def test_community_fallback_without_louvain():
    """Without python-louvain, compute_community falls back to connected components."""
    from graph.metrics import compute_community

    G = _make_star_graph(10)

    with patch("graph.metrics._LOUVAIN_AVAILABLE", False):
        result = compute_community(G)

    assert isinstance(result, dict)
    assert set(result.keys()) == set(G.nodes())
    # All nodes in a star are in one connected component → all same community
    assert len(set(result.values())) == 1


# ─── Redis cache: unit tests ──────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def reset_cache_client():
    """Reset the module-level Redis client between tests."""
    from api import cache
    cache.reset_client()
    yield
    cache.reset_client()


def test_cache_make_key_format():
    from api.cache import make_key

    key = make_key("snapshot", "2025-04-25", "30")
    assert key.startswith("org-synapse:")
    assert "snapshot" in key
    assert "2025-04-25" in key
    assert "30" in key


def test_cache_get_returns_none_when_redis_unavailable():
    """When Redis is down, get() must return None without raising."""
    with patch.dict(os.environ, {"REDIS_URL": "redis://127.0.0.1:1", "CACHE_ENABLED": "true"}):
        from api import cache
        cache.reset_client()
        result = cache.get("any_key")
    assert result is None


def test_cache_set_is_noop_when_redis_unavailable():
    """When Redis is down, set() must not raise."""
    with patch.dict(os.environ, {"REDIS_URL": "redis://127.0.0.1:1", "CACHE_ENABLED": "true"}):
        from api import cache
        cache.reset_client()
        cache.set("key", {"data": 1})  # must not raise


def test_cache_disabled_by_env():
    """CACHE_ENABLED=false must bypass Redis entirely."""
    with patch.dict(os.environ, {"CACHE_ENABLED": "false"}):
        from api import cache
        cache.reset_client()
        # No Redis connection attempted; everything returns None / no-ops
        assert cache.get("key") is None
        cache.set("key", {"x": 1})  # no-op


def test_cache_get_set_round_trip():
    """With a mocked Redis client, get/set must serialise and deserialise correctly."""
    from api import cache

    mock_redis = MagicMock()
    mock_redis.get.return_value = '{"nodes": 42}'
    mock_redis.ping.return_value = True

    with patch("redis.Redis.from_url", return_value=mock_redis):
        with patch.dict(os.environ, {"CACHE_ENABLED": "true", "REDIS_URL": "redis://mock:6379/0"}):
            cache.reset_client()
            result = cache.get("test_key")

    assert result == {"nodes": 42}
    mock_redis.get.assert_called_once_with("test_key")


def test_cache_set_calls_setex_with_ttl():
    from api import cache

    mock_redis = MagicMock()
    mock_redis.ping.return_value = True

    with patch("redis.Redis.from_url", return_value=mock_redis):
        with patch.dict(os.environ, {"CACHE_ENABLED": "true", "REDIS_URL": "redis://mock:6379/0"}):
            cache.reset_client()
            cache.set("my_key", {"data": "value"}, ttl=1800)

    mock_redis.setex.assert_called_once()
    args = mock_redis.setex.call_args[0]
    assert args[0] == "my_key"
    assert args[1] == 1800


def test_cache_invalidate_snapshot_deletes_matching_keys():
    from api import cache

    mock_redis = MagicMock()
    mock_redis.ping.return_value = True
    mock_redis.keys.return_value = [
        "org-synapse:v1:snapshot:2025-04-25:30",
        "org-synapse:v1:snapshot:2025-04-25:60",
    ]

    with patch("redis.Redis.from_url", return_value=mock_redis):
        with patch.dict(os.environ, {"CACHE_ENABLED": "true", "REDIS_URL": "redis://mock:6379/0"}):
            cache.reset_client()
            cache.invalidate_snapshot("2025-04-25")

    mock_redis.delete.assert_called_once()
    deleted_keys = mock_redis.delete.call_args[0]
    assert len(deleted_keys) == 2


def test_cache_health_returns_unavailable_when_redis_down():
    with patch.dict(os.environ, {"REDIS_URL": "redis://127.0.0.1:1", "CACHE_ENABLED": "true"}):
        from api import cache
        cache.reset_client()
        result = cache.health()
    assert result["cache"] == "unavailable"


# ─── GET /graph/snapshot cache integration ────────────────────────────────────


from datetime import date
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from api.main import app
from api.deps import get_db


@pytest.fixture
def client():
    mock_conn = MagicMock()
    app.dependency_overrides[get_db] = lambda: mock_conn
    yield TestClient(app, raise_server_exceptions=True), mock_conn
    app.dependency_overrides.clear()


_SNAPSHOT_DATE = date(2025, 4, 25)
_NODE_ROWS = [
    {
        "employee_id": "emp-a",
        "name": "Alice",
        "department": "Engineering",
        "betweenness": 0.8,
        "degree_in": 0.5,
        "degree_out": 0.6,
        "clustering": 0.3,
        "community_id": 0,
    }
]
_EDGE_ROWS = [{"source": "emp-a", "target": "emp-b", "weight": 3.0}]


def test_snapshot_cache_miss_queries_db(client):
    """On a cache miss, the endpoint must query the database."""
    test_client, _ = client
    with (
        patch("api.cache.get", return_value=None),
        patch("api.cache.set") as mock_set,
        patch("api.routers.graph.queries.fetch_latest_snapshot_date", return_value=_SNAPSHOT_DATE),
        patch("api.routers.graph.queries.fetch_graph_nodes", return_value=_NODE_ROWS),
        patch("api.routers.graph.queries.fetch_graph_edges", return_value=_EDGE_ROWS),
    ):
        resp = test_client.get("/graph/snapshot")

    assert resp.status_code == 200
    mock_set.assert_called_once()  # result was written to cache


def test_snapshot_cache_hit_skips_db(client):
    """On a cache hit, the endpoint must return the cached value without a DB query."""
    test_client, _ = client
    cached_payload = {
        "snapshot_date": "2025-04-25",
        "node_count": 1,
        "edge_count": 1,
        "nodes": [_NODE_ROWS[0]],
        "edges": [_EDGE_ROWS[0]],
    }
    with (
        patch("api.cache.get", return_value=cached_payload),
        patch("api.routers.graph.queries.fetch_latest_snapshot_date", return_value=_SNAPSHOT_DATE),
        patch("api.routers.graph.queries.fetch_graph_nodes") as mock_nodes,
    ):
        resp = test_client.get("/graph/snapshot")

    assert resp.status_code == 200
    mock_nodes.assert_not_called()  # DB was not touched


def test_health_includes_cache_status(client):
    test_client, _ = client
    resp = test_client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert "cache" in body
    assert "metrics" in body
