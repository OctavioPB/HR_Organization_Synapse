"""Data access functions for the FastAPI layer.

Each function accepts an open psycopg2 connection and returns plain dicts.
This separation keeps routers thin and makes unit testing trivial:
mock these functions instead of mocking psycopg2 internals.
"""

from datetime import date, timedelta
from typing import Any


# ─── Helpers ──────────────────────────────────────────────────────────────────


def fetch_latest_snapshot_date(conn) -> date | None:
    """Return the most recent snapshot_date in graph_snapshots, or None."""
    with conn.cursor() as cur:
        cur.execute("SELECT MAX(snapshot_date) FROM graph_snapshots")
        row = cur.fetchone()
    return row["max"] if row and row["max"] else None


# ─── Graph ────────────────────────────────────────────────────────────────────


def fetch_graph_nodes(snapshot_date: date, conn) -> list[dict]:
    """Return node metrics for all employees in graph_snapshots on snapshot_date.

    Joins with employees to include name and department.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                gs.employee_id::text,
                e.name,
                e.department,
                gs.betweenness,
                gs.degree_in,
                gs.degree_out,
                gs.clustering,
                gs.community_id
            FROM graph_snapshots gs
            JOIN employees e ON gs.employee_id = e.id
            WHERE gs.snapshot_date = %s
            ORDER BY gs.betweenness DESC NULLS LAST
            """,
            (snapshot_date,),
        )
        return [dict(r) for r in cur.fetchall()]


def fetch_graph_edges(snapshot_date: date, window_days: int, conn) -> list[dict]:
    """Return aggregated directed edges for the rolling window ending at snapshot_date."""
    end_ts = f"{snapshot_date} 23:59:59+00"
    start_ts = (snapshot_date - timedelta(days=window_days)).isoformat()
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                source_id::text AS source,
                target_id::text AS target,
                SUM(weight)     AS weight
            FROM raw_events
            WHERE ts BETWEEN %s::timestamptz AND %s::timestamptz
            GROUP BY source_id, target_id
            ORDER BY weight DESC
            """,
            (start_ts, end_ts),
        )
        return [dict(r) for r in cur.fetchall()]


def fetch_ego_network(
    employee_id: str,
    snapshot_date: date,
    window_days: int,
    conn,
) -> dict[str, Any]:
    """Return the 2-hop ego-network centred on employee_id.

    Returns dict with keys: node, neighbors, edges.
    Raises KeyError if the employee has no snapshot on snapshot_date.
    """
    # Node metrics
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                gs.employee_id::text,
                e.name,
                e.department,
                gs.betweenness,
                gs.degree_in,
                gs.degree_out,
                gs.clustering,
                gs.community_id
            FROM graph_snapshots gs
            JOIN employees e ON gs.employee_id = e.id
            WHERE gs.snapshot_date = %s
              AND gs.employee_id = %s::uuid
            """,
            (snapshot_date, employee_id),
        )
        node_row = cur.fetchone()

    if not node_row:
        return {}

    node = dict(node_row)

    # 1-hop neighbours: all employees who interacted with this one
    end_ts = f"{snapshot_date} 23:59:59+00"
    start_ts = (snapshot_date - timedelta(days=window_days)).isoformat()
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT
                CASE WHEN source_id::text = %s THEN target_id ELSE source_id END AS neighbor_id
            FROM raw_events
            WHERE ts BETWEEN %s::timestamptz AND %s::timestamptz
              AND (source_id::text = %s OR target_id::text = %s)
            """,
            (employee_id, start_ts, end_ts, employee_id, employee_id),
        )
        neighbor_ids = [str(r["neighbor_id"]) for r in cur.fetchall()]

    # Neighbour metrics from snapshot
    if neighbor_ids:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    gs.employee_id::text,
                    e.name,
                    e.department,
                    gs.betweenness,
                    gs.degree_in,
                    gs.degree_out,
                    gs.clustering,
                    gs.community_id
                FROM graph_snapshots gs
                JOIN employees e ON gs.employee_id = e.id
                WHERE gs.snapshot_date = %s
                  AND gs.employee_id = ANY(%s::uuid[])
                """,
                (snapshot_date, neighbor_ids),
            )
            neighbors = [dict(r) for r in cur.fetchall()]
    else:
        neighbors = []

    # Edges within the ego-network (node + 1-hop neighbors)
    all_ids = [employee_id] + neighbor_ids
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                source_id::text AS source,
                target_id::text AS target,
                SUM(weight)     AS weight
            FROM raw_events
            WHERE ts BETWEEN %s::timestamptz AND %s::timestamptz
              AND source_id = ANY(%s::uuid[])
              AND target_id = ANY(%s::uuid[])
            GROUP BY source_id, target_id
            """,
            (start_ts, end_ts, all_ids, all_ids),
        )
        edges = [dict(r) for r in cur.fetchall()]

    return {"node": node, "neighbors": neighbors, "edges": edges}


def fetch_communities(snapshot_date: date, conn) -> list[dict]:
    """Return community summaries with members, departments, and silo flag."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                gs.community_id,
                gs.employee_id::text,
                e.department
            FROM graph_snapshots gs
            JOIN employees e ON gs.employee_id = e.id
            WHERE gs.snapshot_date = %s
              AND gs.community_id IS NOT NULL
            ORDER BY gs.community_id
            """,
            (snapshot_date,),
        )
        rows = cur.fetchall()

    # Group by community
    from collections import defaultdict
    comm_members: dict[int, list[str]] = defaultdict(list)
    comm_depts: dict[int, set[str]] = defaultdict(set)
    for r in rows:
        comm_members[r["community_id"]].append(r["employee_id"])
        comm_depts[r["community_id"]].add(r["department"])

    # Check which communities have active silo alerts
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT (affected_entities->>'community_id')::int AS community_id
            FROM alerts
            WHERE type = 'silo'
              AND resolved = false
              AND fired_at > NOW() - INTERVAL '7 days'
            """
        )
        silo_comm_ids = {r["community_id"] for r in cur.fetchall()}

    return [
        {
            "community_id": cid,
            "member_count": len(members),
            "members": members,
            "departments": sorted(comm_depts[cid]),
            "is_silo": cid in silo_comm_ids,
        }
        for cid, members in sorted(comm_members.items())
    ]


# ─── Risk ─────────────────────────────────────────────────────────────────────


def fetch_risk_scores(
    snapshot_date: date,
    top: int,
    conn,
) -> list[dict]:
    """Return top-N employees by SPOF score for the given snapshot date."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                rs.employee_id::text,
                e.name,
                e.department,
                rs.spof_score,
                rs.entropy_trend,
                rs.flag,
                rs.scored_at::date AS scored_at
            FROM risk_scores rs
            JOIN employees e ON rs.employee_id = e.id
            WHERE rs.scored_at::date = %s
            ORDER BY rs.spof_score DESC
            LIMIT %s
            """,
            (snapshot_date, top),
        )
        return [dict(r) for r in cur.fetchall()]


def fetch_critical_nodes(threshold: float, conn) -> list[dict]:
    """Return employees with spof_score >= threshold from the most recent scoring run."""
    with conn.cursor() as cur:
        cur.execute(
            """
            WITH latest AS (
                SELECT MAX(scored_at::date) AS d FROM risk_scores
            )
            SELECT
                rs.employee_id::text,
                e.name,
                e.department,
                rs.spof_score,
                rs.entropy_trend,
                rs.flag,
                rs.scored_at::date AS scored_at
            FROM risk_scores rs
            JOIN employees e ON rs.employee_id = e.id
            JOIN latest ON rs.scored_at::date = latest.d
            WHERE rs.spof_score >= %s
            ORDER BY rs.spof_score DESC
            """,
            (threshold,),
        )
        return [dict(r) for r in cur.fetchall()]


def fetch_employee_risk_history(
    employee_id: str,
    days: int,
    conn,
) -> list[dict]:
    """Return SPOF score history for one employee over the last N days."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                rs.scored_at::date AS scored_at,
                rs.spof_score,
                rs.entropy_trend,
                rs.flag
            FROM risk_scores rs
            WHERE rs.employee_id = %s::uuid
              AND rs.scored_at >= NOW() - (%s || ' days')::interval
            ORDER BY rs.scored_at
            """,
            (employee_id, days),
        )
        return [dict(r) for r in cur.fetchall()]


# ─── Alerts ───────────────────────────────────────────────────────────────────


def fetch_silo_alerts(conn) -> list[dict]:
    """Return unresolved silo alerts, most recent first."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                id::text,
                fired_at,
                type,
                severity,
                affected_entities,
                details,
                resolved
            FROM alerts
            WHERE type = 'silo'
              AND resolved = false
            ORDER BY fired_at DESC
            LIMIT 100
            """
        )
        return [dict(r) for r in cur.fetchall()]


def fetch_entropy_alerts(conn) -> list[dict]:
    """Return unresolved withdrawing / connectivity_anomaly alerts."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                id::text,
                fired_at,
                type,
                severity,
                affected_entities,
                details,
                resolved
            FROM alerts
            WHERE type IN ('withdrawing', 'connectivity_anomaly', 'spof_critical')
              AND resolved = false
            ORDER BY fired_at DESC
            LIMIT 200
            """
        )
        return [dict(r) for r in cur.fetchall()]


def fetch_alert_history(days: int, conn) -> list[dict]:
    """Return all alerts fired within the last N days."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                id::text,
                fired_at,
                type,
                severity,
                affected_entities,
                details,
                resolved,
                resolved_at
            FROM alerts
            WHERE fired_at >= NOW() - (%s || ' days')::interval
            ORDER BY fired_at DESC
            LIMIT 1000
            """,
            (days,),
        )
        return [dict(r) for r in cur.fetchall()]


# ─── Churn risk ───────────────────────────────────────────────────────────────


def fetch_churn_scores(
    scored_at: date,
    top: int,
    min_prob: float,
    conn,
) -> list[dict]:
    """Return churn scores for the given scored_at date.

    Args:
        scored_at: Date to query.
        top: Maximum number of rows to return.
        min_prob: Only return rows where churn_prob >= min_prob.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                cs.employee_id::text,
                e.name,
                e.department,
                cs.churn_prob,
                cs.risk_tier,
                cs.model_version,
                cs.scored_at
            FROM churn_scores cs
            JOIN employees e ON cs.employee_id = e.id
            WHERE cs.scored_at = %s
              AND cs.churn_prob >= %s
            ORDER BY cs.churn_prob DESC
            LIMIT %s
            """,
            (scored_at, min_prob, top),
        )
        return [dict(r) for r in cur.fetchall()]


def fetch_latest_churn_date(conn) -> date | None:
    """Return the most recent scored_at date in churn_scores, or None."""
    with conn.cursor() as cur:
        cur.execute("SELECT MAX(scored_at) FROM churn_scores")
        row = cur.fetchone()
    return row["max"] if row and row["max"] else None


# ─── Temporal graph analysis ──────────────────────────────────────────────────


def fetch_temporal_flow(
    employee_id: str,
    weeks: int,
    conn,
) -> list[dict]:
    """Return weekly graph metric time series for one employee.

    Args:
        employee_id: UUID string.
        weeks: Number of most-recent weekly snapshots to return.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                gs.snapshot_date,
                gs.betweenness,
                gs.degree_in,
                gs.degree_out,
                gs.clustering,
                gs.community_id
            FROM graph_snapshots gs
            WHERE gs.employee_id = %s::uuid
            ORDER BY gs.snapshot_date DESC
            LIMIT %s
            """,
            (employee_id, weeks),
        )
        rows = [dict(r) for r in cur.fetchall()]
    rows.reverse()  # oldest first
    return rows


def fetch_employee_temporal_meta(employee_id: str, conn) -> dict | None:
    """Return name and department for one employee."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT name, department FROM employees WHERE id = %s::uuid",
            (employee_id,),
        )
        row = cur.fetchone()
    return dict(row) if row else None


def fetch_temporal_anomaly_scores(
    scored_at: date,
    top: int,
    min_score: float,
    conn,
) -> list[dict]:
    """Return temporal anomaly scores for a given scoring date.

    Args:
        scored_at: Date of the scoring run.
        top: Maximum rows to return.
        min_score: Only return employees with anomaly_score >= min_score.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                ta.employee_id::text,
                e.name,
                e.department,
                ta.anomaly_score,
                ta.anomaly_tier,
                ta.reconstruction_error,
                ta.trend_slope,
                ta.model_version,
                ta.scored_at
            FROM temporal_anomaly_scores ta
            JOIN employees e ON ta.employee_id = e.id
            WHERE ta.scored_at = %s
              AND ta.anomaly_score >= %s
            ORDER BY ta.anomaly_score DESC
            LIMIT %s
            """,
            (scored_at, min_score, top),
        )
        return [dict(r) for r in cur.fetchall()]


def fetch_latest_temporal_anomaly_date(conn) -> date | None:
    """Return the most recent scored_at in temporal_anomaly_scores, or None."""
    with conn.cursor() as cur:
        cur.execute("SELECT MAX(scored_at) FROM temporal_anomaly_scores")
        row = cur.fetchone()
    return row["max"] if row and row["max"] else None


def fetch_employee_churn_history(employee_id: str, conn) -> list[dict]:
    """Return full churn score history for one employee, most recent first."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                cs.employee_id::text,
                e.name,
                e.department,
                cs.churn_prob,
                cs.risk_tier,
                cs.model_version,
                cs.scored_at
            FROM churn_scores cs
            JOIN employees e ON cs.employee_id = e.id
            WHERE cs.employee_id = %s::uuid
            ORDER BY cs.scored_at DESC
            LIMIT 90
            """,
            (employee_id,),
        )
        return [dict(r) for r in cur.fetchall()]
