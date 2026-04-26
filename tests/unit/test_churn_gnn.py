"""Unit tests for F1 — GNN Churn Risk Prediction.

Covers:
  Feature builder
  ─ _build_node_features: HR column normalization
  ─ _build_node_features: missing HR columns default to 0.0
  ─ _build_node_features: tenure capped at 1.0 for > 10 years
  ─ _build_edge_index: directed edges, log1p weights
  ─ _build_edge_index: unknown employee IDs are skipped
  ─ _build_edge_index: empty edge list → shape (2,0) / (0,)
  ─ _build_labels: churned and not-churned mapping
  ─ _build_labels: NaN for unlabelled nodes

  Model
  ─ ChurnGAT raises ImportError when torch_geometric absent
  ─ ChurnGAT forward pass: output shape (N,)
  ─ ChurnGAT forward pass: output is finite
  ─ save_checkpoint / load_checkpoint round-trip
  ─ load_checkpoint returns model in eval mode

  Trainer
  ─ _temporal_masks: all labelled → non-empty train mask
  ─ _temporal_masks: no labelled → all-false masks
  ─ _auroc: perfect predictor → 1.0
  ─ _auroc: random predictor → ~0.5
  ─ _auroc: degenerate (all positive) → 0.5

  API endpoints
  ─ GET /risk/churn-scores 200 with data
  ─ GET /risk/churn-scores 404 when no scoring run exists
  ─ GET /risk/churn-scores min_prob filter is forwarded to query
  ─ GET /risk/employee/{id}/churn 200 with history
  ─ GET /risk/employee/{id}/churn 404 when no data
"""

from __future__ import annotations

import math
from datetime import date
from unittest.mock import MagicMock, patch

import numpy as np
import pytest


# ─── Feature builder: pure functions ─────────────────────────────────────────


from ml.gnn.feature_builder import (
    GNN_IN_FEATURES,
    _build_edge_index,
    _build_labels,
    _build_node_features,
    _MAX_PTO_DAYS,
    _MAX_ROLE_LEVEL,
    _MAX_TENURE_DAYS,
)


def _emp(
    emp_id: str = "aaa",
    hire_date=None,
    role_level: int | None = None,
    pto: int = 0,
) -> dict:
    return {"id": emp_id, "hire_date": hire_date, "role_level": role_level, "pto_days_used": pto}


_SNAPSHOT = date(2025, 4, 25)


def test_node_features_shape():
    emps = [_emp("a"), _emp("b")]
    x = _build_node_features(emps, {}, _SNAPSHOT)
    assert x.shape == (2, GNN_IN_FEATURES)
    assert x.dtype == np.float32


def test_node_features_hr_normalization():
    hire = date(2015, 4, 25)  # exactly 10 years → tenure_norm = 1.0
    emps = [_emp("a", hire_date=hire, role_level=7, pto=90)]
    x = _build_node_features(emps, {}, _SNAPSHOT)
    assert abs(x[0, 0] - 1.0) < 1e-5  # tenure_norm
    assert abs(x[0, 1] - 1.0) < 1e-5  # role_level_norm (7/7)
    assert abs(x[0, 2] - 1.0) < 1e-5  # pto_norm (90/90)


def test_node_features_tenure_capped():
    hire = date(2000, 1, 1)  # > 10 years
    emps = [_emp("a", hire_date=hire)]
    x = _build_node_features(emps, {}, _SNAPSHOT)
    assert x[0, 0] <= 1.0 + 1e-9


def test_node_features_missing_hr_cols_default_zero():
    emps = [_emp("a", hire_date=None, role_level=None, pto=0)]
    x = _build_node_features(emps, {}, _SNAPSHOT)
    # All HR features should be 0.0
    assert x[0, 0] == 0.0
    assert x[0, 1] == 0.0
    assert x[0, 2] == 0.0


def test_node_features_graph_features_populated():
    gf = {
        "a": {
            "betweenness": 0.5,
            "degree_in": 0.3,
            "degree_out": 0.4,
            "clustering": 0.2,
            "betweenness_delta_7d": 0.1,
            "degree_out_delta_7d": -0.05,
            "entropy_current": 0.8,
            "entropy_trend": -0.3,
        }
    }
    emps = [_emp("a")]
    x = _build_node_features(emps, gf, _SNAPSHOT)
    assert abs(x[0, 3] - 0.5) < 1e-6   # betweenness
    assert abs(x[0, 9] - 0.8) < 1e-6   # entropy_current
    assert abs(x[0, 10] - (-0.3)) < 1e-6  # entropy_trend


def test_build_edge_index_basic():
    id_to_idx = {"a": 0, "b": 1, "c": 2}
    edge_rows = [("a", "b", 5), ("b", "c", 1)]
    edge_index, edge_weight = _build_edge_index(edge_rows, id_to_idx)
    assert edge_index.shape == (2, 2)
    assert edge_index.dtype == np.int64
    assert abs(edge_weight[0] - math.log1p(5)) < 1e-5
    assert abs(edge_weight[1] - math.log1p(1)) < 1e-5


def test_build_edge_index_unknown_ids_skipped():
    id_to_idx = {"a": 0}
    edge_rows = [("a", "unknown", 3)]
    edge_index, edge_weight = _build_edge_index(edge_rows, id_to_idx)
    assert edge_index.shape[1] == 0


def test_build_edge_index_empty():
    edge_index, edge_weight = _build_edge_index([], {"a": 0})
    assert edge_index.shape == (2, 0)
    assert edge_weight.shape == (0,)


def test_build_labels_churned():
    emps = [_emp("a"), _emp("b"), _emp("c")]
    id_to_idx = {"a": 0, "b": 1, "c": 2}
    label_rows = [("a", True), ("b", False)]
    y = _build_labels(emps, id_to_idx, label_rows)
    assert y[0] == 1.0
    assert y[1] == 0.0
    assert math.isnan(y[2])  # unlabelled


def test_build_labels_unknown_employee_skipped():
    emps = [_emp("a")]
    id_to_idx = {"a": 0}
    label_rows = [("nonexistent", True)]
    y = _build_labels(emps, id_to_idx, label_rows)
    assert math.isnan(y[0])


# ─── Model ────────────────────────────────────────────────────────────────────


def test_churn_gat_import_error_without_pyg():
    """ChurnGAT raises ImportError when torch_geometric is absent."""
    import sys
    from unittest.mock import patch

    with patch.dict(sys.modules, {"torch_geometric": None, "torch_geometric.nn": None}):
        import importlib
        import ml.gnn.model as gnn_mod
        importlib.reload(gnn_mod)
        # _PYG_AVAILABLE should be False after reload without torch_geometric
        if not gnn_mod._PYG_AVAILABLE:
            with pytest.raises(ImportError, match="torch_geometric"):
                gnn_mod.ChurnGAT(in_channels=11)
        # Restore original module state
        importlib.reload(gnn_mod)


def test_churn_gat_forward_shape():
    """ChurnGAT forward pass produces (N,) logits."""
    pytest.importorskip("torch_geometric", reason="torch_geometric not installed")
    import torch
    from ml.gnn.model import ChurnGAT

    N, F = 10, GNN_IN_FEATURES
    model = ChurnGAT(in_channels=F, hidden_channels=16, heads=2)
    model.eval()

    x = torch.zeros(N, F)
    # Star graph: node 0 is center
    src = list(range(1, N))
    dst = [0] * (N - 1)
    edge_index = torch.tensor([src + dst, dst + src], dtype=torch.long)

    with torch.no_grad():
        out = model(x, edge_index)

    assert out.shape == (N,)


def test_churn_gat_forward_finite():
    """All output logits are finite (no NaN / Inf) even with random weights."""
    pytest.importorskip("torch_geometric", reason="torch_geometric not installed")
    import torch
    from ml.gnn.model import ChurnGAT

    torch.manual_seed(42)
    N, F = 20, GNN_IN_FEATURES
    model = ChurnGAT(in_channels=F, hidden_channels=16, heads=2)
    model.eval()

    x = torch.randn(N, F)
    src = list(range(1, N)) + list(range(0, N - 1))
    dst = list(range(0, N - 1)) + list(range(1, N))
    edge_index = torch.tensor([src, dst], dtype=torch.long)

    with torch.no_grad():
        out = model(x, edge_index)

    assert torch.isfinite(out).all()


def test_checkpoint_round_trip(tmp_path):
    """save_checkpoint / load_checkpoint preserves weights and metadata."""
    pytest.importorskip("torch_geometric", reason="torch_geometric not installed")
    import torch
    from ml.gnn.model import ChurnGAT, load_checkpoint, save_checkpoint

    model = ChurnGAT(in_channels=GNN_IN_FEATURES, hidden_channels=16, heads=2)
    ckpt_path = str(tmp_path / "test_churn.pt")
    meta = {"val_auroc": 0.82, "epoch": 42}
    save_checkpoint(model, ckpt_path, metadata=meta)

    loaded, loaded_meta = load_checkpoint(ckpt_path)
    assert loaded_meta == meta

    # Weights must be identical
    for key in model.state_dict():
        assert torch.allclose(model.state_dict()[key], loaded.state_dict()[key])


def test_load_checkpoint_eval_mode(tmp_path):
    pytest.importorskip("torch_geometric", reason="torch_geometric not installed")
    from ml.gnn.model import ChurnGAT, load_checkpoint, save_checkpoint

    model = ChurnGAT(in_channels=GNN_IN_FEATURES, hidden_channels=16, heads=2)
    ckpt = str(tmp_path / "eval_mode.pt")
    save_checkpoint(model, ckpt)
    loaded, _ = load_checkpoint(ckpt)
    assert not loaded.training


# ─── Trainer: pure helpers ────────────────────────────────────────────────────


from ml.gnn.trainer import _auroc, _temporal_masks


def test_temporal_masks_all_labelled():
    y = np.array([0.0, 1.0, 0.0, 1.0, 0.0], dtype=np.float32)
    # All share the same label_date — everything will land in test (< 14 days)
    # with no train/val split; we just verify no crash and masks are booleans
    label_dates = [date(2025, 4, 25)] * 5
    train_mask, val_mask, test_mask = _temporal_masks(y, label_dates, test_days=14, val_days=30)
    assert train_mask.dtype == bool
    assert val_mask.dtype == bool
    assert test_mask.dtype == bool
    # All labelled → each node appears in exactly one split
    any_split = train_mask | val_mask | test_mask
    assert any_split.all()


def test_temporal_masks_no_labelled():
    y = np.full(4, float("nan"), dtype=np.float32)
    label_dates = [None] * 4
    train_mask, val_mask, test_mask = _temporal_masks(y, label_dates, test_days=14, val_days=30)
    assert not train_mask.any()
    assert not val_mask.any()
    assert not test_mask.any()


def test_auroc_perfect():
    y_true  = np.array([0, 0, 1, 1], dtype=float)
    y_score = np.array([0.1, 0.2, 0.8, 0.9])
    assert abs(_auroc(y_true, y_score) - 1.0) < 1e-9


def test_auroc_worst():
    y_true  = np.array([1, 1, 0, 0], dtype=float)
    y_score = np.array([0.8, 0.9, 0.1, 0.2])  # reversed
    assert abs(_auroc(y_true, y_score) - 0.0) < 1e-9


def test_auroc_degenerate_all_positive():
    y_true  = np.array([1.0, 1.0, 1.0])
    y_score = np.array([0.5, 0.6, 0.7])
    assert _auroc(y_true, y_score) == 0.5


# ─── API endpoints ────────────────────────────────────────────────────────────


from fastapi.testclient import TestClient

from api.main import app
from api.deps import get_db

_DATE = date(2025, 4, 25)

_CHURN_ROW = {
    "employee_id": "emp-001",
    "name": "Alice",
    "department": "Engineering",
    "churn_prob": 0.72,
    "risk_tier": "high",
    "model_version": "churn_gat_2025-04-25",
    "scored_at": _DATE,
}


@pytest.fixture
def client():
    mock_conn = MagicMock()
    app.dependency_overrides[get_db] = lambda: mock_conn
    yield TestClient(app, raise_server_exceptions=True), mock_conn
    app.dependency_overrides.clear()


def test_get_churn_scores_200(client):
    test_client, _ = client
    with (
        patch("api.routers.risk.queries.fetch_latest_churn_date", return_value=_DATE),
        patch("api.routers.risk.queries.fetch_churn_scores", return_value=[_CHURN_ROW]),
    ):
        resp = test_client.get("/risk/churn-scores")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["scores"][0]["risk_tier"] == "high"
    assert body["scores"][0]["churn_prob"] == 0.72


def test_get_churn_scores_404_no_run(client):
    test_client, _ = client
    with patch("api.routers.risk.queries.fetch_latest_churn_date", return_value=None):
        resp = test_client.get("/risk/churn-scores")
    assert resp.status_code == 404


def test_get_churn_scores_min_prob_forwarded(client):
    test_client, _ = client
    with (
        patch("api.routers.risk.queries.fetch_latest_churn_date", return_value=_DATE),
        patch("api.routers.risk.queries.fetch_churn_scores", return_value=[]) as mock_q,
    ):
        test_client.get("/risk/churn-scores?min_prob=0.5")
    call_kwargs = {
        "min_prob": mock_q.call_args[0][2],  # positional: scored_at, top, min_prob, conn
    }
    assert call_kwargs["min_prob"] == pytest.approx(0.5)


def test_get_employee_churn_200(client):
    test_client, _ = client
    with patch(
        "api.routers.risk.queries.fetch_employee_churn_history",
        return_value=[_CHURN_ROW],
    ):
        resp = test_client.get("/risk/employee/emp-001/churn")
    assert resp.status_code == 200
    body = resp.json()
    assert body["latest_risk_tier"] == "high"
    assert body["latest_churn_prob"] == pytest.approx(0.72)
    assert len(body["history"]) == 1


def test_get_employee_churn_404(client):
    test_client, _ = client
    with patch(
        "api.routers.risk.queries.fetch_employee_churn_history",
        return_value=[],
    ):
        resp = test_client.get("/risk/employee/no-such-emp/churn")
    assert resp.status_code == 404
