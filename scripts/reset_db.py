#!/usr/bin/env python3
"""Drop all application data from the dev database.

Truncates every application table (cascade) so the database is structurally
intact but completely empty. Run seed_dev.py afterwards to repopulate.

Usage:
    python scripts/reset_db.py            # prompts for confirmation
    python scripts/reset_db.py --yes      # non-interactive (CI / scripting)
    python scripts/reset_db.py --yes --seed   # reset then immediately reseed
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
except ImportError:
    pass

# ── All application tables in dependency-safe order ───────────────────────────
# TRUNCATE … CASCADE handles FK constraints regardless, but explicit order
# makes the intent readable.
_TABLES = [
    # Graph / risk data
    "risk_scores",
    "graph_snapshots",
    "alerts",
    # Raw ingestion
    "raw_events",
    # ML / churn
    "temporal_anomaly_scores",
    "churn_scores",
    "churn_labels",
    # Knowledge risk
    "knowledge_risk_scores",
    "employee_knowledge",
    "document_knowledge",
    "knowledge_domains",
    # Succession
    "succession_recommendations",
    # Org health
    "org_health_scores",
    # Compliance audit trail
    "consent_audit_log",
    "data_retention_purges",
    # Multi-tenant
    "stripe_webhook_events",
    "tenant_usage",
    "tenant_api_keys",
    "tenants",
    # Core — last because others reference it
    "employees",
]


def _db_params() -> dict:
    return {
        "host":     os.environ.get("POSTGRES_HOST", "localhost"),
        "port":     int(os.environ.get("POSTGRES_PORT", "5433")),
        "dbname":   os.environ.get("POSTGRES_DB", "org_synapse"),
        "user":     os.environ.get("POSTGRES_USER", "opb"),
        "password": os.environ.get("POSTGRES_PASSWORD", "changeme"),
    }


def reset(yes: bool) -> None:
    if not yes:
        print()
        print("  WARNING: This will DELETE ALL DATA in the org_synapse database.")
        print("     Tables are preserved; only rows are removed.")
        print()
        answer = input("  Type 'yes' to continue: ").strip().lower()
        if answer != "yes":
            print("  Aborted.")
            sys.exit(0)

    try:
        import psycopg2
    except ImportError:
        sys.exit("psycopg2 not installed — run: pip install psycopg2-binary")

    p = _db_params()
    print(f"\n  Connecting to {p['host']}:{p['port']}/{p['dbname']} …")

    conn = psycopg2.connect(**p)
    conn.autocommit = False

    try:
        with conn.cursor() as cur:
            table_list = ", ".join(_TABLES)
            cur.execute(f"TRUNCATE {table_list} CASCADE;")
            rows_cleared = cur.rowcount  # psycopg2 returns -1 for TRUNCATE, that's fine
        conn.commit()
        print(f"  OK  Truncated {len(_TABLES)} tables.\n")
    except Exception as exc:
        conn.rollback()
        sys.exit(f"  ERR  Database error: {exc}")
    finally:
        conn.close()

    # Flush Redis cache so stale snapshots are not served after the reset
    try:
        from api.cache import flush_all
        n = flush_all()
        if n:
            print(f"  OK  Flushed {n} Redis cache key(s).\n")
    except Exception:
        pass  # Redis unavailable — not a blocking error


def main() -> None:
    parser = argparse.ArgumentParser(description="Reset the org_synapse dev database.")
    parser.add_argument("--yes",  "-y", action="store_true", help="Skip confirmation prompt")
    parser.add_argument("--seed",        action="store_true", help="Run seed_dev.py after reset")
    parser.add_argument("--employees",   type=int, default=120, help="Employees to seed (default 120)")
    parser.add_argument("--days",        type=int, default=60,  help="Days of events to seed (default 60)")
    args = parser.parse_args()

    reset(yes=args.yes)

    if args.seed:
        seed_script = Path(__file__).parent / "seed_dev.py"
        print("  Running seed_dev.py …\n")
        result = subprocess.run(
            [sys.executable, str(seed_script),
             "--employees", str(args.employees),
             "--days",      str(args.days)],
            check=False,
        )
        sys.exit(result.returncode)


if __name__ == "__main__":
    main()
