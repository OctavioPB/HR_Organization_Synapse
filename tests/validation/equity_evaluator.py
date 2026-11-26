"""Validation: structural-equity flags (graph/equity_stats.py, MATH_MODEL.md §10.5).

Orgs have zero-inflated betweenness (60 % of people broker nothing) and
log-normal degree, the shape real collaboration graphs have.  Three
dimensions are assigned: 2 gender groups, 4 tenure bands, 4 level bands.

    • null (120 people, the demo-org size): labels at random → flags per run.
      Old rule (median < 0.8 × median of group medians) vs. new test.
    • power (400 people): the 0-1y band's degree lowered 40 % / betweenness
      lowered 60 % → share of runs in which that band is flagged.
    • zero-median betweenness: the old rule can never flag it.

Run:
    python tests/validation/equity_evaluator.py
    pytest tests/validation/equity_evaluator.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from graph.equity_stats import apply_fdr, group_tests  # noqa: E402

N = 400
DIMS = {
    "gender_group": (["group_a", "group_b"], [0.55, 0.45]),
    "tenure_band": (["0-1y", "1-3y", "3-5y", "5y+"], [0.2, 0.35, 0.25, 0.2]),
    "level_band": (["ic", "senior_ic", "manager", "director_plus"], [0.5, 0.3, 0.15, 0.05]),
}
K = 5


def make_org(seed: int, disadvantaged: tuple[str, str] | None = None, gap: float = 0.4, n: int = N):
    rng = np.random.default_rng(seed)
    labels = {d: rng.choice(v, n, p=p) for d, (v, p) in DIMS.items()}
    degree = rng.lognormal(2.5, 0.6, n)
    betweenness = np.where(rng.random(n) < 0.6, 0.0, rng.exponential(0.01, n))
    if disadvantaged:
        dim, grp = disadvantaged
        hit = labels[dim] == grp
        degree[hit] *= 1 - gap
        betweenness[hit] *= 1 - gap
    return labels, {"degree": degree, "betweenness": betweenness}


def old_rule_flags(labels, metrics) -> set[tuple[str, str, str]]:
    flags = set()
    for dim, lab in labels.items():
        for metric, x in metrics.items():
            meds = {g: np.median(x[lab == g]) for g in np.unique(lab) if (lab == g).sum() >= K}
            ref = np.median(list(meds.values()))
            flags |= {(dim, metric, g) for g, m in meds.items() if m < 0.8 * ref}
    return flags


def new_flags(labels, metrics, seed: int = 0, n_perm: int = 4000) -> set[tuple[str, str, str]]:
    tests = []
    for dim, lab in labels.items():
        for metric, x in metrics.items():
            tests += group_tests(x, lab, dim, metric, min_group_size=K, n_perm=n_perm, seed=seed)
    return {(t.dimension, t.metric, t.group) for t in apply_fdr(tests) if t.flagged}


def null_rates(seeds=range(20), n: int = 120) -> tuple[float, float]:
    old, new = [], []
    for s in seeds:
        labels, metrics = make_org(s, n=n)
        old.append(len(old_rule_flags(labels, metrics)))
        new.append(len(new_flags(labels, metrics, seed=s)))
    return float(np.mean(old)), float(np.mean(new))


def power(seeds=range(10), target=("tenure_band", "0-1y")) -> dict:
    found = {"degree": 0, "betweenness": 0}
    old_bw = 0
    for s in seeds:
        for metric, gap in (("degree", 0.4), ("betweenness", 0.6)):
            labels, metrics = make_org(s, disadvantaged=target, gap=gap)
            found[metric] += (target[0], metric, target[1]) in new_flags(labels, metrics, seed=s, n_perm=2000)
            if metric == "betweenness":
                old_bw += (target[0], "betweenness", target[1]) in old_rule_flags(labels, metrics)
    n = len(list(seeds))
    return {"degree": found["degree"] / n, "betweenness": found["betweenness"] / n, "old_betweenness": old_bw / n}


_CACHE: dict = {}


def _null():
    if "null" not in _CACHE:
        _CACHE["null"] = null_rates()
    return _CACHE["null"]


def _power():
    if "power" not in _CACHE:
        _CACHE["power"] = power()
    return _CACHE["power"]


def test_null_flags_are_rare():
    old, new = _null()
    assert new <= 0.25  # BH at 10 %: under a global null P(any flag) ≈ 0.10
    assert old >= 4 * new


def test_detects_planted_degree_gap():
    assert _power()["degree"] >= 0.8


def test_detects_planted_betweenness_gap_despite_zero_median():
    p = _power()
    assert p["betweenness"] >= 0.6
    assert p["old_betweenness"] == 0.0  # median betweenness is 0: the old ratio rule cannot fire


if __name__ == "__main__":
    old, new = _null()
    print(f"Null (random labels, 120 people): flags/run  old rule {old:.2f}   new test {new:.2f}")
    p = _power()
    print(
        f"Planted gap (tenure 0-1y, 400 people): degree -40% flagged {p['degree']:.0%}, betweenness -60% {p['betweenness']:.0%}"
    )
    print(f"  old rule on betweenness: {p['old_betweenness']:.0%}")
