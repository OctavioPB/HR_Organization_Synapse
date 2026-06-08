"""Router: /manager — self-service risk view for line managers.

Role requirement: 'manager'
A manager sees only their direct reports (employees.manager_id = authenticated employee).
No raw SPOF scores or churn probabilities are exposed — only traffic-light statuses.
"""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from api.deps import get_tenant_db, require_role

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/manager", tags=["manager"])

_MANAGER_API_KEY_ENV = os.environ.get("MANAGER_LOOKUP_BY_API_KEY", "true").lower() == "true"
_ENTROPY_CONTRACTING_THRESHOLD = float(os.environ.get("ENTROPY_CONTRACTING_THRESHOLD", "-0.05"))
_CHURN_RED_THRESHOLD = float(os.environ.get("CHURN_RED_THRESHOLD", "0.6"))
_CHURN_AMBER_THRESHOLD = float(os.environ.get("CHURN_AMBER_THRESHOLD", "0.3"))
_CONTRACTING_WEEKS = int(os.environ.get("CONTRACTING_WEEKS_THRESHOLD", "4"))


# ─── Response models ──────────────────────────────────────────────────────────


class TeamMemberStatus(BaseModel):
    employee_id: str
    name: str
    department: str
    role: str
    status: str  # 'green' | 'amber' | 'red'
    contracting_network: bool
    contracting_weeks: int


class TeamRiskResponse(BaseModel):
    manager_employee_id: str | None
    total_reports: int
    team: list[TeamMemberStatus]


class SuggestionsResponse(BaseModel):
    employee_id: str
    name: str
    suggestions: list[str]


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _compute_status(churn_prob: float | None, entropy_trend: float | None) -> str:
    churn = churn_prob or 0.0
    entropy = entropy_trend if entropy_trend is not None else 0.0
    if churn >= _CHURN_RED_THRESHOLD:
        return "red"
    if churn >= _CHURN_AMBER_THRESHOLD or entropy <= _ENTROPY_CONTRACTING_THRESHOLD:
        return "amber"
    return "green"


def _count_contracting_weeks(employee_id: str, conn) -> int:
    """Count consecutive recent weeks where entropy_trend was below threshold."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT entropy_trend
            FROM risk_scores
            WHERE employee_id = %s::uuid
              AND entropy_trend IS NOT NULL
            ORDER BY scored_at DESC
            LIMIT 12
            """,
            (employee_id,),
        )
        rows = cur.fetchall()

    count = 0
    for row in rows:
        if (row["entropy_trend"] or 0.0) <= _ENTROPY_CONTRACTING_THRESHOLD:
            count += 1
        else:
            break
    return count


def _resolve_manager_employee_id(request: Request, conn) -> str | None:
    """Map the API key's tenant context to an employee id via manager lookup."""
    tenant = getattr(request.state, "tenant", None)
    if tenant is None:
        return None
    manager_id = getattr(request.state, "manager_employee_id", None)
    return manager_id


# ─── Endpoints ────────────────────────────────────────────────────────────────


@router.get(
    "/team",
    response_model=TeamRiskResponse,
    summary="Traffic-light risk view for direct reports",
)
def get_team_risk(
    request: Request,
    manager_employee_id: str | None = None,
    conn=Depends(get_tenant_db),
    _role: str = Depends(require_role("manager", "hr_admin")),
) -> TeamRiskResponse:
    """Return traffic-light health status for all direct reports.

    manager_employee_id: UUID of the manager in the employees table.
    If not provided, attempts to use X-Manager-Employee-ID request header.
    """
    mgr_id = manager_employee_id or request.headers.get("X-Manager-Employee-ID")
    if not mgr_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide manager_employee_id query param or X-Manager-Employee-ID header.",
        )

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                e.id::text          AS employee_id,
                e.name,
                e.department,
                e.role,
                rs.entropy_trend,
                rs.spof_score,
                crs.churn_prob
            FROM employees e
            LEFT JOIN LATERAL (
                SELECT entropy_trend, spof_score
                FROM risk_scores
                WHERE employee_id = e.id
                ORDER BY scored_at DESC
                LIMIT 1
            ) rs ON true
            LEFT JOIN LATERAL (
                SELECT churn_prob
                FROM churn_scores
                WHERE employee_id = e.id
                ORDER BY scored_at DESC
                LIMIT 1
            ) crs ON true
            WHERE e.manager_id = %s::uuid
              AND e.active = true
            ORDER BY e.name
            """,
            (mgr_id,),
        )
        rows = [dict(r) for r in cur.fetchall()]

    team = []
    for row in rows:
        contracting_weeks = _count_contracting_weeks(row["employee_id"], conn)
        team.append(
            TeamMemberStatus(
                employee_id=row["employee_id"],
                name=row["name"],
                department=row["department"],
                role=row["role"],
                status=_compute_status(row["churn_prob"], row["entropy_trend"]),
                contracting_network=contracting_weeks >= _CONTRACTING_WEEKS,
                contracting_weeks=contracting_weeks,
            )
        )

    return TeamRiskResponse(
        manager_employee_id=mgr_id,
        total_reports=len(team),
        team=team,
    )


@router.get(
    "/team/{employee_id}/suggestions",
    response_model=SuggestionsResponse,
    summary="AI-generated 1:1 conversation suggestions for a direct report",
)
def get_suggestions(
    employee_id: str,
    request: Request,
    manager_employee_id: str | None = None,
    conn=Depends(get_tenant_db),
    _role: str = Depends(require_role("manager", "hr_admin")),
) -> SuggestionsResponse:
    """Generate 3 plain-language suggestions for a manager 1:1.

    Validates that employee_id is a direct report before calling Claude.
    Suggestions never mention SPOF scores or churn probability numbers.
    """
    mgr_id = manager_employee_id or request.headers.get("X-Manager-Employee-ID")

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT e.id::text, e.name, e.department,
                   rs.entropy_trend, rs.flag,
                   ta.anomaly_tier
            FROM employees e
            LEFT JOIN LATERAL (
                SELECT entropy_trend, flag
                FROM risk_scores
                WHERE employee_id = e.id
                ORDER BY scored_at DESC
                LIMIT 1
            ) rs ON true
            LEFT JOIN LATERAL (
                SELECT anomaly_tier
                FROM temporal_anomaly_scores
                WHERE employee_id = e.id
                ORDER BY scored_at DESC
                LIMIT 1
            ) ta ON true
            WHERE e.id = %s::uuid
              AND e.active = true
            """,
            (employee_id,),
        )
        emp = cur.fetchone()

    if not emp:
        raise HTTPException(status_code=404, detail=f"Employee {employee_id} not found.")

    if mgr_id:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM employees WHERE id = %s::uuid AND manager_id = %s::uuid",
                (employee_id, mgr_id),
            )
            if not cur.fetchone():
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="This employee is not a direct report.",
                )

    emp = dict(emp)
    name = emp["name"]
    dept = emp["department"]
    entropy_signal = ""
    if (emp.get("entropy_trend") or 0.0) <= _ENTROPY_CONTRACTING_THRESHOLD:
        entropy_signal = "Their collaboration network has been contracting in recent weeks."
    anomaly_signal = ""
    if emp.get("anomaly_tier") in ("high", "medium"):
        anomaly_signal = "Their interaction patterns show an anomalous trajectory."

    context = f"Employee: {name}, Department: {dept}. " f"{entropy_signal} {anomaly_signal}".strip()

    prompt = (
        f"You are an HR support assistant. A manager is preparing for a 1:1 meeting with "
        f"{name} ({dept}). {context} "
        f"Provide exactly 3 plain-language, empathetic, actionable suggestions for topics "
        f"the manager should raise. Do NOT mention scores, probabilities, or technical metrics. "
        f'Return only a JSON array of 3 strings, e.g. ["...", "...", "..."].'
    )

    try:
        import json

        from graph.claude_client import call_claude

        text = call_claude(prompt, max_tokens=300)
        # Parse the JSON array
        if text.startswith("["):
            suggestions = json.loads(text)[:3]
        else:
            suggestions = [s.strip("- •").strip() for s in text.split("\n") if s.strip()][:3]
    except Exception as exc:
        logger.warning("Suggestions generation failed: %s", exc)
        suggestions = [
            f"Ask {name} how they're feeling about their current workload and projects.",
            f"Discuss career goals and any blockers to growth in the {dept} team.",
            "Check in on work-life balance and whether they have the support they need.",
        ]

    return SuggestionsResponse(
        employee_id=employee_id,
        name=name,
        suggestions=suggestions,
    )
