"""Task callables for ML anomaly detection — invoked by anomaly_detection_dag.

Plain Python functions, no Airflow dependency, fully unit-testable.
"""

import logging
from datetime import date

logger = logging.getLogger(__name__)


def task_extract_features(snapshot_date_str: str, window_days: int = 30) -> dict:
    """Extract graph feature vectors for all employees on snapshot_date.

    Validates that graph_snapshots data is available for the given date.

    Args:
        snapshot_date_str: ISO date string YYYY-MM-DD.
        window_days: Rolling window for entropy computation.

    Returns:
        JSON-serialisable dict with snapshot_date and feature_rows count.
    """
    from ml.features.feature_extractor import extract_features

    d = date.fromisoformat(snapshot_date_str)
    features = extract_features(d, window_days)

    stats = {
        "snapshot_date": snapshot_date_str,
        "feature_rows": len(features),
    }
    logger.info("task_extract_features: %s", stats)
    return stats


def task_run_isolation_forest(
    snapshot_date_str: str,
    window_days: int = 30,
) -> dict:
    """Extract features, run Isolation Forest, and write connectivity_anomaly alerts.

    Self-contained: re-extracts features from DB (no XCom payload dependency).

    Args:
        snapshot_date_str: ISO date string YYYY-MM-DD.
        window_days: Rolling window for entropy computation.

    Returns:
        JSON-serialisable dict with employees_scored and anomalies_detected counts.
    """
    from ml.anomaly.isolation_forest import run_isolation_forest, write_anomaly_alerts
    from ml.features.feature_extractor import extract_features

    d = date.fromisoformat(snapshot_date_str)
    features = extract_features(d, window_days)

    if not features:
        logger.warning(
            "task_run_isolation_forest: no features for %s — skipping", snapshot_date_str
        )
        return {
            "snapshot_date": snapshot_date_str,
            "employees_scored": 0,
            "anomalies_detected": 0,
        }

    results = run_isolation_forest(features)
    write_anomaly_alerts(results, d)

    anomalies_detected = sum(1 for r in results if r["is_anomaly"])
    stats = {
        "snapshot_date": snapshot_date_str,
        "employees_scored": len(results),
        "anomalies_detected": anomalies_detected,
    }
    logger.info("task_run_isolation_forest: %s", stats)
    return stats


def task_summarise_anomalies(anomaly_stats: dict) -> dict:
    """Log and pass through the anomaly detection summary.

    Acts as a terminal task in the DAG — no DB writes.

    Args:
        anomaly_stats: Output dict from task_run_isolation_forest.

    Returns:
        Same dict (pass-through for XCom chain).
    """
    anomalies = anomaly_stats.get("anomalies_detected", 0)
    scored = anomaly_stats.get("employees_scored", 0)
    snapshot = anomaly_stats.get("snapshot_date", "unknown")

    if anomalies > 0:
        logger.warning(
            "task_summarise_anomalies: %d/%d employees flagged as anomalies on %s",
            anomalies, scored, snapshot,
        )
    else:
        logger.info(
            "task_summarise_anomalies: no connectivity anomalies on %s (%d employees scored)",
            snapshot, scored,
        )
    return anomaly_stats
