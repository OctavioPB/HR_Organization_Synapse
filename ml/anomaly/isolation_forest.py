"""Isolation Forest anomaly detection on employee graph feature vectors.

Production path: conformal p-values against the org's own history
(MATH_MODEL.md §8)
────────────────────────────────────────────────────────────────────
The first version fitted the forest on today's snapshot with
contamination = 0.05 and min-max rescaled the scores.  So:

1. Exactly 5 % of the org was flagged every run, whatever happened.
2. The most unusual person always scored 1.0 ("critical"), even in a quiet
   week, and a 0.9 today was not comparable with a 0.9 last week.

detect_anomalies instead asks "is this person's feature vector unusual
compared with what this org has looked like recently?":

    reference R = logged feature vectors from (t − 63d, t − 7d]   (churn_feature_log)
    split R at random into R_fit (forest + scaler, ~20 %) and R_cal (the rest)
    s(x)  = −decision_function(x)                    higher = more anomalous
    p(v)  = (1 + #{r ∈ R_cal : s(r) ≥ s(v)}) / (1 + |R_cal|)     conformal p-value
    alert ⇔ Benjamini–Hochberg at IF_FDR (0.10) across today's employees

R_cal is not used to fit, so p(v) is valid under exchangeability with the
recent past (Vovk et al. 2005; Bates et al. 2023).  The last 7 days are left
out of R so an ongoing episode does not calibrate itself away.  Severity is
from the BH q-value: critical ≤ 0.01, high ≤ 0.05, else medium.
anomaly_score = 1 − p is comparable across runs.

Without enough history (< IF_MIN_REFERENCE rows, e.g. the first weeks after
deployment) the reference falls back to every third day's snapshot,
recomputed with extract_features.  BH can only flag a lone anomaly when
p ≤ fdr / m, so the calibration set needs roughly m / fdr rows (10 × headcount).

run_isolation_forest (fixed contamination, min-max score) is kept as a
ranking utility for the injected-archetype evaluator; it no longer raises
alerts.

Public functions:
    feature_matrix(features) → ndarray                                            (pure)
    conformal_anomalies(X_today, X_reference, fdr, seed) → (score, p, q, flagged)  (pure)
    detect_anomalies(features, reference) → list[dict]                            (pure)
    load_reference(snapshot_date) → ndarray
    run_isolation_forest(features, contamination, random_state) → list[dict]
    write_anomaly_alerts(anomaly_results, snapshot_date)        → None

CLI:
    python ml/anomaly/isolation_forest.py --snapshot-date 2025-04-25
"""

import argparse
import json
import logging
import os
import sys
import uuid
from datetime import date
from pathlib import Path

import numpy as np
import psycopg2.extras
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ingestion.db import get_conn

logger = logging.getLogger(__name__)

_DEFAULT_CONTAMINATION = float(os.environ.get("IF_CONTAMINATION", "0.05"))
_DEFAULT_N_ESTIMATORS = int(os.environ.get("IF_N_ESTIMATORS", "100"))
IF_FDR = float(os.environ.get("IF_FDR", "0.10"))
IF_MIN_REFERENCE = int(os.environ.get("IF_MIN_REFERENCE", "500"))
IF_REFERENCE_DAYS = int(os.environ.get("IF_REFERENCE_DAYS", "63"))
IF_REFERENCE_GAP_DAYS = int(os.environ.get("IF_REFERENCE_GAP_DAYS", "7"))

# Ordered list of feature keys expected in each feature dict.
# Must match build_feature_vector output in feature_extractor.py.
FEATURE_KEYS: list[str] = [
    "betweenness",
    "degree_in",
    "degree_out",
    "clustering",
    "betweenness_delta_7d",
    "degree_out_delta_7d",
    "entropy_current",
    "entropy_trend",
]


def _severity(score: float) -> str:
    if score > 0.9:
        return "critical"
    if score > 0.75:
        return "high"
    return "medium"


def feature_matrix(features: list[dict]) -> np.ndarray:
    """Feature dicts → matrix in the churn_feature_log representation.

    Deltas and entropy trend are clipped to [−1, 1] and entropy capped at 1,
    exactly as ml/gnn/feature_builder does before logging, so today's vectors
    and the logged reference are on the same scale.
    """
    X = np.array([[float(f.get(k, 0.0) or 0.0) for k in FEATURE_KEYS] for f in features], dtype=float)
    if len(X):
        X[:, 4:6] = np.clip(X[:, 4:6], -1.0, 1.0)
        X[:, 6] = np.minimum(X[:, 6], 1.0)
        X[:, 7] = np.clip(X[:, 7], -1.0, 1.0)
    return X


def conformal_anomalies(
    X_today: np.ndarray,
    X_reference: np.ndarray,
    fdr: float = IF_FDR,
    seed: int = 42,
    n_estimators: int = 200,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Split-conformal Isolation Forest p-values + Benjamini–Hochberg.

    Returns (anomaly_score = 1 − p, p, q, flagged), one entry per row of X_today.
    """
    from ml.stats import bh_adjusted

    X_today = np.asarray(X_today, dtype=float)
    X_ref = np.asarray(X_reference, dtype=float)
    n = len(X_today)
    if n == 0 or len(X_ref) < 20:
        ones = np.ones(n)
        return np.zeros(n), ones, ones, np.zeros(n, dtype=bool)

    # Each tree sees at most 256 rows, so the forest needs few; the calibration set
    # sets the p-value resolution (min p = 1 / (1 + |R_cal|)), and BH needs
    # p ≤ fdr / m to flag a lone anomaly, i.e. |R_cal| ≳ m / fdr.  So most of R
    # goes to calibration.
    rng = np.random.default_rng(seed)
    order = rng.permutation(len(X_ref))
    n_fit = int(min(max(256, 0.2 * len(order)), 5000, len(order) // 2))
    fit, cal = X_ref[order[:n_fit]], X_ref[order[n_fit:]]

    scaler = StandardScaler().fit(fit)
    clf = IsolationForest(n_estimators=n_estimators, random_state=seed).fit(scaler.transform(fit))
    s_cal = np.sort(-clf.decision_function(scaler.transform(cal)))
    s_today = -clf.decision_function(scaler.transform(X_today))

    at_least = len(s_cal) - np.searchsorted(s_cal, s_today - 1e-12, side="left")
    p = (1.0 + at_least) / (1.0 + len(s_cal))
    q = bh_adjusted(p)
    return 1.0 - p, p, q, q <= fdr


def _q_severity(q: float) -> str:
    if q <= 0.01:
        return "critical"
    if q <= 0.05:
        return "high"
    return "medium"


def detect_anomalies(
    features: list[dict],
    reference: np.ndarray,
    fdr: float = IF_FDR,
    seed: int = 42,
) -> list[dict]:
    """Calibrated anomaly results for today's employees (see module docstring)."""
    if not features:
        return []
    score, p, q, flagged = conformal_anomalies(feature_matrix(features), reference, fdr=fdr, seed=seed)
    return [
        {
            "employee_id": f["employee_id"],
            "anomaly_score": float(score[i]),
            "p_value": float(p[i]),
            "q_value": float(q[i]),
            "is_anomaly": bool(flagged[i]),
            "severity": _q_severity(float(q[i])),
        }
        for i, f in enumerate(features)
    ]


def load_reference(snapshot_date: date, window_days: int = 30) -> np.ndarray:
    """Recent feature vectors of this org: churn_feature_log, else weekly snapshots."""
    from datetime import timedelta

    from ml.churn.model import CHURN_FEATURE_NAMES
    from ml.churn.pipeline import FEATURE_VERSION

    cols = [CHURN_FEATURE_NAMES.index(k) for k in FEATURE_KEYS]
    start = snapshot_date - timedelta(days=IF_REFERENCE_DAYS)
    end = snapshot_date - timedelta(days=IF_REFERENCE_GAP_DAYS)
    rows: list = []
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT features FROM churn_feature_log
                    WHERE feature_version = %s AND feature_date > %s AND feature_date <= %s
                    """,
                    (FEATURE_VERSION, start, end),
                )
                rows = [np.asarray(r[0], dtype=float)[cols] for r in cur.fetchall()]
    except Exception as exc:  # table missing before migration 019
        logger.warning("load_reference: feature log unavailable (%s)", exc)

    if len(rows) >= IF_MIN_REFERENCE:
        return np.vstack(rows)

    from ml.features.feature_extractor import extract_features

    # Every 3rd day back to the gap: ~18 snapshots, so |R_cal| ≈ 14 × headcount,
    # enough resolution for BH to flag a single anomaly (see conformal_anomalies).
    past: list[dict] = []
    for k in range(IF_REFERENCE_GAP_DAYS, IF_REFERENCE_DAYS - window_days // 2, 3):
        past += extract_features(snapshot_date - timedelta(days=k), window_days)
    logger.info("load_reference: feature log has %d rows; using %d recomputed snapshot rows", len(rows), len(past))
    return feature_matrix(past) if past else np.zeros((0, len(FEATURE_KEYS)))


def run_isolation_forest(
    features: list[dict],
    contamination: float = _DEFAULT_CONTAMINATION,
    random_state: int = 42,
) -> list[dict]:
    """Fit an Isolation Forest on employee feature vectors and flag anomalies.

    Args:
        features: List of feature dicts from extract_features(). Each dict
                  must contain the keys in FEATURE_KEYS plus 'employee_id'.
        contamination: Expected fraction of anomalies in the population.
        random_state: Seed for reproducibility.

    Returns:
        List of result dicts, one per input employee, each with:
            employee_id, anomaly_score (∈ [0,1]), is_anomaly, raw_decision_score.
        Returns [] for empty input and a no-anomaly list for a single employee
        (IF cannot fit on fewer than 2 samples).
    """
    if not features:
        return []

    employee_ids = [f["employee_id"] for f in features]

    # Single-sample edge case: IsolationForest requires ≥ 2 samples.
    if len(features) == 1:
        logger.warning("run_isolation_forest: only 1 employee — skipping anomaly detection")
        return [
            {
                "employee_id": employee_ids[0],
                "anomaly_score": 0.0,
                "is_anomaly": False,
                "raw_decision_score": 0.0,
            }
        ]

    X = np.array(
        [[f.get(k, 0.0) for k in FEATURE_KEYS] for f in features],
        dtype=float,
    )

    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    clf = IsolationForest(
        n_estimators=_DEFAULT_N_ESTIMATORS,
        contamination=contamination,
        random_state=random_state,
    )
    clf.fit(X_scaled)

    # decision_function: positive = normal, negative = anomaly (sklearn convention)
    raw_scores = clf.decision_function(X_scaled)
    predictions = clf.predict(X_scaled)  # -1 = anomaly, 1 = normal

    # Normalise to [0, 1] where 1 = most anomalous
    min_s, max_s = raw_scores.min(), raw_scores.max()
    if max_s > min_s:
        normalised = 1.0 - (raw_scores - min_s) / (max_s - min_s)
    else:
        normalised = np.zeros_like(raw_scores)

    results = [
        {
            "employee_id": emp_id,
            "anomaly_score": float(normalised[i]),
            "is_anomaly": bool(predictions[i] == -1),
            "raw_decision_score": float(raw_scores[i]),
        }
        for i, emp_id in enumerate(employee_ids)
    ]

    anomaly_count = sum(1 for r in results if r["is_anomaly"])
    logger.info(
        "run_isolation_forest: %d employees, %d anomalies (contamination=%.2f)",
        len(results),
        anomaly_count,
        contamination,
    )
    return results


def _alert_text(a: dict, snapshot_date: date) -> str:
    who = f"Connectivity anomaly: employee {a['employee_id'][:8]}… "
    if "p_value" in a:
        return (
            who + f"is more unusual than {100 * (1 - a['p_value']):.1f}% of this org's recent profiles "
            f"(false-discovery-rate controlled) on {snapshot_date}"
        )
    return who + f"score={a['anomaly_score']:.3f} on {snapshot_date}"


def write_anomaly_alerts(
    anomaly_results: list[dict],
    snapshot_date: date,
) -> None:
    """Persist connectivity anomaly alerts to the alerts table.

    Only anomalous employees (is_anomaly=True) produce rows.

    Args:
        anomaly_results: Output of run_isolation_forest().
        snapshot_date: Snapshot date label for the alert details.
    """
    anomalies = [r for r in anomaly_results if r["is_anomaly"]]

    if not anomalies:
        logger.info("write_anomaly_alerts: no anomalies to write for %s", snapshot_date)
        return

    rows = [
        (
            str(uuid.uuid4()),
            "connectivity_anomaly",
            a.get("severity") or _severity(a["anomaly_score"]),
            json.dumps(
                {
                    "employee_id": a["employee_id"],
                    "anomaly_score": round(a["anomaly_score"], 4),
                    "p_value": round(a["p_value"], 6) if "p_value" in a else None,
                    "q_value": round(a["q_value"], 6) if "q_value" in a else None,
                    "snapshot_date": snapshot_date.isoformat(),
                }
            ),
            _alert_text(a, snapshot_date),
        )
        for a in anomalies
    ]

    with get_conn() as conn:
        with conn.cursor() as cur:
            # One open alert per person per week: the same episode is re-detected daily.
            cur.execute(
                """
                SELECT DISTINCT affected_entities->>'employee_id'
                FROM alerts
                WHERE type = 'connectivity_anomaly' AND resolved = false
                  AND fired_at >= %s::date - INTERVAL '7 days'
                """,
                (snapshot_date,),
            )
            already = {r[0] for r in cur.fetchall()}
            rows = [r for r in rows if json.loads(r[3])["employee_id"] not in already]
            if not rows:
                return
            psycopg2.extras.execute_batch(
                cur,
                """
                INSERT INTO alerts (id, type, severity, affected_entities, details)
                VALUES (%s, %s, %s, %s::jsonb, %s)
                """,
                rows,
            )

    critical_count = sum(1 for a in anomalies if (a.get("severity") or _severity(a["anomaly_score"])) == "critical")
    logger.info(
        "write_anomaly_alerts: wrote %d alerts for %s (critical=%d)",
        len(rows),
        snapshot_date,
        critical_count,
    )


# ─── CLI ──────────────────────────────────────────────────────────────────────


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="Run Isolation Forest anomaly detection and write alerts.")
    parser.add_argument("--snapshot-date", type=date.fromisoformat, required=True)
    parser.add_argument(
        "--window-days",
        type=int,
        default=int(os.environ.get("GRAPH_WINDOW_DAYS", "30")),
    )
    parser.add_argument(
        "--contamination",
        type=float,
        default=_DEFAULT_CONTAMINATION,
    )
    args = parser.parse_args()

    from ml.features.feature_extractor import extract_features

    features = extract_features(args.snapshot_date, args.window_days)

    if not features:
        logger.warning("No features found for %s — skipping anomaly detection.", args.snapshot_date)
        return

    results = detect_anomalies(features, load_reference(args.snapshot_date, args.window_days))
    anomaly_count = sum(1 for r in results if r["is_anomaly"])
    logger.info("Anomalies detected: %d / %d", anomaly_count, len(results))

    write_anomaly_alerts(results, args.snapshot_date)
    logger.info("Done for %s.", args.snapshot_date)


if __name__ == "__main__":
    main()
