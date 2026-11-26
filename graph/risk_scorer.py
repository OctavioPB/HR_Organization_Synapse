"""SPOF risk scoring: computes Single-Point-of-Failure scores per employee.

Formula (MATH_MODEL.md §5; v3.4 weights fitted to measured removal damage):
    SPOF(v) = α × R(BC(v), V)                (undirected betweenness, graph.metrics)
            + β × R(CDR(v), V)               (cross-department ratio)
            + γ × R((1 − CC(v)), V)
            − δ × R_signed(entropy_trend(v), V)

    α, β, γ, δ = 0.50, 0.325, 0.05, 0.125   (SPOF_ALPHA … SPOF_DELTA)

Each metric is rank-percentile transformed over the population V before the
linear combination (MODEL.md §5.1), so a weight controls its nominal share
rather than letting the most skewed metric dominate.

Why these weights (Nov 2026).  The v2 weights (0.4, 0.3, 0.2, 0.1) were
theoretically ordered but never fitted.  Against *measured* removal damage —
every employee removed in turn on the public SNAP email-Eu-core network (986
people) and on the synthetic validation org — a least-squares fit gave
betweenness ≈0.8, CDR ≈0.13 and (1 − clustering) ≈ −0.06.  A constrained grid
search (575 weight sets, step 0.025) kept only those passing every synthetic
planted-archetype gate — all bridges critical, precision@critical ≥ 80 %, sole
experts < 0.7, silo members < 0.5 — and ranked them by damage agreement on both
datasets.  The chosen set sits in the best region (α ≈ 0.5, β ≈ 0.3, small γ,
δ ≈ 0.125) with the widest gate margins (silo max 0.47, sole-expert max 0.61).
It raises Spearman(SPOF, damage) from 0.81 to 0.85 on the real network and from
0.36 to 0.57 on the synthetic org.  A small γ is still needed: tightly clustered
silo members must not look like bridges.  δ is a departure-likelihood signal,
not a damage signal, and cannot be fitted on static data.

Uncertainty (MATH_MODEL §5.2).  Rank on a 30-day window is noisy: on real e-mail,
resampling a window's events keeps only ≈77 % of the top-20 brokers.  So
P(critical) comes mainly from an EVENT BOOTSTRAP — the graph is rebuilt from
resampled events SPOF_UNCERTAINTY_SAMPLES times — with each replicate also
drawing weights from a Dirichlet centred on the central weights.  An employee
is ``robust_critical`` when the central score is ≥ 0.7 AND P(critical) ≥ 0.9;
``weight_sensitive`` (flag ``critical_uncertain``) when the central score is
≥ 0.7 but P(critical) < 0.9.  ``score_lo`` / ``score_hi`` are the 10th / 90th
percentiles of the replicate scores (an 80 % interval).  This replaces the v2
two-corner weight bracket, whose strict α > β > γ > δ ordering no longer holds
for fitted weights (γ < δ), and which ignored data noise entirely.

Public functions:
    compute_spof_score(betweenness, cross_dept, clustering, entropy_trend, α, β, γ, δ) → float
    score_all(G, betweenness, clustering, entropy_trends, weights) → dict[str, float]
    score_all_with_bands(G, betweenness, clustering, entropy_trends, weights, raw_edges, …) → dict[str, dict]
    write_scores(scores, entropy_trends, snapshot_date, bands, withdrawing) → None

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
    "alpha": float(os.environ.get("SPOF_ALPHA", "0.50")),
    "beta": float(os.environ.get("SPOF_BETA", "0.325")),
    "gamma": float(os.environ.get("SPOF_GAMMA", "0.05")),
    "delta": float(os.environ.get("SPOF_DELTA", "0.125")),
}

# Score at/above which an employee is flagged critical (MODEL.md §5.5).
_CRITICAL_THRESHOLD = 0.7
# Uncertainty: replicates (event bootstrap + Dirichlet weight jitter), the
# Dirichlet concentration (higher = weights vary less), and the probability of
# being critical required for `robust_critical`.
SPOF_UNCERTAINTY_SAMPLES = int(os.environ.get("SPOF_UNCERTAINTY_SAMPLES", "30"))
SPOF_WEIGHT_CONCENTRATION = float(os.environ.get("SPOF_WEIGHT_CONCENTRATION", "200"))
SPOF_ROBUST_PROBABILITY = float(os.environ.get("SPOF_ROBUST_PROBABILITY", "0.9"))
_WEIGHT_KEYS = ("alpha", "beta", "gamma", "delta")


def _sample_weights(rng, central: dict[str, float], concentration: float) -> dict[str, float]:
    """Dirichlet draw centred on the central weights; zero weights stay zero."""

    keys = [k for k in _WEIGHT_KEYS if central.get(k, 0.0) > 0]
    total = sum(central[k] for k in keys) or 1.0
    draw = rng.dirichlet([concentration * central[k] / total for k in keys])
    out = dict.fromkeys(_WEIGHT_KEYS, 0.0)
    for k, v in zip(keys, draw, strict=True):
        out[k] = float(v) * total
    return out


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
    raw_edges: list | None = None,
    n_samples: int | None = None,
    seed: int = 42,
) -> dict[str, dict]:
    """Central SPOF score plus its uncertainty for every employee.

    Each component is rank-percentile transformed across the population
    (MODEL.md §5.1).  Uncertainty comes from *n_samples* replicates: when
    *raw_edges* (the rows the graph was built from) are given, each replicate
    rebuilds the graph from events resampled with replacement and recomputes
    betweenness and CDR — the dominant source of uncertainty; every replicate
    also draws weights from a Dirichlet centred on the central weights.  Without
    *raw_edges* only the weight jitter applies.

    Returns:
        employee_id → {
            "score":            central SPOF ∈ [0, 1],
            "score_lo":         10th percentile of replicate scores,
            "score_hi":         90th percentile of replicate scores,
            "p_critical":       share of replicates with score ≥ 0.7,
            "robust_critical":  central ≥ 0.7 and p_critical ≥ SPOF_ROBUST_PROBABILITY,
            "weight_sensitive": central ≥ 0.7 but not robust (flag critical_uncertain),
        }
    """
    import numpy as np

    central = weights or _DEFAULT_WEIGHTS
    trends = entropy_trends or {}
    nodes = list(G.nodes())
    n_samples = SPOF_UNCERTAINTY_SAMPLES if n_samples is None else n_samples
    use_clustering = central.get("gamma", 0.0) > 0

    def _components(graph: nx.DiGraph, bc: dict[str, float], cl: dict[str, float] | None):
        cdr = compute_cross_dept_ratio(graph)
        r_bc = _percent_rank({n: bc.get(n, 0.0) for n in nodes})
        r_cdr = _percent_rank({n: cdr.get(n, 0.0) for n in nodes})
        r_cc = _percent_rank({n: 1.0 - (cl or {}).get(n, 0.0) for n in nodes}) if cl is not None else {}
        return r_bc, r_cdr, r_cc

    # R_signed: most-negative (withdrawing) trend → 0, most-positive → 1.
    r_ent = _percent_rank({n: trends.get(n, 0.0) for n in nodes})

    def _scores(comp, w: dict[str, float]) -> np.ndarray:
        r_bc, r_cdr, r_cc = comp
        return np.array(
            [
                max(
                    0.0,
                    min(
                        1.0,
                        w["alpha"] * r_bc.get(n, 0.0)
                        + w["beta"] * r_cdr.get(n, 0.0)
                        + w["gamma"] * r_cc.get(n, 0.0)
                        - w["delta"] * r_ent.get(n, 0.0),
                    ),
                )
                for n in nodes
            ]
        )

    base_comp = _components(G, betweenness, clustering if use_clustering else None)
    central_scores = _scores(base_comp, central)

    rng = np.random.default_rng(seed)
    reps = []
    for _ in range(max(0, n_samples)):
        if raw_edges:
            idx = rng.integers(0, len(raw_edges), len(raw_edges))
            Gb = build_graph([raw_edges[i] for i in idx])
            comp = _components(Gb, compute_betweenness(Gb), compute_clustering(Gb) if use_clustering else None)
        else:
            comp = base_comp
        reps.append(_scores(comp, _sample_weights(rng, central, SPOF_WEIGHT_CONCENTRATION)))
    rep = np.vstack(reps) if reps else central_scores[None, :]

    lo = np.percentile(rep, 10, axis=0)
    hi = np.percentile(rep, 90, axis=0)
    p_crit = (rep >= _CRITICAL_THRESHOLD).mean(axis=0)

    out: dict[str, dict] = {}
    for i, node in enumerate(nodes):
        s_c = float(central_scores[i])
        crosses = s_c >= _CRITICAL_THRESHOLD
        robust = crosses and float(p_crit[i]) >= SPOF_ROBUST_PROBABILITY
        out[node] = {
            "score": s_c,
            "score_lo": float(lo[i]),
            "score_hi": float(hi[i]),
            "p_critical": float(p_crit[i]),
            "robust_critical": robust,
            "weight_sensitive": crosses and not robust,
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
    withdrawing: set[str] | None = None,
) -> None:
    """Persist SPOF scores (and weight-sensitivity bands) to risk_scores.

    ``withdrawing`` is the set of employees whose entropy trend is confidently
    below the withdrawal threshold (EntropySignal.withdrawing).  When omitted,
    the fallback is ``trend < ENTROPY_SLOPE_THRESHOLD`` — previously any
    negative slope counted, which on raw slopes flagged about half the org.

    Flag tiers (MODEL.md §5.5):
        ≥ 0.7 and robust under weight perturbation → critical
        ≥ 0.7 but weight-sensitive                  → critical_uncertain
        0.5 – 0.7                                   → warning
        0.4 – 0.5                                   → elevated
        < 0.4 and confidently withdrawing           → withdrawing
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
    if withdrawing is None:
        from ml.features.feature_extractor import ENTROPY_SLOPE_THRESHOLD

        withdrawing = {e for e, t in entropy_trends.items() if t < ENTROPY_SLOPE_THRESHOLD}

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
        if emp_id in withdrawing:
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
