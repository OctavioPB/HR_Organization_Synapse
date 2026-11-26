"""Bring every tenant schema up to date with data/migrations/.

New tenants are built from the migration set at provisioning time
(api.tenant.apply_tenant_migrations).  Tenants provisioned before that change
were built from a stale inline DDL copy and are missing migrations 009–017;
run this once to repair them, and again after adding any new migration.

Each tenant is migrated in its own transaction, so one failure does not block
the others.  All migrations are idempotent, so re-running is safe.

Usage:
    python scripts/migrate_tenants.py              # all active tenants
    python scripts/migrate_tenants.py --dry-run    # list tenants + migrations, change nothing
    python scripts/migrate_tenants.py --slug acme-corp
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api.tenant import apply_tenant_migrations, tenant_migration_files
from ingestion.db import get_conn

logger = logging.getLogger("migrate_tenants")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--slug", help="Migrate only this tenant")
    parser.add_argument("--dry-run", action="store_true", help="List what would run; change nothing")
    args = parser.parse_args()

    files = [p.name for p in tenant_migration_files()]

    with get_conn() as conn, conn.cursor() as cur:
        if args.slug:
            cur.execute("SELECT slug, schema_name FROM public.tenants WHERE slug = %s", (args.slug,))
        else:
            cur.execute("SELECT slug, schema_name FROM public.tenants WHERE active = true ORDER BY slug")
        tenants = [(r[0], r[1]) for r in cur.fetchall()]

    if not tenants:
        logger.warning("No matching tenants found.")
        return 1 if args.slug else 0

    logger.info("%d tenant(s); %d migrations each: %s … %s", len(tenants), len(files), files[0], files[-1])
    if args.dry_run:
        for slug, schema in tenants:
            logger.info("would migrate %s (%s)", slug, schema)
        return 0

    failures = 0
    for slug, schema in tenants:
        try:
            with get_conn() as conn:  # commits on success, rolls back on error
                apply_tenant_migrations(conn, schema)
            logger.info("migrated %s (%s)", slug, schema)
        except Exception:
            failures += 1
            logger.exception("FAILED %s (%s) — rolled back", slug, schema)

    logger.info("done: %d ok, %d failed", len(tenants) - failures, failures)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
