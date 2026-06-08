"""Weekly DAG: structural equity metrics computation (DEI analytics).

Schedule: Sunday 04:00 UTC (after succession_dag at 04:00).
Waits for graph_builder_dag.flag_spof_critical.

Tasks:
    compute_equity — aggregate centrality metrics by demographic dimension
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from airflow.decorators import dag, task
from airflow.sensors.external_task import ExternalTaskSensor

logger = logging.getLogger(__name__)

_DEFAULT_ARGS = {
    "owner": "org-synapse",
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
    "email_on_failure": False,
}


@dag(
    dag_id="equity_dag",
    description="Weekly structural equity analytics — centrality distribution by demographic group",
    schedule="30 4 * * 0",  # Sunday 04:30 UTC (after succession_dag)
    start_date=datetime(2025, 1, 1),
    catchup=False,
    default_args=_DEFAULT_ARGS,
    tags=["equity", "dei", "weekly"],
)
def equity_dag():
    wait_for_graph = ExternalTaskSensor(
        task_id="wait_for_graph_builder",
        external_dag_id="graph_builder_dag",
        external_task_id="flag_spof_critical",
        allowed_states=["success"],
        mode="reschedule",
        timeout=7200,
        poke_interval=120,
    )

    @task()
    def compute_equity(ds: str | None = None, **context) -> dict:
        import sys
        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

        from etl.tasks.compute_equity import task_compute_equity
        from ingestion.db import get_conn

        snapshot_date = ds or str(date.today())
        with get_conn() as conn:
            result = task_compute_equity(snapshot_date, conn)

        logger.info("Equity DAG: %s", result)
        return result

    equity_result = compute_equity()
    wait_for_graph >> equity_result


equity_dag()
