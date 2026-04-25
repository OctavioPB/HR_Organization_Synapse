"""Silo detection: flags communities that are over-isolated from the rest of the org.

A silo forms when a community's internal edge count is disproportionately high
relative to its external (cross-community) edge count.

isolation_ratio = internal_edges / max(external_edges, 1)

A community is a silo if isolation_ratio > SILO_THRESHOLD.

Public functions:
    detect_silos(G, communities, threshold) → list[SiloAlert]
    write_alerts(alerts, snapshot_date)    → None

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

_DEFAULT_SILO_THRESHOLD = float(os.environ.get("SILO_THRESHOLD", "4.0"))


@dataclass
class SiloAlert:
    """Represents a silo detected in the collaboration graph.

    Attributes:
        community_id: Louvain community identifier.
        members: Employee IDs in this community.
        isolation_ratio: internal_edges / max(external_edges, 1).
        departments: Set of departments represented in this community.
        severity: 'critical' if ratio > 2× threshold, else 'high'.
    """

    community_id: int
    members: list[str]
    isolation_ratio: float
    departments: set[str] = field(default_factory=set)
    severity: str = "high"


def detect_silos(
    G: nx.DiGraph,
    communities: dict[str, int],
    threshold: float = _DEFAULT_SILO_THRESHOLD,
) -> list[SiloAlert]:
    """Detect communities where internal interaction dominates external bridges.

    Args:
        G: Directed weighted collaboration graph.
        communities: Dict mapping employee_id → community_id (from compute_community).
        threshold: isolation_ratio above which a community is flagged as a silo.

    Returns:
        List of SiloAlert, one per flagged community, sorted by isolation_ratio desc.
    """
    # Group members by community
    comm_members: dict[int, set[str]] = {}
    for emp_id, comm_id in communities.items():
        comm_members.setdefault(comm_id, set()).add(emp_id)

    alerts: list[SiloAlert] = []

    for comm_id, members in comm_members.items():
        internal = sum(
            1 for u, v in G.edges()
            if u in members and v in members
        )
        external = sum(
            1 for u, v in G.edges()
            if (u in members) != (v in members)
        )

        isolation_ratio = internal / max(external, 1)

        if isolation_ratio <= threshold:
            continue

        depts = {
            G.nodes[n].get("department", "unknown")
            for n in members
            if n in G.nodes()
        }
        severity = "critical" if isolation_ratio > threshold * 2 else "high"

        alerts.append(SiloAlert(
            community_id=comm_id,
            members=sorted(members),
            isolation_ratio=round(isolation_ratio, 4),
            departments=depts,
            severity=severity,
        ))

    alerts.sort(key=lambda a: a.isolation_ratio, reverse=True)
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
            json.dumps({
                "community_id": alert.community_id,
                "member_count": len(alert.members),
                "departments": sorted(alert.departments),
                "isolation_ratio": alert.isolation_ratio,
                "snapshot_date": snapshot_date.isoformat(),
            }),
            (
                f"Community {alert.community_id} isolation_ratio={alert.isolation_ratio:.2f} "
                f"({len(alert.members)} members, depts: {sorted(alert.departments)})"
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

    logger.info(
        "Wrote %d silo alert(s) for %s", len(alerts), snapshot_date
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="Detect and persist silo alerts.")
    parser.add_argument("--snapshot-date", type=date.fromisoformat, required=True)
    parser.add_argument(
        "--threshold", type=float,
        default=_DEFAULT_SILO_THRESHOLD,
        help=f"Isolation ratio threshold (default: {_DEFAULT_SILO_THRESHOLD})",
    )
    parser.add_argument(
        "--window-days", type=int,
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

    logger.info(
        "Detected %d silo(s) (threshold=%.1f)", len(alerts), args.threshold
    )
    for a in alerts:
        logger.info(
            "  Community %d | ratio=%.2f | severity=%s | depts=%s | members=%d",
            a.community_id, a.isolation_ratio, a.severity,
            sorted(a.departments), len(a.members),
        )

    write_alerts(alerts, args.snapshot_date)


if __name__ == "__main__":
    main()
