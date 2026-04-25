#!/usr/bin/env python3
"""Synthetic collaboration data generator — CLI entry point.

Usage:
    python data/synthetic/generate_org_data.py --employees 200 --days 90
    python data/synthetic/generate_org_data.py --employees 50 --days 30 --no-db
    python data/synthetic/generate_org_data.py --help
"""

import argparse
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import psycopg2

# Add project root to sys.path when running as script from any directory
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ingestion.synthetic import (
    generate_edges,
    generate_employees,
    select_connectors,
    select_withdrawing,
    write_csvs,
    write_to_postgres,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate synthetic collaboration events for Org Synapse."
    )
    parser.add_argument("--employees", type=int, default=200,
                        help="Total number of employees (default: 200)")
    parser.add_argument("--days", type=int, default=90,
                        help="Days of history to simulate (default: 90)")
    parser.add_argument(
        "--departments",
        type=str,
        default="Engineering:0.5,Sales:0.33,HR:0.17",
        help="dept:fraction pairs, comma-separated (fractions should sum to 1.0)",
    )
    parser.add_argument("--output-dir", type=Path, default=Path("data/synthetic"),
                        help="Directory for CSV output (default: data/synthetic)")
    parser.add_argument("--seed", type=int, default=42,
                        help="Random seed for reproducibility (default: 42)")
    parser.add_argument("--no-db", action="store_true",
                        help="Skip PostgreSQL write; generate CSVs only")
    parser.add_argument("--start-date", type=str, default=None,
                        help="Simulation start date YYYY-MM-DD (default: today − days)")
    args = parser.parse_args()

    rng = np.random.default_rng(args.seed)

    # Parse department fractions
    dept_fractions: dict[str, float] = {}
    for part in args.departments.split(","):
        dept, frac = part.strip().split(":")
        dept_fractions[dept.strip()] = float(frac.strip())

    # Resolve start date
    if args.start_date:
        start_date = datetime.fromisoformat(args.start_date).replace(tzinfo=timezone.utc)
    else:
        start_date = (
            datetime.now(timezone.utc) - timedelta(days=args.days)
        ).replace(hour=0, minute=0, second=0, microsecond=0)

    logger.info(
        "Generating %d employees over %d days starting %s (seed=%d)",
        args.employees, args.days, start_date.date(), args.seed,
    )

    employees = generate_employees(args.employees, dept_fractions, rng)

    connector_ids = select_connectors(employees, rng, n_connectors=2)
    withdrawing_id = select_withdrawing(employees, connector_ids, rng)

    connector_names = [e.name for e in employees if e.employee_id in connector_ids]
    withdrawing_name = next(e.name for e in employees if e.employee_id == withdrawing_id)
    logger.info("Connectors: %s", connector_names)
    logger.info("Withdrawing: %s (70%% activity decay in last 15 days)", withdrawing_name)

    edges = generate_edges(
        employees=employees,
        n_days=args.days,
        rng=rng,
        connector_ids=connector_ids,
        withdrawing_id=withdrawing_id,
        start_date=start_date,
    )
    logger.info("Generated %d edges", len(edges))

    write_csvs(employees, edges, args.output_dir)

    if not args.no_db:
        try:
            write_to_postgres(
                employees=employees,
                edges=edges,
                host=os.environ.get("POSTGRES_HOST", "localhost"),
                port=int(os.environ.get("POSTGRES_PORT", "5432")),
                dbname=os.environ.get("POSTGRES_DB", "org_synapse"),
                user=os.environ.get("POSTGRES_USER", "opb"),
                password=os.environ.get("POSTGRES_PASSWORD", "changeme"),
            )
        except psycopg2.OperationalError as exc:
            logger.warning(
                "DB write skipped (Postgres not available): %s\n"
                "Run with --no-db to suppress this warning, or start docker-compose.",
                exc,
            )

    logger.info("Done.")


if __name__ == "__main__":
    main()
