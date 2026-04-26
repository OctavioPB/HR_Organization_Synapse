"""Task callables for silo detection, risk scoring, and SPOF flagging.

Plain Python functions, no Airflow dependency, fully unit-testable.
"""

import logging
from datetime import date

logger = logging.getLogger(__name__)


def task_detect_silos(snapshot_date_str: str, window_days: int = 30) -> dict:
    """Detect silo communities in the collaboration graph and persist alerts.

    Args:
        snapshot_date_str: ISO date string YYYY-MM-DD.
        window_days: Rolling window length in days.

    Returns:
        JSON-serialisable dict with silo_count and critical_count.
    """
    from graph.builder import build_graph, load_raw_edges
    from graph.metrics import compute_community
    from graph.silo_detector import detect_silos, write_alerts

    d = date.fromisoformat(snapshot_date_str)
    raw_edges = load_raw_edges(d, window_days)
    G = build_graph(raw_edges)

    if G.number_of_nodes() == 0:
        logger.warning("task_detect_silos: empty graph for %s", snapshot_date_str)
        return {"snapshot_date": snapshot_date_str, "silo_count": 0, "critical_count": 0}

    communities = compute_community(G)
    alerts = detect_silos(G, communities)
    write_alerts(alerts, d)

    critical = sum(1 for a in alerts if a.severity == "critical")
    stats = {
        "snapshot_date": snapshot_date_str,
        "silo_count": len(alerts),
        "critical_count": critical,
    }
    logger.info("task_detect_silos: %s", stats)
    return stats


def task_score_risks(snapshot_date_str: str, window_days: int = 30) -> dict:
    """Compute SPOF risk scores for all employees and persist to risk_scores.

    Args:
        snapshot_date_str: ISO date string YYYY-MM-DD.
        window_days: Rolling window length in days.

    Returns:
        JSON-serialisable dict with employee_count, critical_count, warning_count.
    """
    from graph.builder import build_graph, load_raw_edges
    from graph.metrics import compute_betweenness, compute_clustering
    from graph.risk_scorer import score_all, write_scores
    from ml.features.feature_extractor import compute_entropy_trends

    d = date.fromisoformat(snapshot_date_str)
    raw_edges = load_raw_edges(d, window_days)
    G = build_graph(raw_edges)

    if G.number_of_nodes() == 0:
        logger.warning("task_score_risks: empty graph for %s", snapshot_date_str)
        return {
            "snapshot_date": snapshot_date_str,
            "employee_count": 0,
            "critical_count": 0,
            "warning_count": 0,
        }

    betweenness = compute_betweenness(G)
    clustering = compute_clustering(G)
    entropy_trends = compute_entropy_trends(d, window_days)
    scores = score_all(G, betweenness, clustering, entropy_trends=entropy_trends)
    write_scores(scores, entropy_trends, d)

    critical = sum(1 for s in scores.values() if s >= 0.7)
    warning = sum(1 for s in scores.values() if 0.5 <= s < 0.7)
    stats = {
        "snapshot_date": snapshot_date_str,
        "employee_count": len(scores),
        "critical_count": critical,
        "warning_count": warning,
    }
    logger.info("task_score_risks: %s", stats)
    return stats


def task_flag_spof_critical(snapshot_date_str: str) -> dict:
    """Read risk_scores for snapshot_date and insert spof_critical alerts for critical employees.

    Idempotent: only inserts alerts for employees not already alerted on this date.

    Args:
        snapshot_date_str: ISO date string YYYY-MM-DD.

    Returns:
        JSON-serialisable dict with alerts_written count.
    """
    import json
    import uuid

    from ingestion.db import get_conn

    d = date.fromisoformat(snapshot_date_str)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT employee_id, spof_score, entropy_trend, flag
                FROM risk_scores
                WHERE scored_at = %s
                  AND flag = 'critical'
                """,
                (d,),
            )
            critical_rows = cur.fetchall()

    if not critical_rows:
        logger.info("task_flag_spof_critical: no critical employees on %s", snapshot_date_str)
        return {"snapshot_date": snapshot_date_str, "alerts_written": 0}

    rows = [
        (
            str(uuid.uuid4()),
            "spof_critical",
            "critical",
            json.dumps(
                {
                    "employee_id": str(emp_id),
                    "spof_score": float(score),
                    "entropy_trend": float(trend),
                    "flag": flag,
                    "snapshot_date": snapshot_date_str,
                }
            ),
            f"Employee {str(emp_id)[:8]}… SPOF score {score:.3f} on {snapshot_date_str}",
        )
        for emp_id, score, trend, flag in critical_rows
    ]

    import psycopg2.extras

    with get_conn() as conn:
        with conn.cursor() as cur:
            psycopg2.extras.execute_batch(
                cur,
                """
                INSERT INTO alerts (id, type, severity, affected_entities, details)
                VALUES (%s, %s, %s, %s::jsonb, %s)
                """,
                rows,
            )

    logger.info(
        "task_flag_spof_critical: wrote %d spof_critical alerts for %s",
        len(rows),
        snapshot_date_str,
    )
    return {"snapshot_date": snapshot_date_str, "alerts_written": len(rows)}
