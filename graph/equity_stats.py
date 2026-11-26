"""Structural-equity tests: is a group's network position lower than everyone else's?

Old rule (MODEL.md §11.1 v2): flag a group if
    median(group) < 0.8 × median over the *group medians*
Three defects:

1. **Wrong reference.**  The median of group medians is not the organisation's
   median; with two groups it is their average, whatever their sizes.
2. **Never fires on betweenness.**  Most people broker no shortest paths, so the
   median betweenness is usually 0 and "< 0.8 × 0" is impossible.
3. **No noise model.**  A median of 5–10 people is very noisy, and one run
   tests 3 dimensions × 2 metrics × every group, so chance flags were certain
   in a large org — about protected groups, where a false flag costs trust.

Test (per dimension D, metric m, reported group g)
──────────────────────────────────────────────────
Reference = every consenting employee with a value for D who is *not* in g.

    ratio(g) = mean(X | g) / mean(X | not g)             test statistic and gap size
    A(g)     = P(X_member > X_non-member) + ½ P(tie)     reported (Mann–Whitney; 0.5 = parity)

Null: group labels shuffled across the same people (group sizes kept).
p(g) = one-sided permutation p-value of ratio(g) being this low.  q-values
are Benjamini–Hochberg adjusted across every test in the run.

    flag  ⇔  q ≤ EQUITY_FDR (0.10)  and  ratio < EQUITY_DISPARITY_RATIO (0.8)

Why a ratio of means and not of medians or ranks: betweenness is zero for
most people, so medians are 0 and a rank statistic barely moves when the
brokers in a group broker 40 % less (validation: rank test 0 % power on a
planted 40 % betweenness gap, mean ratio ≥ 50 %).  The mean is the group's
share of brokerage per head, which is the quantity that matters; the
permutation null makes the test exact despite heavy tails.

The 0.8 ratio is kept as the size-of-gap criterion, still an investigative
heuristic by analogy with the EEOC four-fifths rule, not a legal standard.
The permutation test only adds "and it is not plausibly chance".

Privacy: individual values are used in memory only.  Groups smaller than
min_group_size are never returned (k-anonymity); their members still count in
the reference of every other group, as they are part of the organisation.
The reference must also have at least min_group_size people: otherwise the
reported ratio would reveal the median of a handful of named non-members.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import numpy as np

from ml.stats import bh_adjusted

EQUITY_FDR = float(os.environ.get("EQUITY_FDR", "0.10"))
EQUITY_PERMUTATIONS = int(os.environ.get("EQUITY_PERMUTATIONS", "10000"))
EQUITY_DISPARITY_RATIO = float(os.environ.get("EQUITY_DISPARITY_RATIO", "0.8"))


@dataclass
class GroupTest:
    dimension: str
    metric: str
    group: str
    n: int
    median: float
    p25: float
    p75: float
    mean_ratio: float | None
    effect_size: float
    p_value: float
    q_value: float = 1.0
    flagged: bool = False


def _average_ranks(x: np.ndarray) -> np.ndarray:
    order = np.argsort(x, kind="mergesort")
    ranks = np.empty(len(x))
    sx = x[order]
    i = 0
    while i < len(x):
        j = i
        while j + 1 < len(x) and sx[j + 1] == sx[i]:
            j += 1
        ranks[order[i : j + 1]] = (i + j) / 2.0 + 1.0
        i = j + 1
    return ranks


def group_tests(
    values,
    groups,
    dimension: str,
    metric: str,
    min_group_size: int,
    n_perm: int = EQUITY_PERMUTATIONS,
    seed: int = 0,
) -> list[GroupTest]:
    """Permutation tests for every reportable group of one dimension × metric (no FDR yet)."""
    x = np.asarray(values, dtype=float)
    g = np.asarray(groups, dtype=object)
    n = len(x)
    labels, codes = np.unique(g, return_inverse=True)
    sizes = np.bincount(codes, minlength=len(labels))
    if n < 2 or len(labels) < 2:
        return []

    ranks = _average_ranks(x)
    with np.errstate(divide="ignore", invalid="ignore"):
        a_stat = (np.bincount(codes, weights=ranks, minlength=len(labels)) - sizes * (sizes + 1) / 2) / (
            sizes * (n - sizes)
        )

    total = x.sum()

    def mean_ratio(sums: np.ndarray) -> np.ndarray:
        with np.errstate(divide="ignore", invalid="ignore"):
            return (sums / sizes) / ((total - sums) / (n - sizes))

    observed = mean_ratio(np.bincount(codes, weights=x, minlength=len(labels)))

    rng = np.random.default_rng(seed)
    hits = np.zeros(len(labels))
    for _ in range(n_perm):
        perm = rng.permutation(codes)
        hits += mean_ratio(np.bincount(perm, weights=x, minlength=len(labels))) <= observed + 1e-12
    p = (1.0 + hits) / (1.0 + n_perm)

    out: list[GroupTest] = []
    for k, label in enumerate(labels):
        if sizes[k] < min_group_size or n - sizes[k] < min_group_size:
            continue  # the reference ("everyone else") must be k-anonymous too
        member = codes == k
        med = float(np.median(x[member]))
        ratio = float(observed[k])
        out.append(
            GroupTest(
                dimension=dimension,
                metric=metric,
                group=str(label),
                n=int(sizes[k]),
                median=med,
                p25=float(np.percentile(x[member], 25)),
                p75=float(np.percentile(x[member], 75)),
                mean_ratio=ratio if np.isfinite(ratio) else None,
                effect_size=float(a_stat[k]),
                p_value=float(p[k]),
            )
        )
    return out


def apply_fdr(
    tests: list[GroupTest],
    fdr: float = EQUITY_FDR,
    disparity_ratio: float = EQUITY_DISPARITY_RATIO,
) -> list[GroupTest]:
    """Benjamini–Hochberg across *all* tests of a run, then the size-of-gap criterion."""
    if not tests:
        return tests
    q = bh_adjusted([t.p_value for t in tests])
    for t, qv in zip(tests, q, strict=True):
        t.q_value = float(qv)
        t.flagged = bool(qv <= fdr and t.mean_ratio is not None and t.mean_ratio < disparity_ratio)
    return tests
