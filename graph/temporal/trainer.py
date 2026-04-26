"""Training loop for TemporalRiskGNN.

Training strategy (unsupervised reconstruction)
────────────────────────────────────────────────
Given a rolling window of (n_weeks + 1) snapshots:
    Input  : weeks 1 … T-1
    Target : week T

The model learns to predict week T's graph features from the prior T-1 weeks'
trajectory.  Employees absent from week T are excluded from the MSE loss via
presence_mask.

A long history window (default: 12 weeks + 1 target = 13 total) is sliced into
multiple (n_weeks, target) pairs by a sliding window over the loaded snapshots,
giving one training sample per slide step.

Early stopping monitors the validation reconstruction loss.  The training set
is the first 80% of available slide windows; the validation set is the remaining
20%.

Usage (CLI):
    python graph/temporal/trainer.py --snapshot-date 2025-04-25

Usage (Airflow):
    from graph.temporal.trainer import train
    checkpoint_path, metrics = train(end_date=date(2025, 4, 25))
"""

from __future__ import annotations

import argparse
import logging
import os
from datetime import date, timedelta
from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

logger = logging.getLogger(__name__)

# ─── Hyperparameters ──────────────────────────────────────────────────────────

_N_WEEKS          = int(os.environ.get("TGNN_N_WEEKS", "8"))
_HISTORY_WEEKS    = int(os.environ.get("TGNN_HISTORY_WEEKS", "24"))  # total history to load
_N_EPOCHS         = int(os.environ.get("TGNN_EPOCHS", "150"))
_LR               = float(os.environ.get("TGNN_LR", "1e-3"))
_WEIGHT_DECAY     = float(os.environ.get("TGNN_WEIGHT_DECAY", "1e-5"))
_HIDDEN           = int(os.environ.get("TGNN_HIDDEN", "32"))
_K                = int(os.environ.get("TGNN_K", "1"))
_DROPOUT          = float(os.environ.get("TGNN_DROPOUT", "0.1"))
_PATIENCE         = int(os.environ.get("TGNN_PATIENCE", "15"))
_VAL_FRACTION     = float(os.environ.get("TGNN_VAL_FRACTION", "0.2"))
_CHECKPOINT_DIR   = os.environ.get("TGNN_CHECKPOINT_DIR", "checkpoints")


# ─── Helper ───────────────────────────────────────────────────────────────────


def _snapshots_to_tensors(snapshots, device: str) -> list[dict]:
    """Convert TemporalSnapshot list to list of {x, edge_index} tensor dicts."""
    import torch
    return [
        {
            "x":          torch.tensor(s.x, dtype=torch.float).to(device),
            "edge_index": torch.tensor(s.edge_index, dtype=torch.long).to(device),
        }
        for s in snapshots
    ]


# ─── Training function ────────────────────────────────────────────────────────


def train(
    end_date: date,
    n_weeks: int = _N_WEEKS,
    history_weeks: int = _HISTORY_WEEKS,
    step_days: int = 7,
) -> tuple[str, dict]:
    """Train TemporalRiskGNN and save the best checkpoint.

    Args:
        end_date: Last date in the training window.
        n_weeks: Input sequence length (T-1 snapshots → predict week T).
        history_weeks: Total weeks to load for sliding-window training.
        step_days: Days between consecutive snapshots.

    Returns:
        (checkpoint_path, metrics_dict)
    """
    import torch
    from graph.temporal.sequence_builder import build_snapshot_sequence, TEMPORAL_IN_FEATURES
    from graph.temporal.model import TemporalRiskGNN, save_checkpoint

    # Load total_weeks snapshots; each slide window = (n_weeks input + 1 target)
    total_weeks = history_weeks + 1
    history_start = end_date - timedelta(days=(total_weeks - 1) * step_days)

    logger.info(
        "Loading %d weekly snapshots from %s to %s …",
        total_weeks, history_start, end_date,
    )
    all_snapshots = build_snapshot_sequence(
        end_date=end_date,
        n_weeks=total_weeks,
        step_days=step_days,
    )

    if len(all_snapshots) < n_weeks + 1:
        msg = (
            f"Insufficient snapshots: need {n_weeks + 1}, "
            f"got {len(all_snapshots)}.  Run graph_builder_dag for more dates."
        )
        logger.warning(msg)
        return "", {"error": "insufficient_snapshots", "n_available": len(all_snapshots)}

    # ── Sliding window samples ────────────────────────────────────────────
    # Each sample: (input = all_snapshots[i:i+n_weeks], target = all_snapshots[i+n_weeks])
    n_samples = len(all_snapshots) - n_weeks
    n_val = max(1, int(n_samples * _VAL_FRACTION))
    n_train = n_samples - n_val

    logger.info(
        "Sliding windows: total=%d train=%d val=%d", n_samples, n_train, n_val
    )

    device = "cuda" if torch.cuda.is_available() else "cpu"

    model = TemporalRiskGNN(
        in_channels=TEMPORAL_IN_FEATURES,
        hidden_channels=_HIDDEN,
        K=_K,
        dropout=_DROPOUT,
    ).to(device)

    optimizer = torch.optim.Adam(model.parameters(), lr=_LR, weight_decay=_WEIGHT_DECAY)

    best_val_loss = float("inf")
    best_state: dict | None = None
    patience_counter = 0
    best_epoch = 0

    for epoch in range(1, _N_EPOCHS + 1):
        # ── Train ──────────────────────────────────────────────────────────
        model.train()
        train_loss_total = 0.0

        for i in range(n_train):
            input_snaps  = all_snapshots[i : i + n_weeks]
            target_snap  = all_snapshots[i + n_weeks]

            input_tensors = _snapshots_to_tensors(input_snaps, device)
            x_target = torch.tensor(target_snap.x, dtype=torch.float).to(device)
            mask     = torch.tensor(target_snap.presence_mask, dtype=torch.bool).to(device)

            optimizer.zero_grad()
            x_pred, _ = model(input_tensors)
            loss = model.scalar_loss(x_pred, x_target, presence_mask=mask)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
            optimizer.step()
            train_loss_total += loss.item()

        train_loss = train_loss_total / n_train

        # ── Validate ───────────────────────────────────────────────────────
        model.eval()
        val_loss_total = 0.0
        with torch.no_grad():
            for i in range(n_train, n_samples):
                input_snaps  = all_snapshots[i : i + n_weeks]
                target_snap  = all_snapshots[i + n_weeks]

                input_tensors = _snapshots_to_tensors(input_snaps, device)
                x_target = torch.tensor(target_snap.x, dtype=torch.float).to(device)
                mask     = torch.tensor(target_snap.presence_mask, dtype=torch.bool).to(device)

                x_pred, _ = model(input_tensors)
                val_loss = model.scalar_loss(x_pred, x_target, presence_mask=mask)
                val_loss_total += val_loss.item()

        val_loss = val_loss_total / n_val

        if epoch % 30 == 0 or epoch == 1:
            logger.info(
                "Epoch %d/%d  train_loss=%.6f  val_loss=%.6f",
                epoch, _N_EPOCHS, train_loss, val_loss,
            )

        if val_loss < best_val_loss - 1e-6:
            best_val_loss = val_loss
            best_epoch = epoch
            best_state = {k: v.clone() for k, v in model.state_dict().items()}
            patience_counter = 0
        else:
            patience_counter += 1
            if patience_counter >= _PATIENCE:
                logger.info("Early stopping at epoch %d", epoch)
                break

    if best_state is not None:
        model.load_state_dict(best_state)

    logger.info(
        "Training done — best_epoch=%d best_val_loss=%.6f",
        best_epoch, best_val_loss,
    )

    # ── Save checkpoint ────────────────────────────────────────────────────
    Path(_CHECKPOINT_DIR).mkdir(parents=True, exist_ok=True)
    ckpt_path = str(Path(_CHECKPOINT_DIR) / f"temporal_risk_gnn_{end_date}.pt")
    metadata = {
        "end_date": str(end_date),
        "n_weeks": n_weeks,
        "history_weeks": history_weeks,
        "best_epoch": best_epoch,
        "val_loss": round(best_val_loss, 6),
        "n_train_windows": n_train,
        "n_val_windows": n_val,
    }
    save_checkpoint(model, ckpt_path, metadata=metadata)

    return ckpt_path, metadata


# ─── CLI ──────────────────────────────────────────────────────────────────────


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="Train TemporalRiskGNN")
    parser.add_argument("--snapshot-date", type=date.fromisoformat, required=True)
    parser.add_argument("--n-weeks", type=int, default=_N_WEEKS)
    parser.add_argument("--history-weeks", type=int, default=_HISTORY_WEEKS)
    args = parser.parse_args()

    ckpt, metrics = train(args.snapshot_date, args.n_weeks, args.history_weeks)
    print(f"Checkpoint: {ckpt}")
    print(f"Metrics:    {metrics}")


if __name__ == "__main__":
    main()
