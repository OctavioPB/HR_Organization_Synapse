"""Compliance & Regulatory Reporting logic for F8.

Covers:
  - Data audit: catalogue what is stored per employee and for how long.
  - Data retention purge: delete raw_events > 90 days, graph_snapshots > 12 months.
  - Employee data export: full GDPR Article 20 package for one employee.
  - Consent management: update employee consent flag and write audit log entry.
  - Quarterly HTML compliance report generation.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

# ─── Retention policy (configurable via env in the DAG) ───────────────────────

_RAW_EVENTS_RETENTION_DAYS   = 90
_GRAPH_SNAPSHOTS_RETENTION_MONTHS = 12  # stored as 365 days for simplicity
_GRAPH_SNAPSHOTS_RETENTION_DAYS   = 365

# ─── Data catalogue ───────────────────────────────────────────────────────────

_DATA_CATEGORIES: list[dict[str, Any]] = [
    {
        "table":            "raw_events",
        "description":      "Collaboration metadata events (who contacted whom, channel, timestamp)",
        "personal_data":    True,
        "sensitivity":      "medium",
        "retention_days":   _RAW_EVENTS_RETENTION_DAYS,
        "legal_basis":      "legitimate_interest",
        "fields":           ["source_id", "target_id", "channel", "direction", "ts", "weight"],
        "excludes_content": True,
    },
    {
        "table":            "graph_snapshots",
        "description":      "Daily graph-metric snapshot per employee (centrality, clustering, community)",
        "personal_data":    True,
        "sensitivity":      "medium",
        "retention_days":   _GRAPH_SNAPSHOTS_RETENTION_DAYS,
        "legal_basis":      "legitimate_interest",
        "fields":           ["betweenness", "degree_in", "degree_out", "clustering", "community_id"],
        "excludes_content": True,
    },
    {
        "table":            "risk_scores",
        "description":      "SPOF / entropy / anomaly risk scores per employee",
        "personal_data":    True,
        "sensitivity":      "high",
        "retention_days":   _GRAPH_SNAPSHOTS_RETENTION_DAYS,
        "legal_basis":      "legitimate_interest",
        "fields":           ["spof_score", "entropy_trend", "anomaly_score", "flag"],
        "excludes_content": False,
    },
    {
        "table":            "churn_risk_scores",
        "description":      "Predicted churn probability per employee",
        "personal_data":    True,
        "sensitivity":      "high",
        "retention_days":   _GRAPH_SNAPSHOTS_RETENTION_DAYS,
        "legal_basis":      "legitimate_interest",
        "fields":           ["churn_prob", "risk_tier", "model_version"],
        "excludes_content": False,
    },
    {
        "table":            "employee_knowledge",
        "description":      "Knowledge contribution metadata (document counts, domains)",
        "personal_data":    True,
        "sensitivity":      "low",
        "retention_days":   _GRAPH_SNAPSHOTS_RETENTION_DAYS,
        "legal_basis":      "legitimate_interest",
        "fields":           ["source", "doc_count", "domain", "last_contribution_at"],
        "excludes_content": True,
    },
    {
        "table":            "employees",
        "description":      "Employee master record (name, department, role, consent status)",
        "personal_data":    True,
        "sensitivity":      "medium",
        "retention_days":   None,   # retained for employment duration
        "legal_basis":      "contract",
        "fields":           ["name", "department", "role", "active", "consent"],
        "excludes_content": False,
    },
    {
        "table":            "consent_audit_log",
        "description":      "Audit trail of all consent changes",
        "personal_data":    True,
        "sensitivity":      "low",
        "retention_days":   _GRAPH_SNAPSHOTS_RETENTION_DAYS * 3,   # 3 years
        "legal_basis":      "legal_obligation",
        "fields":           ["employee_id", "changed_by", "previous_value", "new_value", "reason", "changed_at"],
        "excludes_content": False,
    },
]


# ─── Data audit ───────────────────────────────────────────────────────────────


def build_data_audit(conn) -> dict[str, Any]:
    """Return a catalogue of all personal data stored in the system.

    Includes per-table row counts and the retention policy that applies.
    """
    audit: list[dict[str, Any]] = []

    for cat in _DATA_CATEGORIES:
        row_count = _count_table(cat["table"], conn)
        entry = dict(cat)
        entry["row_count"] = row_count
        entry["cutoff_date"] = (
            (date.today() - timedelta(days=cat["retention_days"])).isoformat()
            if cat["retention_days"] is not None
            else None
        )
        audit.append(entry)

    return {
        "generated_at":       datetime.now(timezone.utc).isoformat(),
        "framework":          ["GDPR", "CCPA"],
        "data_controller":    "Org Synapse",
        "dpo_contact":        "privacy@org-synapse.internal",
        "categories":         audit,
        "total_tables":       len(audit),
        "total_personal_rows": sum(e["row_count"] for e in audit if e["personal_data"]),
    }


def _count_table(table_name: str, conn) -> int:
    """Return row count for a given table; 0 if the table doesn't exist."""
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM {table_name}")   # noqa: S608 — table name comes from internal constant
            row = cur.fetchone()
        return int(row[0] if row else 0)
    except Exception:
        return 0


# ─── Data retention purge ─────────────────────────────────────────────────────


def run_retention_purge(conn, triggered_by: str = "api") -> list[dict[str, Any]]:
    """Delete rows that exceed retention policy from raw_events and graph_snapshots.

    Returns a list of purge result dicts (one per table) with rows_deleted and cutoff_date.
    """
    results: list[dict[str, Any]] = []

    raw_cutoff    = date.today() - timedelta(days=_RAW_EVENTS_RETENTION_DAYS)
    graph_cutoff  = date.today() - timedelta(days=_GRAPH_SNAPSHOTS_RETENTION_DAYS)

    tasks = [
        ("raw_events",      "ts::date",             raw_cutoff),
        ("graph_snapshots", "snapshot_date",         graph_cutoff),
    ]

    for table, date_col, cutoff in tasks:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"DELETE FROM {table} WHERE {date_col} < %s",   # noqa: S608
                    (cutoff,),
                )
                rows_deleted = cur.rowcount
            conn.commit()

            _record_purge(
                table=table,
                rows_deleted=rows_deleted,
                cutoff_date=cutoff,
                triggered_by=triggered_by,
                status="completed",
                conn=conn,
            )

            results.append({
                "table":        table,
                "rows_deleted": rows_deleted,
                "cutoff_date":  cutoff.isoformat(),
                "status":       "completed",
            })
            logger.info("Purged %d rows from %s (cutoff %s)", rows_deleted, table, cutoff)

        except Exception as exc:
            conn.rollback()
            _record_purge(
                table=table,
                rows_deleted=0,
                cutoff_date=cutoff,
                triggered_by=triggered_by,
                status="failed",
                conn=conn,
            )
            results.append({
                "table":        table,
                "rows_deleted": 0,
                "cutoff_date":  cutoff.isoformat(),
                "status":       "failed",
                "error":        str(exc),
            })
            logger.error("Purge failed for %s: %s", table, exc)

    return results


def _record_purge(
    table: str,
    rows_deleted: int,
    cutoff_date: date,
    triggered_by: str,
    status: str,
    conn,
) -> None:
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO data_retention_purges
                    (table_name, rows_deleted, cutoff_date, triggered_by, status)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (table, rows_deleted, cutoff_date, triggered_by, status),
            )
        conn.commit()
    except Exception as exc:
        logger.warning("Could not record purge log for %s: %s", table, exc)


# ─── Employee data export (GDPR Article 20) ───────────────────────────────────


def export_employee_data(employee_id: str, conn) -> dict[str, Any] | None:
    """Return a complete GDPR Article 20 data package for one employee.

    Returns None if the employee does not exist.
    """
    employee = _fetch_employee_record(employee_id, conn)
    if employee is None:
        return None

    return {
        "export_generated_at":  datetime.now(timezone.utc).isoformat(),
        "article":              "GDPR Article 20 — Right to Data Portability",
        "employee_id":          employee_id,
        "employee":             employee,
        "raw_events":           _fetch_employee_raw_events(employee_id, conn),
        "graph_snapshots":      _fetch_employee_graph_snapshots(employee_id, conn),
        "risk_scores":          _fetch_employee_risk_scores_export(employee_id, conn),
        "churn_scores":         _fetch_employee_churn_scores_export(employee_id, conn),
        "knowledge_entries":    _fetch_employee_knowledge_export(employee_id, conn),
        "consent_audit_log":    _fetch_consent_audit_log(employee_id, conn),
    }


def _fetch_employee_record(employee_id: str, conn) -> dict | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id::text, name, department, role, active, consent, created_at FROM employees WHERE id = %s::uuid",
            (employee_id,),
        )
        row = cur.fetchone()
    return dict(row) if row else None


def _fetch_employee_raw_events(employee_id: str, conn) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                id::text, source_id::text, target_id::text,
                channel, direction, ts, weight
            FROM raw_events
            WHERE source_id = %s::uuid OR target_id = %s::uuid
            ORDER BY ts DESC
            LIMIT 10000
            """,
            (employee_id, employee_id),
        )
        return [dict(r) for r in cur.fetchall()]


def _fetch_employee_graph_snapshots(employee_id: str, conn) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT snapshot_date, betweenness, degree_in, degree_out, clustering, community_id
            FROM graph_snapshots
            WHERE employee_id = %s::uuid
            ORDER BY snapshot_date DESC
            """,
            (employee_id,),
        )
        return [dict(r) for r in cur.fetchall()]


def _fetch_employee_risk_scores_export(employee_id: str, conn) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT scored_at, spof_score, entropy_trend, anomaly_score, flag
            FROM risk_scores
            WHERE employee_id = %s::uuid
            ORDER BY scored_at DESC
            """,
            (employee_id,),
        )
        return [dict(r) for r in cur.fetchall()]


def _fetch_employee_churn_scores_export(employee_id: str, conn) -> list[dict]:
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT scored_at, churn_prob, risk_tier, model_version
                FROM churn_risk_scores
                WHERE employee_id = %s::uuid
                ORDER BY scored_at DESC
                """,
                (employee_id,),
            )
            return [dict(r) for r in cur.fetchall()]
    except Exception:
        return []


def _fetch_employee_knowledge_export(employee_id: str, conn) -> list[dict]:
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT source, doc_count, domain, last_contribution_at, ingested_at
                FROM employee_knowledge
                WHERE employee_id = %s::uuid
                ORDER BY last_contribution_at DESC NULLS LAST
                """,
                (employee_id,),
            )
            return [dict(r) for r in cur.fetchall()]
    except Exception:
        return []


def _fetch_consent_audit_log(employee_id: str, conn) -> list[dict]:
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT changed_at, changed_by, previous_value, new_value, reason
                FROM consent_audit_log
                WHERE employee_id = %s::uuid
                ORDER BY changed_at DESC
                """,
                (employee_id,),
            )
            return [dict(r) for r in cur.fetchall()]
    except Exception:
        return []


# ─── Consent management ───────────────────────────────────────────────────────


def update_consent(
    employee_id: str,
    new_value: bool,
    changed_by: str,
    reason: str | None,
    conn,
) -> dict[str, Any] | None:
    """Update employee consent and write an audit log entry.

    Returns the updated employee record, or None if the employee doesn't exist.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT consent FROM employees WHERE id = %s::uuid FOR UPDATE",
            (employee_id,),
        )
        row = cur.fetchone()
    if row is None:
        return None

    previous = bool(row["consent"] if hasattr(row, "__getitem__") else row[0])

    with conn.cursor() as cur:
        cur.execute(
            "UPDATE employees SET consent = %s WHERE id = %s::uuid",
            (new_value, employee_id),
        )
        cur.execute(
            """
            INSERT INTO consent_audit_log
                (employee_id, changed_by, previous_value, new_value, reason)
            VALUES (%s::uuid, %s, %s, %s, %s)
            """,
            (employee_id, changed_by, previous, new_value, reason),
        )
    conn.commit()

    return {
        "employee_id":    employee_id,
        "previous_value": previous,
        "new_value":      new_value,
        "changed_by":     changed_by,
        "reason":         reason,
        "changed_at":     datetime.now(timezone.utc).isoformat(),
    }


# ─── Quarterly HTML report ────────────────────────────────────────────────────


def generate_html_report(conn) -> str:
    """Generate a quarterly compliance HTML report.

    Includes summary statistics, data inventory, retention policy,
    recent purge history, and consent status distribution.
    """
    audit  = build_data_audit(conn)
    purges = _fetch_recent_purges(conn)
    consent_stats = _fetch_consent_stats(conn)
    today  = date.today().isoformat()

    rows_audit = "".join(
        f"<tr><td>{c['table']}</td><td>{c['description']}</td>"
        f"<td>{c['row_count']:,}</td>"
        f"<td>{c['retention_days'] if c['retention_days'] else 'Indefinite'}</td>"
        f"<td>{c['legal_basis']}</td><td>{c['sensitivity']}</td></tr>"
        for c in audit["categories"]
    )

    rows_purge = "".join(
        f"<tr><td>{p['purged_at'][:10]}</td><td>{p['table_name']}</td>"
        f"<td>{p['rows_deleted']:,}</td><td>{p['cutoff_date']}</td>"
        f"<td>{p['triggered_by']}</td><td>{p['status']}</td></tr>"
        for p in purges
    ) or "<tr><td colspan='6'>No purge history available.</td></tr>"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Org Synapse — Quarterly Compliance Report</title>
<style>
  body {{ font-family: 'Segoe UI', Arial, sans-serif; color: #1C1C2E; margin: 0; padding: 0; }}
  .hero {{ background: #003366; color: #fff; padding: 40px 48px; }}
  .hero h1 {{ font-size: 32px; margin: 0 0 8px; font-weight: 300; }}
  .hero p {{ color: rgba(255,255,255,0.6); font-size: 14px; margin: 0; }}
  .content {{ max-width: 1100px; margin: 0 auto; padding: 40px 48px; }}
  h2 {{ color: #003366; font-size: 18px; margin-top: 40px; }}
  table {{ width: 100%; border-collapse: collapse; margin-top: 12px; font-size: 13px; }}
  thead tr {{ background: #003366; color: #fff; }}
  th {{ padding: 10px 14px; text-align: left; font-size: 10px; text-transform: uppercase; letter-spacing: 2px; }}
  td {{ padding: 10px 14px; border-bottom: 1px solid #E0EAF4; }}
  tr:nth-child(even) td {{ background: #F4F6F9; }}
  .kpi {{ display: flex; gap: 24px; margin-top: 20px; }}
  .kpi-box {{ background: #F4F6F9; border-radius: 8px; padding: 16px 24px; flex: 1; }}
  .kpi-box .num {{ font-size: 28px; font-weight: 600; color: #003366; }}
  .kpi-box .lbl {{ font-size: 11px; color: #6B7280; text-transform: uppercase; letter-spacing: 2px; }}
  .badge {{ display: inline-block; border-radius: 12px; padding: 2px 10px; font-size: 10px; font-weight: 600; text-transform: uppercase; }}
  .badge-high {{ background: #FDEAEA; color: #7A1020; }}
  .badge-medium {{ background: #FEF0E6; color: #7A3800; }}
  .badge-low {{ background: #E0F7EF; color: #0D5C3A; }}
  footer {{ background: #F4F6F9; padding: 24px 48px; font-size: 11px; color: #6B7280; text-transform: uppercase; letter-spacing: 2px; margin-top: 60px; }}
</style>
</head>
<body>
<div class="hero">
  <div style="font-size:9px;letter-spacing:4px;text-transform:uppercase;color:rgba(255,255,255,0.4);margin-bottom:12px">Org Synapse · Compliance</div>
  <h1>Quarterly Compliance Report</h1>
  <p>Generated: {audit['generated_at'][:10]} &nbsp;|&nbsp; Frameworks: GDPR · CCPA &nbsp;|&nbsp; Data Controller: {audit['data_controller']}</p>
</div>
<div class="content">
  <div class="kpi">
    <div class="kpi-box">
      <div class="num">{audit['total_personal_rows']:,}</div>
      <div class="lbl">Total Personal Data Rows</div>
    </div>
    <div class="kpi-box">
      <div class="num">{audit['total_tables']}</div>
      <div class="lbl">Tables Containing Personal Data</div>
    </div>
    <div class="kpi-box">
      <div class="num">{consent_stats.get('opted_in', 0)}</div>
      <div class="lbl">Employees with Consent</div>
    </div>
    <div class="kpi-box">
      <div class="num">{consent_stats.get('opted_out', 0)}</div>
      <div class="lbl">Employees Opted Out</div>
    </div>
  </div>

  <h2>Data Inventory</h2>
  <table>
    <thead><tr><th>Table</th><th>Description</th><th>Rows</th><th>Retention (days)</th><th>Legal Basis</th><th>Sensitivity</th></tr></thead>
    <tbody>{rows_audit}</tbody>
  </table>

  <h2>Retention Purge History (last 90 days)</h2>
  <table>
    <thead><tr><th>Date</th><th>Table</th><th>Rows Deleted</th><th>Cutoff Date</th><th>Triggered By</th><th>Status</th></tr></thead>
    <tbody>{rows_purge}</tbody>
  </table>

  <h2>Data Protection Contact</h2>
  <p>DPO: <a href="mailto:{audit['dpo_contact']}">{audit['dpo_contact']}</a><br>
  For data subject rights requests (access, erasure, portability), contact your HR administrator.</p>
</div>
<footer>Org Synapse · Compliance Report · {today} · OPB AI Mastery Lab · For authorized use only</footer>
</body>
</html>"""
    return html


def _fetch_recent_purges(conn) -> list[dict]:
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT purged_at, table_name, rows_deleted, cutoff_date, triggered_by, status
                FROM data_retention_purges
                WHERE purged_at >= NOW() - INTERVAL '90 days'
                ORDER BY purged_at DESC
                LIMIT 100
                """
            )
            return [dict(r) for r in cur.fetchall()]
    except Exception:
        return []


def _fetch_consent_stats(conn) -> dict[str, int]:
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    COUNT(*) FILTER (WHERE consent = true)  AS opted_in,
                    COUNT(*) FILTER (WHERE consent = false) AS opted_out
                FROM employees
                WHERE active = true
                """
            )
            row = cur.fetchone()
        return {
            "opted_in":  int(row["opted_in"]  if row else 0),
            "opted_out": int(row["opted_out"] if row else 0),
        }
    except Exception:
        return {"opted_in": 0, "opted_out": 0}
