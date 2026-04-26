"""Unit tests for ml.features.feature_extractor — no database required.

Tests cover all pure computation functions:
  - compute_entropy: Shannon entropy of partner distributions
  - compute_entropy_trend: linear slope of weekly entropy sequences
  - build_feature_vector: assembles the feature dict consumed by IsolationForest
"""

import math

import pytest

from ml.features.feature_extractor import (
    build_feature_vector,
    compute_entropy,
    compute_entropy_trend,
)

_EXPECTED_FEATURE_KEYS = {
    "betweenness",
    "degree_in",
    "degree_out",
    "clustering",
    "betweenness_delta_7d",
    "degree_out_delta_7d",
    "entropy_current",
    "entropy_trend",
}


# ─── compute_entropy ──────────────────────────────────────────────────────────


def test_entropy_empty_returns_zero():
    assert compute_entropy({}) == 0.0


def test_entropy_single_partner_is_zero():
    """All interactions with one person → no uncertainty → H = 0."""
    assert compute_entropy({"A": 100}) == 0.0


def test_entropy_two_equal_partners():
    """Uniform over 2 partners → H = log₂(2) = 1.0 bit."""
    result = compute_entropy({"A": 50, "B": 50})
    assert abs(result - 1.0) < 1e-9


def test_entropy_four_equal_partners():
    """Uniform over 4 partners → H = log₂(4) = 2.0 bits."""
    result = compute_entropy({"A": 1, "B": 1, "C": 1, "D": 1})
    assert abs(result - 2.0) < 1e-9


def test_entropy_is_nonnegative():
    result = compute_entropy({"A": 10, "B": 3, "C": 7})
    assert result >= 0.0


def test_entropy_more_partners_higher():
    """More equally-distributed partners → higher entropy."""
    e2 = compute_entropy({"A": 50, "B": 50})
    e4 = compute_entropy({"A": 25, "B": 25, "C": 25, "D": 25})
    assert e4 > e2


def test_entropy_skewed_distribution():
    """Heavily skewed → low entropy but not zero."""
    e = compute_entropy({"A": 95, "B": 5})
    assert 0.0 < e < 1.0


def test_entropy_zero_count_entries_ignored():
    """Entries with count 0 must not produce NaN (log 0 undefined)."""
    result = compute_entropy({"A": 1, "B": 0, "C": 1})
    assert math.isfinite(result)
    assert abs(result - 1.0) < 1e-9


# ─── compute_entropy_trend ────────────────────────────────────────────────────


def test_entropy_trend_declining_is_negative():
    """Monotonically declining entropy → withdrawing employee → slope < 0."""
    slope = compute_entropy_trend([3.0, 2.0, 1.0, 0.0])
    assert slope < 0


def test_entropy_trend_increasing_is_positive():
    slope = compute_entropy_trend([0.0, 1.0, 2.0, 3.0])
    assert slope > 0


def test_entropy_trend_flat_is_zero():
    slope = compute_entropy_trend([2.0, 2.0, 2.0, 2.0])
    assert abs(slope) < 1e-9


def test_entropy_trend_empty_returns_zero():
    assert compute_entropy_trend([]) == 0.0


def test_entropy_trend_single_value_returns_zero():
    assert compute_entropy_trend([1.5]) == 0.0


def test_entropy_trend_two_points():
    """slope of [0, 1] over indices [0, 1] = 1.0."""
    slope = compute_entropy_trend([0.0, 1.0])
    assert abs(slope - 1.0) < 1e-9


def test_entropy_trend_magnitude_proportional_to_drop():
    """Larger weekly drop → larger negative slope magnitude."""
    slow_decline = compute_entropy_trend([3.0, 2.5, 2.0, 1.5])
    fast_decline = compute_entropy_trend([3.0, 2.0, 1.0, 0.0])
    assert abs(fast_decline) > abs(slow_decline)


# ─── build_feature_vector ─────────────────────────────────────────────────────


def _current() -> dict:
    return {"betweenness": 0.5, "degree_in": 0.3, "degree_out": 0.4, "clustering": 0.6}


def test_build_feature_vector_expected_keys():
    fv = build_feature_vector(_current(), prev=None, entropy_current=1.5, entropy_trend=-0.2)
    assert set(fv.keys()) == _EXPECTED_FEATURE_KEYS


def test_build_feature_vector_values_passthrough():
    fv = build_feature_vector(_current(), prev=None, entropy_current=2.5, entropy_trend=-0.3)
    assert abs(fv["betweenness"] - 0.5) < 1e-9
    assert abs(fv["degree_in"] - 0.3) < 1e-9
    assert abs(fv["degree_out"] - 0.4) < 1e-9
    assert abs(fv["clustering"] - 0.6) < 1e-9
    assert abs(fv["entropy_current"] - 2.5) < 1e-9
    assert abs(fv["entropy_trend"] - (-0.3)) < 1e-9


def test_build_feature_vector_delta_correct():
    prev = {"betweenness": 0.3, "degree_out": 0.2}
    fv = build_feature_vector(_current(), prev=prev, entropy_current=1.0, entropy_trend=0.0)
    assert abs(fv["betweenness_delta_7d"] - 0.2) < 1e-9
    assert abs(fv["degree_out_delta_7d"] - 0.2) < 1e-9


def test_build_feature_vector_no_prior_delta_is_zero():
    """When prev=None (first snapshot), deltas must be 0, not the current value."""
    fv = build_feature_vector(_current(), prev=None, entropy_current=1.0, entropy_trend=0.0)
    assert fv["betweenness_delta_7d"] == 0.0
    assert fv["degree_out_delta_7d"] == 0.0


def test_build_feature_vector_empty_prev_delta_is_zero():
    """prev={} behaves the same as prev=None."""
    fv = build_feature_vector(_current(), prev={}, entropy_current=1.0, entropy_trend=0.0)
    assert fv["betweenness_delta_7d"] == 0.0
    assert fv["degree_out_delta_7d"] == 0.0


def test_build_feature_vector_negative_delta():
    """Drop in betweenness between snapshots → negative delta."""
    prev = {"betweenness": 0.8, "degree_out": 0.7}
    fv = build_feature_vector(_current(), prev=prev, entropy_current=0.5, entropy_trend=-0.1)
    assert fv["betweenness_delta_7d"] < 0
    assert fv["degree_out_delta_7d"] < 0


def test_build_feature_vector_all_zeros_is_valid():
    """All-zero feature vector must not raise."""
    fv = build_feature_vector(
        current={"betweenness": 0.0, "degree_in": 0.0, "degree_out": 0.0, "clustering": 0.0},
        prev={},
        entropy_current=0.0,
        entropy_trend=0.0,
    )
    assert all(v == 0.0 for v in fv.values())
