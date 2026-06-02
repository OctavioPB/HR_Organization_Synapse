"""Daily DAG: detect employee departures and generate structural impact reports.

Schedule: 06:00 UTC daily.
Detects employees where active=FALSE and deactivated_at was yesterday.
Generates a departure impact report for each new departure.

Task chain:
    detect_departures → generate_reports (one per departure) → broadcast_alerts
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from airflow.decorators import dag, task

logger = logging.getLogger(__name__)

_DEFAULT_ARGS = {
    "owner": "org-synapse",
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
    "email_on_failure": False,
}


@dag(
    dag_id="departure_report_dag",
    description="Detect new employee departures and generate structural impact reports",
    schedule="0 6 * * *",
    start_date=datetime(2025, 1, 1),
    catchup=False,
    default_args=_DEFAULT_ARGS,
    tags=["departure", "reporting", "daily"],
)
def departure_report_dag():

    @task()
    def detect_departures(**context) -> list[dict]:
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

        from ingestion.db import get_conn

        yesterday = context["ds"]  # YYYY-MM-DD
        with get_conn() as conn:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT e.id::text AS employee_id, e.name, e.department,
                           e.deactivated_at::date AS departure_date
                    FROM employees e
                    WHERE e.active = FALSE
                      AND e.deactivated_at::date = %s::date
                      AND e.id NOT IN (
                        SELECT employee_id FROM departure_impact_reports
                        WHERE departure_date = %s::date
                      )
                    """,
                    (yesterday, yesterday),
                )
                rows = [dict(r) for r in cur.fetchall()]

        logger.info("detect_departures: found %d new departures on %s", len(rows), yesterday)
        return rows

    @task()
    def generate_reports(departures: list[dict]) -> list[dict]:
        if not departures:
            logger.info("No new departures to process.")
            return []

        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

        from ingestion.db import get_conn
        from etl.tasks.generate_departure_report import task_generate_departure_report

        results = []
        for d in departures:
            try:
                with get_conn() as conn:
                    result = task_generate_departure_report(
                        employee_id=d["employee_id"],
                        departure_date_str=str(d["departure_date"]),
                        conn=conn,
                    )
                    results.append(result)
                    logger.info(
                        "Report generated: %s (%s) — %s",
                        d["name"], d["departure_date"], result.get("status", "unknown"),
                    )
            except Exception as exc:
                logger.error("Report failed for %s: %s", d["employee_id"], exc)
                results.append({"employee_id": d["employee_id"], "status": "failed", "error": str(exc)})

        return results

    @task(trigger_rule="all_done")
    def broadcast_departure_alerts(results: list[dict]) -> dict:
        """Push departure_report_ready alert to connected WebSocket clients."""
        import os
        import httpx

        ready = [r for r in results if r.get("status") == "ready"]
        if not ready:
            return {"broadcast": 0}

        api_url = os.environ.get("API_INTERNAL_URL", "http://localhost:8000")
        api_key = os.environ.get("INTERNAL_API_KEY", "")
        headers = {"X-Internal-Key": api_key} if api_key else {}

        alerts = [
            {
                "type": "departure_report_ready",
                "employee_id": r["employee_id"],
                "departure_date": r["departure_date"],
            }
            for r in ready
        ]

        try:
            resp = httpx.post(
                f"{api_url}/internal/alerts/broadcast",
                json={"source": "departure_report_dag", "alerts": alerts, "metadata": {}},
                headers=headers,
                timeout=10,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            logger.warning("Broadcast failed: %s", exc)
            return {"broadcast": 0, "error": str(exc)}

    departures = detect_departures()
    results    = generate_reports(departures)
    broadcast_departure_alerts(results)


departure_report_dag()
