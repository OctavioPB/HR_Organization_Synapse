#!/usr/bin/env python3
"""One-shot dev seed: generate synthetic data + build graph + score risks.

Replaces running Airflow for local development. Connects directly to the
Postgres instance configured in .env (or environment variables).

Usage:
    python scripts/seed_dev.py
    python scripts/seed_dev.py --employees 200 --days 90
    python scripts/seed_dev.py --skip-generate   # rebuild graph from existing raw_events
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
except ImportError:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("seed_dev")

_DEPT_FRACTIONS = {
    "Engineering": 0.50,
    "Sales":       0.30,
    "HR":          0.20,
}


def _db_params() -> dict:
    return {
        "host":     os.environ.get("POSTGRES_HOST", "localhost"),
        "port":     int(os.environ.get("POSTGRES_PORT", "5433")),
        "dbname":   os.environ.get("POSTGRES_DB", "org_synapse"),
        "user":     os.environ.get("POSTGRES_USER", "opb"),
        "password": os.environ.get("POSTGRES_PASSWORD", "changeme"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed dev database with synthetic data.")
    parser.add_argument("--employees",     type=int, default=120)
    parser.add_argument("--days",          type=int, default=60)
    parser.add_argument("--connectors",    type=int, default=2)
    parser.add_argument("--seed",          type=int, default=42)
    parser.add_argument("--skip-generate", action="store_true",
                        help="Skip data generation (use existing raw_events)")
    args = parser.parse_args()

    import numpy as np
    rng = np.random.default_rng(args.seed)

    snapshot_date = date.today()

    # ── Step 1: Generate synthetic employees + raw_events ─────────────────────
    if not args.skip_generate:
        log.info("Step 1/4 — generating %d employees, %d days of events …",
                 args.employees, args.days)
        from ingestion.synthetic import (
            generate_employees,
            generate_edges,
            select_connectors,
            select_withdrawing,
            write_to_postgres,
        )

        employees      = generate_employees(args.employees, _DEPT_FRACTIONS, rng)
        connector_ids  = select_connectors(employees, rng, n_connectors=args.connectors)
        withdrawing_id = select_withdrawing(employees, connector_ids, rng)
        start_dt       = datetime(
            snapshot_date.year, snapshot_date.month, snapshot_date.day,
            tzinfo=timezone.utc,
        ) - timedelta(days=args.days)

        edges = generate_edges(
            employees=employees,
            n_days=args.days,
            rng=rng,
            connector_ids=connector_ids,
            withdrawing_id=withdrawing_id,
            start_date=start_dt,
        )

        p = _db_params()
        write_to_postgres(
            employees, edges,
            host=p["host"], port=p["port"], dbname=p["dbname"],
            user=p["user"], password=p["password"],
        )
        log.info("  → %d employees, %d edges written", len(employees), len(edges))
    else:
        log.info("Step 1/4 — skipped (--skip-generate)")

    # ── Step 2: Build graph + compute metrics + write snapshot ─────────────────
    log.info("Step 2/4 — computing graph metrics for %s …", snapshot_date)
    from graph.builder import build_graph, load_raw_edges
    from graph.metrics import (
        compute_betweenness,
        compute_clustering,
        compute_community,
        compute_degree_centrality,
        write_snapshot,
    )

    raw_edges = load_raw_edges(snapshot_date, args.days)
    G = build_graph(raw_edges)

    if G.number_of_nodes() == 0:
        log.error("Graph is empty — check postgres is reachable and raw_events were written.")
        sys.exit(1)

    betweenness            = compute_betweenness(G)
    degree_in, degree_out  = compute_degree_centrality(G)
    clustering             = compute_clustering(G)
    communities            = compute_community(G)
    write_snapshot(snapshot_date, betweenness, degree_in, degree_out, clustering, communities)
    log.info("  → %d nodes, %d communities", G.number_of_nodes(), len(set(communities.values())))

    # ── Step 3: Detect silos ──────────────────────────────────────────────────
    log.info("Step 3/4 — detecting silos …")
    from graph.silo_detector import detect_silos, write_alerts

    silo_alerts = detect_silos(G, communities)
    write_alerts(silo_alerts, snapshot_date)
    log.info("  → %d silo alert(s)", len(silo_alerts))

    # ── Step 4: Score SPOF risks ──────────────────────────────────────────────
    log.info("Step 4/4 — scoring SPOF risks …")
    from graph.risk_scorer import score_all, write_scores

    scores = score_all(G, betweenness, clustering)
    write_scores(scores, {}, snapshot_date)
    log.info("  → %d employees scored", len(scores))

    critical = sum(1 for s in scores.values() if s >= 0.7)
    warning  = sum(1 for s in scores.values() if 0.5 <= s < 0.7)

    log.info("")
    log.info("✓  Seed complete.")
    log.info("   Snapshot : %s", snapshot_date)
    log.info("   Nodes    : %d", G.number_of_nodes())
    log.info("   Edges    : %d", G.number_of_edges())
    log.info("   Silos    : %d", len(silo_alerts))
    log.info("   Critical : %d  Warning: %d", critical, warning)
    log.info("")
    log.info("   Open http://localhost:5173 and refresh the dashboard.")


if __name__ == "__main__":
    main()
