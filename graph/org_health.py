"""Org Health Score computation (F9).

Synthesizes structural graph signals into a single 0–100 executive score.
Every component is an ABSOLUTE property of the network, comparable across weeks
and across organisations (MATH_MODEL.md §11):

    spof_risk    brokerage concentration — share of all betweenness held by the
                 top ⌈HEALTH_SPOF_TOP_FRACTION·N⌉ employees, rescaled so an even
                 spread → 0 and total concentration in that group → 1
    silo_risk    size-weighted mean of max(H_D, 0) over departments, where H_D is
                 Coleman's homophily index (graph/silo_detector.py): 0 = people
                 mix at random across departments, 1 = every department closed
    entropy_risk share of employees whose interaction-entropy slope is below
                 ENTROPY_SLOPE_THRESHOLD (withdrawing)
    frag_risk    1 − exp(−λ · (WCC − 1)), convex so the first split is severe

    composite_risk = w_silo·silo + w_spof·spof + w_entropy·entropy + w_frag·frag
    health_score   = 100 · (1 − composite_risk)

History (why this changed): spof_risk used to be the mean SPOF score.  SPOF is
built from population rank-percentiles, whose mean is ≈0.5 for every
organisation by construction, so mean SPOF ≈ (α+β+γ−δ)/2 ≈ 0.4 always — on the
demo data it moved 0.427–0.433 over eight weeks while carrying 35 % of the
score.  silo_risk counted alerts against a department-count denominator and sat
at 1.0; entropy_risk averaged slopes so a few strong withdrawers averaged away.

Weights and calibration constants are environment-configurable.
"""

from __future__ import annotations

import logging
import math
import os
from collections.abc import Iterable
from datetime import date
from typing import Any

logger = logging.getLogger(__name__)

# ─── Weight / calibration constants ───────────────────────────────────────────

_W_SILO = float(os.environ.get("HEALTH_W_SILO", "0.20"))
_W_SPOF = float(os.environ.get("HEALTH_W_SPOF", "0.35"))
_W_ENTROPY = float(os.environ.get("HEALTH_W_ENTROPY", "0.20"))
_W_FRAG = float(os.environ.get("HEALTH_W_FRAG", "0.25"))

# Convex fragmentation penalty steepness (MODEL.md §12.2): λ=1.5 ⇒ WCC=2 → ≈0.78.
_FRAG_LAMBDA = float(os.environ.get("FRAGMENTATION_LAMBDA", "1.5"))
# Fraction of the org treated as "the top" when measuring brokerage concentration.
_SPOF_TOP_FRACTION = float(os.environ.get("HEALTH_SPOF_TOP_FRACTION", "0.05"))
# Weekly entropy slope below which an employee counts as withdrawing (MODEL.md §14).
_ENTROPY_SLOPE_THRESHOLD = float(os.environ.get("ENTROPY_SLOPE_THRESHOLD", "-0.05"))


# ─── Tier thresholds (score, tier) — evaluated top-down ───────────────────────

_TIERS: list[tuple[float, str]] = [
    (80.0, "healthy"),
    (60.0, "caution"),
    (40.0, "at_risk"),
    (0.0, "critical"),
]


def score_tier(score: float) -> str:
    """Map a numeric health score to its qualitative tier string."""
    for threshold, tier in _TIERS:
        if score >= threshold:
            return tier
    return "critical"


# ─── Pure component functions ─────────────────────────────────────────────────


def _clip01(x: float) -> float:
    return max(0.0, min(1.0, x))


def brokerage_concentration(betweenness: Iterable[float], top_fraction: float = _SPOF_TOP_FRACTION) -> float:
    """How much of the org's brokerage sits with its top few people, in [0, 1].

        k   = max(1, ⌈top_fraction · N⌉)
        S_k = Σ top-k betweenness / Σ betweenness
        C   = (S_k − k/N) / (1 − k/N)

    C = 0 when brokerage is spread evenly (the top k hold exactly their k/N
    share); C = 1 when the top k carry all of it.  Unlike mean SPOF this is not
    rank-based, so it moves when dependence on a few people grows or shrinks.
    Returns 0.0 when there is no brokerage at all (empty or edgeless graph).
    """
    values = sorted((max(0.0, float(b)) for b in betweenness), reverse=True)
    n = len(values)
    total = sum(values)
    if n < 2 or total <= 0.0:
        return 0.0
    k = max(1, math.ceil(top_fraction * n))
    if k >= n:
        return 0.0
    share = sum(values[:k]) / total
    baseline = k / n
    return _clip01((share - baseline) / (1.0 - baseline))


def silo_exposure(departments: Iterable[tuple[int, float | None]]) -> float:
    """Size-weighted mean positive homophily across departments, in [0, 1].

    Args:
        departments: (member_count, homophily_index) per department; a None
            index (undefined) is skipped.  Negative indices (outward-looking
            departments) count as 0 — they are not a silo risk, and letting them
            cancel out a closed department elsewhere would hide it.
    """
    num = 0.0
    den = 0
    for n, h in departments:
        if h is None or n <= 0:
            continue
        num += n * _clip01(h)
        den += n
    return num / den if den else 0.0


def withdrawal_share(entropy_trends: Iterable[float | None], threshold: float = _ENTROPY_SLOPE_THRESHOLD) -> float:
    """Fraction of employees with an entropy slope below *threshold* (MODEL.md §12)."""
    trends = [t for t in entropy_trends if t is not None]
    if not trends:
        return 0.0
    return sum(1 for t in trends if t < threshold) / len(trends)


def fragmentation_risk(wcc_count: int, lam: float = _FRAG_LAMBDA) -> float:
    """Convex fragmentation penalty 1 − exp(−λ·(WCC − 1)) (MODEL.md §12.2)."""
    return 1.0 - math.exp(-lam * max(0, wcc_count - 1))


# ─── Pure scoring function ────────────────────────────────────────────────────


def compute_org_health(
    *,
    spof_risk: float,
    silo_risk: float,
    entropy_risk: float,
    wcc_count: int,
    node_count: int,
    silo_count: int = 0,
    avg_spof_score: float = 0.0,
    avg_entropy_trend: float | None = None,
) -> dict[str, Any]:
    """Combine pre-computed components into the health score.  Pure, no I/O.

    Args:
        spof_risk: brokerage_concentration(...) ∈ [0, 1].
        silo_risk: silo_exposure(...) ∈ [0, 1].
        entropy_risk: withdrawal_share(...) ∈ [0, 1].
        wcc_count: Weakly-connected components (fragmentation input).
        node_count: Employees in the snapshot (reported).
        silo_count: Departments currently flagged as silos (reported).
        avg_spof_score: Mean SPOF score (reported for continuity; no longer scored).
        avg_entropy_trend: Mean entropy slope (reported; no longer scored).

    Returns:
        Dict with score, tier, component_scores (the four risks) and reporting fields.
    """
    components = {
        "silo": _clip01(silo_risk),
        "spof": _clip01(spof_risk),
        "entropy": _clip01(entropy_risk),
        "frag": _clip01(fragmentation_risk(wcc_count)),
    }
    composite = (
        _W_SILO * components["silo"]
        + _W_SPOF * components["spof"]
        + _W_ENTROPY * components["entropy"]
        + _W_FRAG * components["frag"]
    )
    score = round(max(0.0, min(100.0, (1.0 - composite) * 100)), 1)
    return {
        "score": score,
        "tier": score_tier(score),
        "silo_count": silo_count,
        "avg_spof_score": round(avg_spof_score, 4),
        "avg_entropy_trend": (round(avg_entropy_trend, 6) if avg_entropy_trend is not None else None),
        "wcc_count": wcc_count,
        "node_count": node_count,
        "component_scores": {k: round(v, 4) for k, v in components.items()},
    }


# ─── DB-backed computation ────────────────────────────────────────────────────


def compute_and_persist(snapshot_date: date, conn) -> dict[str, Any]:
    """Compute and persist the health score for *snapshot_date*.

    Uses *conn* for risk/alert queries.  Opens its own connection for raw
    edges via load_raw_edges (same pattern as graph_builder_dag).
    Idempotent — ON CONFLICT (computed_at) DO UPDATE.
    """
    from api import db as queries
    from graph.builder import build_graph, load_raw_edges

    # 1. Per-employee SPOF + entropy slope from the day's risk scores
    with conn.cursor() as cur:
        cur.execute(
            "SELECT spof_score, entropy_trend FROM risk_scores WHERE scored_at::date = %s",
            (snapshot_date,),
        )
        rows = cur.fetchall()
    spof_scores = [float(r["spof_score"]) for r in rows if r["spof_score"] is not None]
    entropy_trends = [float(r["entropy_trend"]) for r in rows if r["entropy_trend"] is not None]

    # 2. Collaboration graph for the same window the scorers used
    import networkx as nx

    raw_edges = load_raw_edges(snapshot_date, window_days=int(os.environ.get("GRAPH_WINDOW_DAYS", "30")))
    G = build_graph(raw_edges) if raw_edges else nx.DiGraph()

    # 3. Score
    health = org_health_from_graph(
        G,
        entropy_trends=entropy_trends,
        avg_spof_score=(sum(spof_scores) / len(spof_scores)) if spof_scores else 0.0,
    )
    health["computed_at"] = snapshot_date

    # 4. Persist
    queries.persist_org_health(health, conn)
    conn.commit()

    logger.info(
        "Org health for %s: %.1f (%s)  components=%s silos=%d wcc=%d n=%d",
        snapshot_date,
        health["score"],
        health["tier"],
        health["component_scores"],
        health["silo_count"],
        health["wcc_count"],
        health["node_count"],
    )
    return health


def org_health_from_graph(
    G,
    entropy_trends: Iterable[float | None] = (),
    avg_spof_score: float = 0.0,
) -> dict[str, Any]:
    """Derive every health component from the collaboration graph.  Pure, no I/O.

    Uses the same betweenness and silo detector as the rest of the pipeline, so
    the health score cannot disagree with the dashboard alerts.
    """
    import networkx as nx

    from graph.metrics import compute_betweenness
    from graph.silo_detector import department_homophily, detect_silos

    trends = list(entropy_trends)
    if G.number_of_nodes() == 0:
        return compute_org_health(
            spof_risk=0.0,
            silo_risk=0.0,
            entropy_risk=withdrawal_share(trends),
            wcc_count=1,
            node_count=0,
            avg_spof_score=avg_spof_score,
            avg_entropy_trend=(sum(trends) / len(trends)) if trends else None,
        )

    dept_stats = department_homophily(G)
    result = compute_org_health(
        spof_risk=brokerage_concentration(compute_betweenness(G).values()),
        silo_risk=silo_exposure((len(s["members"]), s["homophily"]) for s in dept_stats.values()),
        entropy_risk=withdrawal_share(trends),
        wcc_count=nx.number_weakly_connected_components(G),
        node_count=G.number_of_nodes(),
        silo_count=len(detect_silos(G)),
        avg_spof_score=avg_spof_score,
        avg_entropy_trend=(sum(trends) / len(trends)) if trends else None,
    )
    result["department_homophily"] = {
        d: (round(s["homophily"], 4) if s["homophily"] is not None else None) for d, s in dept_stats.items()
    }
    return result


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
    tier = current["tier"]
    prev = trend[-2]["score"] if len(trend) >= 2 else score
    delta = round(score - prev, 1)

    components = current.get("component_scores") or {}
    if isinstance(components, str):
        import json

        components = json.loads(components)

    ranked = sorted(components.items(), key=lambda kv: kv[1], reverse=True)
    top_risks = [{"factor": k, "risk_level": round(float(v), 3)} for k, v in ranked if float(v) > 0]

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
        "trend_direction": ("improving" if delta > 0.5 else "declining" if delta < -0.5 else "stable"),
        "top_risks": top_risks,
        "recommended_actions": recommended_actions,
        "narrative": narrative,
    }


def _recommend_actions(current: dict, components: dict) -> list[str]:
    actions: list[str] = []
    if float(components.get("silo", 0)) > 0.3:
        n = current.get("silo_count", 1)
        actions.append(f"Schedule cross-team syncs to address {n} active communication silo(s).")
    if float(components.get("spof", 0)) > 0.4:
        actions.append("Initiate cross-training programmes for the top 3 single-points-of-failure.")
    if float(components.get("entropy", 0)) > 0.3:
        actions.append("HR 1:1 check-ins with employees showing a declining engagement trend.")
    if float(components.get("frag", 0)) > 0.3:
        actions.append("Review org chart for structurally disconnected sub-groups.")
    if not actions:
        actions.append("No immediate action required — maintain current monitoring cadence.")
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
    score = current["score"]
    tier = current["tier"].replace("_", " ")
    silo_count = current.get("silo_count", 0)
    node_count = current.get("node_count", 0)
    direction = "improved" if delta > 0.5 else "declined" if delta < -0.5 else "held steady"
    risk_names = " and ".join(r["factor"] for r in top_risks[:2]) if top_risks else "no dominant risk factors"
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
    from graph.claude_client import call_claude

    score = current["score"]
    tier = current["tier"]

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

    return call_claude(prompt, max_tokens=300)
