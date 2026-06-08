"""SPOF risk scoring: computes Single-Point-of-Failure scores per employee.

Formula (MODEL.md §5.1, v2 — rank-percentile normalized):
    SPOF(v) = α × R(BC_norm(v), V)
            + β × R(CDR(v), V)
            + γ × R((1 − CC(v)), V)
            − δ × R_signed(entropy_trend(v), V)

CORRECTION (v2): each metric is passed through a rank-percentile transform
R(·, V) over the population V *before* the linear combination.  Raw values have
wildly different empirical variances (betweenness is heavily right-skewed,
cross-dept ratio near-uniform, entropy trend a near-zero slope), so combining
raw values lets the highest-variance term dominate regardless of its nominal
weight.  Rank-percentile mapping forces each term into [0, 1] uniformly so that
α = 0.4 really contributes 40% of the score variance.  R_signed maps the most
*negative* (withdrawing) entropy trend to percentile 0 and the most positive
(engaging) to 1, so the `− δ × R_signed` term subtracts little from withdrawing
employees (keeps their risk high) and more from engaging ones.

Weight sensitivity (MODEL.md §5.3): the nominal weights are theoretically
ordered (α > β > γ > δ) but not empirically calibrated, so each employee is also
scored under a ±perturbation bracket.  An employee is `robust_critical` only if
their score crosses the critical threshold under ALL weight sets; one that
crosses only under the central weights is `weight_sensitive` and must get
qualitative review before any personnel action.

Weights are configurable via environment variables (SPOF_ALPHA, _BETA, _GAMMA,
_DELTA); the perturbation magnitude via SPOF_PERTURB_RANGE.

Public functions:
    compute_spof_score(betweenness, cross_dept, clustering, entropy_trend, α, β, γ, δ) → float
    score_all(G, betweenness, clustering, entropy_trends, weights) → dict[str, float]
    score_all_with_bands(G, betweenness, clustering, entropy_trends, weights) → dict[str, dict]
    write_scores(scores, entropy_trends, snapshot_date, bands) → None

CLI:
    python graph/risk_scorer.py --snapshot-date 2025-04-25
"""

import argparse
import bisect
import logging
import os
import sys
import uuid
from datetime import date
from pathlib import Path

import networkx as nx
import psycopg2.extras

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from graph.builder import build_graph, load_raw_edges
from graph.metrics import (
    compute_betweenness,
    compute_clustering,
    compute_cross_dept_ratio,
)
from ingestion.db import get_conn

logger = logging.getLogger(__name__)

_DEFAULT_WEIGHTS = {
    "alpha": float(os.environ.get("SPOF_ALPHA", "0.4")),
    "beta": float(os.environ.get("SPOF_BETA", "0.3")),
    "gamma": float(os.environ.get("SPOF_GAMMA", "0.2")),
    "delta": float(os.environ.get("SPOF_DELTA", "0.1")),
}

# Score at/above which an employee is flagged critical (MODEL.md §5.5).
_CRITICAL_THRESHOLD = 0.7

# Weight perturbation for the robustness bracket (MODEL.md §5.3).  The two
# corner weight-sets bracket a ±SPOF_PERTURB_RANGE move in weight space while
# preserving the ordering constraint α > β > γ > δ.  They are derived from the
# central weights so they continue to track SPOF_ALPHA..SPOF_DELTA overrides.
_PERTURB_RANGE = float(os.environ.get("SPOF_PERTURB_RANGE", "0.15"))


def _perturbed_weights(direction: int) -> dict[str, float]:
    """Return a perturbed weight set (direction +1 = 'hi', -1 = 'lo').

    'hi' leans harder on the structural primary signal (betweenness), 'lo'
    spreads weight toward the cross-dept / clustering terms and up-weights the
    noisy entropy term.  Both are renormalised to sum to 1.0 so the score stays
    in the same range as the central formula.
    """
    a = _DEFAULT_WEIGHTS["alpha"] + direction * _PERTURB_RANGE * _DEFAULT_WEIGHTS["alpha"]
    b = _DEFAULT_WEIGHTS["beta"]
    g = _DEFAULT_WEIGHTS["gamma"] - direction * _PERTURB_RANGE * _DEFAULT_WEIGHTS["gamma"]
    d = _DEFAULT_WEIGHTS["delta"] - direction * _PERTURB_RANGE * _DEFAULT_WEIGHTS["delta"]
    total = a + b + g + d
    return {"alpha": a / total, "beta": b / total, "gamma": g / total, "delta": d / total}


_WEIGHTS_LO = _perturbed_weights(-1)
_WEIGHTS_HI = _perturbed_weights(+1)


def compute_spof_score(
    betweenness: float,
    cross_dept_ratio: float,
    clustering: float,
    entropy_trend: float,
    alpha: float,
    beta: float,
    gamma: float,
    delta: float,
) -> float:
    """Compute a single employee's SPOF score.

    Args:
        betweenness: Normalised betweenness centrality ∈ [0, 1].
        cross_dept_ratio: Fraction of edges crossing department boundaries ∈ [0, 1].
        clustering: Clustering coefficient ∈ [0, 1].
        entropy_trend: Linear slope of interaction entropy over 30 days.
                       Negative = withdrawing (increases SPOF score via -δ×trend).
        alpha: Weight for betweenness.
        beta: Weight for cross-dept connectivity.
        gamma: Weight for (1 - clustering).
        delta: Weight for entropy trend signal.

    Returns:
        SPOF score ∈ [0, 1] (approximately).
    """
    return (
        alpha * betweenness
        + beta * cross_dept_ratio
        + gamma * (1.0 - clustering)
        - delta * entropy_trend  # negative trend → positive contribution
    )


def _percent_rank(values: dict[str, float]) -> dict[str, float]:
    """Map each value to its rank percentile ∈ [0, 1] within the population.

    Implements PERCENT_RANK semantics (MODEL.md §5.1): the minimum value maps to
    0.0, the maximum to 1.0, and ties receive the fraction of the population that
    is *strictly* below them.  This makes each metric contribute uniformly to
    the score variance regardless of its raw distribution shape.

    Returns all-zeros when the population has 0 or 1 members (no spread to rank).
    """
    if len(values) <= 1:
        return {k: 0.0 for k in values}
    ordered = sorted(values.values())
    n = len(ordered)
    return {k: bisect.bisect_left(ordered, v) / (n - 1) for k, v in values.items()}


def score_all_with_bands(
    G: nx.DiGraph,
    betweenness: dict[str, float],
    clustering: dict[str, float],
    entropy_trends: dict[str, float] | None = None,
    weights: dict[str, float] | None = None,
) -> dict[str, dict]:
    """Compute SPOF scores plus the weight-sensitivity bracket per employee.

    Each of the four components is rank-percentile transformed across the whole
    population (MODEL.md §5.1) before the linear combination, so the betweenness
    term no longer dominates purely because it is the highest-variance metric.

    For every employee the central score and the lo/hi perturbed scores are
    computed, and the employee is marked ``robust_critical`` only if the score
    crosses the critical threshold under *all three* weight sets (MODEL.md §5.3).

    Args:
        G: Directed collaboration graph with 'department' node attributes.
        betweenness: Per-employee betweenness centrality (raw NetworkX values).
        clustering: Per-employee clustering coefficient.
        entropy_trends: Per-employee entropy trend (defaults to 0.0 if absent).
        weights: Central alpha/beta/gamma/delta dict (defaults to env-var weights).

    Returns:
        Dict mapping employee_id → {
            "score": float,            central SPOF ∈ [0, 1],
            "score_lo": float,         score under the lo-weight perturbation,
            "score_hi": float,         score under the hi-weight perturbation,
            "robust_critical": bool,   critical under all three weight sets,
            "weight_sensitive": bool,  critical centrally but not robustly,
        }
    """
    central = weights or _DEFAULT_WEIGHTS
    trends = entropy_trends or {}
    cross_dept = compute_cross_dept_ratio(G)

    nodes = list(G.nodes())

    # Rank-percentile transform each component across the population V.
    r_bc = _percent_rank({n: betweenness.get(n, 0.0) for n in nodes})
    r_cdr = _percent_rank({n: cross_dept.get(n, 0.0) for n in nodes})
    r_inv_cc = _percent_rank({n: 1.0 - clustering.get(n, 0.0) for n in nodes})
    # R_signed: most-negative (withdrawing) trend → 0, most-positive → 1.
    r_ent = _percent_rank({n: trends.get(n, 0.0) for n in nodes})

    def _score(node: str, w: dict[str, float]) -> float:
        raw = (
            w["alpha"] * r_bc.get(node, 0.0)
            + w["beta"] * r_cdr.get(node, 0.0)
            + w["gamma"] * r_inv_cc.get(node, 0.0)
            - w["delta"] * r_ent.get(node, 0.0)
        )
        return max(0.0, min(1.0, raw))

    out: dict[str, dict] = {}
    for node in nodes:
        s = _score(node, central)
        s_lo = _score(node, _WEIGHTS_LO)
        s_hi = _score(node, _WEIGHTS_HI)
        crosses_central = s >= _CRITICAL_THRESHOLD
        robust = crosses_central and s_lo >= _CRITICAL_THRESHOLD and s_hi >= _CRITICAL_THRESHOLD
        out[node] = {
            "score": s,
            "score_lo": s_lo,
            "score_hi": s_hi,
            "robust_critical": robust,
            "weight_sensitive": crosses_central and not robust,
        }
    return out


def score_all(
    G: nx.DiGraph,
    betweenness: dict[str, float],
    clustering: dict[str, float],
    entropy_trends: dict[str, float] | None = None,
    weights: dict[str, float] | None = None,
) -> dict[str, float]:
    """Compute central SPOF scores for all employees in the graph.

    Thin wrapper over :func:`score_all_with_bands` that returns only the central
    score per employee, preserving the original ``dict[str, float]`` API.

    Args:
        G: Directed collaboration graph with 'department' node attributes.
        betweenness: Per-employee betweenness centrality (raw NetworkX values).
        clustering: Per-employee clustering coefficient.
        entropy_trends: Per-employee entropy trend (defaults to 0.0 if absent).
        weights: Alpha/beta/gamma/delta dict (defaults to env-var weights).

    Returns:
        Dict mapping employee_id → SPOF score ∈ [0, 1].
    """
    return {
        node: detail["score"]
        for node, detail in score_all_with_bands(G, betweenness, clustering, entropy_trends, weights).items()
    }


def write_scores(
    scores: dict[str, float],
    entropy_trends: dict[str, float],
    snapshot_date: date,
    bands: dict[str, dict] | None = None,
) -> None:
    """Persist SPOF scores (and weight-sensitivity bands) to risk_scores.

    Flag tiers (MODEL.md §5.5):
        ≥ 0.7 and robust under weight perturbation → critical
        ≥ 0.7 but weight-sensitive                  → critical_uncertain
        0.5 – 0.7                                   → warning
        0.4 – 0.5                                   → elevated
        < 0.4 and trend < 0                         → withdrawing
        otherwise                                   → normal

    ``critical_uncertain`` employees crossed the threshold only under the
    central weights; they require qualitative investigation before succession
    planning is initiated, rather than an automatic critical alert.

    When ``bands`` is supplied the lo/hi perturbed scores are persisted and the
    robust/weight-sensitive split is honoured.  Without it (legacy callers) every
    ≥0.7 employee is conservatively flagged ``critical``.

    Args:
        scores: Central SPOF score per employee_id.
        entropy_trends: Entropy slope per employee_id (may be empty).
        snapshot_date: Date label for this scoring run.
        bands: Optional per-employee detail from :func:`score_all_with_bands`.
    """
    bands = bands or {}

    def _flag(emp_id: str, score: float) -> str:
        if score >= 0.7:
            detail = bands.get(emp_id)
            if detail is not None and not detail["robust_critical"]:
                return "critical_uncertain"
            return "critical"
        if score >= 0.5:
            return "warning"
        if score >= 0.4:
            return "elevated"
        if entropy_trends.get(emp_id, 0.0) < 0:
            return "withdrawing"
        return "normal"

    rows = [
        (
            str(uuid.uuid4()),
            snapshot_date,
            emp_id,
            round(score, 6),
            round(entropy_trends.get(emp_id, 0.0), 6),
            _flag(emp_id, score),
            round(bands.get(emp_id, {}).get("score_lo", score), 6),
            round(bands.get(emp_id, {}).get("score_hi", score), 6),
            bool(bands.get(emp_id, {}).get("robust_critical", score >= 0.7)),
        )
        for emp_id, score in scores.items()
    ]

    with get_conn() as conn:
        with conn.cursor() as cur:
            psycopg2.extras.execute_batch(
                cur,
                """
                INSERT INTO risk_scores
                    (id, scored_at, employee_id, spof_score, entropy_trend, flag,
                     spof_score_lo, spof_score_hi, weight_robust)
                VALUES (%s, %s, %s::uuid, %s, %s, %s, %s, %s, %s)
                """,
                rows,
                page_size=500,
            )

    logger.info(
        "Wrote %d risk scores for %s | critical=%d critical_uncertain=%d warning=%d elevated=%d",
        len(rows),
        snapshot_date,
        sum(1 for e, s in scores.items() if s >= 0.7 and bands.get(e, {}).get("robust_critical", True)),
        sum(1 for e, s in scores.items() if s >= 0.7 and not bands.get(e, {}).get("robust_critical", True)),
        sum(1 for _, s in scores.items() if 0.5 <= s < 0.7),
        sum(1 for _, s in scores.items() if 0.4 <= s < 0.5),
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="Compute SPOF risk scores and write to risk_scores table.")
    parser.add_argument("--snapshot-date", type=date.fromisoformat, required=True)
    parser.add_argument(
        "--window-days",
        type=int,
        default=int(os.environ.get("GRAPH_WINDOW_DAYS", "30")),
    )
    args = parser.parse_args()

    raw_edges = load_raw_edges(args.snapshot_date, args.window_days)
    G = build_graph(raw_edges)

    if G.number_of_nodes() == 0:
        logger.warning("Graph is empty — no scores to compute.")
        return

    betweenness = compute_betweenness(G)
    clustering = compute_clustering(G)
    bands = score_all_with_bands(G, betweenness, clustering)
    scores = {node: detail["score"] for node, detail in bands.items()}

    top5 = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:5]
    logger.info("Top-5 SPOF scores:")
    for emp_id, score in top5:
        tag = (
            "robust"
            if bands[emp_id]["robust_critical"]
            else ("weight-sensitive" if bands[emp_id]["weight_sensitive"] else "")
        )
        logger.info("  %s… %.4f %s", emp_id[:8], score, tag)

    write_scores(scores, {}, args.snapshot_date, bands=bands)
    logger.info("Done for %s.", args.snapshot_date)


if __name__ == "__main__":
    main()
