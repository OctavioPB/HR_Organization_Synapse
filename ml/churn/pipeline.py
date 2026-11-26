"""DB layer for the calibrated churn model: feature log, training, scoring.

Daily (churn_gnn_score DAG → score_churn):
    1. Build today's feature vector for every active employee
       (feature_builder node features + peer exposure).
    2. Append it to churn_feature_log (point-in-time store, migration 019).
    3. If a trained model exists, write calibrated churn_scores.

Weekly (churn_gnn_train DAG → train_churn_model):
    1. Anchors = logged feature dates spaced CHURN_ANCHOR_STEP_DAYS apart whose
       label window has fully elapsed (anchor + H ≤ as_of).
    2. Labels from departures: employees.left_at, else the earliest
       churn_labels row with churned = true.
    3. fit_churn_model → checkpoint (joblib) with its held-out metrics.

Nothing here recomputes past features from raw_events: those are purged after
90 days, before the label is known, so a recomputation would silently zero
the activity features for every training row (train/serve skew).
"""

from __future__ import annotations

import glob
import json
import logging
import os
from datetime import date, timedelta
from pathlib import Path

import numpy as np

from ml.churn.model import (
    CHURN_FEATURE_NAMES,
    CHURN_HORIZON_DAYS,
    NODE_FEATURE_COLUMNS,
    ChurnModel,
    fit_churn_model,
    risk_tier,
)

logger = logging.getLogger(__name__)

FEATURE_VERSION = "churn_v1"
CHURN_ANCHOR_STEP_DAYS = int(os.environ.get("CHURN_ANCHOR_STEP_DAYS", "14"))
_CHECKPOINT_DIR = os.environ.get("GNN_CHECKPOINT_DIR", "checkpoints")
_TIE_LOOKBACK_DAYS = int(os.environ.get("PEER_TIE_LOOKBACK_DAYS", "90"))  # = raw_events retention


# ─── Loaders ──────────────────────────────────────────────────────────────────


def load_departures(cur) -> dict[str, date]:
    """employee_id → departure date (left_at, else earliest churned label)."""
    cur.execute(
        """
        SELECT e.id::text,
               COALESCE(e.left_at::date, l.first_churn) AS departed
        FROM employees e
        LEFT JOIN (
            SELECT employee_id, MIN(label_date) AS first_churn
            FROM churn_labels WHERE churned = true GROUP BY employee_id
        ) l ON l.employee_id = e.id
        WHERE e.left_at IS NOT NULL OR l.first_churn IS NOT NULL
        """
    )
    return {str(r[0]): r[1] for r in cur.fetchall() if r[1] is not None}


def load_tie_edges(as_of: date, cur, lookback_days: int = _TIE_LOOKBACK_DAYS) -> list[tuple]:
    """Consented interaction volume over the lookback, *including* departed people.

    graph.builder.load_raw_edges keeps active employees only, which is right for
    today's graph but removes exactly the departed peers contagion looks for.
    """
    from graph.edge_weights import weighted_edge_sql

    cur.execute(
        f"""
        SELECT re.source_id::text, re.target_id::text, SUM({weighted_edge_sql("re")}) AS w
        FROM raw_events re
        JOIN employees es ON re.source_id = es.id
        JOIN employees et ON re.target_id = et.id
        WHERE re.ts >= %s AND re.ts < (%s::date + INTERVAL '1 day')
          AND es.consent = true AND et.consent = true
        GROUP BY re.source_id, re.target_id
        """,
        (as_of - timedelta(days=lookback_days), as_of),
    )
    return [(r[0], r[1], float(r[2])) for r in cur.fetchall()]


# ─── Feature log ──────────────────────────────────────────────────────────────


def build_feature_matrix(snapshot_date: date, window_days: int = 30) -> tuple[list[str], np.ndarray, dict]:
    """Today's churn features for active employees, plus the peer-exposure test.

    Returns (employee_ids, X, exposure_by_id) with X columns = CHURN_FEATURE_NAMES.
    """
    from ingestion.db import get_conn
    from ml.churn.contagion import select_peer_exposure
    from ml.features.feature_extractor import extract_features
    from ml.gnn.feature_builder import _build_node_features

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, hire_date,
                       COALESCE(reporting_level, role_level, 0),
                       COALESCE(pto_days_ytd, pto_days_used, 0),
                       COALESCE(tenure_months, 0)
                FROM employees
                WHERE active = true AND consent = true
                ORDER BY id
                """
            )
            employees = [
                {
                    "id": r[0],
                    "hire_date": r[1],
                    "role_level": r[2] or 0,
                    "pto_days_used": r[3] or 0,
                    "tenure_months_hris": r[4] or 0,
                }
                for r in cur.fetchall()
            ]
            departures = load_departures(cur)
            tie_edges = load_tie_edges(snapshot_date, cur)

    if not employees:
        return [], np.zeros((0, len(CHURN_FEATURE_NAMES))), {}

    graph_features = {fv["employee_id"]: fv for fv in extract_features(snapshot_date, window_days)}
    node_x = _build_node_features(employees, graph_features, snapshot_date)
    exposure = select_peer_exposure(tie_edges, departures, snapshot_date)

    ids = [str(e["id"]) for e in employees]
    peer = np.array([exposure[i].exposure if i in exposure else 0.0 for i in ids])
    X = np.column_stack([node_x[:, NODE_FEATURE_COLUMNS], peer])
    return ids, X, exposure


def log_features(ids: list[str], X: np.ndarray, feature_date: date) -> None:
    from ingestion.db import get_conn

    if not ids:
        return
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO churn_feature_log (employee_id, feature_date, feature_version, features)
                VALUES (%s::uuid, %s, %s, %s)
                ON CONFLICT (employee_id, feature_date, feature_version)
                DO UPDATE SET features = EXCLUDED.features, created_at = NOW()
                """,
                [(e, feature_date, FEATURE_VERSION, [float(v) for v in row]) for e, row in zip(ids, X, strict=True)],
            )


def anchor_dates(available: list[date], as_of: date, horizon_days: int, step_days: int) -> list[date]:
    """Logged dates usable as anchors: label window elapsed, ≥ step_days apart (newest first)."""
    out: list[date] = []
    for d in sorted(available, reverse=True):
        if d + timedelta(days=horizon_days) > as_of:
            continue
        if out and (out[-1] - d).days < step_days:
            continue
        out.append(d)
    return out


def load_training_examples(
    as_of: date,
    horizon_days: int = CHURN_HORIZON_DAYS,
    step_days: int = CHURN_ANCHOR_STEP_DAYS,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(X, y, anchor_ordinals) from the feature log and departures."""
    from ingestion.db import get_conn

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT feature_date FROM churn_feature_log WHERE feature_version = %s",
                (FEATURE_VERSION,),
            )
            anchors = anchor_dates([r[0] for r in cur.fetchall()], as_of, horizon_days, step_days)
            if not anchors:
                return np.zeros((0, len(CHURN_FEATURE_NAMES))), np.zeros(0, dtype=int), np.zeros(0, dtype=int)
            departures = load_departures(cur)
            cur.execute(
                """
                SELECT employee_id::text, feature_date, features
                FROM churn_feature_log
                WHERE feature_version = %s AND feature_date = ANY(%s)
                """,
                (FEATURE_VERSION, anchors),
            )
            rows = cur.fetchall()

    X, y, a = [], [], []
    for emp, d, feats in rows:
        left = departures.get(emp)
        if left is not None and left <= d:
            continue  # logged on/after their last day: not at risk
        X.append(feats)
        y.append(int(left is not None and left <= d + timedelta(days=horizon_days)))
        a.append(d.toordinal())
    return np.asarray(X, dtype=float), np.asarray(y, dtype=int), np.asarray(a, dtype=int)


# ─── Train / score ────────────────────────────────────────────────────────────


def train_churn_model(as_of: date) -> tuple[str, dict]:
    """Fit and save the calibrated churn model.  Returns (checkpoint_path, metadata)."""
    import joblib

    X, y, anchors = load_training_examples(as_of)
    model = fit_churn_model(X, y, anchors) if len(y) else None
    if model is None:
        meta = {
            "error": "insufficient_labels",
            "n_examples": int(len(y)),
            "n_positives": int(y.sum()) if len(y) else 0,
            "anchors": int(len(np.unique(anchors))) if len(anchors) else 0,
        }
        logger.warning("train_churn_model: not enough history to train — %s", meta)
        return "", meta

    Path(_CHECKPOINT_DIR).mkdir(parents=True, exist_ok=True)
    path = str(Path(_CHECKPOINT_DIR) / f"churn_logreg_{as_of}.joblib")
    joblib.dump(model, path)
    meta = {"as_of": str(as_of), "feature_version": FEATURE_VERSION, **model.metrics}
    logger.info("train_churn_model: saved %s — %s", path, json.dumps(meta, default=str))
    return path, meta


def latest_model_path() -> str | None:
    candidates = sorted(glob.glob(str(Path(_CHECKPOINT_DIR) / "churn_logreg_*.joblib")))
    return candidates[-1] if candidates else None


def score_churn(snapshot_date: date, window_days: int = 30) -> dict:
    """Log today's features; score with the latest model if one exists."""
    import joblib

    ids, X, exposure = build_feature_matrix(snapshot_date, window_days)
    log_features(ids, X, snapshot_date)

    path = latest_model_path()
    if path is None or not ids:
        logger.info("score_churn: %d feature rows logged; no trained model yet", len(ids))
        return {"logged": len(ids), "scored": 0, "skipped": True}

    model: ChurnModel = joblib.load(path)
    probs = model.predict_proba(X)
    version = Path(path).stem
    rows = []
    for i, emp in enumerate(ids):
        p = float(np.clip(probs[i], 0.0, 1.0))
        ex = exposure.get(emp)
        rows.append(
            (
                emp,
                snapshot_date,
                round(p, 4),
                risk_tier(p, model.base_rate),
                version,
                json.dumps(model.drivers(X[i])),
                round(ex.exposure, 4) if ex else 0.0,
                bool(ex.flagged) if ex else False,
                round(ex.p_value, 4) if ex else None,
            )
        )

    from ingestion.db import get_conn

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO churn_scores
                    (employee_id, scored_at, churn_prob, risk_tier, model_version,
                     risk_drivers, peer_churn_rate, peer_contagion_risk, peer_exposure_p)
                VALUES (%s::uuid, %s, %s, %s, %s, %s::jsonb, %s, %s, %s)
                ON CONFLICT (employee_id, scored_at) DO UPDATE
                    SET churn_prob          = EXCLUDED.churn_prob,
                        risk_tier           = EXCLUDED.risk_tier,
                        model_version       = EXCLUDED.model_version,
                        risk_drivers        = EXCLUDED.risk_drivers,
                        peer_churn_rate     = EXCLUDED.peer_churn_rate,
                        peer_contagion_risk = EXCLUDED.peer_contagion_risk,
                        peer_exposure_p     = EXCLUDED.peer_exposure_p,
                        created_at          = NOW()
                """,
                rows,
            )
    tiers = [r[3] for r in rows]
    return {
        "logged": len(ids),
        "scored": len(rows),
        "high": tiers.count("high"),
        "medium": tiers.count("medium"),
        "low": tiers.count("low"),
        "model_version": version,
    }
