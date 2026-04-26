"""Daily DAG: raw collaboration events → graph snapshot → metrics → silos → risk scores.

Schedule: 02:00 UTC every day.
Window:   30-day rolling window ending at execution_date.

Task chain:
    check_raw_events
        → build_graph
            → compute_metrics
                → detect_silos
                    → score_risks
                        → flag_spof_critical
"""

import logging
import os
from datetime import timedelta

from airflow.decorators import dag, task
from airflow.operators.trigger_dagrun import TriggerDagRunOperator
from airflow.utils.dates import days_ago

logger = logging.getLogger(__name__)

_WINDOW_DAYS = int(os.environ.get("GRAPH_WINDOW_DAYS", "30"))
_MIN_EVENTS = int(os.environ.get("GRAPH_MIN_EVENTS", "100"))

_DEFAULT_ARGS = {
    "owner": "org-synapse",
    "retries": 3,
    "retry_delay": timedelta(minutes=2),
    "retry_exponential_backoff": True,  # delays: 2m, 4m, 8m
    "max_retry_delay": timedelta(minutes=30),
    "email_on_failure": False,
}


def _on_failure_callback(context: dict) -> None:
    """Insert a pipeline_failure alert whenever any task in this DAG fails."""
    from etl.tasks.build_graph import write_pipeline_failure_alert

    write_pipeline_failure_alert(
        dag_id=context["dag"].dag_id,
        task_id=context["task_instance"].task_id,
        run_id=context["run_id"],
    )


@dag(
    dag_id="graph_builder_dag",
    description="Daily graph build: edges → metrics → silos → SPOF scores",
    schedule="0 2 * * *",
    start_date=days_ago(1),
    catchup=False,
    default_args=_DEFAULT_ARGS,
    on_failure_callback=_on_failure_callback,
    tags=["org-synapse", "graph", "daily"],
)
def graph_builder_dag():

    @task(on_failure_callback=_on_failure_callback)
    def check_raw_events(**context) -> int:
        from etl.tasks.build_graph import check_raw_events as _check

        snapshot_date_str = context["ds"]  # YYYY-MM-DD execution date
        return _check(snapshot_date_str, min_events=_MIN_EVENTS)

    @task(on_failure_callback=_on_failure_callback)
    def build_graph(**context) -> dict:
        from etl.tasks.build_graph import task_build_graph as _build

        return _build(context["ds"], window_days=_WINDOW_DAYS)

    @task(on_failure_callback=_on_failure_callback)
    def compute_metrics(**context) -> dict:
        from etl.tasks.compute_centrality import task_compute_metrics as _metrics
        from api.cache import invalidate_snapshot

        result = _metrics(context["ds"], window_days=_WINDOW_DAYS)
        # Invalidate the Redis snapshot cache so the API serves fresh data immediately
        invalidate_snapshot(context["ds"])
        return result

    @task(on_failure_callback=_on_failure_callback)
    def detect_silos(**context) -> dict:
        from etl.tasks.detect_entropy import task_detect_silos as _silos

        return _silos(context["ds"], window_days=_WINDOW_DAYS)

    @task(on_failure_callback=_on_failure_callback)
    def score_risks(**context) -> dict:
        from etl.tasks.detect_entropy import task_score_risks as _score

        return _score(context["ds"], window_days=_WINDOW_DAYS)

    @task(on_failure_callback=_on_failure_callback)
    def flag_spof_critical(**context) -> dict:
        from etl.tasks.detect_entropy import task_flag_spof_critical as _flag

        return _flag(context["ds"])

    trigger_neo4j = TriggerDagRunOperator(
        task_id="trigger_neo4j_import",
        trigger_dag_id="neo4j_import_dag",
        wait_for_completion=False,  # fire-and-forget; Neo4j may not be deployed
        reset_dag_run=True,
    )

    # Chain: each task passes its return value downstream (XCom) but
    # dependencies are explicit to guarantee execution order.
    raw_count = check_raw_events()
    graph_stats = build_graph()
    metric_stats = compute_metrics()
    silo_stats = detect_silos()
    risk_stats = score_risks()
    spof_stats = flag_spof_critical()

    raw_count >> graph_stats >> metric_stats >> silo_stats >> risk_stats >> spof_stats >> trigger_neo4j


graph_builder_dag()
