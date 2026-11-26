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
    peer exposure   random people "depart"                   → no real contagion
    equity          random demographic labels                 → no real gap
    churn           people who stop appearing = departures    → real (noisy) labels

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
    damage well [v3.4 weights + undirected BC: 0.85 over all 986 removals, ~0.9 on this\n    ~90-removal sample; v2 weights + directed BC: 0.81 / 0.87]."""
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
    assert rho >= 0.80, f"Spearman(SPOF, removal damage) = {rho:.2f}"


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


# ─── alert volume: population-level control on real e-mail ────────────────────


def _runs(events, min_weekly=7):
    """One 'run' = everyone eligible in a non-overlapping 12-week window."""
    daily = rnd.daily_initiated_counts(events)
    n_days = len(next(iter(daily.values())))
    for st in range(0, n_days - 84 + 1, 84):
        yield {
            str(pid): c[st : st + 84].reshape(12, 7).sum(1).astype(float)
            for pid, c in daily.items()
            if c[st : st + 63].sum() / 9 >= min_weekly
        }


def test_population_alerts_on_real_null_are_near_zero(events):
    """Each person's weeks shuffled → no real drop anywhere.  Calibrated p-values + BH
    must keep false alerts per run near zero [0.00; raw α = 0.001 gave 1.4–1.7 per run]."""
    from ml.features.changepoint import select_sudden_withdrawals

    rng = np.random.default_rng(3)
    alerts = []
    for run in _runs(events):
        shuffled = {k: rng.permutation(v) for k, v in run.items()}
        alerts.append(len(select_sudden_withdrawals(shuffled, null_draws=8000, seed=1)))
    assert np.mean(alerts) <= 0.25, f"false alerts per run: {alerts}"


def test_population_alerts_still_catch_injected_stops(events):
    """5 % of people get a 90 % drop injected into their real history, pooled over
    3 seeds (~50 injections per seed swing from 15 % to 49 % alone).  [36 % caught]"""
    from ml.features.changepoint import select_sudden_withdrawals

    caught = total = 0
    for seed in (0, 4, 7):
        rng = np.random.default_rng(seed)
        for run in _runs(events):
            injected = set()
            series = {}
            for k, v in run.items():
                w = v.copy()
                if rng.random() < 0.05:
                    w[9:] = rng.binomial(w[9:].astype(int), 0.1)
                    injected.add(k)
                series[k] = w
            hits = select_sudden_withdrawals(series, null_draws=8000, seed=seed)
            caught += len(injected & set(hits))
            total += len(injected)
    assert total > 0 and caught / total >= 0.25, f"caught {caught}/{total}"


# ─── churn and peer exposure on real departure-like events ────────────────────
#
# The temporal network has no HR records, but 331 people stop appearing for
# good (last e-mail ≥ 60 days before the end, active ≥ 90 days before that).
# Treating that as a departure gives real, noisy labels.


def _departures(events):
    from datetime import date, timedelta

    end = int(events[:, 2].max())
    last: dict[int, int] = {}
    first: dict[int, int] = {}
    for s_, t_, d in events:
        for p_ in (int(s_), int(t_)):
            last[p_] = max(last.get(p_, -1), int(d))
            first[p_] = min(first.get(p_, 10**9), int(d))
    base = date(2020, 1, 1)
    deps = {
        str(p_): base + timedelta(days=last[p_]) for p_ in last if last[p_] < end - 60 and last[p_] - first[p_] > 90
    }
    return deps, base, end


@pytest.fixture(scope="module")
def churn_examples(events):
    import collections
    from datetime import timedelta

    from ml.churn.contagion import peer_exposure

    deps, base, end = _departures(events)
    X, y, anchors = [], [], []
    for A in range(120, end - 150, 14):
        w = events[(events[:, 2] > A - 90) & (events[:, 2] <= A)]
        sent = collections.Counter(map(str, w[:, 0]))
        recent = collections.Counter(map(str, w[w[:, 2] > A - 30][:, 0]))
        contacts = collections.defaultdict(set)
        for s_, t_, _ in w:
            contacts[str(s_)].add(int(t_))
        as_of = base + timedelta(days=A)
        expo = peer_exposure([(str(a), str(b), 1.0) for a, b, _ in w], deps, as_of)
        for e in sent:
            left = deps.get(e)
            if left is not None and left <= as_of:
                continue
            X.append([np.log1p(sent[e]), np.log1p(recent[e]), np.log1p(len(contacts[e])), expo.get(e, 0.0)])
            y.append(int(left is not None and left <= as_of + timedelta(days=90)))
            anchors.append(as_of.toordinal())
    return np.array(X), np.array(y), np.array(anchors)


def test_churn_model_out_of_time_on_real_departures(churn_examples):
    """Point-in-time features → purged out-of-time test.  Honest result: weak ranking
    [AUROC 0.57], no worse than the base rate [Brier skill +0.004], and under-called
    probabilities because the departure rate drifted from 1.1 % (train) to 5.9 %
    (test) [ECE 0.046].  This is why the trainer reports held-out ECE and the DAG
    warns above 0.05: calibration is only as good as the base rate is stable."""
    from ml.churn.model import fit_churn_model

    X, y, a = churn_examples
    m = fit_churn_model(X, y, a, horizon_days=90)
    test = m.metrics["test"]
    assert test["auroc"] >= 0.53
    assert test["brier_skill"] >= 0.0
    assert test["ece"] <= 0.06


def test_peer_exposure_adds_nothing_on_real_departures(churn_examples):
    """Finding, not a design target: on this network losing collaborators does not
    predict leaving [AUROC with exposure 0.566 vs 0.560 without; Brier identical].
    The peer-departure alert is therefore worded as a handover/workload check-in,
    not as a churn prediction."""
    from ml.churn.model import fit_churn_model

    X, y, a = churn_examples
    with_e = fit_churn_model(X, y, a, horizon_days=90).metrics["test"]
    without = fit_churn_model(X[:, :3], y, a, horizon_days=90).metrics["test"]
    assert abs(with_e["auroc"] - without["auroc"]) < 0.03


def test_peer_exposure_null_on_real_email(events):
    """20 random people 'depart' in the last 90 days of real e-mail.  New test: false
    alerts per run [0.2]; old unweighted 30 %-of-neighbours rule [23.3]."""
    import collections
    from datetime import timedelta

    from ml.churn.contagion import select_peer_exposure

    deps_, base, end = _departures(events)
    w = events[events[:, 2] > end - 90]
    edges = [(str(a), str(b), 1.0) for a, b, _ in w]
    people = sorted({e for a, b, _ in edges for e in (a, b)})
    nbrs = collections.defaultdict(set)
    for a, b, _ in edges:
        nbrs[a].add(b)
        nbrs[b].add(a)
    as_of = base + timedelta(days=end)
    new, old = [], []
    for seed in range(10):
        rng = np.random.default_rng(seed)
        gone = set(rng.choice(people, 20, replace=False))
        deps = {g: as_of - timedelta(days=int(rng.integers(0, 90))) for g in gone}
        new.append(sum(r.flagged for r in select_peer_exposure(edges, deps, as_of, seed=seed).values()))
        old.append(sum(1 for e, ns in nbrs.items() if e not in gone and len(ns & gone) / len(ns) > 0.3))
    assert np.mean(new) <= 0.5, new
    assert np.mean(old) >= 10 * max(np.mean(new), 0.1), old


def test_equity_null_on_real_network(static_graph):
    """Random demographic labels on the real network's betweenness and degree.
    New test: flags per run [0.2]; old median-of-medians rule [2.5]."""
    from graph.metrics import compute_betweenness
    from tests.validation import equity_evaluator as eq

    nodes = list(static_graph)
    bc = compute_betweenness(static_graph)
    metrics = {
        "betweenness": np.array([bc[n] for n in nodes]),
        "degree": np.array([static_graph.in_degree(n) + static_graph.out_degree(n) for n in nodes], dtype=float),
    }
    new, old = [], []
    for seed in range(10):
        rng = np.random.default_rng(seed)
        labels = {d: rng.choice(v, len(nodes), p=p_) for d, (v, p_) in eq.DIMS.items()}
        old.append(len(eq.old_rule_flags(labels, metrics)))
        new.append(len(eq.new_flags(labels, metrics, seed=seed, n_perm=2000)))
    assert np.mean(new) <= 0.3, new
    assert np.mean(old) > 4 * max(np.mean(new), 0.1), old
