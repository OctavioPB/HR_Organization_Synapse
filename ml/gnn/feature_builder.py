"""Build the PyG Data object used to train and run the Churn GAT.

Node features (GNN_IN_FEATURES = 11) per employee:
    0  tenure_days_norm   — days since hire_date / 3650 (capped at 1.0)
    1  role_level_norm    — role_level / 7.0 (1=IC1 … 7=C-Suite)
    2  pto_days_norm      — pto_days_used / 90.0 (capped at 1.0)
    3  betweenness        — normalised graph betweenness centrality
    4  degree_in          — normalised in-degree
    5  degree_out         — normalised out-degree
    6  clustering         — local clustering coefficient
    7  betweenness_delta  — 7-day betweenness delta
    8  degree_out_delta   — 7-day out-degree delta
    9  entropy_current    — current-week interaction entropy (capped at 1)
    10 entropy_trend      — linear slope of weekly entropy (clipped [-1, 1])

Employees without HR columns (hire_date=NULL, role_level=NULL) get 0.0 for
those features — same as new joiners with no tenure data.

The function ``build_graph_data`` is the main entry point.  It returns:
    - ``x``            torch.FloatTensor  (N, 11)
    - ``edge_index``   torch.LongTensor   (2, E)  directed edges
    - ``edge_weight``  torch.FloatTensor  (E,)    log1p(count)
    - ``y``            torch.FloatTensor  (N,)    0/1 churn labels (NaN for unlabelled)
    - ``employee_ids`` list[str]          ordered node list matching row index
"""

from __future__ import annotations

import logging
import math
from datetime import date, timedelta
from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

logger = logging.getLogger(__name__)

# ─── Constants ────────────────────────────────────────────────────────────────

GNN_IN_FEATURES: int = 11

_MAX_TENURE_DAYS: float = 3650.0   # 10 years → 1.0
_MAX_ROLE_LEVEL: float = 7.0
_MAX_PTO_DAYS: float = 90.0        # rolling 90-day window
_GRAPH_WINDOW_DAYS: int = 30


# ─── Pure helpers ─────────────────────────────────────────────────────────────


def _safe_div(a: float, b: float) -> float:
    return min(a / b, 1.0) if b > 0 else 0.0


def _build_node_features(
    employees: list[dict],
    graph_features: dict[str, dict],
    snapshot_date: date,
) -> np.ndarray:
    """Assemble (N, 11) float32 feature matrix.

    Args:
        employees: Rows from employees table.  Each row must have keys:
            id, hire_date (date | None), role_level (int | None), pto_days_used.
        graph_features: Dict employee_id → feature dict from feature_extractor.
        snapshot_date: Used to compute tenure.

    Returns:
        np.ndarray of shape (N, 11), dtype float32.
    """
    n = len(employees)
    x = np.zeros((n, GNN_IN_FEATURES), dtype=np.float32)

    for i, emp in enumerate(employees):
        emp_id = str(emp["id"])

        # HR features
        hire_date = emp.get("hire_date")
        tenure = (
            (snapshot_date - hire_date).days
            if hire_date is not None
            else 0.0
        )
        role_level = emp.get("role_level") or 0
        pto = emp.get("pto_days_used") or 0

        x[i, 0] = _safe_div(float(tenure), _MAX_TENURE_DAYS)
        x[i, 1] = _safe_div(float(role_level), _MAX_ROLE_LEVEL)
        x[i, 2] = _safe_div(float(pto), _MAX_PTO_DAYS)

        # Graph features (default 0.0 if employee has no snapshot)
        gf = graph_features.get(emp_id, {})
        x[i, 3]  = float(gf.get("betweenness", 0.0))
        x[i, 4]  = float(gf.get("degree_in", 0.0))
        x[i, 5]  = float(gf.get("degree_out", 0.0))
        x[i, 6]  = float(gf.get("clustering", 0.0))
        x[i, 7]  = float(np.clip(gf.get("betweenness_delta_7d", 0.0), -1.0, 1.0))
        x[i, 8]  = float(np.clip(gf.get("degree_out_delta_7d", 0.0), -1.0, 1.0))
        x[i, 9]  = float(min(gf.get("entropy_current", 0.0), 1.0))
        x[i, 10] = float(np.clip(gf.get("entropy_trend", 0.0), -1.0, 1.0))

    return x


def _build_edge_index(
    edge_rows: list[tuple],
    id_to_idx: dict[str, int],
) -> tuple[np.ndarray, np.ndarray]:
    """Build COO edge index and weight arrays from raw_event aggregate rows.

    Args:
        edge_rows: Tuples of (source_id, target_id, interaction_count).
        id_to_idx: Map from employee UUID string → row index in feature matrix.

    Returns:
        edge_index: int64 array of shape (2, E).
        edge_weight: float32 array of shape (E,) — log1p(count).
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

    edge_index = np.array([src_list, dst_list], dtype=np.int64)
    edge_weight = np.array(wt_list, dtype=np.float32)
    return edge_index, edge_weight


def _build_labels(
    employees: list[dict],
    id_to_idx: dict[str, int],
    label_rows: list[tuple],
) -> np.ndarray:
    """Build (N,) float32 label array.  NaN for unlabelled nodes.

    Args:
        employees: Same ordered list used for the feature matrix.
        id_to_idx: Map from employee UUID string → row index.
        label_rows: Tuples of (employee_id, churned bool).
    """
    labels = np.full(len(employees), float("nan"), dtype=np.float32)
    for emp_id, churned in label_rows:
        idx = id_to_idx.get(str(emp_id))
        if idx is not None:
            labels[idx] = 1.0 if churned else 0.0
    return labels


# ─── DB-backed entry point ────────────────────────────────────────────────────


def build_graph_data(
    snapshot_date: date,
    window_days: int = _GRAPH_WINDOW_DAYS,
    label_date: date | None = None,
) -> dict:
    """Build the full graph dataset for training or inference.

    Queries:
        employees              → node list + HR features
        graph_snapshots        → graph metric features
        raw_events             → edge list
        churn_labels           → training labels (optional)

    Args:
        snapshot_date: Date of the graph_snapshot to use as feature source.
        window_days: Rolling window for raw_events aggregation.
        label_date: If provided, loads churn_labels on or before this date.
                    Pass None for inference (labels will all be NaN).

    Returns:
        Dict with keys:
            x             np.ndarray (N, 11)
            edge_index    np.ndarray (2, E)
            edge_weight   np.ndarray (E,)
            y             np.ndarray (N,)   NaN where no label
            employee_ids  list[str]         node order
            snapshot_date date
    """
    from ingestion.db import get_conn
    from ml.features.feature_extractor import extract_features

    window_start = snapshot_date - timedelta(days=window_days)

    with get_conn() as conn:
        with conn.cursor() as cur:
            # Active employees only
            cur.execute(
                """
                SELECT id, hire_date, role_level, pto_days_used
                FROM employees
                WHERE active = true
                ORDER BY id
                """,
            )
            employees = [
                {
                    "id": row[0],
                    "hire_date": row[1],
                    "role_level": row[2],
                    "pto_days_used": row[3] or 0,
                }
                for row in cur.fetchall()
            ]

            # Directed interaction counts for edge construction
            cur.execute(
                """
                SELECT source_id::text, target_id::text, COUNT(*) AS cnt
                FROM raw_events
                WHERE ts >= %s AND ts < (%s::date + INTERVAL '1 day')
                GROUP BY source_id, target_id
                """,
                (window_start, snapshot_date),
            )
            edge_rows = cur.fetchall()

            # Churn labels (only if label_date supplied)
            label_rows: list[tuple] = []
            if label_date is not None:
                cur.execute(
                    """
                    SELECT DISTINCT ON (employee_id) employee_id::text, churned
                    FROM churn_labels
                    WHERE label_date <= %s
                    ORDER BY employee_id, label_date DESC
                    """,
                    (label_date,),
                )
                label_rows = cur.fetchall()

    if not employees:
        logger.warning("build_graph_data: no active employees found")
        return {
            "x": np.zeros((0, GNN_IN_FEATURES), dtype=np.float32),
            "edge_index": np.empty((2, 0), dtype=np.int64),
            "edge_weight": np.empty((0,), dtype=np.float32),
            "y": np.empty((0,), dtype=np.float32),
            "employee_ids": [],
            "snapshot_date": snapshot_date,
        }

    # Graph features from the existing feature extractor
    graph_feature_list = extract_features(snapshot_date, window_days)
    graph_features = {fv["employee_id"]: fv for fv in graph_feature_list}

    id_to_idx: dict[str, int] = {str(emp["id"]): i for i, emp in enumerate(employees)}
    employee_ids = [str(emp["id"]) for emp in employees]

    x = _build_node_features(employees, graph_features, snapshot_date)
    edge_index, edge_weight = _build_edge_index(edge_rows, id_to_idx)
    y = _build_labels(employees, id_to_idx, label_rows)

    logger.info(
        "build_graph_data: N=%d E=%d labelled=%d snapshot=%s",
        len(employees),
        edge_index.shape[1],
        int(np.isfinite(y).sum()),
        snapshot_date,
    )
    return {
        "x": x,
        "edge_index": edge_index,
        "edge_weight": edge_weight,
        "y": y,
        "employee_ids": employee_ids,
        "snapshot_date": snapshot_date,
    }
