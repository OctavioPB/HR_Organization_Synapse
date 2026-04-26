"""Score all active employees using the latest ChurnGAT checkpoint.

Writes one row per employee to the churn_scores table (UPSERT on employee_id,
scored_at).  Existing rows for today are overwritten so re-runs are idempotent.

Risk tiers
──────────
    high    churn_prob >= 0.6
    medium  churn_prob >= 0.3
    low     churn_prob <  0.3

Usage (CLI):
    python ml/gnn/scorer.py --snapshot-date 2025-04-25

Usage (Airflow):
    from ml.gnn.scorer import score
    score(snapshot_date=date(2025, 4, 25))
"""

from __future__ import annotations

import argparse
import glob
import logging
import os
from datetime import date
from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

logger = logging.getLogger(__name__)

_CHECKPOINT_DIR  = os.environ.get("GNN_CHECKPOINT_DIR", "checkpoints")
_HIGH_THRESHOLD  = float(os.environ.get("CHURN_HIGH_THRESHOLD", "0.6"))
_MED_THRESHOLD   = float(os.environ.get("CHURN_MED_THRESHOLD", "0.3"))


def _tier(prob: float) -> str:
    if prob >= _HIGH_THRESHOLD:
        return "high"
    if prob >= _MED_THRESHOLD:
        return "medium"
    return "low"


def _latest_checkpoint() -> str:
    """Return the most recently created checkpoint file path."""
    pattern = str(Path(_CHECKPOINT_DIR) / "churn_gat_*.pt")
    candidates = sorted(glob.glob(pattern))
    if not candidates:
        raise FileNotFoundError(
            f"No churn_gat_*.pt checkpoint found in {_CHECKPOINT_DIR!r}. "
            "Run trainer.py first."
        )
    return candidates[-1]


def score(
    snapshot_date: date,
    window_days: int = 30,
    checkpoint_path: str | None = None,
    scored_at: date | None = None,
) -> list[dict]:
    """Score all active employees and write results to churn_scores.

    Args:
        snapshot_date: Feature snapshot date for node features.
        window_days: Rolling window for edge construction.
        checkpoint_path: Path to .pt file.  Uses latest if None.
        scored_at: Override the scored_at date (default: today).

    Returns:
        List of dicts with keys: employee_id, churn_prob, risk_tier, model_version.
    """
    import torch
    from ml.gnn.feature_builder import build_graph_data
    from ml.gnn.model import load_checkpoint

    ckpt_path = checkpoint_path or _latest_checkpoint()
    device = "cuda" if torch.cuda.is_available() else "cpu"

    model, metadata = load_checkpoint(ckpt_path, device=device)
    model_version = Path(ckpt_path).stem  # e.g. "churn_gat_2025-04-25"

    logger.info("Scoring with checkpoint %s (metadata=%s)", ckpt_path, metadata)

    # Build features — no labels needed for inference
    dataset = build_graph_data(snapshot_date, window_days, label_date=None)

    if not dataset["employee_ids"]:
        logger.warning("No active employees to score")
        return []

    x = torch.tensor(dataset["x"], dtype=torch.float).to(device)
    edge_index = torch.tensor(dataset["edge_index"], dtype=torch.long).to(device)
    edge_weight = torch.tensor(dataset["edge_weight"], dtype=torch.float).to(device)

    model.eval()
    with torch.no_grad():
        logits = model(x, edge_index, edge_weight)
        probs = torch.sigmoid(logits).cpu().numpy()

    scored_date = scored_at or date.today()
    results: list[dict] = []
    for i, emp_id in enumerate(dataset["employee_ids"]):
        prob = float(np.clip(probs[i], 0.0, 1.0))
        results.append(
            {
                "employee_id": emp_id,
                "churn_prob": round(prob, 4),
                "risk_tier": _tier(prob),
                "model_version": model_version,
            }
        )

    _write_scores(results, scored_date)
    logger.info(
        "Scored %d employees for %s — high=%d medium=%d low=%d",
        len(results),
        scored_date,
        sum(1 for r in results if r["risk_tier"] == "high"),
        sum(1 for r in results if r["risk_tier"] == "medium"),
        sum(1 for r in results if r["risk_tier"] == "low"),
    )
    return results


def _write_scores(rows: list[dict], scored_at: date) -> None:
    """Upsert churn_scores rows (idempotent on employee_id, scored_at)."""
    from ingestion.db import get_conn

    if not rows:
        return

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO churn_scores
                    (employee_id, scored_at, churn_prob, risk_tier, model_version)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (employee_id, scored_at) DO UPDATE
                    SET churn_prob    = EXCLUDED.churn_prob,
                        risk_tier     = EXCLUDED.risk_tier,
                        model_version = EXCLUDED.model_version,
                        created_at    = NOW()
                """,
                [
                    (
                        r["employee_id"],
                        scored_at,
                        r["churn_prob"],
                        r["risk_tier"],
                        r["model_version"],
                    )
                    for r in rows
                ],
            )
        conn.commit()


# ─── CLI ──────────────────────────────────────────────────────────────────────


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="Score employees with ChurnGAT")
    parser.add_argument("--snapshot-date", type=date.fromisoformat, required=True)
    parser.add_argument("--window-days", type=int, default=30)
    parser.add_argument("--checkpoint", type=str, default=None)
    args = parser.parse_args()

    results = score(args.snapshot_date, args.window_days, args.checkpoint)
    print(f"Scored {len(results)} employees.")
    if results:
        high = [r for r in results if r["risk_tier"] == "high"]
        print(f"High risk ({len(high)}): {[r['employee_id'][:8] for r in high[:5]]}")


if __name__ == "__main__":
    main()
