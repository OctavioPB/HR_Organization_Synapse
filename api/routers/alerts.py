"""Router: /alerts — silo alerts, entropy/withdrawal signals, and alert history."""

import logging
from fastapi import APIRouter, Depends, HTTPException, Query

from api import db as queries
from api.deps import get_db
from api.models.schemas import AlertItem, AlertsResponse, SiloMember, SiloMembersResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.get("/silos", response_model=AlertsResponse)
def get_silo_alerts(conn=Depends(get_db)) -> AlertsResponse:
    """Active (unresolved) silo community alerts.

    A silo alert fires when a community's isolation_ratio exceeds SILO_THRESHOLD.
    Returns up to 100 most recent unresolved silo alerts.
    """
    rows = queries.fetch_silo_alerts(conn)
    alerts = [AlertItem(**r) for r in rows]
    return AlertsResponse(total=len(alerts), alerts=alerts)


@router.get("/entropy", response_model=AlertsResponse)
def get_entropy_alerts(conn=Depends(get_db)) -> AlertsResponse:
    """Active alerts for withdrawing employees and connectivity anomalies.

    Includes alerts of type: withdrawing, connectivity_anomaly, spof_critical.
    Returns up to 200 most recent unresolved alerts.
    """
    rows = queries.fetch_entropy_alerts(conn)
    alerts = [AlertItem(**r) for r in rows]
    return AlertsResponse(total=len(alerts), alerts=alerts)


@router.get("/silos/{alert_id}/members", response_model=SiloMembersResponse)
def get_silo_members(alert_id: str, conn=Depends(get_db)) -> SiloMembersResponse:
    """Return every employee belonging to a specific silo alert.

    Joins member_ids stored in affected_entities with current employee records,
    graph metrics, and risk scores, sorted by SPOF score descending.
    """
    # Fetch the alert header separately so we can build the response envelope
    rows_alert = queries.fetch_silo_alerts(conn)
    alert = next((a for a in rows_alert if a["id"] == alert_id), None)
    if alert is None:
        raise HTTPException(status_code=404, detail="Silo alert not found")

    entities = alert.get("affected_entities") or {}
    department = (entities.get("departments") or ["Unknown"])[0]
    isolation_ratio = float(entities.get("isolation_ratio", 0.0))

    member_rows = queries.fetch_silo_members(alert_id, conn)
    members = [SiloMember(**r) for r in member_rows]

    return SiloMembersResponse(
        alert_id=alert_id,
        department=department,
        isolation_ratio=isolation_ratio,
        member_count=len(members),
        members=members,
    )


@router.get("/history", response_model=AlertsResponse)
def get_alert_history(
    days: int = Query(default=30, ge=1, le=365),
    conn=Depends(get_db),
) -> AlertsResponse:
    """All alerts fired in the last N days (resolved and unresolved).

    Returns up to 1000 alerts ordered by fired_at descending.
    """
    rows = queries.fetch_alert_history(days, conn)
    alerts = [AlertItem(**r) for r in rows]
    logger.info("GET /alerts/history days=%d total=%d", days, len(alerts))
    return AlertsResponse(total=len(alerts), alerts=alerts)
