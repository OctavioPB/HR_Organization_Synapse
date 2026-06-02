"""Compliance & Regulatory Reporting endpoints (F8).

Endpoints:
    GET  /compliance/data-audit            — full data inventory catalogue
    GET  /compliance/data-export/{id}      — GDPR Article 20 employee data package
    PATCH /compliance/consent/{id}         — update employee consent + write audit log
    POST /compliance/purge                 — trigger data retention purge (admin-guarded)
    GET  /compliance/purge-history         — recent purge log entries
    GET  /compliance/report                — quarterly HTML compliance report
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse

from api.db import fetch_purge_history
from api.deps import get_admin_db, get_db
from api.models.schemas import (
    ConsentUpdateRequest,
    ConsentUpdateResponse,
    DataAuditReport,
    EmployeeDataExport,
    PurgeHistoryResponse,
    RetentionPurgeResponse,
)

router = APIRouter(prefix="/compliance", tags=["compliance"])


# ─── Data audit ───────────────────────────────────────────────────────────────


@router.get(
    "/data-audit",
    response_model=DataAuditReport,
    summary="Data inventory catalogue (GDPR/CCPA)",
)
def data_audit(conn=Depends(get_db)):
    """Return the full catalogue of personal data stored by the system.

    Includes per-table row counts, retention policy, legal basis, and
    data sensitivity classification.
    """
    from graph.compliance import build_data_audit

    result = build_data_audit(conn)
    return result


# ─── GDPR Article 20 data export ──────────────────────────────────────────────


@router.get(
    "/data-export/{employee_id}",
    response_model=EmployeeDataExport,
    summary="GDPR Article 20 — employee data portability export",
)
def employee_data_export(employee_id: str, conn=Depends(get_db)):
    """Return a complete personal data package for one employee.

    Includes raw_events (as sender and recipient), graph_snapshots,
    risk scores, churn scores, knowledge entries, and consent audit log.
    """
    from graph.compliance import export_employee_data

    package = export_employee_data(employee_id, conn)
    if package is None:
        raise HTTPException(status_code=404, detail=f"Employee {employee_id} not found")
    return package


# ─── Consent management ───────────────────────────────────────────────────────


@router.patch(
    "/consent/{employee_id}",
    response_model=ConsentUpdateResponse,
    summary="Update employee consent and record audit log entry",
)
def update_employee_consent(
    employee_id: str,
    body: ConsentUpdateRequest,
    conn=Depends(get_db),
):
    """Set `consent = true/false` for the given employee.

    When consent is revoked (`false`), the employee is excluded from all
    future graph computations. The change is recorded in `consent_audit_log`
    for GDPR accountability.
    """
    from graph.compliance import update_consent

    result = update_consent(
        employee_id=employee_id,
        new_value=body.consent,
        changed_by=body.changed_by,
        reason=body.reason,
        conn=conn,
    )
    if result is None:
        raise HTTPException(status_code=404, detail=f"Employee {employee_id} not found")
    return result


# ─── Data retention purge (admin-guarded) ─────────────────────────────────────


@router.post(
    "/purge",
    response_model=RetentionPurgeResponse,
    status_code=202,
    summary="Trigger data retention purge (admin only)",
)
def trigger_retention_purge(conn=Depends(get_admin_db)):
    """Delete rows that exceed the retention policy.

    - `raw_events`: rows older than 90 days (by `ts`)
    - `graph_snapshots`: rows older than 365 days (by `snapshot_date`)

    Each purge run is recorded in `data_retention_purges` for audit.
    Requires `X-Admin-Key` header.
    """
    from graph.compliance import run_retention_purge

    results = run_retention_purge(conn, triggered_by="api")
    total   = sum(r["rows_deleted"] for r in results)
    return {
        "triggered_at":     datetime.now(timezone.utc).isoformat(),
        "results":          results,
        "total_rows_deleted": total,
    }


# ─── Purge history ────────────────────────────────────────────────────────────


@router.get(
    "/purge-history",
    response_model=PurgeHistoryResponse,
    summary="Recent data retention purge history",
)
def purge_history(limit: int = 50, conn=Depends(get_db)):
    """Return recent purge run entries, most-recent first."""
    if limit < 1 or limit > 500:
        raise HTTPException(status_code=422, detail="limit must be between 1 and 500")
    entries = fetch_purge_history(limit, conn)
    return {"total": len(entries), "entries": entries}


# ─── Quarterly HTML compliance report ─────────────────────────────────────────


@router.get(
    "/report",
    response_class=HTMLResponse,
    summary="Quarterly compliance HTML report",
)
async def compliance_report(conn=Depends(get_db)):
    """Generate and return a quarterly compliance HTML report.

    Includes data inventory, retention policy, consent statistics,
    and recent purge history. Suitable for download or rendering in-browser.
    """
    from graph.compliance import generate_html_report

    try:
        html = await asyncio.to_thread(generate_html_report, conn)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Report generation failed: {exc}") from exc
    return HTMLResponse(content=html)


@router.get(
    "/departure/{employee_id}",
    summary="Departure impact report for one employee",
)
def get_departure_impact_report(employee_id: str, conn=Depends(get_db)) -> dict:
    """Return the full departure impact report for a given employee.

    Includes predicted scores, actual structural impact, and AI narrative.
    Requires hr_admin role in production.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                dr.id::text,
                dr.employee_id::text,
                e.name,
                e.department,
                dr.departure_date,
                dr.generated_at,
                dr.impact_json,
                dr.narrative_text,
                dr.status
            FROM departure_impact_reports dr
            JOIN employees e ON e.id = dr.employee_id
            WHERE dr.employee_id = %s::uuid
            ORDER BY dr.generated_at DESC
            LIMIT 1
            """,
            (employee_id,),
        )
        row = cur.fetchone()

    if not row:
        raise HTTPException(
            status_code=404,
            detail=f"No departure impact report found for employee {employee_id}.",
        )

    row = dict(row)
    return {
        "report_id":      row["id"],
        "employee_id":    row["employee_id"],
        "name":           row["name"],
        "department":     row["department"],
        "departure_date": str(row["departure_date"]),
        "generated_at":   str(row["generated_at"]),
        "impact_json":    row["impact_json"],
        "narrative_text": row["narrative_text"],
        "status":         row["status"],
    }
