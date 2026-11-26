"""Validation on REAL collaboration networks (SNAP email-Eu-core), independent of our generators.

Why this suite exists: every other validation test uses synthetic organisations
generated with the same assumptions the models make, so a model can pass its
own simulation and still fail on real data (it happened: LESSONS_LEARNED §1.16).
These tests use a public e-mail network from a European research institution:

    static   1,005 people, 25,571 directed edges, 42 department labels
    temporal 332,334 timestamped e-mails over 803 days (no labels)

Ground truth on real data is rarely available, so each test uses a NULL that is
known to contain no signal, built from the real data itself, or a known signal
INJECTED into real histories:

    silo index      department labels shuffled across people  → no real silos
    entropy flag    each person's 4 weeks shuffled            → no real trend
    sudden drop     each person's 12 weeks shuffled           → no real drop
    power           withdrawal injected into real histories

Skipped when the data is absent:  python scripts/fetch_external_data.py
Measured values at the time of writing are in brackets next to each gate.
"""

from __future__ import annotations

import numpy as np
import pytest

from tests.validation import real_network_data as rnd

pytestmark = pytest.mark.skipif(not rnd.available(), reason=rnd.FETCH_HINT)


# ─── fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def static_graph():
    return rnd.load_static_graph()


@pytest.fixture(scope="module")
def events():
    return rnd.load_temporal_events()


def _spearman(a, b) -> float:
    ra = np.argsort(np.argsort(a)).astype(float)
    rb = np.argsort(np.argsort(b)).astype(float)
    return float(np.corrcoef(ra, rb)[0, 1])


# ─── silo detection: size bias on a real network ──────────────────────────────


def _dept_stats(G, labels: dict[str, str]):
    import networkx as nx

    from graph.silo_detector import department_homophily

    H = nx.DiGraph()
    H.add_nodes_from((n, {"department": labels[n]}) for n in G)
    H.add_edges_from(G.edges(data=True))
    return [(len(s["members"]), s["isolation_ratio"], s["homophily"]) for s in department_homophily(H).values()]


def test_silo_index_unbiased_under_shuffled_departments(static_graph):
    """With department labels shuffled no department is really a silo.
    The legacy ratio still tracks size [Spearman +0.88]; the homophily index must not [+0.00]."""
    rng = np.random.default_rng(0)
    nodes = list(static_graph)
    real = [static_graph.nodes[n]["department"] for n in nodes]
    ratio_corr, h_corr, h_mean = [], [], []
    for _ in range(25):
        labels = dict(zip(nodes, rng.permutation(real), strict=True))
        rows = [r for r in _dept_stats(static_graph, labels) if r[0] >= 3 and r[2] is not None]
        size, ratio, h = (np.array(c, dtype=float) for c in zip(*rows, strict=True))
        ratio_corr.append(_spearman(size, ratio))
        h_corr.append(_spearman(size, h))
        h_mean.append(h.mean())
    assert np.mean(ratio_corr) > 0.6, "legacy ratio should show the size bias this test guards against"
    assert abs(np.mean(h_corr)) < 0.15, f"homophily index correlates with size under the null: {np.mean(h_corr):+.2f}"
    assert abs(np.mean(h_mean)) < 0.02


def test_no_silos_flagged_under_shuffled_departments(static_graph):
    from graph.silo_detector import detect_silos

    rng = np.random.default_rng(1)
    nodes = list(static_graph)
    real = [static_graph.nodes[n]["department"] for n in nodes]
    G = static_graph.copy()
    for _ in range(10):
        for n, d in zip(nodes, rng.permutation(real), strict=True):
            G.nodes[n]["department"] = d
        assert detect_silos(G) == []


def test_real_departments_are_more_inward_than_chance(static_graph):
    """Sanity: with the REAL labels, departments do collaborate internally [median H 0.18; 4 silos]."""
    labels = {n: static_graph.nodes[n]["department"] for n in static_graph}
    h = np.array([r[2] for r in _dept_stats(static_graph, labels) if r[0] >= 3 and r[2] is not None])
    assert np.median(h) > 0.05


# ─── SPOF vs. actual removal damage on a real network ─────────────────────────


def test_spof_tracks_real_removal_damage(static_graph):
    """Remove people one at a time from the real graph and measure the damage
    (path-length increase + lost reachability, 40 sampled sources).  SPOF must rank
    damage well [Spearman 0.87 on ~100 removals; raw betweenness 0.90]."""
    import networkx as nx

    from graph.metrics import compute_betweenness, compute_clustering
    from graph.risk_scorer import score_all_with_bands

    G = static_graph
    bands = score_all_with_bands(G, compute_betweenness(G), compute_clustering(G))
    spof = {n: b["score"] for n, b in bands.items()}
    U = G.to_undirected()
    rng = np.random.default_rng(0)
    nodes = list(G)
    sources = list(rng.choice(nodes, 40, replace=False))

    def path_stats(H, srcs):
        tot = cnt = 0
        for s in srcs:
            d = nx.single_source_shortest_path_length(H, s)
            tot += sum(d.values())
            cnt += len(d) - 1
        return tot / cnt, cnt

    base_apl, base_cnt = path_stats(U, sources)
    cand = sorted(set(sorted(nodes, key=spof.get, reverse=True)[:40]) | set(rng.choice(nodes, 50, replace=False)))
    damage = []
    for n in cand:
        H = U.copy()
        H.remove_node(n)
        srcs = [s for s in sources if s != n]
        apl, cnt = path_stats(H, srcs)
        lost = max(0.0, 1 - cnt / (base_cnt * len(srcs) / len(sources)))
        damage.append(100 * (apl - base_apl) / base_apl + 100 * lost)
    rho = _spearman([spof[n] for n in cand], damage)
    assert rho >= 0.75, f"Spearman(SPOF, removal damage) = {rho:.2f}"


# ─── entropy trend on real e-mail ─────────────────────────────────────────────


def _entropy_batches(events, rng):
    n_days = int(events[:, 2].max()) + 1
    for start in range(0, n_days - 28, 56):
        wk = {
            p: w
            for p, w in rnd.weekly_partner_counts(events, start).items()
            if sum(sum(x.values()) for x in w.values()) >= 8
        }
        if len(wk) >= 30:
            yield wk


def test_entropy_flag_false_positive_rate_on_real_null(events):
    """Weeks shuffled per person → no real trend.  [2.0 %; legacy 'any negative slope' 48.9 %;
    12.5 % before the extra week-to-week variance term]"""
    from ml.features.feature_extractor import entropy_signals_from_weekly

    rng = np.random.default_rng(0)
    flags = []
    for wk in _entropy_batches(events, rng):
        shuffled = {p: {i: w.get(j, {}) for i, j in enumerate(rng.permutation(4))} for p, w in wk.items()}
        flags += [s.withdrawing for s in entropy_signals_from_weekly(shuffled).values()]
    assert np.mean(flags) <= 0.04, f"false positives {np.mean(flags):.1%} over {len(flags)} person-windows"


def test_entropy_flag_power_on_injected_narrowing(events):
    """Last 2 weeks of a real history re-routed to the person's top-2 recipients
    (same volume, far fewer people).  [58 %]"""
    from ml.features.feature_extractor import entropy_signals_from_weekly

    rng = np.random.default_rng(0)
    hits = []
    for wk in _entropy_batches(events, rng):
        injected, eligible = {}, []
        for p, w in wk.items():
            totals: dict[str, int] = {}
            for x in w.values():
                for q, c in x.items():
                    totals[q] = totals.get(q, 0) + c
            top = [q for q, _ in sorted(totals.items(), key=lambda kv: -kv[1])[:2]]
            w2 = {k: dict(v) for k, v in w.items()}
            for k in (2, 3):
                n = sum(w2.get(k, {}).values())
                w2[k] = {top[0]: n - n // 2, top[-1]: n // 2} if n else {}
            injected[p] = w2
            if len(totals) >= 4:
                eligible.append(p)
        sig = entropy_signals_from_weekly(injected)
        hits += [sig[p].withdrawing for p in eligible]
    assert np.mean(hits) >= 0.45, f"power {np.mean(hits):.0%}"


# ─── sudden withdrawal on real e-mail ─────────────────────────────────────────


def _weekly_segments(events, weeks=12, min_weekly=7):
    daily = rnd.daily_initiated_counts(events)
    n_days = len(next(iter(daily.values())))
    segs = []
    for c in daily.values():
        for st in range(0, n_days - 7 * weeks + 1, 7 * weeks):
            seg = c[st : st + 7 * weeks]
            if seg[: 7 * (weeks - 3)].sum() / (weeks - 3) >= min_weekly:
                segs.append(seg)
    return segs


def test_sudden_withdrawal_false_alarm_rate_on_real_null(events):
    """12 weeks shuffled per person → no real drop.  [1.5 %; the first, Poisson daily
    change-point design false-alarmed on 32–43 % of the same null]"""
    from ml.features.changepoint import detect_activity_drop

    rng = np.random.default_rng(1)
    segs = _weekly_segments(events)
    alarms = [
        detect_activity_drop(rng.permutation(s.reshape(12, 7).sum(1))) is not None for s in segs for _ in range(3)
    ]
    assert np.mean(alarms) <= 0.03, f"false alarms {np.mean(alarms):.2%} over {len(alarms)} shuffled segments"


def test_sudden_withdrawal_power_on_injected_near_stop(events):
    """A 90 % drop for the last 3 weeks injected into real histories.  [53 %]"""
    from ml.features.changepoint import detect_activity_drop

    rng = np.random.default_rng(1)
    hits = []
    for s in _weekly_segments(events):
        if detect_activity_drop(s.reshape(12, 7).sum(1)) is not None:
            continue  # already low for real reasons
        s2 = s.copy()
        s2[63:] = rng.binomial(s2[63:].astype(int), 0.1)
        hits.append(detect_activity_drop(s2.reshape(12, 7).sum(1)) is not None)
    assert np.mean(hits) >= 0.40, f"power {np.mean(hits):.0%}"
