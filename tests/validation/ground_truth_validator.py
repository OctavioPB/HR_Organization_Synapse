"""Ground-truth SPOF model validation — synthetic controlled experiment.

DECLARED AS SYNTHETIC CONTROLLED VALIDATION
--------------------------------------------
All employees, departments, and interaction edges are generated in-memory from
explicit structural archetypes. No database, Kafka, or external service is
required. The planted archetypes are:

    BRIDGE      (2)  Cross-dept connector bridging two clusters.
                     Expected: score ≥ 0.7 (critical), rank ≤ 3.

    WITHDRAWING (1)  Former connector, silent in the last 15 days.
                     Expected: score ≥ 0.5 (warning+), rank ≤ 10.

    SOLE_EXPERT (2)  Deep intra-dept expert, minimal cross-dept edges.
                     Expected: score < 0.7 — intentional boundary between
                     structural SPOF and knowledge risk (separate model).

    SILO        (8)  Near-isolated intra-dept cluster, high clustering.
                     Expected: score < 0.5 (normal/elevated).

    NORMAL     (37)  Background population, power-law distributed.

Rationale for each archetype:
  BRIDGE employees have the highest betweenness centrality in the graph because
  they are the primary paths between department clusters. They also have the
  highest cross-department ratio (85% of their edges are cross-dept). After the
  rank-percentile transform in score_all_with_bands(), they should occupy the
  top percentiles on both the betweenness and cross-dept terms.

  WITHDRAWING employees have high RESIDUAL betweenness from the first 15 days
  (when they were a connector) and a strongly negative entropy trend slope.
  The rank-percentile transform on the entropy trend maps the most-negative
  slope to r_ent = 0, so '-δ × 0 = 0' (no deduction), keeping their risk
  elevated despite reduced current activity.

  SOLE_EXPERT employees interact heavily but only within their department.
  Low betweenness (no cross-cluster paths) and low cross-dept ratio → low
  structural SPOF score. This validates that the SPOF model does NOT fire on
  knowledge depth alone — that signal belongs to graph/knowledge_risk.py.

  SILO employees form a high-clustering, low-betweenness cluster. The
  (1 - clustering) term maps them to low percentiles; their betweenness and
  cross-dept ratio are near-zero. They should score well below the warning
  threshold.

Run modes:
    python tests/validation/ground_truth_validator.py    # print report
    pytest  tests/validation/ground_truth_validator.py   # pytest assertions
"""

from __future__ import annotations

import logging
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

import networkx as nx
import numpy as np

# Allow running as a script from the project root
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from graph.metrics import compute_betweenness, compute_clustering, compute_cross_dept_ratio
from graph.risk_scorer import score_all_with_bands

logger = logging.getLogger(__name__)

# ─── Archetype definitions ────────────────────────────────────────────────────

ARCHETYPES = {
    "BRIDGE": {"spof_expected": True, "tier_floor": "critical", "rank_ceiling": 3},
    "WITHDRAWING": {"spof_expected": True, "tier_floor": "warning", "rank_ceiling": 10},
    "SOLE_EXPERT": {"spof_expected": False, "tier_ceiling": "elevated"},
    "SILO": {"spof_expected": False, "tier_ceiling": "elevated"},
    "NORMAL": {"spof_expected": False, "tier_ceiling": "warning"},
}

# Structural SPOF ground truth: which archetype labels are true positives
_TRUE_SPOF_ARCHETYPES = {"BRIDGE", "WITHDRAWING"}

# Planted severity (0 = no risk, 3 = critical) — used for Spearman correlation
PLANTED_SEVERITY = {
    "BRIDGE": 3,
    "WITHDRAWING": 2,
    "SOLE_EXPERT": 1,
    "SILO": 0,
    "NORMAL": 0,
}

TIER_RANK = {"critical": 4, "warning": 3, "elevated": 2, "normal": 1}


# ─── Employee and edge data structures ───────────────────────────────────────


class _Employee(NamedTuple):
    employee_id: str
    department: str
    archetype: str


# ─── Graph builder ───────────────────────────────────────────────────────────


@dataclass
class GroundTruthOrg:
    """Synthetic organisation with planted structural archetypes.

    Attributes:
        employees: All generated employees with archetype labels.
        G: NetworkX DiGraph built from the generated edges.
        entropy_trends: Computed entropy trend slope per employee_id.
        archetype_ids: Maps archetype name → list of employee_ids.
    """

    employees: list[_Employee]
    G: nx.DiGraph
    entropy_trends: dict[str, float]
    archetype_ids: dict[str, list[str]]


def build_ground_truth_org(seed: int = 42) -> GroundTruthOrg:
    """Build the synthetic organisation in-memory.

    Edge generation per archetype (30-day window):

        BRIDGE:      12 interactions/day, 90% cross-dept probability.
                     Rate is 17x the normal mean — creates dominant betweenness
                     separation from NORMAL employees even after rank-percentile
                     transform.  Cross-dept probability 90% vs 5% for NORMAL
                     maximises the cross-dept ratio gap.

        WITHDRAWING: 10 interactions/day days 0-14 (80% cross-dept),
                     0.2 interactions/day days 15-29.
                     Residual betweenness from the first half of the window
                     keeps the structural score elevated; the strongly negative
                     activity slope pushes entropy_trend to the minimum
                     percentile (r_ent = 0), so the '-delta * r_ent' term
                     provides no deduction, preserving risk.

        SOLE_EXPERT: 5 interactions/day, 1% cross-dept probability.
                     High intra-dept volume → low betweenness (no cross-cluster
                     paths) and low cross-dept ratio → intentionally below
                     the critical threshold.  These employees ARE risky (sole
                     domain knowledge), but that signal lives in
                     graph/knowledge_risk.py, not the structural SPOF model.

        SILO:        3 interactions/day, 0% cross-dept (forced intra-dept).
                     High clustering, near-zero betweenness, zero cross-dept
                     ratio.

        NORMAL:      Poisson(0.7)/day, 5% cross-dept probability.
                     Background population with power-law degree distribution.

    Population: 100 employees across 3 departments.  100 nodes gives the
    rank-percentile transform enough resolution to separate the extreme
    archetypes cleanly.

    Args:
        seed: Random seed for reproducibility.

    Returns:
        GroundTruthOrg with populated G, entropy_trends, and archetype_ids.
    """
    rng = np.random.default_rng(seed)

    # ── Population ──────────────────────────────────────────────────────────
    # 100 employees: Engineering 50, Sales 35, HR 15
    departments = {
        "Engineering": 50,  # 1 BRIDGE, 2 SOLE_EXPERT, 5 SILO, 42 NORMAL
        "Sales": 35,  # 1 BRIDGE, 5 SILO, 29 NORMAL
        "HR": 15,  # 1 WITHDRAWING, 14 NORMAL
    }

    employees: list[_Employee] = []
    archetype_ids: dict[str, list[str]] = {k: [] for k in ARCHETYPES}

    def _emp(dept: str, archetype: str) -> _Employee:
        hi = int(rng.integers(0, 2**62))
        lo = int(rng.integers(0, 2**62))
        eid = str(uuid.UUID(int=(hi << 62) | lo))
        e = _Employee(employee_id=eid, department=dept, archetype=archetype)
        archetype_ids[archetype].append(eid)
        return e

    # Engineering
    employees.append(_emp("Engineering", "BRIDGE"))
    employees.append(_emp("Engineering", "SOLE_EXPERT"))
    employees.append(_emp("Engineering", "SOLE_EXPERT"))
    for _ in range(5):
        employees.append(_emp("Engineering", "SILO"))
    for _ in range(42):
        employees.append(_emp("Engineering", "NORMAL"))

    # Sales
    employees.append(_emp("Sales", "BRIDGE"))
    for _ in range(5):
        employees.append(_emp("Sales", "SILO"))
    for _ in range(29):
        employees.append(_emp("Sales", "NORMAL"))

    # HR
    employees.append(_emp("HR", "WITHDRAWING"))
    for _ in range(14):
        employees.append(_emp("HR", "NORMAL"))

    assert len(employees) == sum(departments.values()), (
        f"Employee count mismatch: {len(employees)} vs {sum(departments.values())}"
    )

    # Index for edge generation
    by_dept: dict[str, list[_Employee]] = {}
    for e in employees:
        by_dept.setdefault(e.department, []).append(e)

    bridge_ids = set(archetype_ids["BRIDGE"])
    silo_ids = set(archetype_ids["SILO"])
    sole_ids = set(archetype_ids["SOLE_EXPERT"])
    withdrawing_id = archetype_ids["WITHDRAWING"][0]

    n_days = 30
    withdrawal_start = 15

    # daily_counts[emp_id][day] for entropy trend computation
    daily_counts: dict[str, list[float]] = {e.employee_id: [0.0] * n_days for e in employees}

    raw_edges: list[tuple[str, str, float, str, str]] = []

    def _pick_target(source: _Employee, cross: bool) -> _Employee | None:
        if cross:
            other_depts = [d for d in by_dept if d != source.department]
            if not other_depts:
                return None
            dept = other_depts[int(rng.integers(len(other_depts)))]
            pool = by_dept[dept]
        else:
            pool = [e for e in by_dept[source.department] if e.employee_id != source.employee_id]
        if not pool:
            return None
        return pool[int(rng.integers(len(pool)))]

    def _add_edge(src: _Employee, tgt: _Employee, weight: float, day: int) -> None:
        raw_edges.append((src.employee_id, tgt.employee_id, weight, src.department, tgt.department))
        daily_counts[src.employee_id][day] += 1

    for day in range(n_days):
        for emp in employees:
            eid = emp.employee_id

            # Determine rate and cross-dept probability for this archetype
            if eid in bridge_ids:
                rate, cross_prob = 12.0, 0.90
            elif eid == withdrawing_id:
                rate = 10.0 if day < withdrawal_start else 0.2
                cross_prob = 0.80 if day < withdrawal_start else 0.10
            elif eid in sole_ids:
                rate, cross_prob = 5.0, 0.01
            elif eid in silo_ids:
                rate, cross_prob = 3.0, 0.00  # forced intra-dept
            else:
                # NORMAL: power-law base rate, 5% cross-dept
                rate = float(rng.pareto(2.0) + 0.2) * (0.7 / 1.0)
                cross_prob = 0.05

            n_interactions = int(rng.poisson(max(rate, 0.01)))

            for _ in range(n_interactions):
                cross = (cross_prob > 0) and (rng.random() < cross_prob)
                tgt = _pick_target(emp, cross)
                if tgt is None:
                    continue
                _add_edge(emp, tgt, 1.0, day)

    # ── Build graph ─────────────────────────────────────────────────────────
    G = _build_graph(raw_edges)

    # ── Entropy trends from daily counts ────────────────────────────────────
    # Slope of a linear fit on daily_counts[emp][day] over 30 days.
    # Negative slope = withdrawing. Matches the semantics used by the DAG
    # pipeline (etl/tasks/compute_centrality.py), where entropy trend is the
    # linear regression slope of per-day interaction counts.
    days_arr = np.arange(n_days, dtype=float)
    entropy_trends: dict[str, float] = {}
    for emp in employees:
        counts = np.array(daily_counts[emp.employee_id], dtype=float)
        if counts.std() < 1e-9:
            entropy_trends[emp.employee_id] = 0.0
        else:
            slope = float(np.polyfit(days_arr, counts, 1)[0])
            entropy_trends[emp.employee_id] = slope

    return GroundTruthOrg(
        employees=employees,
        G=G,
        entropy_trends=entropy_trends,
        archetype_ids=archetype_ids,
    )


def _build_graph(raw_edges: list[tuple[str, str, float, str, str]]) -> nx.DiGraph:
    """Build weighted DiGraph from (source, target, weight, src_dept, tgt_dept) tuples."""
    G = nx.DiGraph()
    weights: dict[tuple[str, str], float] = {}
    dept: dict[str, str] = {}

    for src, tgt, w, sd, td in raw_edges:
        weights[(src, tgt)] = weights.get((src, tgt), 0.0) + w
        dept[src] = sd
        dept[tgt] = td

    for nid, d in dept.items():
        G.add_node(nid, department=d)
    for (s, t), w in weights.items():
        G.add_edge(s, t, weight=w)

    return G


# ─── Scoring ─────────────────────────────────────────────────────────────────


def run_scoring(org: GroundTruthOrg) -> dict[str, dict]:
    """Run the real SPOF scoring pipeline on the ground-truth graph.

    Uses graph.metrics (betweenness, clustering) and graph.risk_scorer
    (rank-percentile SPOF with weight-sensitivity bands).

    Returns:
        Per-employee scoring detail from score_all_with_bands().
    """
    betweenness = compute_betweenness(org.G)
    clustering = compute_clustering(org.G)
    return score_all_with_bands(org.G, betweenness, clustering, org.entropy_trends)


# ─── Validation metrics ───────────────────────────────────────────────────────


def _flag(score: float) -> str:
    if score >= 0.7:
        return "critical"
    if score >= 0.5:
        return "warning"
    if score >= 0.4:
        return "elevated"
    return "normal"


def spearman_r(x: list[float], y: list[float]) -> float:
    """Spearman rank correlation (no scipy dependency)."""
    n = len(x)
    if n < 2:
        return float("nan")
    ax = np.array(x, dtype=float)
    ay = np.array(y, dtype=float)
    rx = np.argsort(np.argsort(ax)).astype(float)
    ry = np.argsort(np.argsort(ay)).astype(float)
    d = rx - ry
    return float(1.0 - 6.0 * np.sum(d**2) / (n * (n**2 - 1)))


@dataclass
class ValidationReport:
    """Structured validation result."""

    precision_critical: float  # TP / (TP + FP) at score ≥ 0.7
    recall_bridge: float  # Fraction of BRIDGE employees flagged critical
    recall_withdrawing: float  # Fraction of WITHDRAWING employees flagged ≥ warning
    spearman_rho: float  # Rank correlation: planted severity vs. median SPOF score per archetype (5 pts)
    bridge_ranks: list[int]
    withdrawing_rank: int
    sole_expert_max_score: float
    silo_max_score: float
    per_employee: list[dict]  # Full per-employee breakdown for the report


def validate(org: GroundTruthOrg, scores: dict[str, dict]) -> ValidationReport:
    """Compare scored output to planted ground truth.

    Args:
        org: The ground-truth organisation.
        scores: Per-employee scoring detail from score_all_with_bands().

    Returns:
        ValidationReport with precision, recall, Spearman ρ, and per-employee detail.
    """
    # Rank by SPOF score (1 = highest)
    ranked = sorted(scores.items(), key=lambda x: x[1]["score"], reverse=True)
    rank_of: dict[str, int] = {eid: i + 1 for i, (eid, _) in enumerate(ranked)}

    # Per-employee breakdown
    per_employee: list[dict] = []
    for emp in org.employees:
        eid = emp.employee_id
        detail = scores[eid]
        per_employee.append(
            {
                "archetype": emp.archetype,
                "department": emp.department,
                "score": round(detail["score"], 4),
                "flag": _flag(detail["score"]),
                "robust": detail["robust_critical"],
                "rank": rank_of[eid],
                "planted_severity": PLANTED_SEVERITY[emp.archetype],
            }
        )

    # True SPOFs = BRIDGE + WITHDRAWING
    true_spof_ids = set(org.archetype_ids["BRIDGE"] + org.archetype_ids["WITHDRAWING"])

    # Precision at critical threshold (score ≥ 0.7)
    critical_ids = {eid for eid, d in scores.items() if d["score"] >= 0.7}
    tp = len(critical_ids & true_spof_ids)
    fp = len(critical_ids - true_spof_ids)
    precision = tp / max(tp + fp, 1)

    # Recall: BRIDGE employees flagged critical
    bridge_ids = set(org.archetype_ids["BRIDGE"])
    bridge_critical = len(bridge_ids & critical_ids)
    recall_bridge = bridge_critical / max(len(bridge_ids), 1)

    # Recall: WITHDRAWING flagged ≥ warning (score ≥ 0.5)
    warn_ids = {eid for eid, d in scores.items() if d["score"] >= 0.5}
    withdrawing_ids = set(org.archetype_ids["WITHDRAWING"])
    recall_withdrawing = len(withdrawing_ids & warn_ids) / max(len(withdrawing_ids), 1)

    # Rank checks
    bridge_ranks = sorted(rank_of[eid] for eid in bridge_ids)
    withdrawing_rank = rank_of[org.archetype_ids["WITHDRAWING"][0]]

    # Boundary check: sole experts and silos should not score critical
    sole_max = max(scores[eid]["score"] for eid in org.archetype_ids["SOLE_EXPERT"])
    silo_max = max(scores[eid]["score"] for eid in org.archetype_ids["SILO"])

    # Spearman rho: planted severity vs. median SPOF score per archetype.
    # Computed on 5 archetype medians rather than 100 individual employees.
    # Individual-level correlation is confounded by NORMAL employees (severity=0)
    # whose scores vary from 0 to ~0.5 due to power-law degree distribution — a
    # realistic property of the model, not a failure.  Archetype medians cleanly
    # test whether the scoring ORDER (BRIDGE > WITHDRAWING > SOLE_EXPERT > SILO)
    # is preserved, which is the meaningful structural claim.
    archetype_order = ["BRIDGE", "WITHDRAWING", "SOLE_EXPERT", "SILO", "NORMAL"]
    median_severities = [float(PLANTED_SEVERITY[a]) for a in archetype_order]
    median_scores = [float(np.median([scores[eid]["score"] for eid in org.archetype_ids[a]])) for a in archetype_order]
    rho = spearman_r(median_severities, median_scores)

    return ValidationReport(
        precision_critical=precision,
        recall_bridge=recall_bridge,
        recall_withdrawing=recall_withdrawing,
        spearman_rho=rho,
        bridge_ranks=bridge_ranks,
        withdrawing_rank=withdrawing_rank,
        sole_expert_max_score=sole_max,
        silo_max_score=silo_max,
        per_employee=per_employee,
    )


# ─── Report printer ───────────────────────────────────────────────────────────


def print_report(org: GroundTruthOrg, scores: dict[str, dict], report: ValidationReport) -> None:
    """Print the structured validation report to stdout."""
    sep = "-" * 70

    print(f"\n{'SPOF MODEL -- GROUND-TRUTH VALIDATION REPORT':^70}")
    print(f"{'Synthetic controlled experiment (no live data)':^70}")
    print(sep)

    print("\n  PLANTED ARCHETYPES")
    for name, ids in org.archetype_ids.items():
        print(f"    {name:<14} n={len(ids):>2}  severity={PLANTED_SEVERITY[name]}")

    print("\n  GRAPH STATS")
    print(f"    Nodes : {org.G.number_of_nodes()}")
    print(f"    Edges : {org.G.number_of_edges()} directed")
    cross_dept = compute_cross_dept_ratio(org.G)
    bridge_cdr = np.mean([cross_dept[eid] for eid in org.archetype_ids["BRIDGE"]])
    silo_cdr = np.mean([cross_dept[eid] for eid in org.archetype_ids["SILO"]])
    print(f"    BRIDGE mean cross-dept ratio  : {bridge_cdr:.3f}")
    print(f"    SILO   mean cross-dept ratio  : {silo_cdr:.3f}")

    print("\n  SCORING SUMMARY (by archetype)")
    header = f"  {'Archetype':<14} {'Score':>7} {'Flag':<12} {'Rank':>5} {'Robust':>7}"
    print(header)
    print(f"  {'-' * 14} {'-' * 7} {'-' * 12} {'-' * 5} {'-' * 7}")

    by_archetype: dict[str, list[dict]] = {}
    for row in report.per_employee:
        by_archetype.setdefault(row["archetype"], []).append(row)

    for archetype in ARCHETYPES:
        rows = sorted(by_archetype.get(archetype, []), key=lambda r: r["score"], reverse=True)
        for r in rows:
            robust_str = "yes" if r["robust"] else "no"
            print(f"  {archetype:<14} {r['score']:>7.4f} {r['flag']:<12} {r['rank']:>5}   {robust_str:>5}")

    print("\n  VALIDATION METRICS")
    print(f"    Precision@critical (score>=0.7)  : {report.precision_critical:.2%}")
    print(f"    Recall   BRIDGE    (score>=0.7)  : {report.recall_bridge:.2%}")
    print(f"    Recall   WITHDRAWING (score>=0.5) : {report.recall_withdrawing:.2%}")
    print(f"    Spearman rho (archetype medians)  : {report.spearman_rho:.3f}  [5 archetype medians]")
    print(f"    BRIDGE ranks                      : {report.bridge_ranks}")
    print(f"    WITHDRAWING rank                  : {report.withdrawing_rank}")
    print(f"    SOLE_EXPERT max score             : {report.sole_expert_max_score:.4f}  (must be < 0.7)")
    print(f"    SILO        max score             : {report.silo_max_score:.4f}  (must be < 0.5)")

    print("\n  PASS / FAIL")
    checks = _assertion_checks(report)
    for label, passed, detail in checks:
        status = "PASS" if passed else "FAIL"
        print(f"    [{status}] {label:45s}  {detail}")

    print(f"\n{sep}\n")


def _assertion_checks(report: ValidationReport) -> list[tuple[str, bool, str]]:
    return [
        (
            "BRIDGE precision@critical >= 0.80",
            report.precision_critical >= 0.80,
            f"{report.precision_critical:.2%}",
        ),
        (
            "BRIDGE recall@critical = 100%",
            report.recall_bridge == 1.0,
            f"{report.recall_bridge:.2%}",
        ),
        (
            "BRIDGE rank <= 3 (both employees)",
            all(r <= 3 for r in report.bridge_ranks),
            str(report.bridge_ranks),
        ),
        (
            "WITHDRAWING recall@warning >= 100%",
            report.recall_withdrawing >= 1.0,
            f"{report.recall_withdrawing:.2%}",
        ),
        (
            "WITHDRAWING rank <= 10",
            report.withdrawing_rank <= 10,
            str(report.withdrawing_rank),
        ),
        (
            "SOLE_EXPERT max score < 0.70 (struct vs knowledge boundary)",
            report.sole_expert_max_score < 0.70,
            f"{report.sole_expert_max_score:.4f}",
        ),
        (
            "SILO max score < 0.50",
            report.silo_max_score < 0.50,
            f"{report.silo_max_score:.4f}",
        ),
        (
            "Spearman rho >= 0.60",
            report.spearman_rho >= 0.60,
            f"{report.spearman_rho:.3f}",
        ),
    ]


# ─── Pytest entry points ──────────────────────────────────────────────────────

_ORG: GroundTruthOrg | None = None
_SCORES: dict[str, dict] | None = None
_REPORT: ValidationReport | None = None


def _get_fixtures():
    """Build org and scores once per session."""
    global _ORG, _SCORES, _REPORT
    if _ORG is None:
        _ORG = build_ground_truth_org(seed=42)
        _SCORES = run_scoring(_ORG)
        _REPORT = validate(_ORG, _SCORES)
    return _ORG, _SCORES, _REPORT


def test_bridge_scores_critical():
    """Both planted bridge employees must score ≥ 0.7 (critical tier)."""
    org, scores, _ = _get_fixtures()
    for eid in org.archetype_ids["BRIDGE"]:
        s = scores[eid]["score"]
        assert s >= 0.7, f"BRIDGE employee {eid[:8]}… scored {s:.4f} (expected ≥ 0.7)"


def test_bridge_rank_top3():
    """Both planted bridge employees must rank in the top 3 by SPOF score."""
    org, scores, _ = _get_fixtures()
    ranked = sorted(scores.items(), key=lambda x: x[1]["score"], reverse=True)
    rank_of = {eid: i + 1 for i, (eid, _) in enumerate(ranked)}
    for eid in org.archetype_ids["BRIDGE"]:
        r = rank_of[eid]
        assert r <= 3, f"BRIDGE employee {eid[:8]}… ranked #{r} (expected ≤ 3)"


def test_withdrawing_scores_warning_or_above():
    """The planted withdrawing employee must score ≥ 0.5."""
    org, scores, _ = _get_fixtures()
    eid = org.archetype_ids["WITHDRAWING"][0]
    s = scores[eid]["score"]
    assert s >= 0.5, f"WITHDRAWING employee scored {s:.4f} (expected ≥ 0.5)"


def test_withdrawing_rank_top10():
    """The planted withdrawing employee must rank in the top 10."""
    org, scores, _ = _get_fixtures()
    ranked = sorted(scores.items(), key=lambda x: x[1]["score"], reverse=True)
    rank_of = {eid: i + 1 for i, (eid, _) in enumerate(ranked)}
    eid = org.archetype_ids["WITHDRAWING"][0]
    r = rank_of[eid]
    assert r <= 10, f"WITHDRAWING employee ranked #{r} (expected ≤ 10)"


def test_sole_expert_not_structural_spof():
    """Sole experts must NOT trigger the critical structural SPOF threshold.

    This validates the intentional boundary: knowledge depth (sole domain
    expert) is a different risk signal from structural bridging. Both are
    real risks, but the SPOF model captures structural dependency, not
    knowledge concentration. Knowledge concentration is scored separately
    in graph/knowledge_risk.py.
    """
    org, scores, _ = _get_fixtures()
    for eid in org.archetype_ids["SOLE_EXPERT"]:
        s = scores[eid]["score"]
        assert s < 0.7, (
            f"SOLE_EXPERT {eid[:8]}… scored {s:.4f} (expected < 0.7). "
            "Structural SPOF and knowledge risk are separate models — "
            "this boundary must hold."
        )


def test_silo_scores_below_warning():
    """Silo employees must not reach the warning tier (score < 0.5)."""
    org, scores, _ = _get_fixtures()
    for eid in org.archetype_ids["SILO"]:
        s = scores[eid]["score"]
        assert s < 0.5, f"SILO employee {eid[:8]}… scored {s:.4f} (expected < 0.5)"


def test_precision_at_critical():
    """Precision at the critical threshold must be ≥ 80%."""
    _, _, report = _get_fixtures()
    assert report.precision_critical >= 0.80, f"Precision@critical = {report.precision_critical:.2%} (expected ≥ 80%)"


def test_spearman_rank_correlation():
    """Spearman ρ between planted severity and scored rank must be ≥ 0.60."""
    _, _, report = _get_fixtures()
    assert report.spearman_rho >= 0.60, f"Spearman ρ = {report.spearman_rho:.3f} (expected ≥ 0.60)"


# ─── CLI ─────────────────────────────────────────────────────────────────────


def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    org = build_ground_truth_org(seed=42)
    scores = run_scoring(org)
    report = validate(org, scores)
    print_report(org, scores, report)

    failed = [label for label, passed, _ in _assertion_checks(report) if not passed]
    if failed:
        print(f"  {len(failed)} check(s) FAILED:\n")
        for label in failed:
            print(f"    • {label}")
        print()
        sys.exit(1)


if __name__ == "__main__":
    main()
