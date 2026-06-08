"""Generate a structural impact report when an employee departs.

Compares graph snapshots at t-90, t-0, t+30, t+60 relative to departure_date
and produces an impact_json + AI-generated narrative.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, timedelta
from typing import Any

logger = logging.getLogger(__name__)

_WINDOW_DAYS = int(os.environ.get("GRAPH_WINDOW_DAYS", "30"))


# ─── Snapshot stats ───────────────────────────────────────────────────────────


def _nearest_snapshot_date(target: date, conn) -> date | None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT snapshot_date
            FROM graph_snapshots
            ORDER BY ABS(snapshot_date - %s::date)
            LIMIT 1
            """,
            (target,),
        )
        row = cur.fetchone()
    return row["snapshot_date"] if row else None


def _graph_stats_for_date(snap_date: date | None, conn) -> dict:
    if snap_date is None:
        return {"node_count": 0, "edge_count": 0, "avg_betweenness": 0.0, "wcc_count": 1, "snapshot_date": None}

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
              COUNT(*)          AS node_count,
              AVG(betweenness)  AS avg_betweenness
            FROM graph_snapshots
            WHERE snapshot_date = %s
            """,
            (snap_date,),
        )
        row = dict(cur.fetchone())

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*) AS edge_count
            FROM raw_events
            WHERE ts BETWEEN (%s::date - %s * INTERVAL '1 day') AND %s::date
            """,
            (snap_date, _WINDOW_DAYS, snap_date),
        )
        edge_row = cur.fetchone()

    return {
        "snapshot_date": str(snap_date),
        "node_count": int(row["node_count"] or 0),
        "edge_count": int(edge_row["edge_count"] or 0),
        "avg_betweenness": round(float(row["avg_betweenness"] or 0), 6),
    }


def _count_new_silo_alerts(after_date: date, window_days: int, conn) -> int:
    end = after_date + timedelta(days=window_days)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*) AS cnt FROM alerts
            WHERE type = 'silo'
              AND fired_at BETWEEN %s::timestamptz AND %s::timestamptz
            """,
            (after_date, end),
        )
        row = cur.fetchone()
    return int(row["cnt"] or 0)


def _find_succession_outcome(employee_id: str, departure_date: date, conn) -> dict | None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT sr.candidate_employee_id::text, e.name, e.department,
                   sr.compatibility_score
            FROM succession_recommendations sr
            JOIN employees e ON e.id = sr.candidate_employee_id
            WHERE sr.source_employee_id = %s::uuid
            ORDER BY sr.compatibility_score DESC
            LIMIT 1
            """,
            (employee_id,),
        )
        row = cur.fetchone()
    return dict(row) if row else None


# ─── Main task ────────────────────────────────────────────────────────────────


def task_generate_departure_report(employee_id: str, departure_date_str: str, conn) -> dict:
    """Compute and persist a departure impact report for one employee."""
    departure_date = date.fromisoformat(departure_date_str)
    logger.info("Generating departure report for %s on %s", employee_id, departure_date)

    # Fetch employee info
    with conn.cursor() as cur:
        cur.execute(
            "SELECT name, department FROM employees WHERE id = %s::uuid",
            (employee_id,),
        )
        emp = cur.fetchone()
    if not emp:
        logger.warning("Employee %s not found; skipping departure report.", employee_id)
        return {"skipped": True, "reason": "employee_not_found"}
    emp_name = emp["name"]
    emp_dept = emp["department"]

    # 1. Fetch SPOF score at t-90
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT spof_score, flag FROM risk_scores
            WHERE employee_id = %s::uuid
              AND scored_at <= %s::timestamptz
            ORDER BY scored_at DESC
            LIMIT 1
            """,
            (employee_id, departure_date - timedelta(days=90)),
        )
        spof_row = cur.fetchone()
    predicted_spof = float(spof_row["spof_score"]) if spof_row else None
    spof_flag = spof_row["flag"] if spof_row else None

    # 2. Fetch churn probability at t-90
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT churn_prob FROM churn_scores
            WHERE employee_id = %s::uuid
              AND scored_at <= %s
            ORDER BY scored_at DESC
            LIMIT 1
            """,
            (employee_id, departure_date - timedelta(days=90)),
        )
        churn_row = cur.fetchone()
    predicted_churn = float(churn_row["churn_prob"]) if churn_row else None

    # 3. Gather snapshot stats at four points
    t_minus_90 = _nearest_snapshot_date(departure_date - timedelta(days=90), conn)
    t_minus_0 = _nearest_snapshot_date(departure_date, conn)
    t_plus_30 = _nearest_snapshot_date(departure_date + timedelta(days=30), conn)
    t_plus_60 = _nearest_snapshot_date(departure_date + timedelta(days=60), conn)

    stats_before = _graph_stats_for_date(t_minus_0, conn)
    stats_30 = _graph_stats_for_date(t_plus_30, conn)
    stats_60 = _graph_stats_for_date(t_plus_60, conn)

    bw_before = stats_before["avg_betweenness"]
    bw_after = stats_30["avg_betweenness"]
    bw_delta_pct = round((bw_after - bw_before) / max(bw_before, 1e-9) * 100, 1) if bw_before > 0 else 0.0

    # 4. New silo alerts after departure
    new_silo_alerts = _count_new_silo_alerts(departure_date, 30, conn)

    # 5. Recovery trajectory
    recovery = "recovering" if (stats_60["avg_betweenness"] >= stats_30["avg_betweenness"]) else "deteriorating"

    # 6. Succession outcome
    succession_candidate = _find_succession_outcome(employee_id, departure_date, conn)

    # Assemble impact_json
    impact_json: dict[str, Any] = {
        "employee_id": employee_id,
        "employee_name": emp_name,
        "employee_dept": emp_dept,
        "departure_date": departure_date_str,
        "predicted_spof_score": predicted_spof,
        "predicted_spof_flag": spof_flag,
        "predicted_churn_prob": predicted_churn,
        "was_flagged_critical": spof_flag == "critical",
        "graph_diameter_delta_pct": bw_delta_pct,
        "new_silo_alerts": new_silo_alerts,
        "recovery_trajectory": recovery,
        "snapshots": {
            "t_minus_90": _graph_stats_for_date(t_minus_90, conn),
            "t_minus_0": stats_before,
            "t_plus_30": stats_30,
            "t_plus_60": stats_60,
        },
        "succession_candidate": succession_candidate,
    }

    # 7. Generate narrative via Claude
    narrative = _generate_narrative(impact_json)

    # 8. Upsert into departure_impact_reports
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO departure_impact_reports
              (employee_id, departure_date, impact_json, narrative_text, status)
            VALUES (%s::uuid, %s, %s::jsonb, %s, 'ready')
            ON CONFLICT (employee_id, departure_date) DO UPDATE SET
              impact_json    = EXCLUDED.impact_json,
              narrative_text = EXCLUDED.narrative_text,
              generated_at   = NOW(),
              status         = 'ready'
            """,
            (employee_id, departure_date, json.dumps(impact_json), narrative),
        )
    conn.commit()

    logger.info("Departure report ready for %s (%s)", emp_name, departure_date)
    return {"employee_id": employee_id, "departure_date": departure_date_str, "status": "ready"}


def _generate_narrative(impact: dict) -> str:
    try:
        from graph.claude_client import call_claude

        prompt = (
            "You are a people analytics advisor writing a concise departure impact report. "
            "Based on the following structural analysis data, write 2 paragraphs: "
            "(1) whether the system's prediction was accurate and what the structural impact was, "
            "(2) the current recovery status and recommended next action. "
            "Be specific, data-led, and professional. Max 150 words total.\n\n"
            f"Data: {json.dumps(impact, indent=2, default=str)}"
        )
        return call_claude(prompt, max_tokens=300)
    except Exception as exc:
        logger.warning("Narrative generation failed: %s", exc)
        name = impact.get("employee_name", "This employee")
        spof = impact.get("predicted_spof_score")
        delta = impact.get("graph_diameter_delta_pct", 0)
        silos = impact.get("new_silo_alerts", 0)
        recovery = impact.get("recovery_trajectory", "unknown")
        spof_str = f"{round(spof * 100)}%" if spof is not None else "unscored"
        return (
            f"{name} was predicted at {spof_str} SPOF risk. After their departure, "
            f"average network betweenness increased by {delta}% and {silos} new silo alert(s) fired. "
            f"The organizational graph is currently {recovery}. "
            "Review the succession plan and prioritise cross-training for the identified candidates."
        )
