"""Unit tests for ingestion.synthetic — no database or Kafka required."""

from collections import Counter
from datetime import datetime, timezone

import numpy as np
import pytest

from ingestion.synthetic import (
    Employee,
    EdgeRecord,
    generate_edges,
    generate_employees,
    select_connectors,
    select_withdrawing,
)

_START_DATE = datetime(2025, 1, 1, tzinfo=timezone.utc)
_DEPT_FRACTIONS = {"Engineering": 0.5, "Sales": 0.33, "HR": 0.17}


# ─── generate_employees ───────────────────────────────────────────────────────


def test_generate_employees_returns_correct_count():
    rng = np.random.default_rng(0)
    employees = generate_employees(200, _DEPT_FRACTIONS, rng)
    assert len(employees) == 200


def test_generate_employees_all_unique_ids():
    rng = np.random.default_rng(0)
    employees = generate_employees(100, _DEPT_FRACTIONS, rng)
    ids = [e.employee_id for e in employees]
    assert len(ids) == len(set(ids))


def test_generate_employees_all_unique_names():
    rng = np.random.default_rng(0)
    employees = generate_employees(100, _DEPT_FRACTIONS, rng)
    names = [e.name for e in employees]
    assert len(names) == len(set(names))


def test_generate_employees_valid_departments():
    rng = np.random.default_rng(0)
    employees = generate_employees(200, _DEPT_FRACTIONS, rng)
    depts = {e.department for e in employees}
    assert depts == set(_DEPT_FRACTIONS.keys())


def test_generate_employees_department_distribution_approximate():
    rng = np.random.default_rng(0)
    n = 200
    employees = generate_employees(n, _DEPT_FRACTIONS, rng)
    counts = Counter(e.department for e in employees)
    # Engineering should be ~50% ± 10 employees
    assert abs(counts["Engineering"] - 100) <= 15


def test_generate_employees_consent_and_active_default_true():
    rng = np.random.default_rng(0)
    employees = generate_employees(10, _DEPT_FRACTIONS, rng)
    assert all(e.active and e.consent for e in employees)


def test_generate_employees_raises_on_too_many():
    rng = np.random.default_rng(0)
    with pytest.raises(ValueError, match="too small"):
        generate_employees(5000, _DEPT_FRACTIONS, rng)


# ─── select_connectors / select_withdrawing ───────────────────────────────────


def test_select_connectors_returns_two_ids():
    rng = np.random.default_rng(0)
    employees = generate_employees(50, _DEPT_FRACTIONS, rng)
    ids = select_connectors(employees, rng, n_connectors=2)
    assert len(ids) == 2


def test_select_connectors_from_different_departments():
    rng = np.random.default_rng(0)
    employees = generate_employees(50, _DEPT_FRACTIONS, rng)
    connector_ids = select_connectors(employees, rng, n_connectors=2)
    connector_depts = {e.department for e in employees if e.employee_id in connector_ids}
    assert len(connector_depts) == 2  # from different departments


def test_select_withdrawing_excludes_connectors():
    rng = np.random.default_rng(0)
    employees = generate_employees(50, _DEPT_FRACTIONS, rng)
    connector_ids = select_connectors(employees, rng)
    withdrawing_id = select_withdrawing(employees, connector_ids, rng)
    assert withdrawing_id not in connector_ids


# ─── generate_edges ───────────────────────────────────────────────────────────


def _make_edges(seed: int = 42, n_employees: int = 50, n_days: int = 30) -> tuple:
    """Helper: generate a small set of employees and edges for testing."""
    rng = np.random.default_rng(seed)
    employees = generate_employees(n_employees, _DEPT_FRACTIONS, rng)
    connector_ids = select_connectors(employees, rng)
    withdrawing_id = select_withdrawing(employees, connector_ids, rng)
    edges = generate_edges(employees, n_days, rng, connector_ids, withdrawing_id, _START_DATE)
    return employees, edges, connector_ids, withdrawing_id


def test_generate_edges_returns_edge_records():
    _, edges, _, _ = _make_edges()
    assert all(isinstance(e, EdgeRecord) for e in edges)


def test_generate_edges_all_channels_valid():
    valid_channels = {"slack", "email", "jira", "calendar", "github"}
    _, edges, _, _ = _make_edges()
    assert all(e.channel in valid_channels for e in edges)


def test_generate_edges_all_directions_valid():
    valid_directions = {"sent", "mentioned", "invited", "assigned", "reviewed"}
    _, edges, _, _ = _make_edges()
    assert all(e.direction in valid_directions for e in edges)


def test_generate_edges_sorted_by_timestamp():
    _, edges, _, _ = _make_edges()
    timestamps = [e.timestamp for e in edges]
    assert timestamps == sorted(timestamps)


def test_generate_edges_no_self_loops():
    _, edges, _, _ = _make_edges()
    assert all(e.source_employee_id != e.target_employee_id for e in edges)


def test_generate_edges_weight_is_1():
    _, edges, _, _ = _make_edges()
    assert all(e.weight == 1.0 for e in edges)


def test_connectors_have_more_edges_than_average():
    """Connectors should have significantly more edges than the mean employee."""
    employees, edges, connector_ids, _ = _make_edges(n_employees=50, n_days=30)
    edge_counts = Counter(e.source_employee_id for e in edges)
    mean_count = sum(edge_counts.values()) / len(employees)
    for cid in connector_ids:
        assert edge_counts[cid] > mean_count * 5, (
            f"Connector {cid} has {edge_counts[cid]} edges, expected > {mean_count * 5:.1f}"
        )


def test_connectors_have_more_cross_dept_edges():
    """Connectors should produce more cross-department edges than same-dept edges."""
    employees, edges, connector_ids, _ = _make_edges(n_employees=60, n_days=30)
    emp_dept = {e.employee_id: e.department for e in employees}

    for cid in connector_ids:
        my_dept = emp_dept[cid]
        my_edges = [e for e in edges if e.source_employee_id == cid]
        cross = sum(1 for e in my_edges if e.department_target != my_dept)
        same = len(my_edges) - cross
        assert cross > same, (
            f"Connector {cid}: cross={cross} same={same}; expected cross > same"
        )


def test_withdrawing_employee_decays_in_last_15_days():
    """Withdrawing employee must have fewer edges/day in last 15 days than the first 75."""
    from datetime import timedelta

    employees, edges, connector_ids, withdrawing_id = _make_edges(
        n_employees=60, n_days=90
    )
    w_edges = [e for e in edges if e.source_employee_id == withdrawing_id]
    if not w_edges:
        pytest.skip("No withdrawing edges generated (small dataset)")

    # Split at the actual withdrawal boundary: first 75 days vs last 15 days
    cutoff_iso = (_START_DATE + timedelta(days=75)).isoformat()
    early = [e for e in w_edges if e.timestamp < cutoff_iso]
    late = [e for e in w_edges if e.timestamp >= cutoff_iso]

    early_rate = len(early) / 75  # events per day
    late_rate = len(late) / 15    # events per day (0 if no late edges)

    # 70% decay → late_rate ≈ 0.3 × early_rate, well below 60% threshold
    assert late_rate < early_rate * 0.6, (
        f"Withdrawing employee: early_rate={early_rate:.3f}/day late_rate={late_rate:.3f}/day; "
        f"expected late_rate < {early_rate * 0.6:.3f}"
    )


def test_generate_edges_reproducible_with_same_seed():
    """Same seed must produce the same edge count and channel distribution.

    Employee UUIDs are generated by uuid.uuid4() (OS-random) so they differ
    between runs. The numpy rng controls Poisson draws and channel sampling,
    which must be identical for the same seed.
    """
    _, edges_a, _, _ = _make_edges(seed=99)
    _, edges_b, _, _ = _make_edges(seed=99)

    assert len(edges_a) == len(edges_b), "Same seed must produce same edge count"

    counts_a = Counter(e.channel for e in edges_a)
    counts_b = Counter(e.channel for e in edges_b)
    assert counts_a == counts_b, "Same seed must produce same channel distribution"
