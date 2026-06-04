"""Org Health Score computation (F9).

Synthesizes all graph metrics into a single 0–100 executive-facing score.

Formula (each component normalised to [0, 1]):
    composite_risk = (
        w_silo    × silo_risk        # active silos vs reference baseline
        + w_spof  × spof_risk        # mean SPOF score across employees
        + w_entropy × entropy_risk   # negative entropy trend (withdrawal signal)
        + w_frag  × frag_risk        # isolated components / total nodes
    )
    health_score = 100 × (1 − composite_risk)

Weights and calibration constants are configurable via environment variables
so ops teams can tune sensitivity without a code deploy.
"""

from __future__ import annotations

import logging
import math
import os
from datetime import date
from typing import Any

logger = logging.getLogger(__name__)

# ─── Weight / calibration constants ───────────────────────────────────────────

_W_SILO    = float(os.environ.get("HEALTH_W_SILO",       "0.20"))
_W_SPOF    = float(os.environ.get("HEALTH_W_SPOF",       "0.35"))
_W_ENTROPY = float(os.environ.get("HEALTH_W_ENTROPY",    "0.20"))
_W_FRAG    = float(os.environ.get("HEALTH_W_FRAG",       "0.25"))

# Silo risk denominator scales with organisation structure (MODEL.md §12.1):
# maximum tolerable silo count ≈ one silo per SILO_THRESHOLD_RATIO departments,
# with a floor of 2.  This replaces the deprecated fixed HEALTH_SILO_REF=10,
# which was organisation-size invariant in the wrong direction.
_SILO_THRESHOLD_RATIO = float(os.environ.get("SILO_THRESHOLD_RATIO", "3.0"))
# Convex fragmentation penalty steepness (MODEL.md §12.2): higher λ punishes the
# first disconnection (WCC 1→2) harder.  λ=1.5 ⇒ WCC=2 maps to risk ≈ 0.78.
_FRAG_LAMBDA = float(os.environ.get("FRAGMENTATION_LAMBDA", "1.5"))
# Entropy slope magnitude that maps to maximum entropy risk
_ENTROPY_SCALE = float(os.environ.get("HEALTH_ENTROPY_SCALE", "0.05"))


def silo_threshold(dept_count: int | None) -> int:
    """Maximum tolerable active-silo count for an org with *dept_count* departments.

    silo_threshold(d) = max(floor(d / SILO_THRESHOLD_RATIO), 2)  (MODEL.md §12.1)

    The floor of 2 keeps the denominator sane for very small organisations.
    When the department count is unknown, falls back to the floor of 2.
    """
    if not dept_count or dept_count <= 0:
        return 2
    return max(int(math.floor(dept_count / _SILO_THRESHOLD_RATIO)), 2)

# ─── Tier thresholds (score, tier) — evaluated top-down ───────────────────────

_TIERS: list[tuple[float, str]] = [
    (80.0, "healthy"),
    (60.0, "caution"),
    (40.0, "at_risk"),
    (0.0,  "critical"),
]


def score_tier(score: float) -> str:
    """Map a numeric health score to its qualitative tier string."""
    for threshold, tier in _TIERS:
        if score >= threshold:
            return tier
    return "critical"


# ─── Pure scoring function ────────────────────────────────────────────────────


def compute_org_health(
    silo_count: int,
    avg_spof_score: float,
    avg_entropy_trend: float | None,
    wcc_count: int,
    node_count: int,
    dept_count: int | None = None,
) -> dict[str, Any]:
    """Return health score dict from pre-aggregated inputs.

    Pure function — no I/O.  All inputs come from the DB layer so this
    function is fully unit-testable without a database.

    Args:
        silo_count: Number of active (unresolved) silo alerts.
        avg_spof_score: Mean SPOF score across all employees in [0, 1].
        avg_entropy_trend: Mean entropy slope (negative = withdrawal). None if unavailable.
        wcc_count: Weakly-connected components in the collaboration graph.
        node_count: Total employees (graph nodes) in the snapshot.
        dept_count: Distinct departments — scales the silo-risk denominator
            (MODEL.md §12.1).  When None the denominator falls back to its floor.

    Returns:
        Dict with keys: score, tier, component_scores, silo_count,
        avg_spof_score, avg_entropy_trend, wcc_count, node_count.
    """
    # Silo risk: denominator scales with org structure rather than a fixed 10
    # (MODEL.md §12.1) — 10 silos in a 50-person org is catastrophic but
    # negligible in a 5,000-person org.
    silo_risk = min(silo_count / silo_threshold(dept_count), 1.0)
    spof_risk = max(0.0, min(1.0, avg_spof_score))

    if avg_entropy_trend is not None:
        # Negative slope means employees are interacting less → risk
        entropy_risk = max(0.0, min(1.0, -avg_entropy_trend / _ENTROPY_SCALE))
    else:
        entropy_risk = 0.0  # no data → neutral assumption

    # Convex (exponential) fragmentation penalty (MODEL.md §12.2): the first
    # disconnection (WCC 1→2) severs the org graph and deserves near-maximum
    # risk immediately, rather than the negligible ~1/n the old linear form gave.
    extra_components = max(0, wcc_count - 1)
    frag_risk = 1.0 - math.exp(-_FRAG_LAMBDA * extra_components)

    composite_risk = (
        _W_SILO    * silo_risk
        + _W_SPOF  * spof_risk
        + _W_ENTROPY * entropy_risk
        + _W_FRAG  * frag_risk
    )

    score = round(max(0.0, min(100.0, (1.0 - composite_risk) * 100)), 1)

    return {
        "score": score,
        "tier": score_tier(score),
        "silo_count": silo_count,
        "avg_spof_score": round(avg_spof_score, 4),
        "avg_entropy_trend": (
            round(avg_entropy_trend, 6) if avg_entropy_trend is not None else None
        ),
        "wcc_count": wcc_count,
        "node_count": node_count,
        "component_scores": {
            "silo":    round(silo_risk,    4),
            "spof":    round(spof_risk,    4),
            "entropy": round(entropy_risk, 4),
            "frag":    round(frag_risk,    4),
        },
    }


# ─── DB-backed computation ────────────────────────────────────────────────────


def compute_and_persist(snapshot_date: date, conn) -> dict[str, Any]:
    """Compute and persist the health score for *snapshot_date*.

    Uses *conn* for risk/alert queries.  Opens its own connection for raw
    edges via load_raw_edges (same pattern as graph_builder_dag).
    Idempotent — ON CONFLICT (computed_at) DO UPDATE.
    """
    import networkx as nx

    from api import db as queries
    from graph.builder import build_graph, load_raw_edges

    # 1. Active silo count
    silos = queries.fetch_silo_alerts(conn)
    silo_count = len(silos)

    # 2. Avg SPOF + entropy from risk_scores on snapshot_date
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                COUNT(*)::int                   AS n,
                COALESCE(AVG(spof_score), 0.0)  AS avg_spof,
                AVG(entropy_trend)              AS avg_entropy
            FROM risk_scores
            WHERE scored_at::date = %s
            """,
            (snapshot_date,),
        )
        row = cur.fetchone()

    node_count = int(row["n"] or 0) if row else 0
    avg_spof   = float(row["avg_spof"] or 0.0) if row else 0.0
    avg_entropy: float | None = (
        float(row["avg_entropy"])
        if row and row["avg_entropy"] is not None
        else None
    )

    # 3. Distinct departments among active employees — scales silo-risk denominator
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(DISTINCT department)::int AS d FROM employees WHERE active = true"
        )
        drow = cur.fetchone()
    dept_count = int(drow["d"] or 0) if drow else 0

    # 4. WCC count from the collaboration graph for this snapshot window
    raw_edges = load_raw_edges(snapshot_date, window_days=30)
    if raw_edges:
        G = build_graph(raw_edges)
        wcc_count = nx.number_weakly_connected_components(G)
        if node_count == 0:
            node_count = G.number_of_nodes()
    else:
        wcc_count = 1

    # 5. Score
    health = compute_org_health(
        silo_count=silo_count,
        avg_spof_score=avg_spof,
        avg_entropy_trend=avg_entropy,
        wcc_count=wcc_count,
        node_count=node_count,
        dept_count=dept_count,
    )
    health["computed_at"] = snapshot_date

    # 6. Persist
    queries.persist_org_health(health, conn)
    conn.commit()

    logger.info(
        "Org health for %s: %.1f (%s)  silo=%d spof=%.3f wcc=%d n=%d",
        snapshot_date, health["score"], health["tier"],
        silo_count, avg_spof, wcc_count, node_count,
    )
    return health


# ─── Briefing generation ──────────────────────────────────────────────────────


def generate_briefing(current: dict, trend: list[dict]) -> dict[str, Any]:
    """Generate the executive briefing for *current* health data.

    Uses Claude if ANTHROPIC_API_KEY is set; falls back to a deterministic
    text template so the endpoint works without credentials (CI, dev, demos).

    Args:
        current: Row dict from fetch_latest_org_health.
        trend: List of weekly score dicts oldest-first (from fetch_org_health_trend).

    Returns:
        Dict: score, tier, trend_delta, trend_direction, top_risks,
              recommended_actions, narrative, computed_at.
    """
    score = current["score"]
    tier  = current["tier"]
    prev  = trend[-2]["score"] if len(trend) >= 2 else score
    delta = round(score - prev, 1)

    components = current.get("component_scores") or {}
    if isinstance(components, str):
        import json
        components = json.loads(components)

    ranked = sorted(components.items(), key=lambda kv: kv[1], reverse=True)
    top_risks = [
        {"factor": k, "risk_level": round(float(v), 3)}
        for k, v in ranked
        if float(v) > 0
    ]

    recommended_actions = _recommend_actions(current, components)
    narrative = _generate_narrative(current, delta, top_risks, recommended_actions)

    computed_at = current.get("computed_at", "")
    if hasattr(computed_at, "isoformat"):
        computed_at = computed_at.isoformat()

    return {
        "computed_at": str(computed_at),
        "score": score,
        "tier": tier,
        "trend_delta": delta,
        "trend_direction": (
            "improving" if delta > 0.5
            else "declining" if delta < -0.5
            else "stable"
        ),
        "top_risks": top_risks,
        "recommended_actions": recommended_actions,
        "narrative": narrative,
    }


def _recommend_actions(current: dict, components: dict) -> list[str]:
    actions: list[str] = []
    if float(components.get("silo", 0)) > 0.3:
        n = current.get("silo_count", 1)
        actions.append(
            f"Schedule cross-team syncs to address {n} active communication silo(s)."
        )
    if float(components.get("spof", 0)) > 0.4:
        actions.append(
            "Initiate cross-training programmes for the top 3 single-points-of-failure."
        )
    if float(components.get("entropy", 0)) > 0.3:
        actions.append(
            "HR 1:1 check-ins with employees showing a declining engagement trend."
        )
    if float(components.get("frag", 0)) > 0.3:
        actions.append(
            "Review org chart for structurally disconnected sub-groups."
        )
    if not actions:
        actions.append(
            "No immediate action required — maintain current monitoring cadence."
        )
    return actions


def _generate_narrative(
    current: dict,
    delta: float,
    top_risks: list[dict],
    actions: list[str],
) -> str:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if api_key:
        try:
            return _claude_narrative(current, delta, top_risks, actions)
        except Exception as exc:
            logger.warning("Claude briefing failed (%s); using template fallback.", exc)
    return _template_narrative(current, delta, top_risks, actions)


def _template_narrative(
    current: dict,
    delta: float,
    top_risks: list[dict],
    actions: list[str],
) -> str:
    score      = current["score"]
    tier       = current["tier"].replace("_", " ")
    silo_count = current.get("silo_count", 0)
    node_count = current.get("node_count", 0)
    direction  = (
        "improved" if delta > 0.5
        else "declined" if delta < -0.5
        else "held steady"
    )
    risk_names = (
        " and ".join(r["factor"] for r in top_risks[:2])
        if top_risks else "no dominant risk factors"
    )
    return (
        f"Organisational health scored {score}/100 this week — tier: {tier}. "
        f"The score {direction} by {abs(delta):.1f} points versus last week. "
        f"Primary risk drivers are {risk_names}, affecting {node_count} tracked employees "
        f"with {silo_count} active communication silo(s). "
        f"Priority recommendation: {actions[0]}"
    )


def _claude_narrative(
    current: dict,
    delta: float,
    top_risks: list[dict],
    actions: list[str],
) -> str:
    import anthropic

    client = anthropic.Anthropic()
    score = current["score"]
    tier  = current["tier"]

    prompt = (
        "Write a 3-sentence executive briefing for an HR intelligence platform.\n\n"
        f"Data this week:\n"
        f"- Org Health Score: {score}/100 (tier: {tier})\n"
        f"- Week-over-week change: {delta:+.1f} points\n"
        f"- Primary risk factors: {[r['factor'] for r in top_risks[:3]]}\n"
        f"- Active silos: {current.get('silo_count', 0)}\n"
        f"- Employees tracked: {current.get('node_count', 0)}\n"
        f"- Recommended priorities: {actions[:2]}\n\n"
        "Style requirements: corporate, data-led, no filler phrases. "
        "Active voice only. No bullet points — write flowing prose. "
        "Do not open with 'The organisation' or 'This week'. "
        "Quantify where possible."
    )

    response = client.messages.create(
        model=os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6"),
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}],
    )
    from anthropic.types import TextBlock
    return next((b.text for b in response.content if isinstance(b, TextBlock)), "").strip()
