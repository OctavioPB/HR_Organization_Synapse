"""Validation: calibrated churn model and peer-exposure test (MATH_MODEL.md §9).

Churn
─────
A synthetic org is simulated *forward in time*: every 14 days each employee
still employed leaves with a hazard that depends on their current features,
and features drift between anchors.  Labels are then derived exactly as in
production (left within 90 days of the anchor), so the test exercises the
point-in-time labelling and the purged temporal split together.  Checks:

    • ranking:      held-out AUROC ≥ 0.70
    • calibration:  ECE ≤ 0.03 and predicted vs. observed base rate within 30 %
    • skill:        Brier skill vs. a constant base-rate forecast > 0
    • null:         with labels shuffled across rows the model learns nothing (AUROC ≤ 0.60)
    • reweighting:  class_weight="balanced" (the old GAT's pos_weight) is badly
                    calibrated, which is why the production model is unweighted

Peer exposure
─────────────
    • null:  random departures on a department-structured org → mean false
             alerts per run ≤ 0.3, vs. the old unweighted 30 % rule
    • power: a team that loses half its members in the last month → most of
             ≥ 35 % of the remaining members are flagged (per-person evidence
             is weak in a 120-person org: losing 2 of 5 departures from your
             close ties happens by chance ~2–3 % of the time)

Run:
    python tests/validation/churn_calibration_evaluator.py
    pytest tests/validation/churn_calibration_evaluator.py
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ml.churn.contagion import select_peer_exposure  # noqa: E402
from ml.churn.model import (  # noqa: E402
    calibration_error,
    fit_churn_model,
    temporal_folds,
)

HORIZON = 90
STEP = 14
TRUE_COEF = np.array([-0.9, 0.0, -0.3, 0.0, -0.6, 0.0, 0.0, -0.5, 0.0, -0.7, 0.8])
TRUE_INTERCEPT = -4.6  # ≈ 1 % per 14 days ≈ 25 %/year attrition


def simulate_org(n: int = 1500, n_anchors: int = 30, seed: int = 0):
    """Forward simulation → (X, y, anchors) with production-style 90-day labels."""
    rng = np.random.default_rng(seed)
    start = date(2025, 1, 6)
    anchors = [start + timedelta(days=STEP * k) for k in range(n_anchors)]
    base = rng.normal(0, 1, (n, len(TRUE_COEF)))
    x = base.copy()
    departed: dict[int, date] = {}
    feats_at: list[np.ndarray] = []
    for t in anchors:
        x = 0.85 * x + 0.15 * base + rng.normal(0, 0.35, x.shape)  # slow drift around a personal mean
        feats_at.append(x.copy())
        hazard = 1 / (1 + np.exp(-(x @ TRUE_COEF + TRUE_INTERCEPT)))
        for i in np.where(rng.random(n) < hazard)[0]:
            if i not in departed:
                departed[i] = t + timedelta(days=int(rng.integers(1, STEP + 1)))
    X, y, a = [], [], []
    last = anchors[-1]
    for k, t in enumerate(anchors):
        if t + timedelta(days=HORIZON) > last:
            continue  # label window not yet elapsed
        for i in range(n):
            left = departed.get(i)
            if left is not None and left <= t:
                continue
            X.append(feats_at[k][i])
            y.append(int(left is not None and left <= t + timedelta(days=HORIZON)))
            a.append(t.toordinal())
    return np.array(X), np.array(y), np.array(a)


def churn_report(seed: int = 0) -> dict:
    X, y, a = simulate_org(seed=seed)
    model = fit_churn_model(X, y, a, horizon_days=HORIZON)
    assert model is not None
    train, test = temporal_folds(a, HORIZON)
    held_out = fit_churn_model(X[train], y[train], a[train], horizon_days=HORIZON, min_positives=0)
    p = held_out.predict_proba(X[test])  # same fit as the one behind model.metrics["test"]

    rng = np.random.default_rng(seed + 1)
    y_null = rng.permutation(y)
    null_model = fit_churn_model(X, y_null, a, horizon_days=HORIZON)
    null_auroc = null_model.metrics["test"]["auroc"]

    from sklearn.linear_model import LogisticRegression

    weighted = LogisticRegression(class_weight="balanced", max_iter=2000).fit(X[train], y[train])
    weighted_ece = calibration_error(y[test], weighted.predict_proba(X[test])[:, 1])

    return {
        "test": model.metrics["test"],
        "mean_pred": float(p.mean()),
        "observed": float(y[test].mean()),
        "null_auroc": null_auroc,
        "weighted_ece": weighted_ece,
    }


# ─── Peer exposure ────────────────────────────────────────────────────────────


def department_org(n_depts: int = 12, size: int = 10, seed: int = 0):
    """Dense within departments, sparse across: (edges, ids, dept_of)."""
    rng = np.random.default_rng(seed)
    ids = [f"e{d:02d}_{k:02d}" for d in range(n_depts) for k in range(size)]
    dept = {e: int(e[1:3]) for e in ids}
    edges = []
    for i, u in enumerate(ids):
        for v in ids[i + 1 :]:
            p = 0.6 if dept[u] == dept[v] else 0.02
            if rng.random() < p:
                edges.append((u, v, float(rng.gamma(2.0, 3.0))))
    return edges, ids, dept


def contagion_null(seed: int, as_of: date = date(2026, 6, 1), n_departures: int = 8):
    edges, ids, _ = department_org(seed=seed)
    rng = np.random.default_rng(seed + 100)
    gone = rng.choice(ids, n_departures, replace=False)
    departures = {e: as_of - timedelta(days=int(rng.integers(0, 90))) for e in gone}
    res = select_peer_exposure(edges, departures, as_of, n_perm=1000, seed=seed)
    new_flags = sum(r.flagged for r in res.values())

    # Old rule: unweighted share of neighbours departed > 0.30
    nbrs: dict[str, set[str]] = {}
    for u, v, _ in edges:
        nbrs.setdefault(u, set()).add(v)
        nbrs.setdefault(v, set()).add(u)
    old_flags = sum(1 for e, ns in nbrs.items() if e not in departures and ns and len(ns & set(gone)) / len(ns) > 0.30)
    return new_flags, old_flags


def contagion_power(seed: int, as_of: date = date(2026, 6, 1)) -> float:
    edges, ids, dept = department_org(seed=seed)
    team = [e for e in ids if dept[e] == 3]
    rng = np.random.default_rng(seed + 200)
    gone = list(rng.choice(team, len(team) // 2, replace=False))
    departures = {e: as_of - timedelta(days=int(rng.integers(0, 30))) for e in gone}
    res = select_peer_exposure(edges, departures, as_of, n_perm=1000, seed=seed)
    stayers = [e for e in team if e not in departures]
    return float(np.mean([res[e].flagged for e in stayers if e in res]))


# ─── Tests ────────────────────────────────────────────────────────────────────

_CACHE: dict = {}


def _report() -> dict:
    if "churn" not in _CACHE:
        _CACHE["churn"] = churn_report()
    return _CACHE["churn"]


def test_churn_ranks_held_out_departures():
    assert _report()["test"]["auroc"] >= 0.70


def test_churn_probabilities_are_calibrated():
    r = _report()
    assert r["test"]["ece"] <= 0.03
    assert abs(r["mean_pred"] - r["observed"]) <= 0.30 * r["observed"]


def test_churn_beats_base_rate_forecast():
    assert _report()["test"]["brier_skill"] > 0


def test_churn_learns_nothing_from_shuffled_labels():
    assert _report()["null_auroc"] <= 0.60


def test_class_reweighting_destroys_calibration():
    r = _report()
    assert r["weighted_ece"] > 5 * r["test"]["ece"]


def test_peer_exposure_null_false_alerts():
    new, old = zip(*(contagion_null(s) for s in range(10)), strict=True)
    assert np.mean(new) <= 0.3
    assert np.mean(old) > np.mean(new)


def test_peer_exposure_detects_team_exodus():
    assert np.mean([contagion_power(s) for s in range(5)]) >= 0.35


if __name__ == "__main__":
    r = churn_report()
    print("Churn (held-out fold):")
    for k, v in r["test"].items():
        print(f"  {k:12s} {v:.4f}" if isinstance(v, float) else f"  {k:12s} {v}")
    print(f"  mean predicted {r['mean_pred']:.4f} vs observed {r['observed']:.4f}")
    print(f"  shuffled-label AUROC {r['null_auroc']:.3f}")
    print(f"  class_weight='balanced' ECE {r['weighted_ece']:.4f}")
    nulls = [contagion_null(s) for s in range(10)]
    print("Peer exposure:")
    print(
        f"  null false alerts/run: new {np.mean([a for a, _ in nulls]):.2f}  old rule {np.mean([b for _, b in nulls]):.2f}"
    )
    print(f"  team-exodus power: {np.mean([contagion_power(s) for s in range(5)]):.2f}")
