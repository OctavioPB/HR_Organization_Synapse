"""Unit tests for F2 — Temporal Graph Analysis.

Covers:
  Sequence builder (pure helpers)
  ─ _build_x: shape and dtype
  ─ _build_x: zero-pads employees absent from a snapshot
  ─ _build_x: presence_mask correctly marks present employees
  ─ _build_x: feature columns mapped to correct indices
  ─ _build_edge_index (temporal): directed edges, log1p weights
  ─ _build_edge_index (temporal): unknown employees skipped
  ─ _build_edge_index (temporal): empty → shape (2,0)/(0,)

  Model
  ─ TemporalRiskGNN raises ImportError when torch_geometric_temporal absent
  ─ TemporalRiskGNN forward: output shape (N, in_channels) and (N, hidden)
  ─ TemporalRiskGNN forward: x_pred is finite
  ─ reconstruction_error: zero when prediction == target
  ─ reconstruction_error: presence_mask zeros out absent nodes
  ─ reconstruction_error: correct per-node MSE on known values
  ─ scalar_loss: equals mean of present nodes' per-node MSE
  ─ save_checkpoint / load_checkpoint round-trip
  ─ load_checkpoint returns model in eval mode

  API endpoints
  ─ GET /graph/temporal/flow 200 with series data
  ─ GET /graph/temporal/flow 404 when no snapshots
  ─ GET /graph/temporal/flow respects weeks parameter
  ─ GET /graph/temporal/anomalies 200 with scores
  ─ GET /graph/temporal/anomalies 404 when no scoring run
  ─ GET /graph/temporal/anomalies min_score forwarded to DB query
"""

from __future__ import annotations

import math
from datetime import date
from unittest.mock import MagicMock, patch

import numpy as np
import pytest


# ─── Sequence builder: pure helpers ──────────────────────────────────────────


from graph.temporal.sequence_builder import (
    TEMPORAL_IN_FEATURES,
    _build_edge_index,
    _build_x,
)


def _make_snapshot_row(
    emp_id: str,
    betweenness: float = 0.5,
    degree_in: float = 0.3,
    degree_out: float = 0.4,
    clustering: float = 0.2,
) -> tuple:
    return (emp_id, betweenness, degree_in, degree_out, clustering)


_VOCAB = ["aaa", "bbb", "ccc"]


def test_build_x_shape():
    rows = [_make_snapshot_row("aaa"), _make_snapshot_row("bbb")]
    x, presence = _build_x(_VOCAB, rows)
    assert x.shape == (3, TEMPORAL_IN_FEATURES)
    assert x.dtype == np.float32


def test_build_x_presence_mask():
    rows = [_make_snapshot_row("aaa"), _make_snapshot_row("ccc")]
    _, presence = _build_x(_VOCAB, rows)
    assert presence[0] is np.bool_(True)   # "aaa" present
    assert presence[1] is np.bool_(False)  # "bbb" absent
    assert presence[2] is np.bool_(True)   # "ccc" present


def test_build_x_absent_employee_is_zero():
    rows = [_make_snapshot_row("aaa")]
    x, _ = _build_x(_VOCAB, rows)
    assert np.all(x[1] == 0.0)  # "bbb" absent → all zeros
    assert np.all(x[2] == 0.0)  # "ccc" absent → all zeros


def test_build_x_feature_columns():
    rows = [_make_snapshot_row("aaa", betweenness=0.8, degree_in=0.6, degree_out=0.4, clustering=0.1)]
    x, _ = _build_x(_VOCAB, rows)
    assert abs(x[0, 0] - 0.8) < 1e-6  # betweenness
    assert abs(x[0, 1] - 0.6) < 1e-6  # degree_in
    assert abs(x[0, 2] - 0.4) < 1e-6  # degree_out
    assert abs(x[0, 3] - 0.1) < 1e-6  # clustering


def test_build_x_unknown_employee_ignored():
    rows = [_make_snapshot_row("zzz")]  # not in vocab
    x, presence = _build_x(_VOCAB, rows)
    assert not presence.any()


def test_temporal_build_edge_index_basic():
    id_to_idx = {"aaa": 0, "bbb": 1, "ccc": 2}
    edge_rows = [("aaa", "bbb", 10), ("bbb", "ccc", 2)]
    edge_index, edge_weight = _build_edge_index(edge_rows, id_to_idx)
    assert edge_index.shape == (2, 2)
    assert edge_index.dtype == np.int64
    assert abs(edge_weight[0] - math.log1p(10)) < 1e-5
    assert abs(edge_weight[1] - math.log1p(2)) < 1e-5


def test_temporal_build_edge_index_unknown_skipped():
    id_to_idx = {"aaa": 0}
    edge_index, edge_weight = _build_edge_index([("aaa", "unknown", 3)], id_to_idx)
    assert edge_index.shape[1] == 0


def test_temporal_build_edge_index_empty():
    edge_index, edge_weight = _build_edge_index([], {"aaa": 0})
    assert edge_index.shape == (2, 0)
    assert edge_weight.shape == (0,)


# ─── Model ────────────────────────────────────────────────────────────────────


def test_temporal_gnn_import_error_without_tgnn():
    """TemporalRiskGNN raises ImportError when torch_geometric_temporal is absent."""
    import sys, importlib
    with patch.dict(
        sys.modules,
        {
            "torch_geometric_temporal": None,
            "torch_geometric_temporal.nn": None,
            "torch_geometric_temporal.nn.recurrent": None,
        },
    ):
        import graph.temporal.model as tmod
        importlib.reload(tmod)
        if not tmod._TGNN_AVAILABLE:
            with pytest.raises(ImportError, match="torch_geometric_temporal"):
                tmod.TemporalRiskGNN(in_channels=TEMPORAL_IN_FEATURES)
        importlib.reload(tmod)


def _make_snapshot_tensors(N: int, E_per_snap: int, F: int, T: int, device: str = "cpu"):
    """Helper: build T synthetic snapshot dicts."""
    import torch
    snaps = []
    for _ in range(T):
        x = torch.randn(N, F)
        # Random edges (no self-loops)
        src = torch.randint(0, N, (E_per_snap,))
        dst = torch.randint(0, N, (E_per_snap,))
        edge_index = torch.stack([src, dst], dim=0)
        snaps.append({"x": x.to(device), "edge_index": edge_index.to(device)})
    return snaps


def test_temporal_gnn_forward_shape():
    pytest.importorskip("torch_geometric_temporal", reason="torch_geometric_temporal not installed")
    import torch
    from graph.temporal.model import TemporalRiskGNN

    N, F, T = 15, TEMPORAL_IN_FEATURES, 4
    model = TemporalRiskGNN(in_channels=F, hidden_channels=16, K=1)
    model.eval()

    snaps = _make_snapshot_tensors(N, E_per_snap=20, F=F, T=T)
    with torch.no_grad():
        x_pred, h_final = model(snaps)

    assert x_pred.shape == (N, F)
    assert h_final.shape == (N, 16)


def test_temporal_gnn_forward_finite():
    pytest.importorskip("torch_geometric_temporal", reason="torch_geometric_temporal not installed")
    import torch
    from graph.temporal.model import TemporalRiskGNN

    torch.manual_seed(0)
    N, F, T = 20, TEMPORAL_IN_FEATURES, 5
    model = TemporalRiskGNN(in_channels=F, hidden_channels=16, K=1)
    model.eval()

    snaps = _make_snapshot_tensors(N, E_per_snap=30, F=F, T=T)
    with torch.no_grad():
        x_pred, _ = model(snaps)

    assert torch.isfinite(x_pred).all()


def test_reconstruction_error_zero_when_perfect():
    pytest.importorskip("torch_geometric_temporal", reason="torch_geometric_temporal not installed")
    import torch
    from graph.temporal.model import TemporalRiskGNN

    model = TemporalRiskGNN(in_channels=TEMPORAL_IN_FEATURES, hidden_channels=16)
    N = 5
    x = torch.ones(N, TEMPORAL_IN_FEATURES)
    err = model.reconstruction_error(x, x)
    assert torch.allclose(err, torch.zeros(N))


def test_reconstruction_error_known_values():
    pytest.importorskip("torch_geometric_temporal", reason="torch_geometric_temporal not installed")
    import torch
    from graph.temporal.model import TemporalRiskGNN

    model = TemporalRiskGNN(in_channels=2, hidden_channels=8)
    x_pred   = torch.tensor([[1.0, 1.0], [0.0, 0.0]], dtype=torch.float)
    x_target = torch.tensor([[0.0, 0.0], [0.0, 0.0]], dtype=torch.float)
    err = model.reconstruction_error(x_pred, x_target)
    # node 0: ((1-0)^2 + (1-0)^2) / 2 = 1.0
    # node 1: 0.0
    assert abs(err[0].item() - 1.0) < 1e-6
    assert abs(err[1].item() - 0.0) < 1e-6


def test_reconstruction_error_presence_mask_zeros_absent():
    pytest.importorskip("torch_geometric_temporal", reason="torch_geometric_temporal not installed")
    import torch
    from graph.temporal.model import TemporalRiskGNN

    model = TemporalRiskGNN(in_channels=2, hidden_channels=8)
    x_pred   = torch.tensor([[2.0, 2.0], [2.0, 2.0]], dtype=torch.float)
    x_target = torch.zeros(2, 2)
    mask = torch.tensor([True, False])
    err = model.reconstruction_error(x_pred, x_target, presence_mask=mask)
    assert err[0].item() > 0
    assert err[1].item() == 0.0  # masked out


def test_scalar_loss_matches_mean_present():
    pytest.importorskip("torch_geometric_temporal", reason="torch_geometric_temporal not installed")
    import torch
    from graph.temporal.model import TemporalRiskGNN

    model = TemporalRiskGNN(in_channels=2, hidden_channels=8)
    x_pred   = torch.tensor([[1.0, 0.0], [0.0, 0.0], [2.0, 0.0]], dtype=torch.float)
    x_target = torch.zeros(3, 2)
    mask = torch.tensor([True, False, True])

    scalar = model.scalar_loss(x_pred, x_target, presence_mask=mask)
    # Present nodes: 0 (err=0.5), 2 (err=2.0); mean = 1.25
    assert abs(scalar.item() - 1.25) < 1e-5


def test_temporal_checkpoint_round_trip(tmp_path):
    pytest.importorskip("torch_geometric_temporal", reason="torch_geometric_temporal not installed")
    import torch
    from graph.temporal.model import TemporalRiskGNN, load_checkpoint, save_checkpoint

    model = TemporalRiskGNN(in_channels=TEMPORAL_IN_FEATURES, hidden_channels=16, K=1)
    ckpt_path = str(tmp_path / "test_temporal.pt")
    meta = {"val_loss": 0.0123, "n_weeks": 8}
    save_checkpoint(model, ckpt_path, metadata=meta)

    loaded, loaded_meta = load_checkpoint(ckpt_path)
    assert loaded_meta == meta
    for key in model.state_dict():
        assert torch.allclose(model.state_dict()[key], loaded.state_dict()[key])


def test_temporal_checkpoint_eval_mode(tmp_path):
    pytest.importorskip("torch_geometric_temporal", reason="torch_geometric_temporal not installed")
    from graph.temporal.model import TemporalRiskGNN, load_checkpoint, save_checkpoint

    model = TemporalRiskGNN(in_channels=TEMPORAL_IN_FEATURES, hidden_channels=16)
    ckpt = str(tmp_path / "eval.pt")
    save_checkpoint(model, ckpt)
    loaded, _ = load_checkpoint(ckpt)
    assert not loaded.training


# ─── API endpoints ────────────────────────────────────────────────────────────


from fastapi.testclient import TestClient

from api.main import app
from api.deps import get_db

_DATE = date(2025, 4, 25)
_EMP_ID = "emp-abc-001"

_SERIES_ROW = {
    "snapshot_date": _DATE,
    "betweenness": 0.42,
    "degree_in": 0.3,
    "degree_out": 0.35,
    "clustering": 0.15,
    "community_id": 2,
}

_META_ROW = {"name": "Alice", "department": "Engineering"}

_ANOMALY_ROW = {
    "employee_id": _EMP_ID,
    "name": "Alice",
    "department": "Engineering",
    "anomaly_score": 0.75,
    "anomaly_tier": "high",
    "reconstruction_error": 0.0032,
    "trend_slope": 0.012,
    "model_version": "temporal_risk_gnn_2025-04-25",
    "scored_at": _DATE,
}


@pytest.fixture
def client():
    mock_conn = MagicMock()
    app.dependency_overrides[get_db] = lambda: mock_conn
    yield TestClient(app, raise_server_exceptions=True), mock_conn
    app.dependency_overrides.clear()


def test_temporal_flow_200(client):
    test_client, _ = client
    with (
        patch("api.routers.graph.queries.fetch_temporal_flow", return_value=[_SERIES_ROW]),
        patch("api.routers.graph.queries.fetch_employee_temporal_meta", return_value=_META_ROW),
    ):
        resp = test_client.get(f"/graph/temporal/flow?employee_id={_EMP_ID}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["weeks"] == 1
    assert body["name"] == "Alice"
    assert len(body["series"]) == 1
    assert body["series"][0]["betweenness"] == pytest.approx(0.42)


def test_temporal_flow_404(client):
    test_client, _ = client
    with patch("api.routers.graph.queries.fetch_temporal_flow", return_value=[]):
        resp = test_client.get(f"/graph/temporal/flow?employee_id=no-such")
    assert resp.status_code == 404


def test_temporal_flow_weeks_param_forwarded(client):
    test_client, _ = client
    with (
        patch(
            "api.routers.graph.queries.fetch_temporal_flow",
            return_value=[_SERIES_ROW],
        ) as mock_flow,
        patch("api.routers.graph.queries.fetch_employee_temporal_meta", return_value=_META_ROW),
    ):
        test_client.get(f"/graph/temporal/flow?employee_id={_EMP_ID}&weeks=24")
    # positional args: (employee_id, weeks, conn)
    assert mock_flow.call_args[0][1] == 24


def test_temporal_anomalies_200(client):
    test_client, _ = client
    with (
        patch("api.routers.graph.queries.fetch_latest_temporal_anomaly_date", return_value=_DATE),
        patch("api.routers.graph.queries.fetch_temporal_anomaly_scores", return_value=[_ANOMALY_ROW]),
    ):
        resp = test_client.get("/graph/temporal/anomalies")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["scores"][0]["anomaly_tier"] == "high"
    assert body["scores"][0]["anomaly_score"] == pytest.approx(0.75)
    assert body["scores"][0]["trend_slope"] == pytest.approx(0.012)


def test_temporal_anomalies_404_no_run(client):
    test_client, _ = client
    with patch(
        "api.routers.graph.queries.fetch_latest_temporal_anomaly_date", return_value=None
    ):
        resp = test_client.get("/graph/temporal/anomalies")
    assert resp.status_code == 404


def test_temporal_anomalies_min_score_forwarded(client):
    test_client, _ = client
    with (
        patch("api.routers.graph.queries.fetch_latest_temporal_anomaly_date", return_value=_DATE),
        patch(
            "api.routers.graph.queries.fetch_temporal_anomaly_scores", return_value=[]
        ) as mock_q,
    ):
        test_client.get("/graph/temporal/anomalies?min_score=0.6")
    # positional args: (scored_at, top, min_score, conn)
    assert mock_q.call_args[0][2] == pytest.approx(0.6)
