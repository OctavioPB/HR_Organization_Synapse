"""Unit tests for ml.anomaly.isolation_forest — no database required.

Tests cover run_isolation_forest() with synthetic feature dicts.
write_anomaly_alerts() is not tested here (requires DB; covered by integration tests).
"""

import pytest

from ml.anomaly.isolation_forest import FEATURE_KEYS, run_isolation_forest


def _make_feature(employee_id: str, **overrides) -> dict:
    """Create a feature dict with all FEATURE_KEYS at 0.5; overrides applied on top."""
    fv: dict = {k: 0.5 for k in FEATURE_KEYS}
    fv["employee_id"] = employee_id
    fv.update(overrides)
    return fv


def _normal_employee(employee_id: str) -> dict:
    """Feature profile representative of a well-connected employee."""
    return _make_feature(
        employee_id,
        betweenness=0.6,
        degree_in=0.5,
        degree_out=0.7,
        clustering=0.4,
        betweenness_delta_7d=0.02,
        degree_out_delta_7d=0.01,
        entropy_current=2.0,
        entropy_trend=0.05,
    )


def _outlier_employee(employee_id: str) -> dict:
    """Feature profile of a disconnecting / withdrawing employee."""
    return _make_feature(
        employee_id,
        betweenness=0.0,
        degree_in=0.0,
        degree_out=0.0,
        clustering=0.0,
        betweenness_delta_7d=-0.5,
        degree_out_delta_7d=-0.5,
        entropy_current=0.0,
        entropy_trend=-1.0,
    )


# ─── Edge cases ───────────────────────────────────────────────────────────────


def test_run_isolation_forest_empty_input():
    assert run_isolation_forest([]) == []


def test_run_isolation_forest_single_employee_no_crash():
    """1 employee cannot be fitted; must return safely without raising."""
    result = run_isolation_forest([_make_feature("E0")])
    assert len(result) == 1
    assert result[0]["employee_id"] == "E0"
    assert result[0]["is_anomaly"] is False


# ─── Output structure ─────────────────────────────────────────────────────────


def test_run_isolation_forest_output_length():
    features = [_make_feature(f"E{i}") for i in range(10)]
    results = run_isolation_forest(features, contamination=0.1)
    assert len(results) == 10


def test_run_isolation_forest_output_keys():
    features = [_make_feature(f"E{i}") for i in range(5)]
    results = run_isolation_forest(features, contamination=0.1)
    for r in results:
        assert "employee_id" in r
        assert "anomaly_score" in r
        assert "is_anomaly" in r
        assert "raw_decision_score" in r


def test_run_isolation_forest_employee_ids_preserved():
    features = [_make_feature(f"EMP-{i}") for i in range(8)]
    results = run_isolation_forest(features, contamination=0.1)
    assert {r["employee_id"] for r in results} == {f"EMP-{i}" for i in range(8)}


# ─── Score properties ─────────────────────────────────────────────────────────


def test_run_isolation_forest_scores_in_01():
    features = [_make_feature(f"E{i}") for i in range(20)]
    results = run_isolation_forest(features, contamination=0.1)
    for r in results:
        assert 0.0 <= r["anomaly_score"] <= 1.0, (
            f"Employee {r['employee_id']} score {r['anomaly_score']} out of [0, 1]"
        )


def test_run_isolation_forest_is_anomaly_is_bool():
    features = [_make_feature(f"E{i}") for i in range(10)]
    results = run_isolation_forest(features, contamination=0.1)
    for r in results:
        assert isinstance(r["is_anomaly"], bool)


def test_run_isolation_forest_anomaly_fraction_matches_contamination():
    """contamination=0.1 on a population with variance → expect ~10% flagged."""
    import random as _random
    rng = _random.Random(0)
    # Varied features so StandardScaler has non-zero variance to work with
    features = [
        _make_feature(
            f"E{i}",
            betweenness=rng.uniform(0.1, 0.9),
            degree_out=rng.uniform(0.1, 0.9),
            entropy_current=rng.uniform(0.5, 2.5),
            entropy_trend=rng.uniform(-0.3, 0.3),
        )
        for i in range(20)
    ]
    results = run_isolation_forest(features, contamination=0.1, random_state=42)
    anomaly_count = sum(1 for r in results if r["is_anomaly"])
    assert 1 <= anomaly_count <= 3, (
        f"Expected ~2 anomalies (10% of 20), got {anomaly_count}"
    )


# ─── Anomaly detection quality ────────────────────────────────────────────────


def test_run_isolation_forest_outlier_gets_higher_score():
    """Clear outlier must have higher anomaly_score than the average normal employee."""
    normal = [_normal_employee(f"N{i}") for i in range(18)]
    outlier = _outlier_employee("OUTLIER")
    results = run_isolation_forest(normal + [outlier], contamination=0.1, random_state=42)

    outlier_score = next(r["anomaly_score"] for r in results if r["employee_id"] == "OUTLIER")
    avg_normal = sum(
        r["anomaly_score"] for r in results if r["employee_id"] != "OUTLIER"
    ) / 18

    assert outlier_score > avg_normal, (
        f"Outlier score {outlier_score:.3f} should exceed average normal {avg_normal:.3f}"
    )


def test_run_isolation_forest_outlier_is_flagged():
    """A clear outlier surrounded by similar normal employees must be is_anomaly=True."""
    normal = [_normal_employee(f"N{i}") for i in range(18)]
    outlier = _outlier_employee("OUTLIER")
    results = run_isolation_forest(normal + [outlier], contamination=0.1, random_state=42)

    outlier_result = next(r for r in results if r["employee_id"] == "OUTLIER")
    assert outlier_result["is_anomaly"] is True, (
        f"Outlier should be flagged; score={outlier_result['anomaly_score']:.3f}"
    )


def test_run_isolation_forest_reproducible_with_same_seed():
    """Same seed must produce identical results."""
    features = [_normal_employee(f"N{i}") for i in range(10)] + [_outlier_employee("OUT")]
    r1 = run_isolation_forest(features, contamination=0.1, random_state=99)
    r2 = run_isolation_forest(features, contamination=0.1, random_state=99)

    for a, b in zip(r1, r2):
        assert a["employee_id"] == b["employee_id"]
        assert abs(a["anomaly_score"] - b["anomaly_score"]) < 1e-9
        assert a["is_anomaly"] == b["is_anomaly"]


def test_run_isolation_forest_all_identical_features():
    """When all employees have identical features, no single node is an outlier."""
    features = [_make_feature(f"E{i}") for i in range(20)]
    results = run_isolation_forest(features, contamination=0.1, random_state=42)
    scores = [r["anomaly_score"] for r in results]
    # All scores should be very close (uniform anomaly landscape)
    assert max(scores) - min(scores) < 0.01, (
        f"Expected uniform scores, got range [{min(scores):.3f}, {max(scores):.3f}]"
    )
