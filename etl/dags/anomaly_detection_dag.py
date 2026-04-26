"""Weekly DAG: ML anomaly detection on rolling graph metrics.

Schedule: 03:00 UTC every Monday.
On completion it triggers risk_scoring_dag so that SPOF scores reflect
the latest anomaly signals.

Task chain:
    extract_features
        → run_isolation_forest
            → write_anomaly_alerts
                → trigger_risk_scoring
"""

import logging
import os
from datetime import timedelta

from airflow.decorators import dag, task
from airflow.operators.trigger_dagrun import TriggerDagRunOperator
from airflow.utils.dates import days_ago

logger = logging.getLogger(__name__)

_WINDOW_DAYS = int(os.environ.get("GRAPH_WINDOW_DAYS", "30"))

_DEFAULT_ARGS = {
    "owner": "org-synapse",
    "retries": 3,
    "retry_delay": timedelta(minutes=5),
    "retry_exponential_backoff": True,  # delays: 5m, 10m, 20m
    "max_retry_delay": timedelta(minutes=60),
    "email_on_failure": False,
}


@dag(
    dag_id="anomaly_detection_dag",
    description="Weekly ML anomaly detection on graph metrics",
    schedule="0 3 * * 1",
    start_date=days_ago(1),
    catchup=False,
    default_args=_DEFAULT_ARGS,
    tags=["org-synapse", "ml", "weekly"],
)
def anomaly_detection_dag():

    @task()
    def extract_features(**context) -> dict:
        """Extract rolling graph features for all employees."""
        from etl.tasks.run_anomaly import task_extract_features as _extract

        return _extract(context["ds"], window_days=_WINDOW_DAYS)

    @task()
    def run_isolation_forest(feature_stats: dict) -> dict:
        """Run Isolation Forest and write connectivity_anomaly alerts."""
        from etl.tasks.run_anomaly import task_run_isolation_forest as _run_if

        snapshot_date_str = feature_stats["snapshot_date"]
        return _run_if(snapshot_date_str, window_days=_WINDOW_DAYS)

    @task()
    def write_anomaly_alerts(anomaly_stats: dict) -> dict:
        """Log anomaly detection summary (alerts already written by run_isolation_forest)."""
        from etl.tasks.run_anomaly import task_summarise_anomalies as _summarise

        return _summarise(anomaly_stats)

    trigger_risk_scoring = TriggerDagRunOperator(
        task_id="trigger_risk_scoring",
        trigger_dag_id="risk_scoring_dag",
        wait_for_completion=False,
        reset_dag_run=True,
    )

    features = extract_features()
    anomalies = run_isolation_forest(features)
    summary = write_anomaly_alerts(anomalies)
    summary >> trigger_risk_scoring


anomaly_detection_dag()
