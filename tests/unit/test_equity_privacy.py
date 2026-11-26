"""Privacy guard: structural-equity aggregates must never expose small groups.

A median over 1–4 people can reveal an individual's network position, so groups
below MIN_GROUP_SIZE are (a) never persisted by the equity task and (b) never
returned by /equity/centrality-distribution, even if older rows exist.
"""

from __future__ import annotations

from datetime import datetime, UTC
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from api.deps import get_db
from api.main import app
from etl.tasks.compute_equity import MIN_GROUP_SIZE, task_compute_equity

_LATEST = datetime(2026, 6, 25, tzinfo=UTC)


def _row(group: str, n: int, median: float = 0.01) -> dict:
    return {
        "group_value": group,
        "metric": "betweenness",
        "median_score": median,
        "p25_score": median / 2,
        "p75_score": median * 2,
        "member_count": n,
        "below_org_median": False,
    }


def _conn_returning(rows: list[dict]) -> MagicMock:
    cur = MagicMock()
    cur.fetchone.return_value = {"latest": _LATEST}
    cur.fetchall.return_value = rows
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur
    return conn


@pytest.fixture
def client_with():
    def _make(rows: list[dict]) -> TestClient:
        conn = _conn_returning(rows)
        app.dependency_overrides[get_db] = lambda: conn
        return TestClient(app)

    yield _make
    app.dependency_overrides.clear()


def test_default_min_group_size_is_five():
    assert MIN_GROUP_SIZE == 5


def test_router_suppresses_groups_below_minimum(client_with):
    client = client_with([_row("1-3y", 12), _row("5y+", MIN_GROUP_SIZE - 1), _row("0-1y", 1)])
    resp = client.get("/equity/centrality-distribution?dimension=tenure_band&metric=betweenness")
    assert resp.status_code == 200
    body = resp.json()
    assert [g["group_value"] for g in body["groups"]] == ["1-3y"]
    assert body["suppressed_groups"] == 2
    assert body["min_group_size"] == MIN_GROUP_SIZE


def test_router_keeps_group_exactly_at_minimum(client_with):
    client = client_with([_row("3-5y", MIN_GROUP_SIZE)])
    body = client.get("/equity/centrality-distribution").json()
    assert [g["group_value"] for g in body["groups"]] == ["3-5y"]
    assert body["suppressed_groups"] == 0


def test_router_never_leaks_suppressed_labels_or_stats(client_with):
    client = client_with([_row("1-3y", 20), _row("secret_group", 2, median=0.999)])
    text = client.get("/equity/centrality-distribution").text
    assert "secret_group" not in text
    assert "0.999" not in text


def test_task_never_persists_small_groups():
    """Rows for a group below MIN_GROUP_SIZE must never be written."""
    people = [
        {"gender_group": "big", "tenure_band": "1-3y", "level_band": "ic", "betweenness": 0.01 * i, "degree": i}
        for i in range(12)
    ] + [
        {"gender_group": "tiny", "tenure_band": "1-3y", "level_band": "ic", "betweenness": 0.5, "degree": 3}
        for _ in range(MIN_GROUP_SIZE - 1)
    ]
    cur = MagicMock()
    cur.fetchone.return_value = {"cnt": len(people)}
    cur.fetchall.return_value = people
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur

    task_compute_equity("2026-06-25", conn)

    inserts = [c.args[1] for c in cur.execute.call_args_list if "INSERT INTO structural_equity_scores" in c.args[0]]
    written_groups = {params[1] for params in inserts}
    assert "tiny" not in written_groups
    assert "big" not in written_groups  # a single reportable group has no "everyone else" to compare with
