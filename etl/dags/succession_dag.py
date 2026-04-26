"""Airflow DAG: Succession Planning — weekly cross-training recommendations.

Schedule: weekly on Sunday at 04:00 UTC (after temporal_gnn_score at 02:00).

Waits for graph_builder_dag.compute_metrics via ExternalTaskSensor before
computing recommendations. Knowledge scores (F3) are incorporated
automatically if present; the algorithm degrades gracefully when F3
hasn't run (domain_overlap = 0 for all pairs).

Tasks:
    1. compute_succession  — run graph/succession.py compute_and_persist
    2. log_summary         — report number of SPOF employees covered and candidates found
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from airflow.decorators import dag, task
from airflow.sensors.external_task import ExternalTaskSensor

logger = logging.getLogger(__name__)

_DEFAULT_ARGS: dict = {
    "owner": "org-synapse",
    "depends_on_past": False,
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
    "retry_exponential_backoff": True,
    "max_retry_delay": timedelta(minutes=30),
    "email_on_failure": False,
}


@dag(
    dag_id="succession_dag",
    description="Weekly succession planning — cross-training recommendations for high-SPOF employees",
    schedule="0 4 * * 0",  # Sunday 04:00 UTC
    start_date=datetime(2025, 1, 1),
    catchup=False,
    default_args=_DEFAULT_ARGS,
    tags=["succession", "weekly"],
)
def succession_dag():
    # Wait for today's graph metrics (SPOF scores + community assignments)
    wait_for_graph = ExternalTaskSensor(
        task_id="wait_for_graph_builder",
        external_dag_id="graph_builder_dag",
        external_task_id="compute_metrics",
        allowed_states=["success"],
        mode="reschedule",
        timeout=7200,
        poke_interval=120,
    )

    @task()
    def compute_succession(ds: str | None = None, **context) -> dict:
        """Compute and persist succession planning recommendations."""
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

        from graph.succession import compute_and_persist
        from ingestion.db import get_conn

        snapshot_date = date.fromisoformat(ds) if ds else date.today()
        logger.info("Computing succession recommendations for %s …", snapshot_date)

        with get_conn() as conn:
            n_rows = compute_and_persist(snapshot_date, conn)

        logger.info("Succession DAG: %d recommendation rows written.", n_rows)
        return {"rows_written": n_rows, "snapshot_date": str(snapshot_date)}

    @task()
    def log_summary(result: dict) -> None:
        n = result.get("rows_written", 0)
        dt = result.get("snapshot_date", "unknown")
        if n == 0:
            logger.warning(
                "Succession DAG: no recommendations written for %s. "
                "Check that risk_scores has data above the SPOF threshold "
                "(SUCCESSION_MIN_SPOF_SCORE env var, default 0.3).",
                dt,
            )
        else:
            logger.info(
                "Succession DAG complete for %s: %d cross-training pairs identified.",
                dt, n,
            )

    result = compute_succession()
    wait_for_graph >> result
    log_summary(result)


succession_dag()
