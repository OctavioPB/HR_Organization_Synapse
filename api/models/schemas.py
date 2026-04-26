"""Pydantic response and request models for the Org Synapse API.

NOTE: Employee names are included in responses for the development build.
Sprint 7 will add RBAC filtering so 'executive' and 'analyst' roles
receive anonymized data without names.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field


# ─── Graph ────────────────────────────────────────────────────────────────────


class NodeMetrics(BaseModel):
    employee_id: str
    name: str
    department: str
    betweenness: float = 0.0
    degree_in: float = 0.0
    degree_out: float = 0.0
    clustering: float = 0.0
    community_id: int | None = None


class GraphEdge(BaseModel):
    source: str
    target: str
    weight: float


class GraphSnapshot(BaseModel):
    snapshot_date: date
    node_count: int
    edge_count: int
    nodes: list[NodeMetrics]
    edges: list[GraphEdge]


class Community(BaseModel):
    community_id: int
    member_count: int
    members: list[str]
    departments: list[str]
    is_silo: bool


class CommunitiesResponse(BaseModel):
    snapshot_date: date
    community_count: int
    communities: list[Community]


class EgoNetwork(BaseModel):
    employee_id: str
    snapshot_date: date
    node: NodeMetrics
    neighbors: list[NodeMetrics]
    edges: list[GraphEdge]


# ─── Risk ─────────────────────────────────────────────────────────────────────


class RiskScore(BaseModel):
    employee_id: str
    name: str
    department: str
    spof_score: float
    entropy_trend: float | None = None
    flag: str | None = None
    scored_at: date


class RiskScoresResponse(BaseModel):
    snapshot_date: date
    total: int
    scores: list[RiskScore]


class EmployeeRiskPoint(BaseModel):
    scored_at: date
    spof_score: float
    entropy_trend: float | None = None
    flag: str | None = None


class EmployeeRiskHistory(BaseModel):
    employee_id: str
    days: int
    history: list[EmployeeRiskPoint]


class SimulateRequest(BaseModel):
    remove_employee_id: str = Field(..., description="UUID of the employee to remove")
    window_days: int = Field(default=30, ge=1, le=365)


class GraphHealthStats(BaseModel):
    node_count: int
    edge_count: int
    avg_betweenness: float
    max_betweenness: float
    weakly_connected_components: int


class SimulateResponse(BaseModel):
    removed_employee_id: str
    before: GraphHealthStats
    after: GraphHealthStats
    impact: dict[str, Any]


# ─── Alerts ───────────────────────────────────────────────────────────────────


class AlertItem(BaseModel):
    id: str
    fired_at: datetime
    type: str
    severity: str
    affected_entities: Any
    details: str | None = None
    resolved: bool = False
    resolved_at: datetime | None = None


class AlertsResponse(BaseModel):
    total: int
    alerts: list[AlertItem]
