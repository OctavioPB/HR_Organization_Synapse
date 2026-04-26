"""Score all employees with TemporalRiskGNN: reconstruction error as anomaly signal.

Inference procedure
───────────────────
Given n_weeks historical snapshots ending at (end_date - step_days) and the
target snapshot at end_date:

    1. Run the trained model on the n_weeks history → predicted X̂_{end_date}
    2. Compute per-node reconstruction error = MSE(X̂, X_actual)
    3. Normalise to [0, 1] using per-run max (or historical 99th percentile)
    4. Compute 4-week trend slope of the normalised scores
    5. Write to temporal_anomaly_scores (UPSERT on employee_id, scored_at)

Anomaly tiers:
    high    normalised_score >= 0.6
    medium  normalised_score >= 0.3
    low     normalised_score <  0.3

Usage (CLI):
    python graph/temporal/scorer.py --snapshot-date 2025-04-25

Usage (Airflow):
    from graph.temporal.scorer import score
    score(end_date=date(2025, 4, 25))
"""

from __future__ import annotations

import argparse
import glob
import logging
import os
from datetime import date, timedelta
from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

logger = logging.getLogger(__name__)

_CHECKPOINT_DIR  = os.environ.get("TGNN_CHECKPOINT_DIR", "checkpoints")
_N_WEEKS         = int(os.environ.get("TGNN_N_WEEKS", "8"))
_HIGH_THRESHOLD  = float(os.environ.get("TEMPORAL_HIGH_THRESHOLD", "0.6"))
_MED_THRESHOLD   = float(os.environ.get("TEMPORAL_MED_THRESHOLD", "0.3"))


def _tier(score: float) -> str:
    if score >= _HIGH_THRESHOLD:
        return "high"
    if score >= _MED_THRESHOLD:
        return "medium"
    return "low"


def _latest_checkpoint() -> str:
    pattern = str(Path(_CHECKPOINT_DIR) / "temporal_risk_gnn_*.pt")
    candidates = sorted(glob.glob(pattern))
    if not candidates:
        raise FileNotFoundError(
            f"No temporal_risk_gnn_*.pt checkpoint found in {_CHECKPOINT_DIR!r}. "
            "Run temporal/trainer.py first."
        )
    return candidates[-1]


def _compute_trend_slopes(
    employee_ids: list[str],
    scored_at: date,
    current_scores: np.ndarray,
    n_lookback_weeks: int = 4,
) -> np.ndarray:
    """Load the last n_lookback_weeks anomaly scores and compute linear slope.

    Returns (N,) float array.  Zero for employees with < 2 historical points.
    """
    from ingestion.db import get_conn

    lookback_dates = [
        scored_at - timedelta(weeks=w)
        for w in range(1, n_lookback_weeks + 1)
    ]

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT employee_id::text, scored_at, anomaly_score
                FROM temporal_anomaly_scores
                WHERE employee_id = ANY(%s::uuid[])
                  AND scored_at = ANY(%s)
                ORDER BY employee_id, scored_at
                """,
                (employee_ids, lookback_dates),
            )
            rows = cur.fetchall()

    history: dict[str, list[float]] = {eid: [] for eid in employee_ids}
    for emp_id, _, a_score in rows:
        history[str(emp_id)].append(float(a_score))

    slopes = np.zeros(len(employee_ids), dtype=np.float32)
    for i, emp_id in enumerate(employee_ids):
        past = history.get(emp_id, [])
        # Append current score as the last point
        series = past + [float(current_scores[i])]
        if len(series) >= 2:
            x = np.arange(len(series), dtype=float)
            slopes[i] = float(np.polyfit(x, series, 1)[0])
    return slopes


def score(
    end_date: date,
    n_weeks: int = _N_WEEKS,
    step_days: int = 7,
    checkpoint_path: str | None = None,
    scored_at: date | None = None,
) -> list[dict]:
    """Run temporal anomaly scoring for all employees.

    Args:
        end_date: Target snapshot date (the date being scored).
        n_weeks: Input sequence length (same as training).
        step_days: Days between consecutive snapshots.
        checkpoint_path: Path to .pt file; uses latest if None.
        scored_at: Override the scored_at date (default: end_date).

    Returns:
        List of dicts with keys:
            employee_id, anomaly_score, anomaly_tier, reconstruction_error,
            trend_slope, model_version.
    """
    import torch
    from graph.temporal.sequence_builder import build_snapshot_sequence
    from graph.temporal.model import load_checkpoint

    ckpt_path = checkpoint_path or _latest_checkpoint()
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model, metadata = load_checkpoint(ckpt_path, device=device)
    model_version = Path(ckpt_path).stem

    scored_date = scored_at or end_date

    # ── Build sequence: n_weeks history → predict end_date ─────────────
    # Load (n_weeks + 1) snapshots: history[0..n_weeks-1] + target[n_weeks]
    all_snaps = build_snapshot_sequence(
        end_date=end_date,
        n_weeks=n_weeks + 1,
        step_days=step_days,
    )

    if len(all_snaps) < 2:
        logger.warning("Insufficient snapshots for scoring at %s", end_date)
        return []

    # Use all but the last as input; last as ground truth
    input_snaps = all_snaps[:-1]
    target_snap = all_snaps[-1]

    if not target_snap.presence_mask.any():
        logger.warning("Target snapshot %s has no active employees", end_date)
        return []

    employee_ids = []
    # Reload vocabulary from the sequence builder
    vocab_size = target_snap.x.shape[0]

    # We need to know the ordered employee_id list.
    # Re-derive it from DB using the same logic as sequence_builder.
    from ingestion.db import get_conn
    with get_conn() as conn:
        with conn.cursor() as cur:
            snap_dates = [s.snapshot_date for s in all_snaps]
            cur.execute(
                """
                SELECT DISTINCT employee_id::text
                FROM graph_snapshots
                WHERE snapshot_date = ANY(%s)
                ORDER BY employee_id
                """,
                (snap_dates,),
            )
            employee_ids = [r[0] for r in cur.fetchall()]

    if len(employee_ids) != vocab_size:
        logger.error(
            "Vocabulary mismatch: sequence_builder N=%d, re-derived N=%d. "
            "Aborting — re-train the model.",
            vocab_size, len(employee_ids),
        )
        return []

    # ── Inference ─────────────────────────────────────────────────────────
    input_tensors = [
        {
            "x":          torch.tensor(s.x, dtype=torch.float).to(device),
            "edge_index": torch.tensor(s.edge_index, dtype=torch.long).to(device),
        }
        for s in input_snaps
    ]
    x_target = torch.tensor(target_snap.x, dtype=torch.float).to(device)
    mask     = torch.tensor(target_snap.presence_mask, dtype=torch.bool).to(device)

    model.eval()
    with torch.no_grad():
        x_pred, _ = model(input_tensors)
        per_node_mse = model.reconstruction_error(x_pred, x_target, presence_mask=mask)

    raw_errors: np.ndarray = per_node_mse.cpu().numpy()  # (N,)

    # ── Normalise to [0, 1] ────────────────────────────────────────────────
    present_errors = raw_errors[target_snap.presence_mask]
    p99 = float(np.percentile(present_errors, 99)) if len(present_errors) > 0 else 1.0
    norm_denom = max(p99, 1e-8)
    normalised = np.clip(raw_errors / norm_denom, 0.0, 1.0).astype(np.float32)

    # ── Trend slopes ──────────────────────────────────────────────────────
    slopes = _compute_trend_slopes(employee_ids, scored_date, normalised)

    # ── Assemble results ──────────────────────────────────────────────────
    results: list[dict] = []
    for i, emp_id in enumerate(employee_ids):
        if not target_snap.presence_mask[i]:
            continue  # absent from target snapshot — skip
        results.append(
            {
                "employee_id": emp_id,
                "anomaly_score":       round(float(normalised[i]), 4),
                "anomaly_tier":        _tier(float(normalised[i])),
                "reconstruction_error": round(float(raw_errors[i]), 6),
                "trend_slope":         round(float(slopes[i]), 6),
                "model_version":       model_version,
            }
        )

    _write_scores(results, scored_date, n_weeks)

    high   = sum(1 for r in results if r["anomaly_tier"] == "high")
    medium = sum(1 for r in results if r["anomaly_tier"] == "medium")
    logger.info(
        "Temporal scoring done — %d employees scored  high=%d medium=%d low=%d",
        len(results), high, medium, len(results) - high - medium,
    )
    return results


def _write_scores(rows: list[dict], scored_at: date, n_weeks: int) -> None:
    """Upsert temporal_anomaly_scores (idempotent)."""
    from ingestion.db import get_conn

    if not rows:
        return

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO temporal_anomaly_scores
                    (employee_id, scored_at, anomaly_score, anomaly_tier,
                     reconstruction_error, trend_slope, model_version, n_weeks)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (employee_id, scored_at) DO UPDATE
                    SET anomaly_score        = EXCLUDED.anomaly_score,
                        anomaly_tier         = EXCLUDED.anomaly_tier,
                        reconstruction_error = EXCLUDED.reconstruction_error,
                        trend_slope          = EXCLUDED.trend_slope,
                        model_version        = EXCLUDED.model_version,
                        n_weeks              = EXCLUDED.n_weeks,
                        created_at           = NOW()
                """,
                [
                    (
                        r["employee_id"],
                        scored_at,
                        r["anomaly_score"],
                        r["anomaly_tier"],
                        r["reconstruction_error"],
                        r["trend_slope"],
                        r["model_version"],
                        n_weeks,
                    )
                    for r in rows
                ],
            )
        conn.commit()


# ─── CLI ──────────────────────────────────────────────────────────────────────


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="Score employees with TemporalRiskGNN")
    parser.add_argument("--snapshot-date", type=date.fromisoformat, required=True)
    parser.add_argument("--n-weeks", type=int, default=_N_WEEKS)
    parser.add_argument("--checkpoint", type=str, default=None)
    args = parser.parse_args()

    results = score(args.snapshot_date, args.n_weeks, checkpoint_path=args.checkpoint)
    print(f"Scored {len(results)} employees.")
    high = [r for r in results if r["anomaly_tier"] == "high"]
    print(f"High-tier anomalies ({len(high)}):")
    for r in sorted(high, key=lambda x: -x["anomaly_score"])[:5]:
        print(f"  {r['employee_id'][:8]}…  score={r['anomaly_score']:.3f}  trend={r['trend_slope']:+.4f}")


if __name__ == "__main__":
    main()
