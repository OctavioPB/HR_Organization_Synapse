"""Silo detection: flags departments whose collaboration stays inside far more than chance.

Metric: Coleman's homophily index, per department D (MATH_MODEL.md §7)
──────────────────────────────────────────────────────────────────────
    p_D = internal(D) / (internal(D) + outgoing(D))     observed internal share
    q_D = (n_D − 1) / (N − 1)                           share expected under random mixing
    H_D = (p_D − q_D) / (1 − q_D)                       0 = random, 1 = fully closed, < 0 = outward

    silo      if H_D ≥ SILO_HOMOPHILY_THRESHOLD           (default 0.50)
    critical  if H_D ≥ SILO_HOMOPHILY_CRITICAL            (default 0.75)

Why not the old ratio: internal/outgoing grows with department size even when
people collaborate completely at random — its random-mixing value is
(n−1)/(N−n), i.e. ≈1.0 for a department holding half the org but ≈0.2 for a
small one — so one fixed threshold over-flagged large departments and could
invert the ranking.  H_D subtracts that baseline and is bounded, so a single
threshold means the same thing for every department.

``isolation_ratio`` is still computed and stored for continuity with older
alerts and the UI, but it no longer drives detection or severity.

p_D is measured on interaction volume (summed semantic edge weight), matching
the per-interaction random-target null behind q_D.  Only D's outgoing activity
is used, so a department is not excused because others reach into it.

Public functions:
    homophily_index(internal, outgoing, n_dept, n_total) → float | None
    department_homophily(G) → dict[str, dict]
    detect_silos(G, communities, threshold, critical) → list[SiloAlert]
    write_alerts(alerts, snapshot_date) → None

CLI:
    python graph/silo_detector.py --snapshot-date 2025-04-25
"""

import argparse
import json
import logging
import os
import sys
import uuid
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import networkx as nx
import psycopg2.extras

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from graph.builder import build_graph, load_raw_edges
from graph.metrics import compute_community
from ingestion.db import get_conn

logger = logging.getLogger(__name__)

SILO_HOMOPHILY_THRESHOLD = float(os.environ.get("SILO_HOMOPHILY_THRESHOLD", "0.50"))
SILO_HOMOPHILY_CRITICAL = float(os.environ.get("SILO_HOMOPHILY_CRITICAL", "0.75"))
# Departments smaller than this are never flagged: with 1–2 people the random
# baseline q is ~0 and a single busy pair would read as a "silo".
SILO_MIN_DEPT_SIZE = int(os.environ.get("SILO_MIN_DEPT_SIZE", "3"))


@dataclass
class SiloAlert:
    """A department whose collaboration is markedly more inward than chance.

    Attributes:
        community_id: Stable index of the department (sorted name order).
        members: Employee IDs in this department.
        isolation_ratio: internal / max(outgoing, 1) — legacy, informational only.
        departments: The department name (single-element set).
        severity: 'critical' if homophily ≥ SILO_HOMOPHILY_CRITICAL, else 'high'.
        homophily: Coleman homophily index H_D ∈ [−1, 1].
        internal_share: Observed p_D.
        expected_internal_share: Random-mixing q_D.
    """

    community_id: int
    members: list[str]
    isolation_ratio: float
    departments: set[str] = field(default_factory=set)
    severity: str = "high"
    homophily: float = 0.0
    internal_share: float = 0.0
    expected_internal_share: float = 0.0


def homophily_index(internal: float, outgoing: float, n_dept: int, n_total: int) -> float | None:
    """Coleman homophily index for one group; None when it is undefined.

    *internal* / *outgoing* are interaction volumes (summed edge weight) of the
    group's outgoing activity that stays inside / leaves the group.

    Undefined when the group has no outgoing activity at all or is the whole
    graph (no "outside" to compare against).
    """
    total = internal + outgoing
    if total == 0 or n_total <= 1 or n_dept >= n_total:
        return None
    p = internal / total
    q = (n_dept - 1) / (n_total - 1)
    if q >= 1.0:
        return None
    return (p - q) / (1.0 - q)


def department_homophily(G: nx.DiGraph) -> dict[str, dict]:
    """Per-department homophily statistics for the whole graph (silo or not).

    Returns:
        {department: {members, internal, outgoing, isolation_ratio,
                      internal_share, expected_internal_share, homophily}}
        homophily is None where undefined.  Departments containing every node
        (i.e. no department data) are omitted.
    """
    dept_members: dict[str, set[str]] = {}
    for node in G.nodes():
        dept_members.setdefault(G.nodes[node].get("department", "unknown"), set()).add(node)

    n_total = G.number_of_nodes()
    # Unique-edge counts feed only the legacy ratio.  H uses interaction VOLUME
    # (summed semantic weight): the random-mixing baseline q assumes each
    # interaction picks its target at random, and unique edges saturate inside a
    # small department (19 colleagues can only ever be 19 edges, 40 outsiders 40),
    # which would understate p exactly where it matters.
    internal_n: dict[str, int] = dict.fromkeys(dept_members, 0)
    outgoing_n: dict[str, int] = dict.fromkeys(dept_members, 0)
    internal_w: dict[str, float] = dict.fromkeys(dept_members, 0.0)
    outgoing_w: dict[str, float] = dict.fromkeys(dept_members, 0.0)
    dept_of = {n: d for d, ms in dept_members.items() for n in ms}
    for u, v, data in G.edges(data=True):
        du = dept_of[u]
        w = float(data.get("weight", 1.0))
        if dept_of[v] == du:
            internal_n[du] += 1
            internal_w[du] += w
        else:
            outgoing_n[du] += 1
            outgoing_w[du] += w

    out: dict[str, dict] = {}
    for dept, members in dept_members.items():
        n = len(members)
        if n >= n_total:
            continue
        tot_w = internal_w[dept] + outgoing_w[dept]
        out[dept] = {
            "members": members,
            "internal": internal_n[dept],
            "outgoing": outgoing_n[dept],
            "isolation_ratio": internal_n[dept] / max(outgoing_n[dept], 1),
            "internal_share": internal_w[dept] / tot_w if tot_w else 0.0,
            "expected_internal_share": (n - 1) / (n_total - 1) if n_total > 1 else 0.0,
            "homophily": homophily_index(internal_w[dept], outgoing_w[dept], n, n_total),
        }
    return out


def detect_silos(
    G: nx.DiGraph,
    communities: dict[str, int] | None = None,
    threshold: float = SILO_HOMOPHILY_THRESHOLD,
    critical: float = SILO_HOMOPHILY_CRITICAL,
    min_size: int = SILO_MIN_DEPT_SIZE,
) -> list[SiloAlert]:
    """Detect departments whose homophily index reaches *threshold*.

    Departments rather than Louvain communities: the department is the unit HR
    can act on, and Louvain can merge a siloed department into a hub's community.

    Args:
        G: Directed collaboration graph (nodes must carry a 'department' attr).
        communities: Unused — kept for API compatibility with existing callers.
        threshold: Minimum homophily index for a silo.
        critical: Homophily index at or above which severity is 'critical'.
        min_size: Departments with fewer members are never flagged.

    Returns:
        SiloAlerts sorted by homophily index, highest first.
    """
    del communities  # kept in the signature for callers; not used
    stats = department_homophily(G)
    alerts: list[SiloAlert] = []
    for dept_idx, dept in enumerate(sorted(stats)):
        s = stats[dept]
        h = s["homophily"]
        logger.debug(
            "Dept '%s': n=%d internal=%d outgoing=%d p=%.3f q=%.3f H=%s",
            dept,
            len(s["members"]),
            s["internal"],
            s["outgoing"],
            s["internal_share"],
            s["expected_internal_share"],
            "n/a" if h is None else f"{h:.3f}",
        )
        if h is None or len(s["members"]) < min_size or h < threshold:
            continue
        alerts.append(
            SiloAlert(
                community_id=dept_idx,
                members=sorted(s["members"]),
                isolation_ratio=round(s["isolation_ratio"], 4),
                departments={dept},
                severity="critical" if h >= critical else "high",
                homophily=round(h, 4),
                internal_share=round(s["internal_share"], 4),
                expected_internal_share=round(s["expected_internal_share"], 4),
            )
        )
    alerts.sort(key=lambda a: a.homophily, reverse=True)
    return alerts


def write_alerts(
    alerts: list[SiloAlert],
    snapshot_date: date,
) -> None:
    """Persist silo alerts to the alerts table.

    Args:
        alerts: Silo alerts from detect_silos().
        snapshot_date: The date of the snapshot that produced these alerts.
    """
    if not alerts:
        logger.info("No silo alerts to write for %s", snapshot_date)
        return

    rows = [
        (
            str(uuid.uuid4()),
            "silo",
            alert.severity,
            json.dumps(
                {
                    "community_id": alert.community_id,
                    "member_count": len(alert.members),
                    "member_ids": alert.members,
                    "departments": sorted(alert.departments),
                    "isolation_ratio": alert.isolation_ratio,
                    "homophily": alert.homophily,
                    "internal_share": alert.internal_share,
                    "expected_internal_share": alert.expected_internal_share,
                    "snapshot_date": snapshot_date.isoformat(),
                }
            ),
            (
                f"{next(iter(alert.departments), 'Unknown')} dept. homophily={alert.homophily:.2f} "
                f"({alert.internal_share:.0%} of ties internal vs {alert.expected_internal_share:.0%} by chance; "
                f"{len(alert.members)} members)"
            ),
        )
        for alert in alerts
    ]

    with get_conn() as conn:
        with conn.cursor() as cur:
            psycopg2.extras.execute_batch(
                cur,
                """
                INSERT INTO alerts (id, type, severity, affected_entities, details)
                VALUES (%s, %s, %s, %s::jsonb, %s)
                """,
                rows,
            )

    logger.info("Wrote %d silo alert(s) for %s", len(alerts), snapshot_date)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="Detect and persist silo alerts.")
    parser.add_argument("--snapshot-date", type=date.fromisoformat, required=True)
    parser.add_argument(
        "--threshold",
        type=float,
        default=SILO_HOMOPHILY_THRESHOLD,
        help=f"Homophily-index threshold (default: {SILO_HOMOPHILY_THRESHOLD})",
    )
    parser.add_argument(
        "--window-days",
        type=int,
        default=int(os.environ.get("GRAPH_WINDOW_DAYS", "30")),
    )
    args = parser.parse_args()

    raw_edges = load_raw_edges(args.snapshot_date, args.window_days)
    G = build_graph(raw_edges)

    if G.number_of_nodes() == 0:
        logger.warning("Graph is empty — skipping silo detection.")
        return

    communities = compute_community(G)
    alerts = detect_silos(G, communities, threshold=args.threshold)

    logger.info("Detected %d silo(s) (homophily threshold=%.2f)", len(alerts), args.threshold)
    for a in alerts:
        logger.info(
            "  Dept %d | H=%.2f | severity=%s | depts=%s | members=%d",
            a.community_id,
            a.homophily,
            a.severity,
            sorted(a.departments),
            len(a.members),
        )

    write_alerts(alerts, args.snapshot_date)


if __name__ == "__main__":
    main()
