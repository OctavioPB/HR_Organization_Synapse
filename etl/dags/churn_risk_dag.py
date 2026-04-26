"""Airflow DAG: Churn Risk GNN — weekly training + daily scoring.

Schedules:
    churn_gnn_train   — every Sunday at 02:00 UTC (weekly model refresh)
    churn_gnn_score   — every day    at 01:30 UTC (daily inference)

Dependency:
    graph_builder_dag must have run on the same date before churn_gnn_score
    runs, so churn_gnn_score uses a sensor to wait for today's graph_snapshot.

Training DAG tasks:
    1. build_features   — verify graph_snapshots exist for snapshot_date
    2. train_model      — run ml/gnn/trainer.py; saves checkpoint to ./checkpoints/
    3. log_metrics      — push val_auroc/test_auroc to Airflow XCom for alerting

Scoring DAG tasks:
    1. score_employees  — load latest checkpoint, write to churn_scores
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from airflow.decorators import dag, task
from airflow.operators.python import PythonOperator
from airflow.sensors.external_task import ExternalTaskSensor

logger = logging.getLogger(__name__)

_DEFAULT_ARGS: dict = {
    "owner": "org-synapse",
    "depends_on_past": False,
    "retries": 3,
    "retry_delay": timedelta(minutes=2),
    "retry_exponential_backoff": True,
    "max_retry_delay": timedelta(minutes=30),
    "email_on_failure": False,
}

# ─── Training DAG ─────────────────────────────────────────────────────────────


@dag(
    dag_id="churn_gnn_train",
    description="Weekly ChurnGAT model training",
    schedule="0 2 * * 0",  # Every Sunday at 02:00 UTC
    start_date=datetime(2025, 1, 1),
    catchup=False,
    default_args=_DEFAULT_ARGS,
    tags=["gnn", "churn", "weekly"],
)
def churn_gnn_train_dag():
    @task()
    def train_model(ds: str | None = None, **context) -> dict:
        """Run ChurnGAT training for the current execution date."""
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

        from ml.gnn.trainer import train

        snapshot_date = date.fromisoformat(ds) if ds else date.today()
        logger.info("Training ChurnGAT for snapshot_date=%s", snapshot_date)
        checkpoint_path, metrics = train(
            snapshot_date=snapshot_date,
            window_days=30,
        )
        logger.info("Training complete — checkpoint=%s metrics=%s", checkpoint_path, metrics)
        return metrics

    @task()
    def log_metrics(metrics: dict) -> None:
        """Log training metrics; alert if val_auroc below threshold."""
        val_auroc = metrics.get("val_auroc", 0.0)
        test_auroc = metrics.get("test_auroc")
        n_train = metrics.get("n_train", 0)

        logger.info(
            "ChurnGAT metrics — val_auroc=%.4f test_auroc=%s n_train=%d",
            val_auroc,
            f"{test_auroc:.4f}" if test_auroc is not None else "N/A",
            n_train,
        )

        if n_train < 10:
            logger.warning(
                "Low training sample count (%d). "
                "Consider adding more churn_labels rows.",
                n_train,
            )

        if val_auroc < 0.65:
            logger.warning(
                "val_auroc=%.4f is below the 0.65 alert threshold. "
                "Model quality degraded — review feature pipeline.",
                val_auroc,
            )

    metrics = train_model()
    log_metrics(metrics)


churn_gnn_train_dag()


# ─── Scoring DAG ──────────────────────────────────────────────────────────────


@dag(
    dag_id="churn_gnn_score",
    description="Daily ChurnGAT scoring — writes to churn_scores",
    schedule="30 1 * * *",  # Every day at 01:30 UTC (after graph_builder_dag at 01:00)
    start_date=datetime(2025, 1, 1),
    catchup=False,
    default_args=_DEFAULT_ARGS,
    tags=["gnn", "churn", "daily"],
)
def churn_gnn_score_dag():
    # Wait for today's graph snapshot before scoring
    wait_for_graph = ExternalTaskSensor(
        task_id="wait_for_graph_builder",
        external_dag_id="graph_builder_dag",
        external_task_id="compute_metrics",
        allowed_states=["success"],
        mode="reschedule",
        timeout=3600,
        poke_interval=120,
    )

    @task()
    def score_employees(ds: str | None = None, **context) -> dict:
        """Load latest checkpoint and write churn_scores for today."""
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

        from ml.gnn.scorer import score

        snapshot_date = date.fromisoformat(ds) if ds else date.today()
        logger.info("Scoring employees for snapshot_date=%s", snapshot_date)

        try:
            results = score(snapshot_date=snapshot_date, window_days=30)
        except FileNotFoundError as exc:
            logger.warning(
                "No checkpoint found — skipping scoring. "
                "Run churn_gnn_train first. Error: %s", exc
            )
            return {"scored": 0, "skipped": True}

        high_count   = sum(1 for r in results if r["risk_tier"] == "high")
        medium_count = sum(1 for r in results if r["risk_tier"] == "medium")
        low_count    = sum(1 for r in results if r["risk_tier"] == "low")

        logger.info(
            "Scoring complete — total=%d high=%d medium=%d low=%d",
            len(results), high_count, medium_count, low_count,
        )
        return {
            "scored": len(results),
            "high": high_count,
            "medium": medium_count,
            "low": low_count,
        }

    score_task = score_employees()
    wait_for_graph >> score_task  # type: ignore[operator]


churn_gnn_score_dag()
