"""Pydantic response and request models for the Org Synapse API.

NOTE: Employee names are included in responses for the development build.
Sprint 7 will add RBAC filtering so 'executive' and 'analyst' roles
receive anonymized data without names.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

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


# ─── Neo4j graph queries ──────────────────────────────────────────────────────


class PathNode(BaseModel):
    employee_id: str
    name: str | None = None
    department: str | None = None


class ShortestPathResponse(BaseModel):
    from_employee_id: str
    to_employee_id: str
    path: list[PathNode]
    hops: int
    source: Literal["neo4j", "networkx"]


class ReachableEmployee(BaseModel):
    employee_id: str
    name: str | None = None
    department: str | None = None
    spof_score: float | None = None


class ReachabilityResponse(BaseModel):
    employee_id: str
    hops: int
    reachable_count: int
    reachable: list[ReachableEmployee]
    source: Literal["neo4j", "networkx"]


class KnowledgeIsland(BaseModel):
    employee_id: str
    name: str | None = None
    department: str | None = None
    connection_count: int


class KnowledgeIslandsResponse(BaseModel):
    total: int
    max_size: int
    islands: list[KnowledgeIsland]
    source: Literal["neo4j", "networkx"]


# ─── Churn risk ───────────────────────────────────────────────────────────────


class ChurnScore(BaseModel):
    employee_id: str
    name: str
    department: str
    churn_prob: float = Field(..., ge=0.0, le=1.0, description="Model output probability [0,1]")
    risk_tier: Literal["high", "medium", "low"]
    model_version: str
    scored_at: date


class ChurnScoresResponse(BaseModel):
    scored_at: date
    total: int
    scores: list[ChurnScore]


class EmployeeChurnDetail(BaseModel):
    employee_id: str
    name: str
    department: str
    latest_churn_prob: float | None = None
    latest_risk_tier: str | None = None
    history: list[ChurnScore]


# ─── Knowledge risk ───────────────────────────────────────────────────────────


class DomainExpertise(BaseModel):
    domain: str
    doc_count: int
    is_sole_expert: bool
    expertise_score: float


class KnowledgeScore(BaseModel):
    employee_id: str
    name: str
    department: str
    knowledge_score: float = Field(..., ge=0.0, le=1.0)
    sole_expert_count: int
    domain_count: int
    doc_count: int
    enhanced_spof_score: float | None = None
    impacted_departments: list[str] = []
    computed_at: date


class KnowledgeScoresResponse(BaseModel):
    computed_at: date
    total: int
    scores: list[KnowledgeScore]


class KnowledgeDomain(BaseModel):
    domain: str
    total_docs: int
    contributor_count: int
    sole_expert_id: str | None = None
    sole_expert_name: str | None = None


class KnowledgeDomainsResponse(BaseModel):
    total: int
    domains: list[KnowledgeDomain]


class EmployeeKnowledgeProfile(BaseModel):
    employee_id: str
    name: str
    department: str
    knowledge_score: float
    sole_expert_count: int
    domain_count: int
    doc_count: int
    enhanced_spof_score: float | None = None
    domains: list[DomainExpertise]
    computed_at: date


class KnowledgeImpactStatement(BaseModel):
    employee_id: str
    name: str
    department: str
    sole_expert_count: int
    domain_count: int
    knowledge_score: float
    enhanced_spof_score: float | None = None
    sole_expert_domains: list[str]
    impacted_departments: list[str]
    statement: str
    computed_at: str


# ─── Temporal graph analysis ──────────────────────────────────────────────────


class TemporalMetricPoint(BaseModel):
    snapshot_date: date
    betweenness: float = 0.0
    degree_in: float = 0.0
    degree_out: float = 0.0
    clustering: float = 0.0
    community_id: int | None = None


class TemporalFlowResponse(BaseModel):
    employee_id: str
    name: str | None = None
    department: str | None = None
    weeks: int
    series: list[TemporalMetricPoint]


class TemporalAnomalyScore(BaseModel):
    employee_id: str
    name: str
    department: str
    anomaly_score: float = Field(..., ge=0.0, le=1.0)
    anomaly_tier: Literal["high", "medium", "low"]
    reconstruction_error: float
    trend_slope: float = Field(..., description="Positive = worsening, negative = recovering")
    model_version: str
    scored_at: date


class TemporalAnomalyResponse(BaseModel):
    scored_at: date
    total: int
    scores: list[TemporalAnomalyScore]


# ─── Succession planning ──────────────────────────────────────────────────────


class SuccessionCandidate(BaseModel):
    candidate_employee_id: str
    name: str
    department: str
    compatibility_score: float = Field(..., ge=0.0, le=1.0)
    structural_overlap: float = Field(..., ge=0.0, le=1.0)
    clustering_score: float = Field(..., ge=0.0, le=1.0)
    domain_overlap: float = Field(..., ge=0.0, le=1.0)
    rank: int


class SuccessionRecommendation(BaseModel):
    source_employee_id: str
    source_name: str
    source_department: str
    spof_score: float
    computed_at: date
    candidates: list[SuccessionCandidate]


class SuccessionResponse(BaseModel):
    computed_at: date
    total: int
    recommendations: list[SuccessionRecommendation]


# ─── Multi-tenant (F6) ───────────────────────────────────────────────────────


class TenantCreateRequest(BaseModel):
    slug: str = Field(..., min_length=3, max_length=63,
                      pattern=r"^[a-z0-9][a-z0-9_-]{1,61}[a-z0-9]$")
    name: str = Field(..., min_length=2, max_length=200)
    plan: Literal["free", "starter", "pro", "enterprise"] = "free"


class TenantCreateResponse(BaseModel):
    tenant_id: str
    slug: str
    name: str
    plan: str
    schema_name: str
    raw_api_key: str   # shown once — not stored


class TenantDetail(BaseModel):
    id: str
    slug: str
    name: str
    plan: str
    schema_name: str
    active: bool
    stripe_customer_id: str | None = None
    created_at: datetime


class TenantListResponse(BaseModel):
    total: int
    tenants: list[TenantDetail]


class TenantApiKeyResponse(BaseModel):
    key_id: str
    tenant_id: str
    name: str
    raw_api_key: str   # shown once — not stored
    created_at: datetime


class UsageMonth(BaseModel):
    month: date
    event_count: int
    reported_to_stripe: bool = False


class BillingUsageResponse(BaseModel):
    tenant_id: str
    plan: str
    current_month_events: int
    plan_limit: int | None = None
    usage_pct: float | None = None
    history: list[UsageMonth]


# ─── Org Health Score (F9) ───────────────────────────────────────────────────


class OrgHealthComponentScores(BaseModel):
    silo: float = Field(..., ge=0.0, le=1.0)
    spof: float = Field(..., ge=0.0, le=1.0)
    entropy: float = Field(..., ge=0.0, le=1.0)
    frag: float = Field(..., ge=0.0, le=1.0)


class OrgHealthScore(BaseModel):
    computed_at: date
    score: float = Field(..., ge=0.0, le=100.0)
    tier: Literal["healthy", "caution", "at_risk", "critical"]
    silo_count: int
    avg_spof_score: float
    avg_entropy_trend: float | None = None
    wcc_count: int
    node_count: int
    component_scores: OrgHealthComponentScores


class OrgHealthTrendPoint(BaseModel):
    computed_at: date
    score: float
    tier: Literal["healthy", "caution", "at_risk", "critical"]
    silo_count: int
    avg_spof_score: float


class OrgHealthTrend(BaseModel):
    weeks: int
    points: list[OrgHealthTrendPoint]


class RiskFactor(BaseModel):
    factor: str
    risk_level: float = Field(..., ge=0.0, le=1.0)


class OrgHealthBriefing(BaseModel):
    computed_at: str
    score: float
    tier: Literal["healthy", "caution", "at_risk", "critical"]
    trend_delta: float
    trend_direction: Literal["improving", "declining", "stable"]
    top_risks: list[RiskFactor]
    recommended_actions: list[str]
    narrative: str


# ─── Compliance (F8) ─────────────────────────────────────────────────────────


class DataCategory(BaseModel):
    table: str
    description: str
    personal_data: bool
    sensitivity: Literal["low", "medium", "high"]
    retention_days: int | None
    legal_basis: str
    fields: list[str]
    excludes_content: bool
    row_count: int
    cutoff_date: str | None = None


class DataAuditReport(BaseModel):
    generated_at: str
    framework: list[str]
    data_controller: str
    dpo_contact: str
    categories: list[DataCategory]
    total_tables: int
    total_personal_rows: int


class RetentionPurgeResult(BaseModel):
    table: str
    rows_deleted: int
    cutoff_date: str
    status: Literal["completed", "failed", "partial"]
    error: str | None = None


class RetentionPurgeResponse(BaseModel):
    triggered_at: str
    results: list[RetentionPurgeResult]
    total_rows_deleted: int


class PurgeHistoryEntry(BaseModel):
    purged_at: datetime
    table_name: str
    rows_deleted: int
    cutoff_date: date
    triggered_by: str
    status: str


class PurgeHistoryResponse(BaseModel):
    total: int
    entries: list[PurgeHistoryEntry]


class ConsentUpdateRequest(BaseModel):
    consent: bool
    changed_by: str = Field(..., min_length=2, max_length=100)
    reason: str | None = None


class ConsentUpdateResponse(BaseModel):
    employee_id: str
    previous_value: bool
    new_value: bool
    changed_by: str
    reason: str | None = None
    changed_at: str


class EmployeeDataExport(BaseModel):
    export_generated_at: str
    article: str
    employee_id: str
    employee: dict[str, Any]
    raw_events: list[dict[str, Any]]
    graph_snapshots: list[dict[str, Any]]
    risk_scores: list[dict[str, Any]]
    churn_scores: list[dict[str, Any]]
    knowledge_entries: list[dict[str, Any]]
    consent_audit_log: list[dict[str, Any]]


# ─── NL Query (F7) ───────────────────────────────────────────────────────────


class NLQueryRequest(BaseModel):
    question: str = Field(..., min_length=3, max_length=2000)


class ToolCall(BaseModel):
    name: str
    input: dict[str, Any]
    result_summary: str


class NLQueryResponse(BaseModel):
    answer: str
    tools_used: list[ToolCall]
    model: str
    turns: int
    latency_ms: int


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
