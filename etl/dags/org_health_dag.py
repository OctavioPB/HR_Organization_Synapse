"""Airflow DAG: org_health_dag — Weekly Org Health Score & Executive Briefing (F9).

Schedule: Every Monday at 06:00 UTC (after graph_builder_dag has run).

Tasks:
    compute_health  — reads graph/risk data, writes org_health_scores row
    generate_brief  — fetches score + trend, generates Claude narrative
    deliver_brief   — sends briefing to configured Slack channel or email

trigger_rule for generate_brief and deliver_brief: "all_done" so the
delivery step always runs and can report partial failures.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timedelta

from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.sensors.external_task import ExternalTaskSensor

logger = logging.getLogger(__name__)

_API_URL = os.environ.get("API_INTERNAL_URL", "http://api:8000")

default_args = {
    "owner": "org-synapse",
    "retries": 1,
    "retry_delay": timedelta(minutes=5),
    "email_on_failure": False,
}


# ─── Task implementations ──────────────────────────────────────────────────────


def _compute_health(**context) -> dict:
    """Compute and persist the org health score for today's snapshot date."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

    from ingestion.db import get_conn
    from graph.org_health import compute_and_persist

    snapshot_date = context["ds_nodash"]
    snap = date(int(snapshot_date[:4]), int(snapshot_date[4:6]), int(snapshot_date[6:]))

    with get_conn() as conn:
        health = compute_and_persist(snap, conn)

    logger.info("Health score: %.1f (%s)", health["score"], health["tier"])
    return {k: str(v) if hasattr(v, "isoformat") else v for k, v in health.items()}


def _generate_brief(**context) -> dict:
    """Generate executive briefing and push to XCom."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

    from ingestion.db import get_conn
    from graph.org_health import generate_briefing
    from api import db as queries

    with get_conn() as conn:
        current = queries.fetch_latest_org_health(conn)
        trend   = queries.fetch_org_health_trend(8, conn)

    if not current:
        raise RuntimeError("No org health score found — compute_health may have failed.")

    briefing = generate_briefing(current, trend)
    logger.info(
        "Briefing generated: score=%.1f tier=%s delta=%+.1f",
        briefing["score"], briefing["tier"], briefing["trend_delta"],
    )
    return briefing


def _deliver_brief(**context) -> None:
    """Send the briefing to Slack and/or email.

    Slack is used when SLACK_BOT_TOKEN + SLACK_BRIEFING_CHANNEL are set.
    SendGrid email is used when SENDGRID_API_KEY + BRIEFING_EMAIL_TO are set.
    If neither is configured, the task logs the briefing and succeeds silently.
    """
    ti = context["ti"]
    briefing: dict = ti.xcom_pull(task_ids="generate_brief")
    if not briefing:
        logger.warning("No briefing data in XCom — skipping delivery.")
        return

    score      = briefing["score"]
    tier       = briefing["tier"]
    delta      = briefing["trend_delta"]
    narrative  = briefing["narrative"]
    actions    = briefing.get("recommended_actions", [])
    computed   = briefing.get("computed_at", "—")

    _deliver_slack(score, tier, delta, narrative, actions, computed)
    _deliver_email(score, tier, delta, narrative, actions, computed)


def _deliver_slack(
    score: float, tier: str, delta: float,
    narrative: str, actions: list[str], computed: str,
) -> None:
    token   = os.environ.get("SLACK_BOT_TOKEN", "")
    channel = os.environ.get("SLACK_BRIEFING_CHANNEL", "")
    if not (token and channel):
        logger.info("Slack delivery skipped — SLACK_BOT_TOKEN or SLACK_BRIEFING_CHANNEL not set.")
        return

    tier_emoji = {"healthy": ":white_check_mark:", "caution": ":warning:",
                  "at_risk": ":x:", "critical": ":rotating_light:"}.get(tier, ":bar_chart:")
    delta_str  = f"+{delta:.1f}" if delta >= 0 else f"{delta:.1f}"

    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": "📊 Weekly Org Health Briefing"}},
        {"type": "section", "text": {"type": "mrkdwn",
            "text": f"*{score}/100* {tier_emoji} `{tier.upper()}` · {delta_str} pts vs last week · _{computed}_"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": narrative}},
        {"type": "section", "text": {"type": "mrkdwn",
            "text": "*Recommended actions:*\n" + "\n".join(f"• {a}" for a in actions)}},
    ]

    try:
        import urllib.request
        payload = json.dumps({"channel": channel, "blocks": blocks}).encode()
        req = urllib.request.Request(
            "https://slack.com/api/chat.postMessage",
            data=payload,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read())
        if not body.get("ok"):
            logger.warning("Slack API error: %s", body.get("error"))
        else:
            logger.info("Briefing delivered to Slack channel %s", channel)
    except Exception as exc:
        logger.warning("Slack delivery failed: %s", exc)


def _deliver_email(
    score: float, tier: str, delta: float,
    narrative: str, actions: list[str], computed: str,
) -> None:
    api_key  = os.environ.get("SENDGRID_API_KEY", "")
    to_email = os.environ.get("BRIEFING_EMAIL_TO", "")
    from_email = os.environ.get("BRIEFING_EMAIL_FROM", "noreply@org-synapse.internal")
    if not (api_key and to_email):
        logger.info("Email delivery skipped — SENDGRID_API_KEY or BRIEFING_EMAIL_TO not set.")
        return

    delta_str = f"+{delta:.1f}" if delta >= 0 else f"{delta:.1f}"
    subject   = f"[Org Synapse] Weekly Health Briefing — {score}/100 ({tier}) {delta_str} pts"
    body_html = (
        f"<h2>Org Health Score: {score}/100</h2>"
        f"<p><b>Tier:</b> {tier} &nbsp;·&nbsp; <b>Change:</b> {delta_str} pts &nbsp;·&nbsp; "
        f"<b>Computed:</b> {computed}</p>"
        f"<p>{narrative}</p>"
        f"<h3>Recommended Actions</h3><ul>"
        + "".join(f"<li>{a}</li>" for a in actions)
        + "</ul>"
    )

    payload = json.dumps({
        "personalizations": [{"to": [{"email": to_email}]}],
        "from": {"email": from_email},
        "subject": subject,
        "content": [{"type": "text/html", "value": body_html}],
    }).encode()

    try:
        import urllib.request
        req = urllib.request.Request(
            "https://api.sendgrid.com/v3/mail/send",
            data=payload,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            logger.info("Email briefing sent to %s (HTTP %d)", to_email, resp.status)
    except Exception as exc:
        logger.warning("Email delivery failed: %s", exc)


# ─── DAG definition ───────────────────────────────────────────────────────────

with DAG(
    dag_id="org_health_dag",
    description="Weekly Org Health Score computation and executive briefing delivery",
    schedule_interval="0 6 * * 1",   # Every Monday 06:00 UTC
    start_date=datetime(2025, 1, 6),
    catchup=False,
    default_args=default_args,
    tags=["org-synapse", "f9", "executive"],
) as dag:

    wait_for_graph = ExternalTaskSensor(
        task_id="wait_for_graph_builder",
        external_dag_id="graph_builder_dag",
        external_task_id="compute_metrics",
        timeout=3600,
        mode="reschedule",
        poke_interval=300,
    )

    compute_health = PythonOperator(
        task_id="compute_health",
        python_callable=_compute_health,
    )

    generate_brief = PythonOperator(
        task_id="generate_brief",
        python_callable=_generate_brief,
        trigger_rule="all_done",
    )

    deliver_brief = PythonOperator(
        task_id="deliver_brief",
        python_callable=_deliver_brief,
        trigger_rule="all_done",
    )

    wait_for_graph >> compute_health >> generate_brief >> deliver_brief
