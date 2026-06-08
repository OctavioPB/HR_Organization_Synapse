"""Isolation Forest anomaly detection -- injected-case validation.

WHAT THIS ANSWERS
-----------------
"Does the Isolation Forest actually detect the anomalies it claims to detect?"

The existing unit tests (tests/unit/test_isolation_forest.py) verify structure,
score bounds, and a single binary outlier (fully disconnected employee).  This
evaluator goes further by:

  1. Injecting 4 distinct anomaly archetypes at two severity levels each
     (8 injected anomalies total) into a realistic noisy background.
  2. Measuring precision and recall against the flagged employee set.
  3. Measuring ranking quality: are severe anomalies ranked above moderate ones?
  4. Testing detection at the PRODUCTION contamination (0.05) separately from
     detection at the ORACLE contamination (8/108) to simulate real alerts.
  5. Documenting which archetype is hardest to detect (honest finding).

ANOMALY ARCHETYPES
------------------
Each archetype represents a real connectivity failure mode from the domain:

  SUDDEN_DROPOUT   -- Employee was active, has gone silent.
                      Signature: all graph features near zero, large negative deltas.

  ENTROPY_COLLAPSE -- Employee is still active but converged to one contact.
                      Signature: degree_out moderate, entropy_current very low,
                      entropy_trend sharply negative.

  ACTIVITY_SPIKE   -- Unusual hyperactivity; possible bot, spam, or gaming pattern.
                      Signature: degree_out and betweenness at extreme high,
                      degree_in asymmetrically low (sends but rarely receives).

  BRIDGE_COLLAPSE  -- Former cross-department connector suddenly isolated.
                      Signature: betweenness_delta and degree_out_delta very
                      negative; current values still moderate (not yet zero).

Each archetype is injected at two levels:
  SEVERE   -- Feature values that are extreme outliers (expected: always detected).
  MODERATE -- Feature values that are clear but subtle (expected: often detected).

BACKGROUND POPULATION
---------------------
100 normal employees with realistic Gaussian noise on all 8 features.
Using constant-valued normals (as in the unit tests) makes the IF's job trivial.
The noisy background represents the actual variance of a real org graph snapshot.

RUN MODES
---------
    python tests/validation/anomaly_detection_evaluator.py   # full report
    pytest  tests/validation/anomaly_detection_evaluator.py  # assertions
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ml.anomaly.isolation_forest import FEATURE_KEYS, run_isolation_forest

logger = logging.getLogger(__name__)

SEED = 42
N_NORMAL = 100
# Contamination used for the "oracle" run: exactly n_anomalies / total
# This tells IF to flag exactly as many employees as we injected.
_PRODUCTION_CONTAMINATION = 0.05


# ─── Anomaly archetypes ───────────────────────────────────────────────────────


def _fv(employee_id: str, **kw) -> dict:
    """Build a feature dict. Unset keys default to mid-range (0.35)."""
    fv: dict = {k: 0.35 for k in FEATURE_KEYS}
    fv["employee_id"] = employee_id
    fv.update(kw)
    return fv


# Each entry: (employee_id, severity_label, severity_rank, feature_kwargs)
# severity_rank: 2 = severe, 1 = moderate, 0 = normal (used for Spearman)
INJECTED_ANOMALIES: list[tuple[str, str, int, dict]] = [
    # ── SUDDEN_DROPOUT ────────────────────────────────────────────────────────
    # Employee was well-connected; now completely silent.
    # Most extreme anomaly type -- IF should always catch severe case.
    (
        "DROPOUT_SEVERE",
        "SUDDEN_DROPOUT",
        2,
        dict(
            betweenness=0.00,
            degree_in=0.00,
            degree_out=0.00,
            clustering=0.00,
            betweenness_delta_7d=-0.48,
            degree_out_delta_7d=-0.50,
            entropy_current=0.00,
            entropy_trend=-0.95,
        ),
    ),
    (
        "DROPOUT_MODERATE",
        "SUDDEN_DROPOUT",
        1,
        dict(
            betweenness=0.04,
            degree_in=0.06,
            degree_out=0.06,
            clustering=0.08,
            betweenness_delta_7d=-0.22,
            degree_out_delta_7d=-0.24,
            entropy_current=0.12,
            entropy_trend=-0.42,
        ),
    ),
    # ── ENTROPY_COLLAPSE ──────────────────────────────────────────────────────
    # Still sends messages, but converged to a single contact.
    # The graph degree is moderate; anomaly lives in entropy dimensions.
    (
        "ENTROPY_SEVERE",
        "ENTROPY_COLLAPSE",
        2,
        dict(
            betweenness=0.06,
            degree_in=0.18,
            degree_out=0.32,
            clustering=0.85,  # tightly clustered because talks to only one person
            betweenness_delta_7d=-0.12,
            degree_out_delta_7d=-0.04,
            entropy_current=0.04,  # ~0 bits diversity
            entropy_trend=-0.92,
        ),
    ),
    (
        "ENTROPY_MODERATE",
        "ENTROPY_COLLAPSE",
        1,
        dict(
            betweenness=0.10,
            degree_in=0.22,
            degree_out=0.38,
            clustering=0.72,
            betweenness_delta_7d=-0.06,
            degree_out_delta_7d=-0.02,
            entropy_current=0.28,
            entropy_trend=-0.48,
        ),
    ),
    # ── ACTIVITY_SPIKE ────────────────────────────────────────────────────────
    # Extreme hyperactivity: sends to many contacts, receives almost nothing.
    # Could indicate bot behavior, data anomaly, or unusual project crunch.
    (
        "SPIKE_SEVERE",
        "ACTIVITY_SPIKE",
        2,
        dict(
            betweenness=0.96,
            degree_in=0.04,  # asymmetric: sends but doesn't receive
            degree_out=0.98,
            clustering=0.05,
            betweenness_delta_7d=0.58,
            degree_out_delta_7d=0.61,
            entropy_current=3.50,  # very high diversity (many different contacts)
            entropy_trend=0.88,
        ),
    ),
    (
        "SPIKE_MODERATE",
        "ACTIVITY_SPIKE",
        1,
        dict(
            betweenness=0.84,
            degree_in=0.12,
            degree_out=0.89,
            clustering=0.10,
            betweenness_delta_7d=0.32,
            degree_out_delta_7d=0.34,
            entropy_current=2.88,
            entropy_trend=0.55,
        ),
    ),
    # ── BRIDGE_COLLAPSE ───────────────────────────────────────────────────────
    # Former cross-department connector has suddenly withdrawn from that role.
    # Current metrics are moderate; the anomaly lives in the large negative deltas.
    (
        "BRIDGE_SEVERE",
        "BRIDGE_COLLAPSE",
        2,
        dict(
            betweenness=0.05,
            degree_in=0.22,
            degree_out=0.18,
            clustering=0.42,
            betweenness_delta_7d=-0.54,  # was ~0.59, now 0.05
            degree_out_delta_7d=-0.40,  # was ~0.58, now 0.18
            entropy_current=0.85,
            entropy_trend=-0.38,
        ),
    ),
    (
        "BRIDGE_MODERATE",
        "BRIDGE_COLLAPSE",
        1,
        dict(
            betweenness=0.14,
            degree_in=0.28,
            degree_out=0.26,
            clustering=0.38,
            betweenness_delta_7d=-0.28,  # was ~0.42, now 0.14
            degree_out_delta_7d=-0.22,
            entropy_current=1.10,
            entropy_trend=-0.18,
        ),
    ),
]

_ARCHETYPES = ["SUDDEN_DROPOUT", "ENTROPY_COLLAPSE", "ACTIVITY_SPIKE", "BRIDGE_COLLAPSE"]


# ─── Background population ────────────────────────────────────────────────────


def _make_normal_population(n: int, seed: int) -> list[dict]:
    """Generate n normal employees with realistic Gaussian noise on all 8 features.

    Using a noisy normal population (not constant-valued) makes detection
    meaningfully harder than the unit tests and represents real org snapshots
    where natural variation exists.
    """
    rng = np.random.default_rng(seed)
    records = []
    for i in range(n):
        records.append(
            _fv(
                f"NORMAL_{i:03d}",
                betweenness=float(rng.uniform(0.05, 0.62)),
                degree_in=float(rng.uniform(0.12, 0.72)),
                degree_out=float(rng.uniform(0.18, 0.76)),
                clustering=float(rng.uniform(0.18, 0.78)),
                betweenness_delta_7d=float(np.clip(rng.normal(0.0, 0.05), -0.18, 0.18)),
                degree_out_delta_7d=float(np.clip(rng.normal(0.0, 0.04), -0.15, 0.15)),
                entropy_current=float(rng.uniform(0.90, 3.10)),
                entropy_trend=float(np.clip(rng.normal(0.0, 0.09), -0.28, 0.28)),
            )
        )
    return records


# ─── Evaluation ───────────────────────────────────────────────────────────────


@dataclass
class AnomalyEvalReport:
    # Per-injected-anomaly results
    per_anomaly: list[dict]
    # Aggregate metrics
    recall_severe: float  # fraction of severe anomalies detected (any contamination)
    recall_all: float  # fraction of all 8 detected
    precision_oracle: float  # precision at oracle-contamination flagged set
    recall_oracle: float  # recall at oracle-contamination flagged set
    recall_production: float  # recall at production contamination (0.05)
    severity_order_holds: bool  # severe score > moderate for ALL 4 archetypes
    severity_spearman: float  # Spearman(group_severity, group_median_score)
    hardest_archetype: str  # archetype with lowest severe anomaly score
    n_total: int
    n_anomalies: int
    n_normal: int
    contamination_oracle: float
    # Scores by group
    normal_median_score: float
    moderate_median_score: float
    severe_median_score: float


def _spearman(x: list[float], y: list[float]) -> float:
    """Spearman rank correlation (pure numpy, no scipy)."""
    n = len(x)
    if n < 2:
        return float("nan")
    ax = np.array(x, dtype=float)
    ay = np.array(y, dtype=float)
    rx = np.argsort(np.argsort(ax)).astype(float)
    ry = np.argsort(np.argsort(ay)).astype(float)
    d = rx - ry
    return float(1.0 - 6.0 * np.sum(d**2) / (n * (n**2 - 1)))


def run_evaluation(seed: int = SEED) -> AnomalyEvalReport:
    """Build dataset, run IF at two contamination levels, compute all metrics."""
    normal_pop = _make_normal_population(N_NORMAL, seed)
    anomaly_pop = [_fv(emp_id, **feat_kw) for emp_id, archetype, severity_rank, feat_kw in INJECTED_ANOMALIES]
    all_features = normal_pop + anomaly_pop
    n_total = len(all_features)
    n_anomalies = len(anomaly_pop)
    contamination_oracle = n_anomalies / n_total

    # ── Oracle run: IF told exactly how many anomalies to flag ────────────────
    oracle_results = run_isolation_forest(
        all_features,
        contamination=contamination_oracle,
        random_state=seed,
    )

    # ── Production run: real default contamination ────────────────────────────
    production_results = run_isolation_forest(
        all_features,
        contamination=_PRODUCTION_CONTAMINATION,
        random_state=seed,
    )

    # Index results by employee_id
    oracle_by_id = {r["employee_id"]: r for r in oracle_results}
    production_by_id = {r["employee_id"]: r for r in production_results}

    # ── Per-anomaly metrics ───────────────────────────────────────────────────
    anomaly_ids = {emp_id for emp_id, _, _, _ in INJECTED_ANOMALIES}
    # Rank by anomaly_score descending (1 = most anomalous)
    ranked = sorted(oracle_results, key=lambda r: r["anomaly_score"], reverse=True)
    rank_of = {r["employee_id"]: i + 1 for i, r in enumerate(ranked)}

    per_anomaly = []
    for emp_id, archetype, severity_rank, _ in INJECTED_ANOMALIES:
        oracle_r = oracle_by_id[emp_id]
        production_r = production_by_id[emp_id]
        percentile = 1.0 - (rank_of[emp_id] - 1) / (n_total - 1)  # 1.0 = top rank
        per_anomaly.append(
            {
                "employee_id": emp_id,
                "archetype": archetype,
                "severity": severity_rank,
                "anomaly_score": round(oracle_r["anomaly_score"], 4),
                "rank": rank_of[emp_id],
                "percentile": round(percentile, 3),
                "detected_oracle": oracle_r["is_anomaly"],
                "detected_prod": production_r["is_anomaly"],
            }
        )

    # ── Aggregate metrics ─────────────────────────────────────────────────────
    severe_rows = [p for p in per_anomaly if p["severity"] == 2]
    moderate_rows = [p for p in per_anomaly if p["severity"] == 1]

    recall_severe = sum(1 for p in severe_rows if p["detected_oracle"]) / len(severe_rows)
    recall_all = sum(1 for p in per_anomaly if p["detected_oracle"]) / len(per_anomaly)

    # Oracle precision/recall
    oracle_flagged_ids = {r["employee_id"] for r in oracle_results if r["is_anomaly"]}
    tp_oracle = len(oracle_flagged_ids & anomaly_ids)
    fp_oracle = len(oracle_flagged_ids - anomaly_ids)
    precision_oracle = tp_oracle / max(tp_oracle + fp_oracle, 1)
    recall_oracle = tp_oracle / max(n_anomalies, 1)

    # Production recall
    prod_flagged_ids = {r["employee_id"] for r in production_results if r["is_anomaly"]}
    tp_prod = len(prod_flagged_ids & anomaly_ids)
    recall_production = tp_prod / max(n_anomalies, 1)

    # Severity ordering: for each archetype, severe.score > moderate.score?
    severity_order_holds = True
    for arch in _ARCHETYPES:
        arch_rows = [p for p in per_anomaly if p["archetype"] == arch]
        if len(arch_rows) < 2:
            continue
        sev_score = next(p["anomaly_score"] for p in arch_rows if p["severity"] == 2)
        mod_score = next(p["anomaly_score"] for p in arch_rows if p["severity"] == 1)
        if sev_score <= mod_score:
            severity_order_holds = False

    # Spearman on 3 group medians: NORMAL vs MODERATE vs SEVERE
    normal_scores = [oracle_by_id[f"NORMAL_{i:03d}"]["anomaly_score"] for i in range(N_NORMAL)]
    moderate_scores = [p["anomaly_score"] for p in moderate_rows]
    severe_scores = [p["anomaly_score"] for p in severe_rows]

    grp_severity = [0.0, 1.0, 2.0]
    grp_median = [
        float(np.median(normal_scores)),
        float(np.median(moderate_scores)),
        float(np.median(severe_scores)),
    ]
    severity_spearman = _spearman(grp_severity, grp_median)

    # Hardest archetype: which severe case has the LOWEST anomaly score?
    hardest = min(
        (p for p in per_anomaly if p["severity"] == 2),
        key=lambda p: p["anomaly_score"],
    )

    return AnomalyEvalReport(
        per_anomaly=per_anomaly,
        recall_severe=round(recall_severe, 3),
        recall_all=round(recall_all, 3),
        precision_oracle=round(precision_oracle, 3),
        recall_oracle=round(recall_oracle, 3),
        recall_production=round(recall_production, 3),
        severity_order_holds=severity_order_holds,
        severity_spearman=round(severity_spearman, 3),
        hardest_archetype=hardest["archetype"],
        n_total=n_total,
        n_anomalies=n_anomalies,
        n_normal=N_NORMAL,
        contamination_oracle=round(contamination_oracle, 4),
        normal_median_score=round(float(np.median(normal_scores)), 4),
        moderate_median_score=round(float(np.median(moderate_scores)), 4),
        severe_median_score=round(float(np.median(severe_scores)), 4),
    )


# ─── Report ───────────────────────────────────────────────────────────────────


def print_report(report: AnomalyEvalReport) -> None:
    sep = "-" * 72
    print(f"\n{'ISOLATION FOREST -- INJECTED-CASE ANOMALY DETECTION VALIDATION':^72}")
    print(f"{'4 archetypes x 2 severities (8 total) + 100 noisy normal employees':^72}")
    print(sep)

    print("\n  DATASET")
    print(f"    Normal employees  : {report.n_normal}")
    print(f"    Injected anomalies: {report.n_anomalies}  (4 severe + 4 moderate)")
    print(f"    Total             : {report.n_total}")
    print(f"    Oracle contamination : {report.contamination_oracle:.4f}  (= 8/{report.n_total})")
    print(f"    Production contamination: {_PRODUCTION_CONTAMINATION:.2f}  (production default)")

    print("\n  SCORE DISTRIBUTION BY GROUP")
    print(f"    Normal   median score : {report.normal_median_score:.4f}")
    print(f"    Moderate median score : {report.moderate_median_score:.4f}")
    print(f"    Severe   median score : {report.severe_median_score:.4f}")
    print(f"    Spearman (3 group medians) : {report.severity_spearman:.3f}")

    print(f"\n  PER-ANOMALY RESULTS  (oracle contamination = {report.contamination_oracle:.4f})")
    hdr = (
        f"  {'Employee':<18} {'Archetype':<18} {'Sev':>3} "
        f"{'Score':>7} {'Rank':>5} {'Pct':>6} {'Oracle':>8} {'Prod':>7}"
    )
    print(hdr)
    print(f"  {'-'*18} {'-'*18} {'-'*3} {'-'*7} {'-'*5} {'-'*6} {'-'*8} {'-'*7}")
    for p in sorted(report.per_anomaly, key=lambda x: x["severity"], reverse=True):
        ok_o = "YES" if p["detected_oracle"] else "NO"
        ok_p = "YES" if p["detected_prod"] else "NO"
        sev_label = "SEVERE" if p["severity"] == 2 else "moderate"
        print(
            f"  {p['employee_id']:<18} {p['archetype']:<18} {sev_label:>8} "
            f"{p['anomaly_score']:>7.4f} {p['rank']:>5} {p['percentile']:>6.1%} "
            f"{ok_o:>8} {ok_p:>7}"
        )

    print("\n  AGGREGATE METRICS")
    print(f"    Recall@severe  (oracle) : {report.recall_severe:.1%}  (all 4 severe detected?)")
    print(f"    Recall@all     (oracle) : {report.recall_all:.1%}  (all 8 detected?)")
    print(f"    Precision      (oracle) : {report.precision_oracle:.1%}  (of flagged, how many true?)")
    print(f"    Recall@severe  (prod)   : {report.recall_production:.1%}  (at 5% contamination)")
    print(f"    Severity order holds    : {'YES' if report.severity_order_holds else 'NO -- see per-anomaly table'}")
    print(f"    Hardest archetype       : {report.hardest_archetype}")

    print("\n  PASS / FAIL")
    for label, passed, detail in _assertion_checks(report):
        status = "PASS" if passed else "FAIL"
        print(f"    [{status}] {label:<54} {detail}")

    print(f"\n{sep}\n")


def _assertion_checks(report: AnomalyEvalReport) -> list[tuple[str, bool, str]]:
    return [
        (
            "Recall@severe (oracle) = 100%",
            report.recall_severe == 1.0,
            f"{report.recall_severe:.1%}",
        ),
        (
            "Recall@all (oracle) >= 62%  (>=5 of 8)",
            report.recall_all >= 0.625,
            f"{report.recall_all:.1%}",
        ),
        (
            "Precision (oracle) >= 62%",
            report.precision_oracle >= 0.625,
            f"{report.precision_oracle:.1%}",
        ),
        (
            "Recall@severe (production, 5%) >= 50%",
            report.recall_production >= 0.50,
            f"{report.recall_production:.1%}",
        ),
        (
            "Severity order holds (severe > moderate, all archetypes)",
            report.severity_order_holds,
            "YES" if report.severity_order_holds else "NO",
        ),
        (
            "Spearman (group medians) >= 0.90",
            report.severity_spearman >= 0.90,
            f"{report.severity_spearman:.3f}",
        ),
        (
            "Normal median score < Moderate median score",
            report.normal_median_score < report.moderate_median_score,
            f"{report.normal_median_score:.4f} < {report.moderate_median_score:.4f}",
        ),
        (
            "Moderate median score < Severe median score",
            report.moderate_median_score < report.severe_median_score,
            f"{report.moderate_median_score:.4f} < {report.severe_median_score:.4f}",
        ),
    ]


# ─── Pytest entry points ──────────────────────────────────────────────────────

_REPORT: AnomalyEvalReport | None = None


def _get_report() -> AnomalyEvalReport:
    global _REPORT
    if _REPORT is None:
        _REPORT = run_evaluation(seed=SEED)
    return _REPORT


def test_severe_anomalies_all_detected():
    """All 4 severe injected anomalies must be flagged when contamination
    is set to the oracle value (= n_injected / n_total).

    If any severe anomaly is missed here, the IF model cannot distinguish
    extreme feature-space outliers from the noisy normal background.  This
    is the absolute minimum requirement for anomaly detection to be useful.
    """
    report = _get_report()
    assert report.recall_severe == 1.0, (
        f"Recall@severe = {report.recall_severe:.1%} (expected 100%). "
        "At least one severe injected anomaly was not detected even when "
        "contamination was set to exactly n_anomalies / n_total.  "
        "Check whether the feature values are truly extreme vs. the "
        "normal population or whether StandardScaler is suppressing the signal."
    )


def test_recall_at_least_62_percent():
    """At least 5 of the 8 injected anomalies must be detected at oracle contamination.

    Moderate anomalies are designed to overlap with the upper tail of the normal
    distribution.  62% recall (5/8) is the minimum bar: all 4 severe + at least
    1 moderate must be detected.  Recall < 62% means the IF cannot distinguish
    even the clearest moderate signals from natural variation.
    """
    report = _get_report()
    assert report.recall_all >= 0.625, (
        f"Recall@all = {report.recall_all:.1%} (expected >= 62.5%). "
        f"Only {int(report.recall_all * report.n_anomalies)}/8 anomalies detected."
    )


def test_precision_at_oracle_contamination():
    """Precision at oracle contamination must be >= 62%.

    When IF is told exactly how many anomalies to flag (oracle contamination),
    at least 5 of the 8 flagged employees should be true injected anomalies.
    False positives here mean the IF is distracted by natural variance in the
    normal population rather than the planted anomalous signals.
    """
    report = _get_report()
    assert report.precision_oracle >= 0.625, (
        f"Precision (oracle) = {report.precision_oracle:.1%} (expected >= 62.5%). "
        "More than 3 of the 8 flagged employees were normal employees, not "
        "injected anomalies.  The noisy background is overwhelming the signal."
    )


def test_recall_at_production_contamination():
    """At production contamination (5%), at least 2 of the 4 severe anomalies
    must be flagged.

    At 5% contamination with 108 total employees, IF flags ~5-6 employees.
    At least half of the severe anomalies should appear in this set; otherwise
    the production alert system misses the most extreme withdrawal patterns.
    """
    report = _get_report()
    severe_detected_prod = sum(1 for p in report.per_anomaly if p["severity"] == 2 and p["detected_prod"])
    assert severe_detected_prod >= 2, (
        f"Only {severe_detected_prod}/4 severe anomalies detected at production "
        f"contamination ({_PRODUCTION_CONTAMINATION}). "
        "The most extreme connectivity failures should appear in routine alerts."
    )


def test_severity_ordering_within_archetypes():
    """For every archetype, the severe variant must score higher than moderate.

    This validates that the anomaly score is monotone in severity: DROPOUT_SEVERE
    must score above DROPOUT_MODERATE, ENTROPY_SEVERE above ENTROPY_MODERATE, etc.
    A violation means the IF score is non-monotone in a way that would mislead
    triage (a moderate anomaly would look more urgent than a severe one).
    """
    report = _get_report()
    violations = []
    for arch in _ARCHETYPES:
        rows = {p["severity"]: p for p in report.per_anomaly if p["archetype"] == arch}
        if 2 in rows and 1 in rows:
            if rows[2]["anomaly_score"] <= rows[1]["anomaly_score"]:
                violations.append(
                    f"{arch}: severe={rows[2]['anomaly_score']:.4f} " f"<= moderate={rows[1]['anomaly_score']:.4f}"
                )
    assert not violations, "Severity ordering violated for:\n  " + "\n  ".join(violations)


def test_group_score_ordering():
    """Median score: Normal < Moderate < Severe (strict ordering).

    This is the core correctness claim of the IF model: employees with more
    anomalous feature vectors should receive higher anomaly scores on average.
    """
    report = _get_report()
    assert report.normal_median_score < report.moderate_median_score, (
        f"Normal median ({report.normal_median_score:.4f}) not < "
        f"Moderate median ({report.moderate_median_score:.4f})"
    )
    assert report.moderate_median_score < report.severe_median_score, (
        f"Moderate median ({report.moderate_median_score:.4f}) not < "
        f"Severe median ({report.severe_median_score:.4f})"
    )


def test_severity_spearman():
    """Spearman rho on 3 group medians (Normal=0, Moderate=1, Severe=2) must be >= 0.90.

    Three data points and a monotone expected relationship gives rho in {-1, 0, 1}.
    rho = 1.0 means the group ordering is perfectly preserved.
    rho < 0.90 (which means < 1.0 with 3 groups) indicates at least one group
    inversion: either Normal median >= Moderate, or Moderate >= Severe.
    The individual tests above catch these violations, but this test provides
    a single summary metric analogous to the SPOF and churn evaluations.
    """
    report = _get_report()
    assert report.severity_spearman >= 0.90, (
        f"Spearman rho (group medians) = {report.severity_spearman:.3f} (expected >= 0.90). "
        "The severity ordering of group-level medians is not preserved."
    )


def test_hardest_archetype_documented():
    """Document which anomaly archetype is hardest to detect. Always passes.

    SUDDEN_DROPOUT tends to be easy (all features near zero).
    BRIDGE_COLLAPSE tends to be harder (current metrics are moderate; only
    the delta features signal the anomaly).
    ENTROPY_COLLAPSE is harder still (degree is moderate; only entropy signals).
    ACTIVITY_SPIKE is hardest in some populations where a few normal employees
    also show high activity.

    This test is a diagnostic, not a correctness gate.
    """
    report = _get_report()
    assert True, f"Hardest archetype: {report.hardest_archetype}"


# ─── CLI ─────────────────────────────────────────────────────────────────────


def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    print(f"\n  Generating dataset ({N_NORMAL} normal + {len(INJECTED_ANOMALIES)} anomalies) ...")
    report = run_evaluation(seed=SEED)
    print_report(report)
    failed = [label for label, passed, _ in _assertion_checks(report) if not passed]
    if failed:
        print(f"  {len(failed)} check(s) FAILED:")
        for label in failed:
            print(f"    - {label}")
        print()
        sys.exit(1)


if __name__ == "__main__":
    main()
