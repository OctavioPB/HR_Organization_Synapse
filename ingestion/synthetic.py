"""Core synthetic data generation logic.

All functions are pure (no side effects) so they can be unit-tested without
a database or Kafka connection. The CLI in data/synthetic/generate_org_data.py
is a thin wrapper around this module.
"""

import csv
import logging
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import NamedTuple

import numpy as np
import psycopg2
import psycopg2.extras

logger = logging.getLogger(__name__)

# ─── Constants ────────────────────────────────────────────────────────────────

_CHANNEL_WEIGHTS: list[tuple[str, float]] = [
    ("slack", 0.40),
    ("email", 0.20),
    ("jira", 0.15),
    ("calendar", 0.15),
    ("github", 0.10),
]

_DIRECTION_BY_CHANNEL: dict[str, list[str]] = {
    "slack":    ["sent", "mentioned"],
    "email":    ["sent"],
    "jira":     ["assigned", "mentioned"],
    "calendar": ["invited"],
    "github":   ["reviewed", "assigned"],
}

_ROLES_BY_DEPT: dict[str, list[str]] = {
    "Engineering": ["Software Engineer", "Senior Engineer", "Tech Lead", "Engineering Manager"],
    "Sales":       ["Account Executive", "Sales Representative", "Sales Manager", "BDR"],
    "HR":          ["HR Specialist", "Recruiter", "HR Manager", "People Ops Analyst"],
}

_FIRST_NAMES = [
    "Alice", "Bob", "Carol", "Dave", "Eva", "Frank", "Grace", "Hector",
    "Ivy", "James", "Kate", "Liam", "Mia", "Noah", "Olivia", "Paul",
    "Quinn", "Rita", "Sam", "Tara", "Uma", "Victor", "Wendy", "Xavier",
    "Yara", "Zoe", "Aaron", "Beth", "Carl", "Diana", "Ethan", "Fiona",
    "George", "Hana", "Ian", "Julia", "Kevin", "Laura",
]

_LAST_NAMES = [
    "Smith", "Jones", "Williams", "Brown", "Davis", "Miller", "Wilson",
    "Moore", "Taylor", "Anderson", "Thomas", "Jackson", "White", "Harris",
    "Martin", "Garcia", "Martinez", "Robinson", "Clark", "Rodriguez",
    "Lewis", "Lee", "Walker", "Hall", "Allen", "Young", "Hernandez",
    "King", "Wright", "Lopez",
]

# ─── Data types ───────────────────────────────────────────────────────────────


class Employee(NamedTuple):
    employee_id: str
    name: str
    department: str
    role: str
    active: bool
    consent: bool


class EdgeRecord(NamedTuple):
    event_id: str
    source_employee_id: str
    target_employee_id: str
    channel: str
    direction: str
    department_source: str
    department_target: str
    timestamp: str  # ISO 8601
    weight: float


# ─── Employee generation ──────────────────────────────────────────────────────


def generate_employees(
    n: int,
    dept_fractions: dict[str, float],
    rng: np.random.Generator,
) -> list[Employee]:
    """Generate n employees distributed across departments.

    Args:
        n: Total number of employees to generate.
        dept_fractions: Department name → fraction of total employees.
        rng: Seeded NumPy random generator for reproducibility.

    Returns:
        List of Employee NamedTuples with unique UUIDs and names.

    Raises:
        ValueError: If the name pool is too small for n employees.
    """
    all_names = [f"{fn} {ln}" for fn in _FIRST_NAMES for ln in _LAST_NAMES]
    if len(all_names) < n:
        raise ValueError(
            f"Name pool ({len(all_names)}) too small for {n} employees. "
            f"Reduce --employees or extend _FIRST_NAMES/_LAST_NAMES."
        )
    chosen_idx = rng.choice(len(all_names), size=n, replace=False)
    names = [all_names[i] for i in chosen_idx]

    # Build department list proportional to fractions
    departments: list[str] = []
    for dept, frac in dept_fractions.items():
        departments.extend([dept] * round(frac * n))
    while len(departments) < n:
        departments.append(next(iter(dept_fractions)))
    departments = departments[:n]
    rng.shuffle(departments)

    employees: list[Employee] = []
    for i in range(n):
        dept = departments[i]
        role_options = _ROLES_BY_DEPT.get(dept, ["Employee"])
        role = role_options[int(rng.integers(len(role_options)))]
        employees.append(Employee(
            employee_id=str(uuid.uuid4()),
            name=names[i],
            department=dept,
            role=role,
            active=True,
            consent=True,
        ))

    return employees


def select_connectors(
    employees: list[Employee],
    rng: np.random.Generator,
    n_connectors: int = 2,
) -> set[str]:
    """Select employee IDs to act as cross-department connectors.

    Picks one employee per department (up to n_connectors) to ensure
    the connectors come from different departments.

    Args:
        employees: Full employee list.
        rng: Seeded random generator.
        n_connectors: Number of connectors to select.

    Returns:
        Set of employee_id strings.
    """
    dept_map: dict[str, list[Employee]] = {}
    for e in employees:
        dept_map.setdefault(e.department, []).append(e)

    connector_ids: set[str] = set()
    for dept, emps in list(dept_map.items()):
        if len(connector_ids) >= n_connectors:
            break
        idx = int(rng.integers(len(emps)))
        connector_ids.add(emps[idx].employee_id)

    return connector_ids


def select_withdrawing(
    employees: list[Employee],
    exclude_ids: set[str],
    rng: np.random.Generator,
) -> str:
    """Select one normal employee to model as withdrawing.

    Args:
        employees: Full employee list.
        exclude_ids: IDs already assigned to connectors.
        rng: Seeded random generator.

    Returns:
        employee_id string of the selected withdrawing employee.
    """
    candidates = [e for e in employees if e.employee_id not in exclude_ids]
    return candidates[int(rng.integers(len(candidates)))].employee_id


# ─── Edge generation ──────────────────────────────────────────────────────────


def _sample_channel(rng: np.random.Generator) -> str:
    channels = [c for c, _ in _CHANNEL_WEIGHTS]
    weights = np.array([w for _, w in _CHANNEL_WEIGHTS])
    return channels[int(rng.choice(len(channels), p=weights / weights.sum()))]


def generate_edges(
    employees: list[Employee],
    n_days: int,
    rng: np.random.Generator,
    connector_ids: set[str],
    withdrawing_id: str,
    start_date: datetime,
    silo_ids: set[str] | None = None,
) -> list[EdgeRecord]:
    """Generate collaboration edges with behavioral properties.

    Properties encoded:
      - Power-law (Pareto α=2) degree distribution for normal employees.
      - Connectors: 6 events/day mean, 85% cross-department probability.
      - Withdrawing employee: 30% of normal activity in last 15 days (70% decay).
      - Silo employees: 3% cross-department probability (nearly isolated cluster).

    Args:
        employees: Full employee list produced by generate_employees().
        n_days: Number of days to simulate.
        rng: Seeded random generator.
        connector_ids: Employee IDs with high cross-dept activity.
        withdrawing_id: Employee ID with activity decay at the end.
        start_date: First simulated day (UTC).
        silo_ids: Employee IDs that form isolated clusters (very low cross-dept
                  probability). Produces detectable silo alerts in the graph.

    Returns:
        EdgeRecord list sorted by ascending timestamp.
    """
    emp_by_dept: dict[str, list[Employee]] = {}
    for e in employees:
        emp_by_dept.setdefault(e.department, []).append(e)

    n = len(employees)
    base_rates = rng.pareto(2.0, n) + 0.2  # heavy tail, minimum 0.2 events/day

    # Scale normal employees to mean ≈ 0.7 events/day
    _silo_ids: set[str] = silo_ids or set()
    special_ids = connector_ids | {withdrawing_id}
    normal_idx = [i for i, e in enumerate(employees) if e.employee_id not in special_ids]
    if normal_idx:
        nm = base_rates[normal_idx].mean()
        base_rates[normal_idx] = base_rates[normal_idx] / nm * 0.7

    # Override special employees
    for i, emp in enumerate(employees):
        if emp.employee_id in connector_ids:
            base_rates[i] = 6.0
        elif emp.employee_id == withdrawing_id:
            base_rates[i] = 1.2

    withdrawal_start_day = n_days - 15
    edges: list[EdgeRecord] = []

    for day_offset in range(n_days):
        day_ts = start_date + timedelta(days=day_offset)

        for i, emp in enumerate(employees):
            rate = float(base_rates[i])
            if emp.employee_id == withdrawing_id and day_offset >= withdrawal_start_day:
                rate *= 0.3  # 70% reduction

            n_interactions = int(rng.poisson(rate))

            for _ in range(n_interactions):
                is_connector = emp.employee_id in connector_ids
                is_silo     = emp.employee_id in _silo_ids
                cross_prob  = 0.85 if is_connector else (0.03 if is_silo else 0.08)

                if rng.random() < cross_prob:
                    other_depts = [d for d in emp_by_dept if d != emp.department]
                    if other_depts:
                        dept = other_depts[int(rng.integers(len(other_depts)))]
                        candidates = emp_by_dept[dept]
                    else:
                        candidates = [e for e in employees if e.employee_id != emp.employee_id]
                else:
                    same_dept = [e for e in emp_by_dept.get(emp.department, [])
                                 if e.employee_id != emp.employee_id]
                    candidates = same_dept or [e for e in employees
                                               if e.employee_id != emp.employee_id]

                if not candidates:
                    continue

                target = candidates[int(rng.integers(len(candidates)))]
                channel = _sample_channel(rng)
                dir_opts = _DIRECTION_BY_CHANNEL[channel]
                direction = dir_opts[int(rng.integers(len(dir_opts)))]

                hour = int(rng.integers(9, 18))
                minute = int(rng.integers(0, 60))
                ts = day_ts.replace(hour=hour, minute=minute, second=0, microsecond=0)

                edges.append(EdgeRecord(
                    event_id=str(uuid.uuid4()),
                    source_employee_id=emp.employee_id,
                    target_employee_id=target.employee_id,
                    channel=channel,
                    direction=direction,
                    department_source=emp.department,
                    department_target=target.department,
                    timestamp=ts.isoformat(),
                    weight=1.0,
                ))

    edges.sort(key=lambda e: e.timestamp)
    return edges


# ─── I/O helpers ──────────────────────────────────────────────────────────────


def write_csvs(
    employees: list[Employee],
    edges: list[EdgeRecord],
    output_dir: Path,
) -> None:
    """Write employees.csv and edges.csv to output_dir.

    Args:
        employees: Employee list.
        edges: Edge list.
        output_dir: Target directory (created if absent).
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    emp_path = output_dir / "employees.csv"
    with emp_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(Employee._fields)
        writer.writerows(employees)
    logger.info("Wrote %d employees → %s", len(employees), emp_path)

    edge_path = output_dir / "edges.csv"
    with edge_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(EdgeRecord._fields)
        writer.writerows(edges)
    logger.info("Wrote %d edges → %s", len(edges), edge_path)


def write_to_postgres(
    employees: list[Employee],
    edges: list[EdgeRecord],
    host: str,
    port: int,
    dbname: str,
    user: str,
    password: str,
) -> None:
    """Insert employees and raw_events directly into PostgreSQL.

    Uses execute_batch for efficiency. Employees are inserted first to satisfy
    the foreign key constraints on raw_events.

    Args:
        employees: Employees to insert (ON CONFLICT DO NOTHING).
        edges: Edges to insert as raw_events (ON CONFLICT DO NOTHING).
        host, port, dbname, user, password: Connection parameters.

    Raises:
        psycopg2.Error: On any database failure.
    """
    conn = psycopg2.connect(host=host, port=port, dbname=dbname, user=user, password=password)
    try:
        with conn.cursor() as cur:
            psycopg2.extras.execute_batch(
                cur,
                """
                INSERT INTO employees (id, name, department, role, active, consent)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO NOTHING
                """,
                [(e.employee_id, e.name, e.department, e.role, e.active, e.consent)
                 for e in employees],
                page_size=500,
            )
            logger.info("Inserted %d employees", len(employees))

            psycopg2.extras.execute_batch(
                cur,
                """
                INSERT INTO raw_events (id, source_id, target_id, channel, direction, ts, weight)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                [(e.event_id, e.source_employee_id, e.target_employee_id,
                  e.channel, e.direction, e.timestamp, e.weight)
                 for e in edges],
                page_size=1000,
            )
            logger.info("Inserted %d raw_events", len(edges))

        conn.commit()
    except psycopg2.Error:
        conn.rollback()
        raise
    finally:
        conn.close()
