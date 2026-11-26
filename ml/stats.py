"""Shared statistical helpers for population-level alerting.

Every detector in the platform runs on every employee (or every group) on every
run.  A per-test significance level therefore produces false alerts in
proportion to headcount.  These helpers turn per-test evidence into a
population-level decision with a controlled false-discovery rate.

Public functions:
    benjamini_hochberg(p_values, q) → bool mask                     (pure)
    bh_adjusted(p_values) → q-values                                 (pure)
    permutation_pvalue(observed, null_draws, greater=True) → float  (pure)
"""

from __future__ import annotations

import numpy as np


def benjamini_hochberg(p_values, q: float) -> np.ndarray:
    """Boolean mask of discoveries controlling the false-discovery rate at *q*.

    Benjamini & Hochberg (1995): sort the m p-values, find the largest k with
    p_(k) ≤ q·k/m and reject the k smallest.  Valid for independent or
    positively dependent tests, which covers the detectors here.
    """
    p = np.asarray(p_values, dtype=float)
    n = len(p)
    if n == 0:
        return np.zeros(0, dtype=bool)
    order = np.argsort(p)
    passed = p[order] <= q * np.arange(1, n + 1) / n
    hits = np.zeros(n, dtype=bool)
    if passed.any():
        hits[order[: int(np.max(np.where(passed)[0])) + 1]] = True
    return hits


def bh_adjusted(p_values) -> np.ndarray:
    """Benjamini–Hochberg adjusted p-values (q-values): reject where q ≤ FDR level.

    q_(i) = min_{j ≥ i} p_(j) · m / j, capped at 1.  Equivalent to
    benjamini_hochberg(p, level) == (q <= level).
    """
    p = np.asarray(p_values, dtype=float)
    m = len(p)
    if m == 0:
        return np.zeros(0)
    order = np.argsort(p)
    scaled = p[order] * m / np.arange(1, m + 1)
    q_sorted = np.minimum.accumulate(scaled[::-1])[::-1]
    q = np.empty(m)
    q[order] = np.minimum(q_sorted, 1.0)
    return q


def permutation_pvalue(observed: float, null_draws, greater: bool = True) -> float:
    """One-sided permutation p-value with the +1 correction (never exactly 0).

    p = (1 + #{null ≥ observed}) / (1 + #null)   for greater=True
    p = (1 + #{null ≤ observed}) / (1 + #null)   for greater=False
    """
    null = np.asarray(null_draws, dtype=float)
    if null.size == 0:
        return 1.0
    hits = np.sum(null >= observed - 1e-12) if greater else np.sum(null <= observed + 1e-12)
    return float((1 + hits) / (1 + null.size))
