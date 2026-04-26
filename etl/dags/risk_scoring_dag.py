"""On-demand DAG: recompute SPOF risk scores and write spof_critical alerts.

Schedule: None (triggered manually or by anomaly_detection_dag).
Uses the most recent snapshot date available in graph_snapshots.
"""

import logging
from datetime import timedelta

from airflow.decorators import dag, task
from airflow.utils.dates import days_ago

logger = logging.getLogger(__name__)

_DEFAULT_ARGS = {
    "owner": "org-synapse",
    "retries": 1,
    "retry_delay": timedelta(minutes=5),
    "email_on_failure": False,
}


@dag(
    dag_id="risk_scoring_dag",
    description="On-demand SPOF risk scoring triggered by anomaly detection or manually",
    schedule=None,
    start_date=days_ago(1),
    catchup=False,
    default_args=_DEFAULT_ARGS,
    tags=["org-synapse", "risk", "on-demand"],
)
def risk_scoring_dag():

    @task()
    def resolve_snapshot_date(**context) -> str:
        """Return the most recent snapshot date from graph_snapshots.

        Falls back to the DAG execution date if graph_snapshots is empty.
        """
        from ingestion.db import get_conn

        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT MAX(snapshot_date) FROM graph_snapshots"
                )
                row = cur.fetchone()

        if row and row[0]:
            resolved = row[0].isoformat()
        else:
            resolved = context["ds"]
            logger.warning(
                "graph_snapshots is empty — falling back to execution date %s", resolved
            )

        logger.info("resolve_snapshot_date: using %s", resolved)
        return resolved

    @task()
    def score_risks(snapshot_date_str: str) -> dict:
        """Recompute SPOF scores and persist to risk_scores."""
        from etl.tasks.detect_entropy import task_score_risks as _score

        return _score(snapshot_date_str)

    @task()
    def flag_spof_critical(snapshot_date_str: str) -> dict:
        """Write spof_critical alerts for every employee flagged 'critical'."""
        from etl.tasks.detect_entropy import task_flag_spof_critical as _flag

        return _flag(snapshot_date_str)

    snapshot_date = resolve_snapshot_date()
    risk_stats = score_risks(snapshot_date)
    spof_stats = flag_spof_critical(snapshot_date)

    risk_stats >> spof_stats


risk_scoring_dag()
