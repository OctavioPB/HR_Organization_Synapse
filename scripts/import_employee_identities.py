"""Link existing employees to their work e-mail and HRIS ID (migration 020).

The HRIS sync matches only on (hris_source, hris_external_id) or a unique work
e-mail.  Employees created before migration 020 have neither, so they must be
linked once from an export you trust (e.g. the HRIS employee list joined to
the internal employee IDs by your IT team).

CSV columns (header required):
    employee_id        internal UUID (employees.id)           required
    work_email         case-insensitive, unique               optional
    hris_source        workday | bamboohr                     required with hris_external_id
    hris_external_id   the HRIS worker / employee ID          optional

The whole file is validated before anything is written: duplicate e-mails or
HRIS IDs inside the file, unknown employee IDs, and identifiers already
assigned to a *different* employee all abort the import with a report.  An
identifier already set on the same employee is left as is.

Usage:
    python scripts/import_employee_identities.py --csv identities.csv            # dry run
    python scripts/import_employee_identities.py --csv identities.csv --apply
"""

from __future__ import annotations

import argparse
import csv
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_SOURCES = {"workday", "bamboohr"}


def parse_rows(rows: list[dict]) -> tuple[list[dict], list[str]]:
    """Normalise and validate CSV rows on their own (no DB).  Returns (clean, errors)."""
    clean: list[dict] = []
    errors: list[str] = []
    seen_email: dict[str, int] = {}
    seen_hris: dict[tuple[str, str], int] = {}
    seen_emp: dict[str, int] = {}
    for n, row in enumerate(rows, start=2):  # line 1 is the header
        emp = (row.get("employee_id") or "").strip()
        email = (row.get("work_email") or "").strip().lower()
        source = (row.get("hris_source") or "").strip().lower()
        ext = (row.get("hris_external_id") or "").strip()
        try:
            emp = str(uuid.UUID(emp))
        except ValueError:
            errors.append(f"line {n}: employee_id {emp!r} is not a UUID")
            continue
        if email and ("@" not in email or email.startswith("@") or email.endswith("@")):
            errors.append(f"line {n}: work_email {email!r} is not an e-mail address")
        if ext and source not in _SOURCES:
            errors.append(f"line {n}: hris_source must be one of {sorted(_SOURCES)} when hris_external_id is set")
        if not email and not ext:
            errors.append(f"line {n}: nothing to link (no work_email or hris_external_id)")
        hris_key = (source, ext) if ext else None
        for key, seen, label in ((email or None, seen_email, "work_email"), (hris_key, seen_hris, "HRIS ID")):
            if key is None:
                continue
            if key in seen:
                errors.append(f"line {n}: {label} duplicates line {seen[key]}")
            else:
                seen[key] = n
        if emp in seen_emp:
            errors.append(f"line {n}: employee_id duplicates line {seen_emp[emp]}")
        seen_emp[emp] = n
        clean.append(
            {
                "employee_id": emp,
                "work_email": email or None,
                "hris_source": source or None,
                "hris_external_id": ext or None,
            }
        )
    return clean, errors


def check_against_db(clean: list[dict], cur) -> list[str]:
    """Identifiers already held by another employee, and unknown employee IDs."""
    errors: list[str] = []
    for r in clean:
        cur.execute("SELECT 1 FROM employees WHERE id = %s::uuid", (r["employee_id"],))
        if cur.fetchone() is None:
            errors.append(f"employee {r['employee_id']} does not exist")
            continue
        if r["work_email"]:
            cur.execute(
                "SELECT id::text FROM employees WHERE LOWER(work_email) = %s AND id <> %s::uuid",
                (r["work_email"], r["employee_id"]),
            )
            other = cur.fetchone()
            if other:
                errors.append(f"work_email {r['work_email']} already belongs to employee {other[0]}")
        if r["hris_external_id"]:
            cur.execute(
                """
                SELECT id::text FROM employees
                WHERE hris_source = %s AND hris_external_id = %s AND id <> %s::uuid
                """,
                (r["hris_source"], r["hris_external_id"], r["employee_id"]),
            )
            other = cur.fetchone()
            if other:
                errors.append(f"{r['hris_source']} ID {r['hris_external_id']} already belongs to employee {other[0]}")
    return errors


def apply(clean: list[dict], cur) -> int:
    for r in clean:
        cur.execute(
            """
            UPDATE employees SET
                work_email       = COALESCE(%s, work_email),
                hris_source      = CASE WHEN %s::text IS NOT NULL THEN %s ELSE hris_source END,
                hris_external_id = COALESCE(%s, hris_external_id)
            WHERE id = %s::uuid
            """,
            (r["work_email"], r["hris_external_id"], r["hris_source"], r["hris_external_id"], r["employee_id"]),
        )
    return len(clean)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--csv", required=True, type=Path)
    parser.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    args = parser.parse_args()

    with args.csv.open(newline="", encoding="utf-8-sig") as fh:
        clean, errors = parse_rows(list(csv.DictReader(fh)))

    from ingestion.db import get_conn

    with get_conn() as conn:
        with conn.cursor() as cur:
            if not errors:
                errors = check_against_db(clean, cur)
            if errors:
                print(f"{len(errors)} problem(s); nothing written:")
                for e in errors[:50]:
                    print("  -", e)
                conn.rollback()
                return 1
            if not args.apply:
                print(f"Dry run OK: {len(clean)} employee(s) would be linked. Re-run with --apply.")
                return 0
            print(f"Linked {apply(clean, cur)} employee(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
