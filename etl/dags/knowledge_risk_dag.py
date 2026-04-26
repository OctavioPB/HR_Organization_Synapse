"""Airflow DAG: Knowledge Risk — document ingestion + knowledge score computation.

Schedules:
    knowledge_ingest   — daily at 00:30 UTC (fetch new docs from Confluence/Notion)
    knowledge_score    — daily at 01:00 UTC (compute scores after ingestion)

The scoring DAG waits for the ingestion DAG to finish via ExternalTaskSensor,
and also waits for the graph_builder_dag (to join with fresh SPOF scores).

Ingestion DAG tasks:
    1. ingest_confluence  — fetch pages from Confluence (if ENABLE_CONFLUENCE=true)
    2. ingest_notion      — fetch pages from Notion (if ENABLE_NOTION=true)

Scoring DAG tasks:
    1. compute_knowledge_scores  — run graph/knowledge_risk.py
"""

from __future__ import annotations

import logging
import os
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


# ─── Ingestion DAG ────────────────────────────────────────────────────────────


@dag(
    dag_id="knowledge_ingest",
    description="Daily Confluence + Notion document metadata ingestion",
    schedule="30 0 * * *",  # 00:30 UTC daily
    start_date=datetime(2025, 1, 1),
    catchup=False,
    default_args=_DEFAULT_ARGS,
    tags=["knowledge-risk", "ingestion", "daily"],
)
def knowledge_ingest_dag():
    @task()
    def ingest_confluence(**context) -> dict:
        """Ingest document metadata from Confluence if enabled."""
        if os.environ.get("ENABLE_CONFLUENCE", "false").lower() != "true":
            logger.info("ENABLE_CONFLUENCE is not set — skipping Confluence ingestion.")
            return {"source": "confluence", "ingested": 0, "skipped": True}

        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

        from ingestion.connectors.confluence_connector import ConfluenceConnector
        from ingestion.db import get_conn

        connector = ConfluenceConnector()
        health = connector.health_check()
        if not health.get("healthy"):
            logger.warning(
                "Confluence health check failed: %s — skipping ingestion.",
                health.get("error"),
            )
            return {"source": "confluence", "ingested": 0, "error": health.get("error")}

        with get_conn() as conn:
            ingested = connector.ingest(conn)

        logger.info("Confluence: ingested %d documents.", ingested)
        return {"source": "confluence", "ingested": ingested}

    @task()
    def ingest_notion(**context) -> dict:
        """Ingest document metadata from Notion if enabled."""
        if os.environ.get("ENABLE_NOTION", "false").lower() != "true":
            logger.info("ENABLE_NOTION is not set — skipping Notion ingestion.")
            return {"source": "notion", "ingested": 0, "skipped": True}

        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

        from ingestion.connectors.notion_connector import NotionConnector
        from ingestion.db import get_conn

        connector = NotionConnector()
        health = connector.health_check()
        if not health.get("healthy"):
            logger.warning(
                "Notion health check failed: %s — skipping ingestion.",
                health.get("error"),
            )
            return {"source": "notion", "ingested": 0, "error": health.get("error")}

        with get_conn() as conn:
            ingested = connector.ingest(conn)

        logger.info("Notion: ingested %d documents.", ingested)
        return {"source": "notion", "ingested": ingested}

    @task()
    def log_ingestion_summary(confluence_result: dict, notion_result: dict) -> None:
        total = confluence_result.get("ingested", 0) + notion_result.get("ingested", 0)
        logger.info(
            "Knowledge ingestion complete — confluence=%d notion=%d total=%d",
            confluence_result.get("ingested", 0),
            notion_result.get("ingested", 0),
            total,
        )
        if total == 0:
            logger.warning(
                "No documents ingested.  Check connector credentials and "
                "ENABLE_CONFLUENCE / ENABLE_NOTION env vars."
            )

    c_result = ingest_confluence()
    n_result = ingest_notion()
    log_ingestion_summary(c_result, n_result)


knowledge_ingest_dag()


# ─── Scoring DAG ──────────────────────────────────────────────────────────────


@dag(
    dag_id="knowledge_score",
    description="Daily knowledge risk scoring — writes to employee_knowledge + knowledge_risk_scores",
    schedule="0 1 * * *",  # 01:00 UTC daily (after ingestion at 00:30)
    start_date=datetime(2025, 1, 1),
    catchup=False,
    default_args=_DEFAULT_ARGS,
    tags=["knowledge-risk", "scoring", "daily"],
)
def knowledge_score_dag():
    # Wait for today's document ingestion
    wait_for_ingest = ExternalTaskSensor(
        task_id="wait_for_knowledge_ingest",
        external_dag_id="knowledge_ingest",
        external_task_id="log_ingestion_summary",
        allowed_states=["success"],
        mode="reschedule",
        timeout=3600,
        poke_interval=120,
    )

    # Wait for today's graph SPOF scores (for enhanced_spof computation)
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
    def compute_knowledge_scores(ds: str | None = None, **context) -> dict:
        """Compute knowledge concentration scores for all employees."""
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

        from graph.knowledge_risk import compute_and_persist
        from ingestion.db import get_conn

        snapshot_date = date.fromisoformat(ds) if ds else date.today()
        logger.info("Computing knowledge risk scores for %s …", snapshot_date)

        with get_conn() as conn:
            n_scored = compute_and_persist(snapshot_date, conn)

        logger.info("Knowledge scoring complete: %d employees scored.", n_scored)
        return {"scored": n_scored, "snapshot_date": str(snapshot_date)}

    score_task = compute_knowledge_scores()
    [wait_for_ingest, wait_for_graph] >> score_task  # type: ignore[operator]


knowledge_score_dag()
