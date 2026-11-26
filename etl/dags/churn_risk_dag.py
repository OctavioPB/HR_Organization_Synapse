"""Airflow DAG: churn risk — weekly training + daily scoring.

Default model: calibrated logistic regression on point-in-time features
(ml/churn, MATH_MODEL.md §9.1).  The ChurnGAT is a challenger only: set
CHURN_MODEL=gat to use it, and only after it beats the baseline on held-out
data (ml.churn.model.select_champion).  Its trainer cannot currently build a
leakage-free split (see ml/churn/model.py docstring).

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

        import os

        snapshot_date = date.fromisoformat(ds) if ds else date.today()
        if os.environ.get("CHURN_MODEL", "logreg") == "gat":
            from ml.gnn.trainer import train

            logger.info("Training ChurnGAT challenger for snapshot_date=%s", snapshot_date)
            checkpoint_path, metrics = train(snapshot_date=snapshot_date, window_days=30)
        else:
            from ml.churn.pipeline import train_churn_model

            logger.info("Training calibrated churn model as of %s", snapshot_date)
            checkpoint_path, metrics = train_churn_model(snapshot_date)
        logger.info("Training complete — checkpoint=%s metrics=%s", checkpoint_path, metrics)
        return metrics

    @task()
    def log_metrics(metrics: dict) -> None:
        """Log training metrics; alert if val_auroc below threshold."""
        if metrics.get("error"):
            logger.warning("Churn model not trained: %s", metrics)
            return
        test = metrics.get("test", {})
        val_auroc = metrics.get("val_auroc", test.get("auroc", 0.0)) or 0.0
        test_auroc = metrics.get("test_auroc", test.get("auroc"))
        n_train = metrics.get("n_train", metrics.get("train", {}).get("n", 0))
        if test:
            logger.info(
                "Held-out calibration — brier=%.4f skill=%.3f ece=%.4f",
                test.get("brier", float("nan")),
                test.get("brier_skill", float("nan")),
                test.get("ece", float("nan")),
            )
            if test.get("ece", 0.0) > 0.05:
                logger.warning(
                    "Held-out ECE %.3f > 0.05: probabilities are miscalibrated, usually because the "
                    "departure base rate shifted. Treat churn_prob as a ranking until it recovers.",
                    test["ece"],
                )

        logger.info(
            "ChurnGAT metrics — val_auroc=%.4f test_auroc=%s n_train=%d",
            val_auroc,
            f"{test_auroc:.4f}" if test_auroc is not None else "N/A",
            n_train,
        )

        if n_train < 10:
            logger.warning(
                "Low training sample count (%d). Consider adding more churn_labels rows.",
                n_train,
            )

        if val_auroc < 0.65:
            logger.warning(
                "val_auroc=%.4f is below the 0.65 alert threshold. Model quality degraded — review feature pipeline.",
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

        import os

        snapshot_date = date.fromisoformat(ds) if ds else date.today()
        logger.info("Scoring employees for snapshot_date=%s", snapshot_date)

        if os.environ.get("CHURN_MODEL", "logreg") != "gat":
            from ml.churn.pipeline import score_churn

            # Always logs today's features (training needs them), scores if a model exists.
            return score_churn(snapshot_date=snapshot_date, window_days=30)

        from ml.gnn.scorer import score

        try:
            results = score(snapshot_date=snapshot_date, window_days=30)
        except FileNotFoundError as exc:
            logger.warning("No checkpoint found — skipping scoring. Run churn_gnn_train first. Error: %s", exc)
            return {"scored": 0, "skipped": True}

        high_count = sum(1 for r in results if r["risk_tier"] == "high")
        medium_count = sum(1 for r in results if r["risk_tier"] == "medium")
        low_count = sum(1 for r in results if r["risk_tier"] == "low")

        logger.info(
            "Scoring complete — total=%d high=%d medium=%d low=%d",
            len(results),
            high_count,
            medium_count,
            low_count,
        )
        return {
            "scored": len(results),
            "high": high_count,
            "medium": medium_count,
            "low": low_count,
        }

    @task()
    def peer_contagion(ds: str | None = None, **context) -> dict:
        """Rule-based turnover-contagion alert layer (MODEL.md §7.4.1).

        Runs after scoring; needs no checkpoint, so it still fires even when the
        GNN has not been trained yet.  Enriches today's churn_scores with
        peer_churn_rate and raises peer_contagion_risk alerts.
        """
        import sys
        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

        from etl.tasks.compute_peer_contagion import task_compute_peer_contagion

        snapshot_date = ds or date.today().isoformat()
        return task_compute_peer_contagion(snapshot_date, window_days=30)

    score_task = score_employees()
    contagion_task = peer_contagion()
    wait_for_graph >> score_task >> contagion_task  # type: ignore[operator]


churn_gnn_score_dag()
