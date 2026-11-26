"""Calibrated churn model: point-in-time examples, purged temporal split, held-out calibration check.

Why this replaces the ChurnGAT as the default (MATH_MODEL.md §9)
──────────────────────────────────────────────────────────────────
1. On the planted-contagion validation org a 3-feature logistic regression beat
   the graph model (AUROC 0.72 vs 0.62).  A graph model has to *earn* its place
   against this baseline on held-out data (select_champion), not be assumed.
2. The GAT trainer could not train at all: it stamped every label with the same
   date, so the temporal split put every example in the test fold and aborted
   with zero training examples.  Its features came from today's snapshot of
   *active* employees, so people who had left were never in the data.
3. Its probabilities were never calibrated, so "0.7 churn risk" had no tested
   meaning.

Model
─────
Example (v, t): employee v at risk at anchor date t (hired ≤ t, not departed
by t).  Features are computed as of t only.  Label:

    y(v, t) = 1  ⇔  v departs in (t, t + H]          H = CHURN_HORIZON_DAYS (90)

Anchors whose window has not fully elapsed (t + H > as_of) are not labelled.

Split by anchor date, never by row: the latest anchors are the test fold, the
rest train.  Training anchors whose label window reaches the test fold are
purged, so no training label is decided by events inside the test period.
Reported on the test fold: AUROC, PR-AUC, Brier, Brier skill vs. the base
rate, and expected calibration error (ECE).  The production model is then
refit on every labelled row, so the reported numbers are a slightly
conservative estimate of it.

Calibration is *checked*, not patched.  An unweighted, lightly regularised LR
is calibrated by construction (its score equations match predicted and
observed totals), and on the forward-simulated org its held-out ECE is 0.011.
A separate recalibration fold does not fit anyway: with 90-day labels and
14-day anchors, purging leaves it empty for any history shorter than ~2 years.
Class reweighting (the GAT's pos_weight) is not used: it does nothing for
ranking and pushed held-out ECE to 0.23 in the same simulation (0.011 unweighted).

Risk tiers are relative to the base rate, because a calibrated 90-day
probability for a ~15 %/year attrition org rarely exceeds 0.3:

    high    p̂ ≥ CHURN_HIGH_LIFT (3.0) × base rate
    medium  p̂ ≥ CHURN_MEDIUM_LIFT (1.5) × base rate

Public API:
    at_risk_labels(...)          → [(employee_id, y)]            (pure)
    temporal_folds(anchors, ...) → (train, test) masks           (pure)
    calibration_error(y, p)      → ECE                           (pure)
    fit_churn_model(X, y, anchors, ...) → ChurnModel             (pure)
    select_champion(base, challenger, ...) → "baseline" | "challenger"
    risk_tier(p, base_rate)      → "high" | "medium" | "low"
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np

CHURN_HORIZON_DAYS = int(os.environ.get("CHURN_HORIZON_DAYS", "90"))
CHURN_MIN_POSITIVES = int(os.environ.get("CHURN_MIN_POSITIVES", "20"))
CHURN_HIGH_LIFT = float(os.environ.get("CHURN_HIGH_LIFT", "3.0"))
CHURN_MEDIUM_LIFT = float(os.environ.get("CHURN_MEDIUM_LIFT", "1.5"))
CHURN_LR_C = float(os.environ.get("CHURN_LR_C", "1.0"))
CHURN_CHALLENGER_MIN_GAIN = float(os.environ.get("CHURN_CHALLENGER_MIN_GAIN", "0.02"))

# Features fed to the model, by column of ml/gnn/feature_builder._build_node_features
# (+ peer exposure appended).  PTO (column 2) is excluded: the employees table
# only holds its *current* value, so for a past anchor it would leak information
# from after the anchor (a departed person's PTO is frozen at departure).
CHURN_FEATURE_NAMES: list[str] = [
    "tenure",
    "role_level",
    "betweenness",
    "degree_in",
    "degree_out",
    "clustering",
    "betweenness_delta_7d",
    "degree_out_delta_7d",
    "entropy_current",
    "entropy_trend",
    "peer_exposure",
]
NODE_FEATURE_COLUMNS: list[int] = [0, 1, 3, 4, 5, 6, 7, 8, 9, 10]


# ─── Labels and folds ─────────────────────────────────────────────────────────


def at_risk_labels(
    employees: list[dict],
    departures: dict[str, date],
    anchor: date,
    horizon_days: int = CHURN_HORIZON_DAYS,
) -> list[tuple[str, int]]:
    """Employees at risk at *anchor* with their horizon label.

    employees: dicts with "id" and optional "hire_date".
    departures: employee_id → departure date (absent = still employed).
    """
    end = anchor + timedelta(days=horizon_days)
    out: list[tuple[str, int]] = []
    for emp in employees:
        emp_id = str(emp["id"])
        hire = emp.get("hire_date")
        if hire is not None and hire > anchor:
            continue
        left = departures.get(emp_id)
        if left is not None and left <= anchor:
            continue
        out.append((emp_id, int(left is not None and left <= end)))
    return out


def temporal_folds(
    anchors: np.ndarray,
    horizon_days: int = CHURN_HORIZON_DAYS,
    test_frac: float = 0.25,
) -> tuple[np.ndarray, np.ndarray]:
    """Row masks (train, test) split by anchor date, with purging.

    *anchors* holds each row's anchor as a date ordinal.  The last ⌈test_frac⌉
    of the unique anchors form the test fold.  A training row whose label
    window (anchor, anchor + H] reaches the first test anchor is dropped.
    """
    a = np.asarray(anchors, dtype=int)
    uniq = np.unique(a)
    k = len(uniq)
    if k < 2:
        empty = np.zeros(len(a), dtype=bool)
        return empty, empty
    n_test = min(k - 1, max(1, math.ceil(k * test_frac)))
    test_start = uniq[k - n_test]
    test = a >= test_start
    train = a + horizon_days < test_start
    return train, test


# ─── Metrics ──────────────────────────────────────────────────────────────────


def _auroc(y: np.ndarray, s: np.ndarray) -> float:
    """Mann–Whitney AUROC with average ranks for ties."""
    y = np.asarray(y, dtype=int)
    s = np.asarray(s, dtype=float)
    n_pos = int(y.sum())
    n_neg = len(y) - n_pos
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    order = np.argsort(s, kind="mergesort")
    ranks = np.empty(len(s), dtype=float)
    sorted_s = s[order]
    i = 0
    while i < len(s):
        j = i
        while j + 1 < len(s) and sorted_s[j + 1] == sorted_s[i]:
            j += 1
        ranks[order[i : j + 1]] = (i + j) / 2.0 + 1.0
        i = j + 1
    return float((ranks[y == 1].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg))


def _average_precision(y: np.ndarray, s: np.ndarray) -> float:
    y = np.asarray(y, dtype=int)
    if y.sum() == 0:
        return float("nan")
    order = np.argsort(-np.asarray(s, dtype=float), kind="mergesort")
    hits = y[order]
    precision = np.cumsum(hits) / np.arange(1, len(hits) + 1)
    return float(np.sum(precision * hits) / hits.sum())


def calibration_error(y, p, n_bins: int = 10) -> float:
    """Expected calibration error over equal-frequency bins.

    ECE = Σ_b (n_b / n) · |mean(y_b) − mean(p_b)|
    Equal-frequency bins keep each bin populated when probabilities are small.
    """
    y = np.asarray(y, dtype=float)
    p = np.asarray(p, dtype=float)
    if len(y) == 0:
        return float("nan")
    order = np.argsort(p, kind="mergesort")
    ece = 0.0
    for chunk in np.array_split(order, min(n_bins, len(y))):
        if len(chunk) == 0:
            continue
        ece += len(chunk) / len(y) * abs(y[chunk].mean() - p[chunk].mean())
    return float(ece)


def evaluate_probabilities(y, p, base_rate: float) -> dict:
    """AUROC, PR-AUC, Brier, Brier skill vs. a constant base-rate forecast, ECE."""
    y = np.asarray(y, dtype=float)
    p = np.asarray(p, dtype=float)
    brier = float(np.mean((p - y) ** 2)) if len(y) else float("nan")
    brier_ref = float(np.mean((base_rate - y) ** 2)) if len(y) else float("nan")
    skill = 1.0 - brier / brier_ref if brier_ref and brier_ref > 0 else float("nan")
    return {
        "n": int(len(y)),
        "positives": int(y.sum()),
        "auroc": _auroc(y, p),
        "pr_auc": _average_precision(y, p),
        "brier": brier,
        "brier_skill": skill,
        "ece": calibration_error(y, p),
    }


# ─── Model ────────────────────────────────────────────────────────────────────


def _logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


@dataclass
class ChurnModel:
    """Standardised logistic regression."""

    mean: np.ndarray
    scale: np.ndarray
    coef: np.ndarray
    intercept: float
    base_rate: float = 0.0
    horizon_days: int = CHURN_HORIZON_DAYS
    feature_names: list[str] = field(default_factory=lambda: list(CHURN_FEATURE_NAMES))
    metrics: dict = field(default_factory=dict)

    def _raw(self, X: np.ndarray) -> np.ndarray:
        z = (np.asarray(X, dtype=float) - self.mean) / self.scale
        return z @ self.coef + self.intercept

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        """Calibrated P(departure within horizon_days)."""
        return 1.0 / (1.0 + np.exp(-self._raw(X)))

    def drivers(self, x: np.ndarray, k: int = 3) -> list[dict]:
        """Top-k features pushing this person's risk up (coef × standardised value)."""
        z = (np.asarray(x, dtype=float) - self.mean) / self.scale
        contrib = self.coef * z
        order = np.argsort(-contrib)
        return [
            {"feature": self.feature_names[i], "contribution": round(float(contrib[i]), 4)}
            for i in order[:k]
            if contrib[i] > 0
        ]


def _fit_lr(X: np.ndarray, y: np.ndarray, C: float):
    from sklearn.linear_model import LogisticRegression

    mean = X.mean(axis=0)
    scale = X.std(axis=0)
    scale[scale < 1e-9] = 1.0
    lr = LogisticRegression(C=C, max_iter=2000)
    lr.fit((X - mean) / scale, y)
    return mean, scale, lr.coef_[0].astype(float), float(lr.intercept_[0])


def fit_churn_model(
    X: np.ndarray,
    y: np.ndarray,
    anchors: np.ndarray,
    horizon_days: int = CHURN_HORIZON_DAYS,
    C: float = CHURN_LR_C,
    min_positives: int = CHURN_MIN_POSITIVES,
) -> ChurnModel | None:
    """Evaluate on the purged held-out fold, then refit on all labelled rows.

    Returns None when the training fold has fewer than *min_positives*
    departures (or stayers): a model fitted on a handful of leavers is noise
    with a probability attached.
    """
    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=int)
    train, test = temporal_folds(anchors, horizon_days)
    n_pos = int(y[train].sum())
    if n_pos < min_positives or (int(train.sum()) - n_pos) < min_positives:
        return None

    mean, scale, coef, intercept = _fit_lr(X[train], y[train], C)
    held_out = ChurnModel(mean, scale, coef, intercept, horizon_days=horizon_days)
    base_rate = float(y[train].mean())
    metrics: dict = {"train": {"n": int(train.sum()), "positives": n_pos}}
    if test.any():
        metrics["test"] = evaluate_probabilities(y[test], held_out.predict_proba(X[test]), base_rate)

    mean, scale, coef, intercept = _fit_lr(X, y, C)
    model = ChurnModel(mean, scale, coef, intercept, horizon_days=horizon_days)
    model.base_rate = float(y.mean())
    model.metrics = metrics
    return model


def select_champion(
    baseline: dict,
    challenger: dict | None,
    min_gain: float = CHURN_CHALLENGER_MIN_GAIN,
) -> str:
    """Pick the graph challenger only if it wins on held-out data on both counts.

    The challenger must beat the baseline's test AUROC by at least *min_gain*
    and must not have a worse Brier score.  Ties and missing metrics keep the
    baseline: complexity has to earn its place.
    """
    if not challenger:
        return "baseline"
    try:
        gain = challenger["auroc"] - baseline["auroc"]
        brier_ok = challenger["brier"] <= baseline["brier"]
    except (KeyError, TypeError):
        return "baseline"
    if not (np.isfinite(gain) and gain >= min_gain and brier_ok):
        return "baseline"
    return "challenger"


def risk_tier(
    prob: float,
    base_rate: float,
    high_lift: float = CHURN_HIGH_LIFT,
    medium_lift: float = CHURN_MEDIUM_LIFT,
) -> str:
    """Tier by lift over the base rate (see module docstring)."""
    if base_rate <= 0:
        return "low"
    if prob >= high_lift * base_rate:
        return "high"
    if prob >= medium_lift * base_rate:
        return "medium"
    return "low"
