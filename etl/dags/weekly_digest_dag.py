"""Weekly digest DAG — Org Health + risk signals → email + Slack.

Schedule: Sunday 23:00 UTC (delivers Monday morning globally).
Depends on org_health_dag completing before it runs.

Task chain:
    compile_digest_data
        → generate_digest_narrative
            → send_email_digest (parallel)
            → send_slack_digest (parallel)
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta

from airflow.decorators import dag, task
from airflow.sensors.external_task import ExternalTaskSensor

logger = logging.getLogger(__name__)

_DEFAULT_ARGS = {
    "owner": "org-synapse",
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
    "email_on_failure": False,
}

_DASHBOARD_URL = os.environ.get("DASHBOARD_URL", "http://localhost:5173")


@dag(
    dag_id="weekly_digest_dag",
    description="Weekly HR digest: Org Health score + risk signals → email + Slack",
    schedule="0 23 * * 0",  # Sunday 23:00 UTC
    start_date=datetime(2025, 1, 1),
    catchup=False,
    default_args=_DEFAULT_ARGS,
    tags=["digest", "weekly", "notifications"],
)
def weekly_digest_dag():
    wait_for_org_health = ExternalTaskSensor(
        task_id="wait_for_org_health",
        external_dag_id="org_health_dag",
        external_task_id="score_org_health",
        allowed_states=["success"],
        mode="reschedule",
        timeout=3600,
        poke_interval=120,
    )

    @task()
    def compile_digest_data(**context) -> dict:
        import sys
        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

        from ingestion.db import get_conn

        with get_conn() as conn:
            conn.autocommit = True
            cur = conn.cursor()

            # Latest org health
            cur.execute(
                """
                SELECT score, tier,
                       score - LAG(score) OVER (ORDER BY computed_at) AS delta
                FROM org_health_scores
                ORDER BY computed_at DESC
                LIMIT 2
                """
            )
            rows = cur.fetchall()
            health_row = rows[0] if rows else {}
            score = float(health_row.get("score", 0)) if health_row else 0.0
            tier = health_row.get("tier", "caution") if health_row else "caution"
            delta = float(health_row.get("delta") or 0.0) if health_row else 0.0

            # Active silos
            cur.execute("SELECT COUNT(*) AS cnt FROM alerts WHERE type='silo' AND resolved=FALSE")
            silo_count = cur.fetchone()["cnt"]

            # Critical SPOF employees
            cur.execute(
                """
                SELECT COUNT(DISTINCT employee_id) AS cnt
                FROM risk_scores
                WHERE flag = 'critical'
                  AND scored_at >= NOW() - INTERVAL '7 days'
                """
            )
            critical_spof_count = cur.fetchone()["cnt"]

            # Onboarding at-risk (if table exists)
            onboarding_risk_count = 0
            try:
                cur.execute(
                    """
                    SELECT COUNT(*) AS cnt
                    FROM onboarding_integration_scores
                    WHERE below_cohort_threshold = TRUE
                      AND scored_date >= CURRENT_DATE - 7
                    """
                )
                onboarding_risk_count = cur.fetchone()["cnt"]
            except Exception:
                pass

            # Top risk departments
            cur.execute(
                """
                SELECT e.department, AVG(rs.spof_score) AS avg_spof
                FROM risk_scores rs
                JOIN employees e ON e.id = rs.employee_id
                WHERE rs.scored_at >= NOW() - INTERVAL '7 days'
                  AND rs.spof_score > 0.5
                GROUP BY e.department
                ORDER BY avg_spof DESC
                LIMIT 3
                """
            )
            top_risks = [{"department": r["department"], "avg_spof": float(r["avg_spof"])} for r in cur.fetchall()]

            cur.close()

        import datetime as dt

        now = dt.datetime.utcnow()
        week_label = now.strftime("Week of %B %d, %Y")
        generated_at = now.strftime("%Y-%m-%d %H:%M UTC")

        return {
            "score": round(score, 1),
            "tier": tier,
            "delta": round(delta, 1),
            "silo_count": int(silo_count),
            "critical_spof_count": int(critical_spof_count),
            "onboarding_risk_count": int(onboarding_risk_count),
            "top_risks": top_risks,
            "week_label": week_label,
            "generated_at": generated_at,
        }

    @task()
    def generate_digest_narrative(digest_data: dict) -> dict:
        try:
            from graph.claude_client import call_claude

            prompt = (
                "You are a people analytics advisor. Based on this week's organizational data, "
                "write one concise paragraph (max 80 words) recommending the single highest-priority "
                "HR action. Be specific and data-led. Do not use filler phrases.\n\n"
                f"Data: {json.dumps(digest_data, indent=2)}"
            )
            narrative = call_claude(prompt, max_tokens=200)
        except Exception as exc:
            logger.warning("Narrative generation failed: %s", exc)
            tier = digest_data.get("tier", "caution")
            score = digest_data.get("score", 0)
            narrative = (
                f"The organizational health score is {score}/100 ({tier}). "
                f"There are {digest_data.get('critical_spof_count', 0)} critical SPOF employees "
                f"and {digest_data.get('silo_count', 0)} active silos. "
                "Review the critical node panel and investigate the highest-risk departments this week."
            )

        return {**digest_data, "narrative": narrative}

    @task()
    def send_email_digest(digest_data: dict) -> dict:
        import sys
        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

        from ingestion.db import get_conn

        with get_conn() as conn:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute("SELECT email_recipients, enabled_email FROM digest_config LIMIT 1")
                config_row = cur.fetchone()

        if not config_row or not config_row["enabled_email"]:
            logger.info("Email digest disabled or not configured — skipping.")
            return {"sent": 0, "skipped": True}

        recipients = config_row["email_recipients"]
        if not recipients:
            logger.info("No email recipients configured — skipping.")
            return {"sent": 0, "skipped": True}

        sendgrid_key = os.environ.get("SENDGRID_API_KEY", "")
        if not sendgrid_key:
            logger.warning("SENDGRID_API_KEY not set — skipping email digest.")
            return {"sent": 0, "skipped": True}

        try:
            from jinja2 import Environment, FileSystemLoader

            template_dir = Path(__file__).parents[1] / "templates"
            env = Environment(loader=FileSystemLoader(str(template_dir)))
            template = env.get_template("digest_email.html")
            html_content = template.render(
                **digest_data,
                dashboard_url=_DASHBOARD_URL,
            )

            import sendgrid as sg
            from sendgrid.helpers.mail import Mail

            client = sg.SendGridAPIClient(sendgrid_key)
            message = Mail(
                from_email=os.environ.get("DIGEST_FROM_EMAIL", "noreply@orgsynapse.io"),
                to_emails=recipients,
                subject=f"Org Synapse Weekly Digest — {digest_data.get('tier', '').replace('_', ' ').title()} ({digest_data.get('score', 0)}/100)",
                html_content=html_content,
            )
            response = client.send(message)
            logger.info("Email digest sent: status=%s recipients=%d", response.status_code, len(recipients))
            return {"sent": len(recipients), "status": response.status_code}
        except Exception as exc:
            logger.error("Email digest send failed: %s", exc)
            return {"sent": 0, "error": str(exc)}

    @task()
    def send_slack_digest(digest_data: dict) -> dict:
        import sys
        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

        from ingestion.db import get_conn

        with get_conn() as conn:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute("SELECT slack_webhook_url, enabled_slack FROM digest_config LIMIT 1")
                config_row = cur.fetchone()

        if not config_row or not config_row["enabled_slack"] or not config_row["slack_webhook_url"]:
            logger.info("Slack digest disabled or webhook not configured — skipping.")
            return {"sent": False, "skipped": True}

        webhook_url = config_row["slack_webhook_url"]
        score = digest_data.get("score", 0)
        tier = digest_data.get("tier", "caution").replace("_", " ").title()
        delta = digest_data.get("delta", 0)
        delta_text = f"+{delta}" if delta > 0 else str(delta)
        narrative = digest_data.get("narrative", "")
        silo_count = digest_data.get("silo_count", 0)
        spof_count = digest_data.get("critical_spof_count", 0)

        blocks = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": f"Org Synapse Weekly Digest — {tier} ({score}/100)"},
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": narrative},
            },
            {"type": "divider"},
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Health Score:* {score}/100 ({delta_text} vs last week)"},
                    {"type": "mrkdwn", "text": f"*Active Silos:* {silo_count}"},
                    {"type": "mrkdwn", "text": f"*Critical SPOF Employees:* {spof_count}"},
                    {"type": "mrkdwn", "text": f"*Week:* {digest_data.get('week_label', '')}"},
                ],
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Open Dashboard →"},
                        "url": _DASHBOARD_URL,
                        "style": "primary",
                    }
                ],
            },
        ]

        try:
            import httpx

            resp = httpx.post(webhook_url, json={"blocks": blocks}, timeout=10)
            resp.raise_for_status()
            logger.info("Slack digest sent successfully.")
            return {"sent": True}
        except Exception as exc:
            logger.error("Slack digest send failed: %s", exc)
            return {"sent": False, "error": str(exc)}

    # DAG wiring
    digest_data = compile_digest_data()
    full_data = generate_digest_narrative(digest_data)

    wait_for_org_health >> digest_data
    send_email_digest(full_data)
    send_slack_digest(full_data)


weekly_digest_dag()
