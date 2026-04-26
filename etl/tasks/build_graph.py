"""Task callables for graph construction — invoked by graph_builder_dag.

These are plain Python functions (not Airflow operators) so they can be
unit-tested without a running Airflow instance.
"""

import logging
import uuid
from datetime import date

logger = logging.getLogger(__name__)


def check_raw_events(snapshot_date_str: str, min_events: int = 100) -> int:
    """Verify that at least min_events were ingested on snapshot_date.

    Args:
        snapshot_date_str: ISO date string YYYY-MM-DD.
        min_events: Minimum acceptable event count.

    Returns:
        Actual event count if check passes.

    Raises:
        ValueError: If count < min_events (Airflow marks the task as failed).
    """
    from ingestion.db import get_conn

    d = date.fromisoformat(snapshot_date_str)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*)
                FROM raw_events
                WHERE ts >= %s::date
                  AND ts <  (%s::date + INTERVAL '1 day')
                """,
                (d, d),
            )
            count = cur.fetchone()[0]

    if count < min_events:
        raise ValueError(
            f"Insufficient data for {snapshot_date_str}: "
            f"found {count} events, required >= {min_events}"
        )

    logger.info("check_raw_events OK: %d events on %s", count, snapshot_date_str)
    return count


def task_build_graph(snapshot_date_str: str, window_days: int = 30) -> dict:
    """Build the collaboration graph for the rolling window and return stats.

    Args:
        snapshot_date_str: End date of the window (ISO YYYY-MM-DD).
        window_days: Rolling window length in days.

    Returns:
        JSON-serialisable stats dict (node_count, edge_count, raw_interactions).
    """
    from graph.builder import build_graph, load_raw_edges

    d = date.fromisoformat(snapshot_date_str)
    raw_edges = load_raw_edges(d, window_days)
    G = build_graph(raw_edges)

    stats = {
        "snapshot_date": snapshot_date_str,
        "node_count": G.number_of_nodes(),
        "edge_count": G.number_of_edges(),
        "raw_interactions": len(raw_edges),
    }
    logger.info("task_build_graph: %s", stats)
    return stats


def write_pipeline_failure_alert(dag_id: str, task_id: str, run_id: str) -> None:
    """Insert a pipeline_failure alert into the alerts table.

    Called from on_failure_callback — must not raise so it never masks the
    original task failure.
    """
    import json

    try:
        from ingestion.db import get_conn

        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO alerts (id, type, severity, affected_entities, details)
                    VALUES (%s, 'pipeline_failure', 'critical', %s::jsonb, %s)
                    """,
                    (
                        str(uuid.uuid4()),
                        json.dumps({"dag_id": dag_id, "task_id": task_id, "run_id": run_id}),
                        f"Pipeline failure in {dag_id}.{task_id} (run {run_id})",
                    ),
                )
    except Exception as exc:
        logger.error("Could not write pipeline_failure alert: %s", exc)
