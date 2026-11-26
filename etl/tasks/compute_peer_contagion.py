"""Peer-departure alert layer (MODEL.md §7.4.1, MATH_MODEL.md §9.2).

AlKetbi et al. (2025) report that employees are ~23 % more likely to leave when
more than 30 % of their immediate peers left within six months.  This task
turns that finding into a tested alert; the maths lives in
ml/churn/contagion.py:

    E(v) = Σ_{u departed recently} s(v,u) · 2^(−age_u / half_life)

where s(v,u) is u's share of v's collaboration volume.  Each E(v) is compared
against the same departures landed on random people (permutation test), and
alerts are Benjamini–Hochberg controlled at PEER_CONTAGION_FDR with an
effect-size floor: at least PEER_CONTAGION_MIN_EXPOSURE (30 %) of v's
collaboration volume was with people who left in the window.

Previous version, kept for reference: ``peer_churn_rate`` = unweighted share
of last-30-day neighbours who departed, alert if > 0.30.  It could not fire in
production: the 30-day graph contained active employees only, so a departed
peer was never a neighbour.  It also opened its DB connection incorrectly
(``get_conn()`` is a context manager) and re-inserted the same alert every day.

The departure window defaults to 90 days rather than the paper's 180, because
raw_events are retained for 90 days (graph/compliance.py): ties to someone who
left earlier than that are no longer observable.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import date

logger = logging.getLogger(__name__)


def peer_churn_rate(neighbors: set[str], departed: set[str]) -> float:
    """Fraction of *neighbors* who appear in the *departed* set (MODEL.md §7.4.1).

    Returns 0.0 for an employee with no neighbors (no peers to lose).
    """
    if not neighbors:
        return 0.0
    return len(neighbors & departed) / len(neighbors)


def task_compute_peer_contagion(
    snapshot_date_str: str,
    window_days: int = 30,
    conn=None,
) -> dict:
    """Compute peer exposure, run the permutation test, raise FDR-controlled alerts.

    Args:
        snapshot_date_str: ISO date YYYY-MM-DD.
        window_days: Unused; kept for DAG signature compatibility.
        conn: Optional open psycopg2 connection.  Opens a pooled one if None.

    Returns:
        JSON-serialisable stats: employee_count, flagged_count, new_alerts, ...
    """
    from ml.churn.contagion import (
        PEER_CHURN_WINDOW_DAYS,
        PEER_CONTAGION_FDR,
        PEER_CONTAGION_MIN_EXPOSURE,
        select_peer_exposure,
    )
    from ml.churn.pipeline import load_departures, load_tie_edges

    snapshot_date = date.fromisoformat(snapshot_date_str)

    if conn is None:
        from ingestion.db import get_conn

        with get_conn() as own:
            return task_compute_peer_contagion(snapshot_date_str, window_days, conn=own)

    with conn.cursor() as cur:
        departures = load_departures(cur)
        tie_edges = load_tie_edges(snapshot_date, cur)

    results = select_peer_exposure(tie_edges, departures, snapshot_date)
    flagged = [r for r in results.values() if r.flagged]

    with conn.cursor() as cur:
        cur.executemany(
            """
            UPDATE churn_scores
            SET peer_churn_rate = %s, peer_contagion_risk = %s, peer_exposure_p = %s
            WHERE employee_id = %s::uuid AND scored_at = %s
            """,
            [
                (round(r.exposure, 4), r.flagged, round(r.p_value, 4), r.employee_id, snapshot_date)
                for r in results.values()
            ],
        )

        cur.execute(
            """
            SELECT DISTINCT affected_entities->>'employee_id'
            FROM alerts
            WHERE type = 'peer_contagion_risk' AND resolved = false
              AND fired_at >= %s::date - %s * INTERVAL '1 day'
            """,
            (snapshot_date, PEER_CHURN_WINDOW_DAYS),
        )
        already = {row[0] for row in cur.fetchall()}

        new_alerts = 0
        for r in flagged:
            if r.employee_id in already:
                continue
            cur.execute(
                """
                INSERT INTO alerts (id, type, severity, affected_entities, details)
                VALUES (%s, 'peer_contagion_risk', %s, %s::jsonb, %s)
                """,
                (
                    str(uuid.uuid4()),
                    "high" if r.departed_share >= 0.5 else "medium",
                    json.dumps(
                        {
                            "employee_id": r.employee_id,
                            "peer_exposure": round(r.exposure, 4),
                            "departed_share": round(r.departed_share, 4),
                            "p_value": round(r.p_value, 4),
                            "departed_peers": r.departed_peers,
                            "snapshot_date": snapshot_date_str,
                        }
                    ),
                    f"About {round(r.departed_share * 100)}% of this person's recent collaboration was with "
                    f"{r.departed_peers} colleague(s) who left in the last {PEER_CHURN_WINDOW_DAYS} days — "
                    f"more than random departures would explain (FDR {PEER_CONTAGION_FDR:.0%}). "
                    "Check in on workload and handover gaps.",
                ),
            )
            new_alerts += 1
    conn.commit()

    stats = {
        "snapshot_date": snapshot_date_str,
        "employee_count": len(results),
        "departures_in_window": sum(
            1 for d in departures.values() if 0 <= (snapshot_date - d).days < PEER_CHURN_WINDOW_DAYS
        ),
        "flagged_count": len(flagged),
        "new_alerts": new_alerts,
        "min_exposure": PEER_CONTAGION_MIN_EXPOSURE,
        "fdr": PEER_CONTAGION_FDR,
    }
    logger.info("task_compute_peer_contagion: %s", stats)
    return stats
