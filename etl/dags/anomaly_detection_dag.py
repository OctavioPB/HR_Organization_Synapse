"""Weekly DAG: run ML anomaly detection on rolling graph metrics.

Schedule: 03:00 UTC every Monday.
Sprint 4 stubs: actual ML tasks are placeholders that will be filled in S4.
On completion it triggers risk_scoring_dag.
"""

import logging
from datetime import timedelta

from airflow.decorators import dag, task
from airflow.operators.trigger_dagrun import TriggerDagRunOperator
from airflow.utils.dates import days_ago

logger = logging.getLogger(__name__)

_DEFAULT_ARGS = {
    "owner": "org-synapse",
    "retries": 1,
    "retry_delay": timedelta(minutes=10),
    "email_on_failure": False,
}


@dag(
    dag_id="anomaly_detection_dag",
    description="Weekly ML anomaly detection on graph metrics (S4 stubs)",
    schedule="0 3 * * 1",
    start_date=days_ago(1),
    catchup=False,
    default_args=_DEFAULT_ARGS,
    tags=["org-synapse", "ml", "weekly"],
)
def anomaly_detection_dag():

    @task()
    def extract_features(**context) -> dict:
        """Extract rolling graph features for anomaly detection.

        Sprint 4: replace stub with ml.features.feature_extractor.extract_features().
        """
        logger.info(
            "extract_features stub — Sprint 4 will implement feature extraction for %s",
            context["ds"],
        )
        return {"snapshot_date": context["ds"], "feature_rows": 0}

    @task()
    def run_isolation_forest(feature_stats: dict) -> dict:
        """Run Isolation Forest anomaly detection on extracted features.

        Sprint 4: replace stub with ml.anomaly.isolation_forest.run().
        """
        logger.info(
            "run_isolation_forest stub — Sprint 4 will implement anomaly detection "
            "for snapshot %s",
            feature_stats.get("snapshot_date"),
        )
        return {
            "snapshot_date": feature_stats.get("snapshot_date"),
            "anomalies_detected": 0,
        }

    @task()
    def write_anomaly_alerts(anomaly_stats: dict) -> dict:
        """Persist anomaly alerts to the alerts table.

        Sprint 4: replace stub with write_alerts() call from ml layer.
        """
        logger.info(
            "write_anomaly_alerts stub — Sprint 4 will persist anomaly alerts "
            "for snapshot %s (anomalies=%d)",
            anomaly_stats.get("snapshot_date"),
            anomaly_stats.get("anomalies_detected", 0),
        )
        return anomaly_stats

    trigger_risk_scoring = TriggerDagRunOperator(
        task_id="trigger_risk_scoring",
        trigger_dag_id="risk_scoring_dag",
        wait_for_completion=False,
        reset_dag_run=True,
    )

    features = extract_features()
    anomalies = run_isolation_forest(features)
    alerts = write_anomaly_alerts(anomalies)
    alerts >> trigger_risk_scoring


anomaly_detection_dag()
