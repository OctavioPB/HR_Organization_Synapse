"""Isolation Forest anomaly detection on employee graph feature vectors.

Detects employees with unusually low connectivity or sudden drops in
interaction diversity relative to their peers in the same snapshot.

The anomaly score is normalised to [0, 1] where 1 = most anomalous.
Severity thresholds:
    score > 0.9 → critical
    score > 0.75 → high
    otherwise   → medium

Public functions:
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
        len(results), anomaly_count, contamination,
    )
    return results


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
            _severity(a["anomaly_score"]),
            json.dumps(
                {
                    "employee_id": a["employee_id"],
                    "anomaly_score": round(a["anomaly_score"], 4),
                    "snapshot_date": snapshot_date.isoformat(),
                }
            ),
            (
                f"Connectivity anomaly: employee {a['employee_id'][:8]}… "
                f"score={a['anomaly_score']:.3f} on {snapshot_date}"
            ),
        )
        for a in anomalies
    ]

    with get_conn() as conn:
        with conn.cursor() as cur:
            psycopg2.extras.execute_batch(
                cur,
                """
                INSERT INTO alerts (id, type, severity, affected_entities, details)
                VALUES (%s, %s, %s, %s::jsonb, %s)
                """,
                rows,
            )

    critical_count = sum(1 for a in anomalies if _severity(a["anomaly_score"]) == "critical")
    logger.info(
        "write_anomaly_alerts: wrote %d alerts for %s (critical=%d)",
        len(rows), snapshot_date, critical_count,
    )


# ─── CLI ──────────────────────────────────────────────────────────────────────


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    parser = argparse.ArgumentParser(
        description="Run Isolation Forest anomaly detection and write alerts."
    )
    parser.add_argument("--snapshot-date", type=date.fromisoformat, required=True)
    parser.add_argument(
        "--window-days", type=int,
        default=int(os.environ.get("GRAPH_WINDOW_DAYS", "30")),
    )
    parser.add_argument(
        "--contamination", type=float,
        default=_DEFAULT_CONTAMINATION,
    )
    args = parser.parse_args()

    from ml.features.feature_extractor import extract_features

    features = extract_features(args.snapshot_date, args.window_days)

    if not features:
        logger.warning("No features found for %s — skipping anomaly detection.", args.snapshot_date)
        return

    results = run_isolation_forest(features, contamination=args.contamination)
    anomaly_count = sum(1 for r in results if r["is_anomaly"])
    logger.info("Anomalies detected: %d / %d", anomaly_count, len(results))

    write_anomaly_alerts(results, args.snapshot_date)
    logger.info("Done for %s.", args.snapshot_date)


if __name__ == "__main__":
    main()
