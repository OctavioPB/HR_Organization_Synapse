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

_DEFAULT_SILO_THRESHOLD = float(os.environ.get("SILO_THRESHOLD", "2.5"))


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
    """Detect departments where internal interaction dominates cross-department bridges.

    Groups nodes by their 'department' attribute rather than Louvain community ID.
    This is more reliable for demo and HR-actionable: it directly answers "is HR
    siloed from the rest of the org?" regardless of how Louvain partitions the graph.
    Louvain can fail to isolate silo departments when high-activity connectors dominate.

    Args:
        G: Directed weighted collaboration graph (nodes must have 'department' attr).
        communities: Unused — kept for API compatibility with the DAG caller.
        threshold: isolation_ratio above which a department is flagged as a silo.

    Returns:
        List of SiloAlert, one per flagged department, sorted by isolation_ratio desc.
    """
    # Group members by department attribute on the graph node
    dept_members: dict[str, set[str]] = {}
    for node in G.nodes():
        dept = G.nodes[node].get("department", "unknown")
        dept_members.setdefault(dept, set()).add(node)

    total_nodes = G.number_of_nodes()
    alerts: list[SiloAlert] = []

    for dept_idx, (dept, members) in enumerate(sorted(dept_members.items())):
        # A department that IS the whole graph means no department data — skip.
        if len(members) >= total_nodes:
            logger.debug(
                "Skipping dept '%s': contains all %d employees (no dept attribute set)",
                dept, len(members),
            )
            continue

        internal = sum(
            1 for u, v in G.edges()
            if u in members and v in members
        )
        # Count only outgoing edges (u in dept, v outside) — this measures how
        # isolated a department's own communication behaviour is, unaffected by
        # how often other active departments reach INTO them.
        outgoing_external = sum(
            1 for u, v in G.edges()
            if u in members and v not in members
        )

        isolation_ratio = internal / max(outgoing_external, 1)

        logger.debug(
            "Dept '%s': %d members, %d internal, %d outgoing-external, ratio=%.2f",
            dept, len(members), internal, outgoing_external, isolation_ratio,
        )

        if isolation_ratio <= threshold:
            continue

        severity = "critical" if isolation_ratio > threshold * 2 else "high"

        alerts.append(SiloAlert(
            community_id=dept_idx,
            members=sorted(members),
            isolation_ratio=round(isolation_ratio, 4),
            departments={dept},
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
                "member_ids": alert.members,
                "departments": sorted(alert.departments),
                "isolation_ratio": alert.isolation_ratio,
                "snapshot_date": snapshot_date.isoformat(),
            }),
            (
                f"{next(iter(alert.departments), 'Unknown')} dept. isolation_ratio={alert.isolation_ratio:.2f} "
                f"({len(alert.members)} members)"
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
