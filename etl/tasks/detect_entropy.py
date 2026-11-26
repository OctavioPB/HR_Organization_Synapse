"""Task callables for silo detection, risk scoring, and SPOF flagging.

Plain Python functions, no Airflow dependency, fully unit-testable.
"""

import logging
from datetime import date, timedelta

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
    from graph.risk_scorer import score_all_with_bands, write_scores
    from ml.features.feature_extractor import compute_entropy_signals

    d = date.fromisoformat(snapshot_date_str)
    raw_edges = load_raw_edges(d, window_days)
    G = build_graph(raw_edges)

    if G.number_of_nodes() == 0:
        logger.warning("task_score_risks: empty graph for %s", snapshot_date_str)
        return {
            "snapshot_date": snapshot_date_str,
            "employee_count": 0,
            "critical_count": 0,
            "critical_uncertain_count": 0,
            "warning_count": 0,
        }

    betweenness = compute_betweenness(G)
    clustering = compute_clustering(G)
    # Robust, shrunk entropy trend + confident-withdrawal flag (MATH_MODEL.md §4).
    signals = compute_entropy_signals(d, window_days)
    entropy_trends = {e: s.slope for e, s in signals.items()}
    withdrawing = {e for e, s in signals.items() if s.withdrawing}
    bands = score_all_with_bands(G, betweenness, clustering, entropy_trends=entropy_trends)
    scores = {node: detail["score"] for node, detail in bands.items()}
    write_scores(scores, entropy_trends, d, bands=bands, withdrawing=withdrawing)

    # Robust criticals fire automatic alerts; weight-sensitive ones are held for
    # qualitative review (MODEL.md §5.3, §5.5).
    critical = sum(1 for b in bands.values() if b["robust_critical"])
    critical_uncertain = sum(1 for b in bands.values() if b["weight_sensitive"])
    warning = sum(1 for s in scores.values() if 0.5 <= s < 0.7)
    stats = {
        "snapshot_date": snapshot_date_str,
        "employee_count": len(scores),
        "critical_count": critical,
        "critical_uncertain_count": critical_uncertain,
        "warning_count": warning,
        "withdrawing_count": len(withdrawing),
    }
    logger.info("task_score_risks: %s", stats)
    return stats


def task_detect_sudden_withdrawal(snapshot_date_str: str) -> dict:
    """Fire ``sudden_withdrawal`` alerts when recent activity is implausibly low
    against the employee's own baseline (ml/features/changepoint.py).

    The test runs daily on overlapping windows, so an employee with an
    unresolved sudden_withdrawal alert fired within the recent window is not
    alerted again — one sustained drop produces one alert.

    Returns:
        JSON-serialisable dict with detected and alerts_written counts.
    """
    import json
    import uuid

    import psycopg2.extras

    from ingestion.db import get_conn
    from ml.features.changepoint import SUDDEN_WITHDRAWAL_RECENT_WEEKS, compute_sudden_withdrawals

    d = date.fromisoformat(snapshot_date_str)
    recent_days = 7 * SUDDEN_WITHDRAWAL_RECENT_WEEKS
    recent = compute_sudden_withdrawals(d)

    if not recent:
        logger.info("task_detect_sudden_withdrawal: none on %s", snapshot_date_str)
        return {"snapshot_date": snapshot_date_str, "detected": 0, "alerts_written": 0}

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT affected_entities->>'employee_id'
                FROM alerts
                WHERE type = 'sudden_withdrawal' AND resolved = false
                  AND fired_at >= %s::date - %s * INTERVAL '1 day'
                """,
                (d, recent_days),
            )
            already = {r[0] for r in cur.fetchall()}

        rows = []
        window_start = d - timedelta(days=recent_days - 1)
        for emp_id, r in recent.items():
            if emp_id in already:
                continue
            rows.append(
                (
                    str(uuid.uuid4()),
                    "sudden_withdrawal",
                    "high" if r.drop >= 0.8 else "medium",
                    json.dumps(
                        {
                            "employee_id": emp_id,
                            "window_start": window_start.isoformat(),
                            "weekly_before": r.weekly_before,
                            "weekly_after": r.weekly_after,
                            "drop": r.drop,
                            "p_value": r.p_value,
                            "snapshot_date": snapshot_date_str,
                        }
                    ),
                    (
                        f"Employee {emp_id[:8]}… initiated {r.weekly_after:.1f} interactions/week over the last "
                        f"{r.recent_weeks} weeks vs. {r.weekly_before:.1f} before ({r.drop:.0%} drop) — "
                        "check for leave before acting"
                    ),
                )
            )
        if rows:
            with conn.cursor() as cur:
                psycopg2.extras.execute_batch(
                    cur,
                    """
                    INSERT INTO alerts (id, type, severity, affected_entities, details)
                    VALUES (%s, %s, %s, %s::jsonb, %s)
                    """,
                    rows,
                )

    stats = {"snapshot_date": snapshot_date_str, "detected": len(recent), "alerts_written": len(rows)}
    logger.info("task_detect_sudden_withdrawal: %s", stats)
    return stats


def task_flag_spof_critical(snapshot_date_str: str) -> dict:
    """Read risk_scores for snapshot_date and insert spof_critical alerts for critical employees.

    Only ``critical`` (robust under weight perturbation) employees fire automatic
    alerts.  ``critical_uncertain`` (weight-sensitive) employees are intentionally
    excluded — per MODEL.md §5.3/§5.5 they require qualitative review before any
    succession action and should not generate an automatic critical alert.

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
