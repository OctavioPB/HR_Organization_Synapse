"""Peer-departure exposure and its permutation test (MATH_MODEL.md §9.2).

The old rule was ``|departed neighbours| / |neighbours| > 0.30`` on the last
30 days' graph.  Three problems:

1. It could never fire.  The graph is built from *active* employees only, and
   people who left 1–6 months ago have no events in the last 30 days, so a
   departed peer was never a neighbour.
2. A colleague you e-mailed once counted as much as your closest collaborator,
   and a departure five months ago as much as one last week.
3. There was no reference for "more than chance": in a small team with normal
   attrition, 30 % of someone's peers leaving can happen by luck.

Exposure
────────
Ties come from the retained raw_events (90 days), consented employees, active
or not, so the departed are still in the graph.  With undirected tie strength
w(v,u) = w(v→u) + w(u→v) and tie share s(v,u) = w(v,u) / Σ_x w(v,x):

    E(v) = Σ_{u departed in (t − W, t]}  s(v,u) · 2^(−(t − d_u) / half_life)

E(v) ∈ [0, 1] is the share of v's collaboration volume that has recently left,
discounted by how long ago it left.  W = PEER_CHURN_WINDOW_DAYS (90, = raw_events retention),
half_life = PEER_CHURN_HALF_LIFE_DAYS (45).

Test
────
Null: the same departures (same dates, so the same decay weights), landed on
random people in the network.  Each permutation reassigns the decay vector
across nodes; p(v) = permutation p-value of E(v).  Benjamini–Hochberg at
PEER_CONTAGION_FDR (0.10) across employees, plus an effect-size floor on the
undiscounted share

    share(v) = Σ_{u departed in (t − W, t]} s(v,u)  ≥  PEER_CONTAGION_MIN_EXPOSURE (0.30)

so a statistically unusual but small exposure is not an alert.  The floor is
on the undiscounted share because that is the quantity the 30 % finding is
about; the decayed E(v) is the test statistic and the churn-model feature.

What it does not do: tell contagion from a shared cause (a team that loses its
manager loses people together).  Either way the remaining people are at higher
risk, which is what the alert claims; the alert text says "peer departures",
not "contagion".
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date

import numpy as np

from ml.stats import benjamini_hochberg

PEER_CHURN_WINDOW_DAYS = int(os.environ.get("PEER_CHURN_WINDOW_DAYS", "90"))
PEER_CHURN_HALF_LIFE_DAYS = float(os.environ.get("PEER_CHURN_HALF_LIFE_DAYS", "45"))
PEER_CONTAGION_FDR = float(os.environ.get("PEER_CONTAGION_FDR", "0.10"))
PEER_CONTAGION_MIN_EXPOSURE = float(
    os.environ.get("PEER_CONTAGION_MIN_EXPOSURE", os.environ.get("PEER_CONTAGION_THRESHOLD", "0.30"))
)
PEER_CONTAGION_PERMUTATIONS = int(os.environ.get("PEER_CONTAGION_PERMUTATIONS", "2000"))


@dataclass
class PeerExposure:
    employee_id: str
    exposure: float  # decayed E(v)
    departed_share: float  # undiscounted share of collaboration volume that left
    p_value: float
    departed_peers: int
    flagged: bool


def tie_share_matrix(raw_edges):
    """Undirected tie-share matrix S (sparse CSR) with rows summing to 1 (0 for isolated nodes).

    raw_edges: iterable of (source, target, weight, ...) tuples.
    Returns (ids, S).  Sparse because a 10k-person org would need 800 MB dense.
    """
    from scipy import sparse

    strength: dict[tuple[str, str], float] = {}
    nodes: set[str] = set()
    for e in raw_edges:
        s, t, w = str(e[0]), str(e[1]), float(e[2])
        if s == t:
            continue
        nodes.update((s, t))
        key = (s, t) if s < t else (t, s)
        strength[key] = strength.get(key, 0.0) + w
    ids = sorted(nodes)
    idx = {n: i for i, n in enumerate(ids)}
    rows, cols, vals = [], [], []
    for (a, b), w in strength.items():
        rows += [idx[a], idx[b]]
        cols += [idx[b], idx[a]]
        vals += [w, w]
    n = len(ids)
    S = sparse.csr_matrix((vals, (rows, cols)), shape=(n, n), dtype=float)
    totals = np.asarray(S.sum(axis=1)).ravel()
    inv = np.divide(1.0, totals, out=np.zeros(n), where=totals > 0)
    return ids, sparse.diags(inv) @ S


def departure_weights(
    ids: list[str],
    departures: dict[str, date],
    as_of: date,
    window_days: int = PEER_CHURN_WINDOW_DAYS,
    half_life_days: float = PEER_CHURN_HALF_LIFE_DAYS,
) -> np.ndarray:
    """Decay weight per node: 2^(−age/half_life) if departed in (as_of − W, as_of], else 0."""
    d = np.zeros(len(ids), dtype=float)
    for i, emp in enumerate(ids):
        left = departures.get(emp)
        if left is None:
            continue
        age = (as_of - left).days
        if 0 <= age < window_days:
            d[i] = 2.0 ** (-age / half_life_days)
    return d


def peer_exposure(
    raw_edges,
    departures: dict[str, date],
    as_of: date,
    window_days: int = PEER_CHURN_WINDOW_DAYS,
    half_life_days: float = PEER_CHURN_HALF_LIFE_DAYS,
) -> dict[str, float]:
    """E(v) for every node still employed at *as_of* (no test)."""
    ids, S = tie_share_matrix(raw_edges)
    if not ids:
        return {}
    d = departure_weights(ids, departures, as_of, window_days, half_life_days)
    E = np.asarray(S @ d).ravel()
    return {emp: float(E[i]) for i, emp in enumerate(ids) if not _gone(emp, departures, as_of)}


def _gone(emp: str, departures: dict[str, date], as_of: date) -> bool:
    left = departures.get(emp)
    return left is not None and left <= as_of


def _sequential_permutation_p(S, d, E, current, n_perm: int, fdr: float, seed: int) -> np.ndarray:
    """Permutation p-values with Besag–Clifford (1991) sequential stopping.

    BH needs p-values as small as fdr / (2m) to reject anything, so the budget
    is max(n_perm, ⌈2m / fdr⌉) permutations.  Most people are nowhere near
    significant: a node stops once it has STOP_HITS null draws at or above its
    observed exposure, with p = STOP_HITS / draws, and the remaining budget is
    spent only on candidates.
    """
    stop_hits = 10
    m = int(current.sum())
    budget = max(n_perm, int(np.ceil(2 * max(m, 1) / fdr)))
    rng = np.random.default_rng(seed)
    exceed = np.zeros(len(E))
    draws = np.zeros(len(E))
    active = current.copy()
    batch = 200
    done = 0
    while done < budget and active.any():
        k = min(batch, budget - done)
        D = np.stack([rng.permutation(d) for _ in range(k)], axis=1)
        rows = np.where(active)[0]
        null = np.asarray(S[rows] @ D)
        exceed[rows] += (null >= E[rows, None] - 1e-12).sum(axis=1)
        draws[rows] += k
        done += k
        active[rows] = exceed[rows] < stop_hits
    p = np.ones(len(E))
    stopped = current & (exceed >= stop_hits)
    p[stopped] = exceed[stopped] / draws[stopped]
    running = current & ~stopped
    p[running] = (1.0 + exceed[running]) / (1.0 + draws[running])
    return np.minimum(p, 1.0)


def select_peer_exposure(
    raw_edges,
    departures: dict[str, date],
    as_of: date,
    window_days: int = PEER_CHURN_WINDOW_DAYS,
    half_life_days: float = PEER_CHURN_HALF_LIFE_DAYS,
    fdr: float = PEER_CONTAGION_FDR,
    min_exposure: float = PEER_CONTAGION_MIN_EXPOSURE,
    n_perm: int = PEER_CONTAGION_PERMUTATIONS,
    seed: int = 0,
) -> dict[str, PeerExposure]:
    """Exposure, permutation p-value and FDR-controlled flag for every current employee."""
    ids, S = tie_share_matrix(raw_edges)
    if not ids:
        return {}
    d = departure_weights(ids, departures, as_of, window_days, half_life_days)
    E = np.asarray(S @ d).ravel()
    current = np.array([not _gone(emp, departures, as_of) for emp in ids])

    p = np.ones(len(ids))
    if d.any() and n_perm > 0:
        p = _sequential_permutation_p(S, d, E, current, n_perm, fdr, seed)

    departed_mask = d > 0
    share = np.asarray(S @ departed_mask.astype(float)).ravel()

    hits = np.zeros(len(ids), dtype=bool)
    cur_idx = np.where(current)[0]
    if len(cur_idx):
        hits[cur_idx] = benjamini_hochberg(p[cur_idx], fdr) & (share[cur_idx] >= min_exposure)

    out: dict[str, PeerExposure] = {}
    for i in cur_idx:
        out[ids[i]] = PeerExposure(
            employee_id=ids[i],
            exposure=float(E[i]),
            departed_share=float(share[i]),
            p_value=float(p[i]),
            departed_peers=int(np.count_nonzero(S.getrow(i).toarray().ravel() * departed_mask)),
            flagged=bool(hits[i]),
        )
    return out
