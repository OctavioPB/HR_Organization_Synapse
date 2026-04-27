"""Airflow DAG: compliance_dag — Quarterly Compliance Report & Data Retention (F8).

Schedule: First day of each quarter at 08:00 UTC
    (Jan 1, Apr 1, Jul 1, Oct 1)

Tasks:
    run_purge     — delete raw_events > 90 days, graph_snapshots > 365 days
    gen_report    — generate HTML compliance report and save to disk
    deliver_report — email the report to the DPO / compliance team

trigger_rule for gen_report and deliver_report: "all_done" so the
delivery step always runs even if the purge step had partial failures.
"""

from __future__ import annotations

import logging
import os
from datetime import timedelta
from pathlib import Path

from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.utils.dates import days_ago

logger = logging.getLogger(__name__)

_REPORT_DIR = os.environ.get("COMPLIANCE_REPORT_DIR", "/tmp/compliance_reports")
_DPO_EMAIL  = os.environ.get("DPO_EMAIL", "")
_SENDGRID_API_KEY = os.environ.get("SENDGRID_API_KEY", "")

default_args = {
    "owner":          "org-synapse",
    "retries":        1,
    "retry_delay":    timedelta(minutes=5),
    "email_on_failure": False,
}


# ─── Task implementations ──────────────────────────────────────────────────────


def _run_purge(**context) -> dict:
    """Delete rows that exceed the data retention policy."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

    from ingestion.db import get_conn
    from graph.compliance import run_retention_purge

    with get_conn() as conn:
        results = run_retention_purge(conn, triggered_by="airflow")

    total = sum(r["rows_deleted"] for r in results)
    logger.info("Retention purge complete: %d total rows deleted", total)
    for r in results:
        logger.info("  %s: %d rows deleted (cutoff %s)", r["table"], r["rows_deleted"], r["cutoff_date"])

    return {"results": results, "total_rows_deleted": total}


def _gen_report(**context) -> str:
    """Generate the quarterly compliance HTML report and write to disk."""
    import sys
    from datetime import date
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

    from ingestion.db import get_conn
    from graph.compliance import generate_html_report

    with get_conn() as conn:
        html = generate_html_report(conn)

    report_dir = Path(_REPORT_DIR)
    report_dir.mkdir(parents=True, exist_ok=True)
    quarter = (date.today().month - 1) // 3 + 1
    filename = report_dir / f"compliance_report_{date.today().year}_Q{quarter}.html"
    filename.write_text(html, encoding="utf-8")
    logger.info("Compliance report written to %s (%d bytes)", filename, len(html))
    return str(filename)


def _deliver_report(**context) -> None:
    """Email the compliance report to the DPO if SendGrid is configured."""
    report_path_str = context["ti"].xcom_pull(task_ids="gen_report")
    if not report_path_str:
        logger.warning("No report path from gen_report task — skipping delivery")
        return

    report_path = Path(report_path_str)
    if not report_path.exists():
        logger.warning("Report file not found at %s — skipping delivery", report_path)
        return

    html_content = report_path.read_text(encoding="utf-8")

    if not _DPO_EMAIL:
        logger.info("DPO_EMAIL not set — compliance report saved locally at %s", report_path)
        return

    if not _SENDGRID_API_KEY:
        logger.info("SENDGRID_API_KEY not set — skipping email delivery")
        return

    _send_email(html_content, report_path.name)


def _send_email(html_content: str, report_filename: str) -> None:
    """Send the compliance report via SendGrid."""
    import json
    import urllib.request

    from datetime import date
    quarter = (date.today().month - 1) // 3 + 1
    subject = f"Org Synapse — Q{quarter} {date.today().year} Compliance Report"

    payload = json.dumps({
        "personalizations": [{"to": [{"email": _DPO_EMAIL}]}],
        "from":             {"email": "compliance@org-synapse.internal"},
        "subject":          subject,
        "content": [
            {
                "type":  "text/html",
                "value": html_content,
            }
        ],
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.sendgrid.com/v3/mail/send",
        data=payload,
        headers={
            "Authorization": f"Bearer {_SENDGRID_API_KEY}",
            "Content-Type":  "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            logger.info("Compliance report emailed to %s (HTTP %s)", _DPO_EMAIL, resp.status)
    except Exception as exc:
        logger.warning("Failed to email compliance report: %s", exc)


# ─── DAG definition ───────────────────────────────────────────────────────────

with DAG(
    dag_id="compliance_dag",
    description="Quarterly compliance report and data retention purge (F8)",
    default_args=default_args,
    # First day of each quarter at 08:00 UTC: Jan 1, Apr 1, Jul 1, Oct 1
    schedule_interval="0 8 1 1,4,7,10 *",
    start_date=days_ago(1),
    catchup=False,
    tags=["compliance", "gdpr", "ccpa", "retention"],
) as dag:

    run_purge = PythonOperator(
        task_id="run_purge",
        python_callable=_run_purge,
    )

    gen_report = PythonOperator(
        task_id="gen_report",
        python_callable=_gen_report,
        trigger_rule="all_done",
    )

    deliver_report = PythonOperator(
        task_id="deliver_report",
        python_callable=_deliver_report,
        trigger_rule="all_done",
    )

    run_purge >> gen_report >> deliver_report
