"""Airflow DAG: Temporal Graph Analysis — weekly training + daily scoring.

Schedules:
    temporal_gnn_train  — every Sunday at 03:00 UTC (weekly model refresh)
    temporal_gnn_score  — every day   at 02:00 UTC (after graph_builder_dag)

Both DAGs use ExternalTaskSensor to wait for today's graph_builder_dag run.
The training DAG additionally waits for sufficient historical snapshots (>= 10)
before attempting to train; it logs a warning and exits cleanly otherwise.

Training DAG tasks:
    1. check_history  — verify >= (n_weeks + 1) graph_snapshots exist
    2. train_model    — run graph/temporal/trainer.py; saves .pt checkpoint
    3. log_metrics    — log val_loss; warn if val_loss is > 0.05 (high error)

Scoring DAG tasks:
    1. score_employees — load latest checkpoint, write temporal_anomaly_scores
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

_VAL_LOSS_WARN_THRESHOLD = 0.05


# ─── Training DAG ─────────────────────────────────────────────────────────────


@dag(
    dag_id="temporal_gnn_train",
    description="Weekly TemporalRiskGNN model training",
    schedule="0 3 * * 0",  # Every Sunday at 03:00 UTC
    start_date=datetime(2025, 1, 1),
    catchup=False,
    default_args=_DEFAULT_ARGS,
    tags=["temporal-gnn", "weekly"],
)
def temporal_gnn_train_dag():
    @task()
    def check_history(ds: str | None = None, **context) -> dict:
        """Verify sufficient graph_snapshots exist before training."""
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

        import os
        from ingestion.db import get_conn

        n_weeks     = int(os.environ.get("TGNN_N_WEEKS", "8"))
        history_req = int(os.environ.get("TGNN_HISTORY_WEEKS", "24")) + 1

        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(DISTINCT snapshot_date) FROM graph_snapshots")
                n_distinct = cur.fetchone()[0]

        if n_distinct < history_req:
            logger.warning(
                "Only %d distinct snapshot dates found; need %d for training. "
                "Skipping this week — run graph_builder_dag daily to build history.",
                n_distinct, history_req,
            )
            return {"sufficient": False, "n_available": n_distinct, "n_required": history_req}

        logger.info(
            "History check passed: %d snapshot dates available (need %d)",
            n_distinct, history_req,
        )
        return {"sufficient": True, "n_available": n_distinct}

    @task()
    def train_model(history_info: dict, ds: str | None = None, **context) -> dict:
        """Train TemporalRiskGNN on up to TGNN_HISTORY_WEEKS of weekly snapshots."""
        if not history_info.get("sufficient", False):
            logger.info("Skipping training — insufficient history.")
            return {"skipped": True}

        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

        from graph.temporal.trainer import train

        snapshot_date = date.fromisoformat(ds) if ds else date.today()
        logger.info("Training TemporalRiskGNN for snapshot_date=%s", snapshot_date)
        checkpoint_path, metrics = train(end_date=snapshot_date)
        logger.info("Training complete — checkpoint=%s metrics=%s", checkpoint_path, metrics)
        return metrics

    @task()
    def log_metrics(metrics: dict) -> None:
        """Log training metrics; warn if val_loss is above alert threshold."""
        if metrics.get("skipped"):
            logger.info("Training was skipped — no metrics to log.")
            return

        if metrics.get("error"):
            logger.warning("Training returned error: %s", metrics)
            return

        val_loss = metrics.get("val_loss", float("nan"))
        logger.info(
            "TemporalRiskGNN training — best_epoch=%d val_loss=%.6f "
            "train_windows=%d val_windows=%d",
            metrics.get("best_epoch", 0),
            val_loss,
            metrics.get("n_train_windows", 0),
            metrics.get("n_val_windows", 0),
        )
        if val_loss > _VAL_LOSS_WARN_THRESHOLD:
            logger.warning(
                "val_loss=%.6f exceeds threshold %.4f — model quality may be degraded. "
                "Check feature pipeline and snapshot coverage.",
                val_loss, _VAL_LOSS_WARN_THRESHOLD,
            )

    history_check = check_history()
    metrics = train_model(history_check)
    log_metrics(metrics)


temporal_gnn_train_dag()


# ─── Scoring DAG ──────────────────────────────────────────────────────────────


@dag(
    dag_id="temporal_gnn_score",
    description="Daily temporal anomaly scoring — writes to temporal_anomaly_scores",
    schedule="0 2 * * *",  # Every day at 02:00 UTC (after graph_builder_dag at 01:00)
    start_date=datetime(2025, 1, 1),
    catchup=False,
    default_args=_DEFAULT_ARGS,
    tags=["temporal-gnn", "daily"],
)
def temporal_gnn_score_dag():
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
        """Load latest checkpoint and write temporal_anomaly_scores for today."""
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

        from graph.temporal.scorer import score

        end_date = date.fromisoformat(ds) if ds else date.today()
        logger.info("Temporal scoring for end_date=%s", end_date)

        try:
            results = score(end_date=end_date)
        except FileNotFoundError as exc:
            logger.warning(
                "No checkpoint found — skipping scoring. "
                "Run temporal_gnn_train first. Error: %s", exc,
            )
            return {"scored": 0, "skipped": True}

        high_count   = sum(1 for r in results if r["anomaly_tier"] == "high")
        medium_count = sum(1 for r in results if r["anomaly_tier"] == "medium")
        low_count    = sum(1 for r in results if r["anomaly_tier"] == "low")

        logger.info(
            "Temporal scoring complete — total=%d high=%d medium=%d low=%d",
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


temporal_gnn_score_dag()
