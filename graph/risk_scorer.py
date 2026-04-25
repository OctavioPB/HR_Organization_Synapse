"""SPOF risk scoring: computes Single-Point-of-Failure scores per employee.

Formula:
    SPOF = α × betweenness
         + β × cross_dept_ratio
         + γ × (1 − clustering)
         − δ × entropy_trend      (negative trend = withdrawing → increases SPOF)

All component metrics are normalised to [0, 1]. Weights sum to 1.0.
Weights are configurable via environment variables (SPOF_ALPHA, _BETA, _GAMMA, _DELTA).

Public functions:
    compute_spof_score(betweenness, cross_dept, clustering, entropy_trend, α, β, γ, δ) → float
    score_all(G, betweenness, clustering, entropy_trends, weights) → dict[str, float]
    write_scores(scores, entropy_trends, snapshot_date) → None

CLI:
    python graph/risk_scorer.py --snapshot-date 2025-04-25
"""

import argparse
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
    "beta":  float(os.environ.get("SPOF_BETA",  "0.3")),
    "gamma": float(os.environ.get("SPOF_GAMMA", "0.2")),
    "delta": float(os.environ.get("SPOF_DELTA", "0.1")),
}


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


def score_all(
    G: nx.DiGraph,
    betweenness: dict[str, float],
    clustering: dict[str, float],
    entropy_trends: dict[str, float] | None = None,
    weights: dict[str, float] | None = None,
) -> dict[str, float]:
    """Compute SPOF scores for all employees in the graph.

    Args:
        G: Directed collaboration graph with 'department' node attributes.
        betweenness: Per-employee betweenness centrality.
        clustering: Per-employee clustering coefficient.
        entropy_trends: Per-employee entropy trend (defaults to 0.0 if absent).
        weights: Alpha/beta/gamma/delta dict (defaults to env-var weights).

    Returns:
        Dict mapping employee_id → SPOF score.
    """
    w = weights or _DEFAULT_WEIGHTS
    trends = entropy_trends or {}
    cross_dept = compute_cross_dept_ratio(G)

    scores: dict[str, float] = {}
    for node in G.nodes():
        scores[node] = compute_spof_score(
            betweenness=betweenness.get(node, 0.0),
            cross_dept_ratio=cross_dept.get(node, 0.0),
            clustering=clustering.get(node, 0.0),
            entropy_trend=trends.get(node, 0.0),
            alpha=w["alpha"],
            beta=w["beta"],
            gamma=w["gamma"],
            delta=w["delta"],
        )
    return scores


def write_scores(
    scores: dict[str, float],
    entropy_trends: dict[str, float],
    snapshot_date: date,
) -> None:
    """Persist SPOF scores to the risk_scores table.

    Determines flag level per score:
        ≥ 0.7 → critical
        ≥ 0.5 → warning
        < 0.5 and trend < 0 → withdrawing
        otherwise → normal

    Args:
        scores: SPOF score per employee_id.
        entropy_trends: Entropy slope per employee_id (may be empty).
        snapshot_date: Date label for this scoring run.
    """
    def _flag(emp_id: str, score: float) -> str:
        if score >= 0.7:
            return "critical"
        if score >= 0.5:
            return "warning"
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
        )
        for emp_id, score in scores.items()
    ]

    with get_conn() as conn:
        with conn.cursor() as cur:
            psycopg2.extras.execute_batch(
                cur,
                """
                INSERT INTO risk_scores
                    (id, scored_at, employee_id, spof_score, entropy_trend, flag)
                VALUES (%s, %s, %s::uuid, %s, %s, %s)
                """,
                rows,
                page_size=500,
            )

    logger.info(
        "Wrote %d risk scores for %s | critical=%d warning=%d withdrawing=%d",
        len(rows), snapshot_date,
        sum(1 for _, s in scores.items() if s >= 0.7),
        sum(1 for _, s in scores.items() if 0.5 <= s < 0.7),
        sum(1 for emp, _ in scores.items() if entropy_trends.get(emp, 0.0) < 0),
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    parser = argparse.ArgumentParser(
        description="Compute SPOF risk scores and write to risk_scores table."
    )
    parser.add_argument("--snapshot-date", type=date.fromisoformat, required=True)
    parser.add_argument(
        "--window-days", type=int,
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
    scores = score_all(G, betweenness, clustering)

    top5 = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:5]
    logger.info("Top-5 SPOF scores:")
    for emp_id, score in top5:
        logger.info("  %s… %.4f", emp_id[:8], score)

    write_scores(scores, {}, args.snapshot_date)
    logger.info("Done for %s.", args.snapshot_date)


if __name__ == "__main__":
    main()
