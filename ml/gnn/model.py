"""Graph Attention Network (GAT) for employee churn risk prediction.

Architecture:
    Input  → GATConv(in → hidden*heads)  → ELU  → dropout
           → GATConv(hidden*heads → hidden, heads=1)  → ELU
           → Linear(hidden → 32)  → ReLU  → dropout
           → Linear(32 → 1)  →  (logit, apply sigmoid at inference time)

Design notes:
    - Two attention layers are sufficient for a 1-hop neighbourhood signal.
      A 200-employee org has diameter ~4; deeper nets over-smooth and hurt recall.
    - BCEWithLogitsLoss is applied externally; this module returns raw logits.
    - soft import of torch_geometric: the module raises ImportError with a clear
      message if PyTorch Geometric is not installed, rather than a cryptic
      AttributeError at call time.
"""

from __future__ import annotations

import logging
from typing import Optional

import torch
import torch.nn as nn
import torch.nn.functional as F

try:
    from torch_geometric.nn import GATConv
    _PYG_AVAILABLE = True
except ImportError:
    _PYG_AVAILABLE = False
    GATConv = None  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)

# ─── Constants ────────────────────────────────────────────────────────────────

DEFAULT_HIDDEN: int = 64
DEFAULT_HEADS: int = 4
DEFAULT_DROPOUT: float = 0.3


# ─── Model ────────────────────────────────────────────────────────────────────


class ChurnGAT(nn.Module):
    """Two-layer GAT classifier.

    Args:
        in_channels: Number of input node features (GNN_IN_FEATURES).
        hidden_channels: Per-head hidden size in GATConv layer 1.
        heads: Number of attention heads in layer 1.
        dropout: Dropout applied inside GATConv and in the MLP head.
    """

    def __init__(
        self,
        in_channels: int,
        hidden_channels: int = DEFAULT_HIDDEN,
        heads: int = DEFAULT_HEADS,
        dropout: float = DEFAULT_DROPOUT,
    ) -> None:
        if not _PYG_AVAILABLE:
            raise ImportError(
                "torch_geometric is required for ChurnGAT.  "
                "Install it with: pip install torch-geometric"
            )
        super().__init__()

        self.dropout = dropout

        # Layer 1: multi-head attention, concatenate outputs
        self.conv1 = GATConv(
            in_channels,
            hidden_channels,
            heads=heads,
            dropout=dropout,
            concat=True,
        )

        # Layer 2: single-head attention, average outputs
        self.conv2 = GATConv(
            hidden_channels * heads,
            hidden_channels,
            heads=1,
            dropout=dropout,
            concat=False,
        )

        # MLP head: binary classification
        self.classifier = nn.Sequential(
            nn.Linear(hidden_channels, 32),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(32, 1),
        )

    def forward(
        self,
        x: torch.Tensor,
        edge_index: torch.Tensor,
        edge_weight: Optional[torch.Tensor] = None,
    ) -> torch.Tensor:
        """Forward pass.

        Args:
            x: Node feature matrix, shape (N, in_channels).
            edge_index: COO edge index, shape (2, E).
            edge_weight: Optional per-edge weights, shape (E,).  Ignored by
                         GATConv internally but accepted for API consistency.

        Returns:
            Logit per node, shape (N,).  Apply sigmoid for probabilities.
        """
        x = F.dropout(x, p=self.dropout, training=self.training)
        x = F.elu(self.conv1(x, edge_index))
        x = F.dropout(x, p=self.dropout, training=self.training)
        x = F.elu(self.conv2(x, edge_index))
        return self.classifier(x).squeeze(-1)


# ─── Checkpoint helpers ───────────────────────────────────────────────────────


def save_checkpoint(
    model: ChurnGAT,
    path: str,
    metadata: dict | None = None,
) -> None:
    """Save model weights + optional metadata dict."""
    payload = {
        "state_dict": model.state_dict(),
        "model_config": {
            "in_channels": model.conv1.in_channels,
            "hidden_channels": model.conv2.out_channels,
            "heads": model.conv1.heads,
            "dropout": model.dropout,
        },
        "metadata": metadata or {},
    }
    torch.save(payload, path)
    logger.info("Saved ChurnGAT checkpoint to %s", path)


def load_checkpoint(path: str, device: str = "cpu") -> tuple[ChurnGAT, dict]:
    """Load a ChurnGAT from disk.

    Returns:
        (model, metadata_dict)
    """
    payload = torch.load(path, map_location=device, weights_only=False)
    cfg = payload["model_config"]
    model = ChurnGAT(
        in_channels=cfg["in_channels"],
        hidden_channels=cfg["hidden_channels"],
        heads=cfg["heads"],
        dropout=cfg["dropout"],
    )
    model.load_state_dict(payload["state_dict"])
    model.eval()
    logger.info("Loaded ChurnGAT checkpoint from %s", path)
    return model, payload.get("metadata", {})
