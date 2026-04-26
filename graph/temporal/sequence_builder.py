"""Build aligned temporal snapshot sequences for the Temporal Risk GNN.

Every snapshot in a sequence uses the *same* ordered node vocabulary so that
index i always refers to the same employee across all time steps.  Employees
absent from a snapshot are zero-padded (presence_mask=False).

Node features per snapshot (TEMPORAL_IN_FEATURES = 4):
    0  betweenness   — normalised betweenness centrality from graph_snapshots
    1  degree_in     — normalised in-degree
    2  degree_out    — normalised out-degree
    3  clustering    — clustering coefficient

Why only 4 features?
  - They are pre-computed and stored in graph_snapshots (no re-computation).
  - They form a sufficient statistical signature for temporal anomaly detection;
    the GRU implicitly learns delta information across time steps.
  - HR features (tenure, role_level) change slowly and add noise to temporal signal.

Public API:
    TemporalSnapshot   — dataclass, one snapshot in the sequence
    build_snapshot_sequence(end_date, n_weeks, step_days)
                       — returns list[TemporalSnapshot], length = n_weeks
                         oldest first, most recent last
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

logger = logging.getLogger(__name__)

TEMPORAL_IN_FEATURES: int = 4  # betweenness, degree_in, degree_out, clustering

_FEATURE_COLS = ("betweenness", "degree_in", "degree_out", "clustering")


# ─── Data structures ──────────────────────────────────────────────────────────


@dataclass
class TemporalSnapshot:
    """One weekly graph snapshot, aligned to the shared employee vocabulary.

    Attributes:
        x             Node feature matrix (N, TEMPORAL_IN_FEATURES), float32.
                      Zero rows indicate employees absent from this snapshot.
        edge_index    Directed COO edge index (2, E), int64.
        edge_weight   Per-edge log1p(interaction_count) (E,), float32.
        snapshot_date Date of the graph_snapshot.
        presence_mask Boolean mask (N,); True where the employee had a snapshot.
    """

    x: np.ndarray
    edge_index: np.ndarray
    edge_weight: np.ndarray
    snapshot_date: date
    presence_mask: np.ndarray


# ─── Pure helpers ─────────────────────────────────────────────────────────────


def _build_x(
    employee_ids: list[str],
    snapshot_rows: list[tuple],
) -> tuple[np.ndarray, np.ndarray]:
    """Build (N, 4) feature matrix + (N,) presence mask.

    Args:
        employee_ids: Ordered node vocabulary (N entries).
        snapshot_rows: (employee_id, betweenness, degree_in, degree_out, clustering)

    Returns:
        (x float32 (N, 4), presence bool (N,))
    """
    n = len(employee_ids)
    x = np.zeros((n, TEMPORAL_IN_FEATURES), dtype=np.float32)
    presence = np.zeros(n, dtype=bool)
    id_to_idx = {eid: i for i, eid in enumerate(employee_ids)}

    for row in snapshot_rows:
        emp_id = str(row[0])
        idx = id_to_idx.get(emp_id)
        if idx is None:
            continue
        x[idx, 0] = float(row[1] or 0.0)  # betweenness
        x[idx, 1] = float(row[2] or 0.0)  # degree_in
        x[idx, 2] = float(row[3] or 0.0)  # degree_out
        x[idx, 3] = float(row[4] or 0.0)  # clustering
        presence[idx] = True

    return x, presence


def _build_edge_index(
    edge_rows: list[tuple],
    id_to_idx: dict[str, int],
) -> tuple[np.ndarray, np.ndarray]:
    """Build COO edge index from interaction count rows.

    Args:
        edge_rows: (source_id, target_id, count)
        id_to_idx: Employee UUID → node index.
    """
    src_list, dst_list, wt_list = [], [], []
    for source_id, target_id, count in edge_rows:
        s = id_to_idx.get(str(source_id))
        t = id_to_idx.get(str(target_id))
        if s is None or t is None:
            continue
        src_list.append(s)
        dst_list.append(t)
        wt_list.append(math.log1p(float(count)))

    if not src_list:
        return np.empty((2, 0), dtype=np.int64), np.empty((0,), dtype=np.float32)

    return (
        np.array([src_list, dst_list], dtype=np.int64),
        np.array(wt_list, dtype=np.float32),
    )


# ─── DB-backed entry point ────────────────────────────────────────────────────


def build_snapshot_sequence(
    end_date: date,
    n_weeks: int = 8,
    step_days: int = 7,
) -> list[TemporalSnapshot]:
    """Load n_weeks consecutive weekly snapshots ending at end_date.

    The snapshots are aligned to a shared node vocabulary: all employees active
    at end_date (or with a snapshot in any of the n_weeks windows).  Employees
    absent from a specific snapshot have x=0 and presence_mask=False.

    Args:
        end_date: Most recent snapshot date (inclusive).  The oldest snapshot
                  is end_date - (n_weeks - 1) * step_days.
        n_weeks: Number of snapshots in the sequence (sequence length T).
        step_days: Days between consecutive snapshots (default: 7 = weekly).

    Returns:
        List of TemporalSnapshot, oldest first, length = n_weeks.
        May return fewer snapshots if graph_snapshots don't exist for all dates.
    """
    from ingestion.db import get_conn

    snapshot_dates = [
        end_date - timedelta(days=(n_weeks - 1 - i) * step_days)
        for i in range(n_weeks)
    ]

    with get_conn() as conn:
        with conn.cursor() as cur:
            # ── Employee vocabulary ────────────────────────────────────────
            # Union of employees present in any snapshot within the window
            cur.execute(
                """
                SELECT DISTINCT employee_id::text
                FROM graph_snapshots
                WHERE snapshot_date = ANY(%s)
                ORDER BY employee_id
                """,
                (snapshot_dates,),
            )
            employee_ids: list[str] = [r[0] for r in cur.fetchall()]

            if not employee_ids:
                logger.warning(
                    "build_snapshot_sequence: no graph_snapshots found for "
                    "any of the %d dates ending at %s",
                    n_weeks, end_date,
                )
                return []

            id_to_idx: dict[str, int] = {eid: i for i, eid in enumerate(employee_ids)}

            # ── Load features for each snapshot date ───────────────────────
            cur.execute(
                """
                SELECT snapshot_date, employee_id::text,
                       betweenness, degree_in, degree_out, clustering
                FROM graph_snapshots
                WHERE snapshot_date = ANY(%s)
                ORDER BY snapshot_date, employee_id
                """,
                (snapshot_dates,),
            )
            rows_by_date: dict[date, list] = {}
            for row in cur.fetchall():
                d = row[0]
                rows_by_date.setdefault(d, []).append(row[1:])

            # ── Load edges for each weekly window ──────────────────────────
            # One edge query per snapshot (each covers step_days)
            edges_by_date: dict[date, list] = {}
            for snap_date in snapshot_dates:
                window_start = snap_date - timedelta(days=step_days - 1)
                cur.execute(
                    """
                    SELECT source_id::text, target_id::text, COUNT(*) AS cnt
                    FROM raw_events
                    WHERE ts >= %s AND ts < (%s::date + INTERVAL '1 day')
                    GROUP BY source_id, target_id
                    """,
                    (window_start, snap_date),
                )
                edges_by_date[snap_date] = cur.fetchall()

    # ── Assemble TemporalSnapshot objects ─────────────────────────────────
    snapshots: list[TemporalSnapshot] = []
    for snap_date in snapshot_dates:
        snapshot_feature_rows = rows_by_date.get(snap_date, [])
        # snapshot_feature_rows: list of (employee_id, betweenness, degree_in, degree_out, clustering)
        full_rows = [(r[0], r[1], r[2], r[3], r[4]) for r in snapshot_feature_rows]
        x, presence = _build_x(employee_ids, full_rows)
        edge_index, edge_weight = _build_edge_index(
            edges_by_date.get(snap_date, []), id_to_idx
        )

        snapshots.append(
            TemporalSnapshot(
                x=x,
                edge_index=edge_index,
                edge_weight=edge_weight,
                snapshot_date=snap_date,
                presence_mask=presence,
            )
        )

        logger.debug(
            "Snapshot %s: N=%d present=%d E=%d",
            snap_date,
            len(employee_ids),
            int(presence.sum()),
            edge_index.shape[1],
        )

    n_loaded = sum(1 for s in snapshots if s.presence_mask.any())
    logger.info(
        "build_snapshot_sequence: %d weeks ending %s, vocab=%d, loaded=%d/%d",
        n_weeks, end_date, len(employee_ids), n_loaded, n_weeks,
    )
    return snapshots
