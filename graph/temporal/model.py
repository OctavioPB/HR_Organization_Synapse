"""Temporal Risk GNN: GConvGRU-based model for graph trajectory anomaly detection.

Architecture (unsupervised reconstruction)
──────────────────────────────────────────
Given a sequence of T-1 weekly graph snapshots, the model predicts the T-th
snapshot's node features.  At inference time, reconstruction error against the
actual T-th snapshot is the anomaly signal.

  For t = 1 … T-1:
    H_t = GConvGRU(X_t, edge_index_t, H_{t-1})   # (N, hidden)

  X̂_T = Linear(H_{T-1})                           # (N, in_channels)
  Loss = MSE(X̂_T, X_T) masked to present nodes

GConvGRU (Seo et al. 2018) learns a graph-aware gating mechanism: the reset
and update gates are computed via Chebyshev graph convolutions rather than
standard matrix products.  This allows the model to weight neighbourhood
information differently from self-information when deciding what to remember.

Soft dependency: torch_geometric_temporal.  Tests skip if it is absent.
"""

from __future__ import annotations

import logging
from typing import Optional

import torch
import torch.nn as nn

try:
    from torch_geometric_temporal.nn.recurrent import GConvGRU
    _TGNN_AVAILABLE = True
except ImportError:
    _TGNN_AVAILABLE = False
    GConvGRU = None  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)

# ─── Constants ────────────────────────────────────────────────────────────────

DEFAULT_HIDDEN: int = 32
DEFAULT_K: int = 1       # Chebyshev filter order; K=1 → simple 1-hop convolution


# ─── Model ────────────────────────────────────────────────────────────────────


class TemporalRiskGNN(nn.Module):
    """GConvGRU-based temporal anomaly detector.

    Args:
        in_channels: Number of node features per snapshot (TEMPORAL_IN_FEATURES).
        hidden_channels: GRU hidden state dimension.
        K: Chebyshev filter order for GConvGRU.
        dropout: Applied to the reconstruction head.
    """

    def __init__(
        self,
        in_channels: int,
        hidden_channels: int = DEFAULT_HIDDEN,
        K: int = DEFAULT_K,
        dropout: float = 0.1,
    ) -> None:
        if not _TGNN_AVAILABLE:
            raise ImportError(
                "torch_geometric_temporal is required for TemporalRiskGNN.  "
                "Install it with: pip install torch-geometric-temporal"
            )
        super().__init__()
        self.in_channels = in_channels
        self.hidden_channels = hidden_channels
        self.dropout = dropout

        self.gconv_gru = GConvGRU(in_channels, hidden_channels, K)

        # Reconstruction head: predict X_T from H_{T-1}
        self.decoder = nn.Sequential(
            nn.Linear(hidden_channels, hidden_channels),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_channels, in_channels),
        )

    def forward(
        self,
        snapshots: list[dict],
    ) -> tuple[torch.Tensor, torch.Tensor]:
        """Process a sequence of T snapshots.

        Args:
            snapshots: List of dicts, each with:
                "x"           torch.FloatTensor  (N, in_channels)
                "edge_index"  torch.LongTensor   (2, E)

        Returns:
            x_pred:   (N, in_channels) — predicted features at step T+1
            h_final:  (N, hidden_channels) — final GRU hidden state for step T
        """
        H: Optional[torch.Tensor] = None

        for snap in snapshots:
            x = snap["x"]           # (N, F)
            edge_index = snap["edge_index"]  # (2, E)

            H = self.gconv_gru(x, edge_index, H)

        # H is now H_{T}; decode to predict X_{T+1}
        x_pred = self.decoder(H)
        return x_pred, H

    def reconstruction_error(
        self,
        x_pred: torch.Tensor,
        x_target: torch.Tensor,
        presence_mask: Optional[torch.Tensor] = None,
    ) -> torch.Tensor:
        """Per-node mean squared reconstruction error.

        Args:
            x_pred:       (N, F) predicted features.
            x_target:     (N, F) actual features.
            presence_mask:(N,) boolean; only present nodes contribute to loss.
                          Pass None to include all nodes.

        Returns:
            Per-node MSE tensor (N,).  Absent nodes get 0.0.
        """
        per_node_mse = ((x_pred - x_target) ** 2).mean(dim=-1)  # (N,)
        if presence_mask is not None:
            per_node_mse = per_node_mse * presence_mask.float()
        return per_node_mse

    def scalar_loss(
        self,
        x_pred: torch.Tensor,
        x_target: torch.Tensor,
        presence_mask: Optional[torch.Tensor] = None,
    ) -> torch.Tensor:
        """Mean scalar loss over present nodes (for backprop)."""
        per_node = self.reconstruction_error(x_pred, x_target, presence_mask)
        if presence_mask is not None and presence_mask.any():
            return per_node.sum() / presence_mask.float().sum()
        return per_node.mean()


# ─── Checkpoint helpers ───────────────────────────────────────────────────────


def save_checkpoint(
    model: TemporalRiskGNN,
    path: str,
    metadata: dict | None = None,
) -> None:
    payload = {
        "state_dict": model.state_dict(),
        "model_config": {
            "in_channels": model.in_channels,
            "hidden_channels": model.hidden_channels,
            "K": model.gconv_gru.K,
            "dropout": model.dropout,
        },
        "metadata": metadata or {},
    }
    torch.save(payload, path)
    logger.info("Saved TemporalRiskGNN checkpoint to %s", path)


def load_checkpoint(path: str, device: str = "cpu") -> tuple[TemporalRiskGNN, dict]:
    """Load a TemporalRiskGNN from disk.

    Returns:
        (model, metadata_dict)
    """
    payload = torch.load(path, map_location=device, weights_only=False)
    cfg = payload["model_config"]
    model = TemporalRiskGNN(
        in_channels=cfg["in_channels"],
        hidden_channels=cfg["hidden_channels"],
        K=cfg["K"],
        dropout=cfg["dropout"],
    )
    model.load_state_dict(payload["state_dict"])
    model.eval()
    logger.info("Loaded TemporalRiskGNN checkpoint from %s", path)
    return model, payload.get("metadata", {})
