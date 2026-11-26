"""Loaders for the public SNAP email-Eu-core networks (real-network test bed).

Not a test module (pytest collects test_*.py, *_validator.py, *_evaluator.py).
Data is fetched by scripts/fetch_external_data.py into data/external/snap/;
``available()`` lets tests skip cleanly when it is absent.

Format (verified on download): whitespace-separated integers, 0-indexed nodes.
  email-Eu-core.txt.gz                    "u v"      u emailed v at least once
  email-Eu-core-department-labels.txt.gz  "node dept"  42 departments (0–41)
  email-Eu-core-temporal.txt.gz           "src dst t"  t = seconds from 0, 803 days;
                                                       node IDs are NOT the static file's IDs
"""

from __future__ import annotations

import gzip
from collections import defaultdict
from pathlib import Path

import networkx as nx
import numpy as np

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "external" / "snap"
STATIC_EDGES = DATA_DIR / "email-Eu-core.txt.gz"
LABELS = DATA_DIR / "email-Eu-core-department-labels.txt.gz"
TEMPORAL = DATA_DIR / "email-Eu-core-temporal.txt.gz"
FETCH_HINT = "Real-network data not present. Run: python scripts/fetch_external_data.py"

_DAY = 86_400


def available(*paths: Path) -> bool:
    return all(p.exists() for p in (paths or (STATIC_EDGES, LABELS, TEMPORAL)))


def _rows(path: Path):
    with gzip.open(path, "rt", encoding="ascii") as fh:
        for line in fh:
            parts = line.split()
            if parts:
                yield tuple(int(p) for p in parts)


def load_static_graph(largest_wcc: bool = True) -> nx.DiGraph:
    """Directed email graph with a 'department' attribute ("D00"…"D41") on every node.

    Self-loops are dropped.  Edge weight = 1 (the static file has no counts).
    """
    dept = {n: f"D{d:02d}" for n, d in _rows(LABELS)}
    G = nx.DiGraph()
    for n, d in dept.items():
        G.add_node(str(n), department=d)
    for u, v in _rows(STATIC_EDGES):
        if u != v:
            G.add_edge(str(u), str(v), weight=1.0)
    if largest_wcc:
        keep = max(nx.weakly_connected_components(G), key=len)
        G = G.subgraph(keep).copy()
    return G


def load_temporal_events() -> np.ndarray:
    """(N, 3) int64 array of (src, dst, day) with self-loops removed, day = t // 86400."""
    arr = np.array([r for r in _rows(TEMPORAL) if r[0] != r[1]], dtype=np.int64)
    arr[:, 2] //= _DAY
    return arr


def daily_initiated_counts(events: np.ndarray) -> dict[int, np.ndarray]:
    """Per-sender emails sent per day over the full span (zeros included)."""
    n_days = int(events[:, 2].max()) + 1
    out: dict[int, np.ndarray] = defaultdict(lambda: np.zeros(n_days))
    for src, _dst, day in events:
        out[int(src)][int(day)] += 1
    return dict(out)


def weekly_partner_counts(events: np.ndarray, start_day: int, n_weeks: int = 4) -> dict[str, dict[int, dict[str, int]]]:
    """{sender: {week: {recipient: count}}} for n_weeks 7-day weeks starting at start_day.

    Same shape as ml.features.feature_extractor._load_weekly_interactions output.
    """
    end = start_day + 7 * n_weeks
    sel = events[(events[:, 2] >= start_day) & (events[:, 2] < end)]
    out: dict[str, dict[int, dict[str, int]]] = {}
    for src, dst, day in sel:
        wk = int((day - start_day) // 7)
        bucket = out.setdefault(str(src), {}).setdefault(wk, {})
        bucket[str(dst)] = bucket.get(str(dst), 0) + 1
    return out
