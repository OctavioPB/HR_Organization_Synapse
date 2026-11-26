"""HRIS connector — syncs enrichment fields and departures from Workday or BambooHR.

Supported providers (HRIS_PROVIDER env var):
    workday    — Workday REST API (Workers endpoint)
    bamboohr   — BambooHR REST API v1 (custom report, terminated employees included)

Fields synced per employee (all nullable):
    hire_date, tenure_months, days_since_promotion, is_comp_band_max,
    pto_days_ytd, reporting_level

Identity (migration 020)
────────────────────────
An HRIS record is matched to an employee by, in order:

    1. (hris_source, hris_external_id)   the HRIS's own worker ID — unique
    2. LOWER(work_email)                 unique — and the HRIS ID is then linked

Nothing else.  The previous version matched ``LOWER(name) = email`` or the
first employee whose *role* contained the e-mail prefix, so it could update
the wrong person.  Records that match no employee are counted and logged
(by HRIS ID, never by name), not guessed.  Link existing employees once with
scripts/import_employee_identities.py.

Departures
──────────
A record with a termination date on or before today marks the employee as
departed: ``active = false``, ``left_at`` = termination date,
``departure_type`` ∈ {voluntary, involuntary, unknown}, and one
``churn_labels`` row (``churned`` = true unless involuntary — the churn model
predicts voluntary departure).  Future-dated terminations wait for their date.
A rehire (new hire date after the recorded departure, no current termination)
reactivates the employee.

Usage:
    from ingestion.connectors.hris_connector import sync_all
    report = sync_all(conn)      # {"matched", "linked", "unmatched", "departures", ...}
"""

from __future__ import annotations

import logging
import os
from collections.abc import Generator, Iterable
from datetime import date, datetime

import httpx

logger = logging.getLogger(__name__)

_PROVIDER = os.environ.get("HRIS_PROVIDER", "bamboohr").lower()
_BASE_URL = os.environ.get("HRIS_BASE_URL", "")
_CLIENT_ID = os.environ.get("HRIS_CLIENT_ID", "")
_CLIENT_SECRET = os.environ.get("HRIS_CLIENT_SECRET", "")
_TENANT_ID = os.environ.get("HRIS_TENANT_ID", "")  # Workday tenant name
_BAMBOOHR_SUBDOMAIN = os.environ.get("BAMBOOHR_SUBDOMAIN", "")
_BAMBOOHR_API_KEY = os.environ.get("BAMBOOHR_API_KEY", "")

_BAMBOOHR_REPORT_FIELDS = [
    "id",
    "workEmail",
    "hireDate",
    "jobTitle",
    "status",
    "employmentHistoryStatus",
    "terminationDate",
    "terminationTypeId",
    "ptoBalance",
]


class HRISConnector:
    """Connector that yields normalised HRIS records (see _record)."""

    def __init__(self):
        self._client: httpx.Client | None = None
        self._token: str | None = None

    # ── BaseProducer interface ─────────────────────────────────────────────

    def connect(self) -> None:
        if _PROVIDER == "workday":
            self._connect_workday()
        elif _PROVIDER == "bamboohr":
            self._connect_bamboohr()
        else:
            raise ValueError(f"Unsupported HRIS provider: {_PROVIDER}")

    def stream_events(self) -> Generator[dict, None, None]:
        if _PROVIDER == "workday":
            yield from self._stream_workday()
        elif _PROVIDER == "bamboohr":
            yield from self._stream_bamboohr()

    def disconnect(self) -> None:
        if self._client:
            self._client.close()
            self._client = None

    # ── Workday ───────────────────────────────────────────────────────────

    def _connect_workday(self) -> None:
        token_url = f"{_BASE_URL}/ccx/oauth2/{_TENANT_ID}/token"
        resp = httpx.post(
            token_url,
            data={
                "grant_type": "client_credentials",
                "client_id": _CLIENT_ID,
                "client_secret": _CLIENT_SECRET,
            },
            timeout=30,
        )
        resp.raise_for_status()
        self._token = resp.json()["access_token"]
        self._client = httpx.Client(
            headers={"Authorization": f"Bearer {self._token}"},
            timeout=60,
        )
        logger.info("HRIS Workday: authenticated successfully.")

    def _stream_workday(self) -> Generator[dict, None, None]:
        """Fetch workers from Workday REST API (paginated)."""
        url = f"{_BASE_URL}/ccx/api/v1/{_TENANT_ID}/workers"
        params = {"limit": 100, "offset": 0}
        while True:
            resp = self._client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            workers = data.get("data", [])
            for worker in workers:
                yield parse_workday_worker(worker)
            if not data.get("total") or params["offset"] + len(workers) >= data["total"]:
                break
            params["offset"] += len(workers)

    # ── BambooHR ──────────────────────────────────────────────────────────

    def _connect_bamboohr(self) -> None:
        self._client = httpx.Client(
            auth=(_BAMBOOHR_API_KEY, "x"),
            headers={"Accept": "application/json"},
            timeout=60,
        )
        logger.info("HRIS BambooHR: client configured.")

    def _stream_bamboohr(self) -> Generator[dict, None, None]:
        """Fetch all employees, terminated ones included, via a custom report.

        The /employees/directory endpoint lists current employees only, so
        departures were invisible to the previous version.
        """
        base = f"https://api.bamboohr.com/api/gateway.php/{_BAMBOOHR_SUBDOMAIN}/v1"
        resp = self._client.post(
            f"{base}/reports/custom",
            params={"format": "JSON", "onlyCurrent": "false"},
            json={"fields": _BAMBOOHR_REPORT_FIELDS},
        )
        resp.raise_for_status()
        for emp in resp.json().get("employees", []):
            yield parse_bamboohr_employee(emp)


# ── Parsing (pure) ────────────────────────────────────────────────────────────


def _record(
    source: str,
    external_id: str,
    email: str,
    hire_date: date | None,
    termination_date: date | None,
    termination_type: str | None,
    **enrichment,
) -> dict:
    end = termination_date if termination_date and termination_date <= date.today() else None
    return {
        "hris_source": source,
        "external_id": str(external_id or "").strip(),
        "email": (email or "").strip().lower(),
        "hire_date": hire_date,
        "tenure_months": _months_between(hire_date, end or date.today()) if hire_date else None,
        "termination_date": termination_date,
        "termination_type": termination_type,
        "days_since_promotion": enrichment.get("days_since_promotion"),
        "is_comp_band_max": bool(enrichment.get("is_comp_band_max", False)),
        "pto_days_ytd": int(enrichment.get("pto_days_ytd") or 0),
        "reporting_level": enrichment.get("reporting_level"),
    }


def termination_kind(text: str | None) -> str:
    """Map an HRIS termination type/reason label to voluntary | involuntary | unknown."""
    t = (text or "").lower()
    if "involuntary" in t or any(k in t for k in ("dismiss", "layoff", "laid off", "redundan", "fired")):
        return "involuntary"
    if "voluntary" in t or any(k in t for k in ("resign", "retire")):
        return "voluntary"
    return "unknown"


def parse_workday_worker(worker: dict) -> dict:
    hire_date = _parse_date(worker.get("hireDate", ""))
    promo_date = _parse_date(worker.get("lastPositionChangeDate", ""))
    term_date = _parse_date(worker.get("terminationDate") or (worker.get("primaryJob") or {}).get("endDate") or "")
    reason = (worker.get("terminationReason") or {}).get("descriptor") or (
        worker.get("primaryTerminationCategory") or {}
    ).get("descriptor")
    return _record(
        "workday",
        worker.get("id", ""),
        _extract_email(worker),
        hire_date,
        term_date,
        termination_kind(reason) if term_date else None,
        days_since_promotion=_days_since(promo_date) if promo_date else None,
        is_comp_band_max=worker.get("isAtCompBandMax", False),
        pto_days_ytd=worker.get("ptoDaysUsedYTD", 0),
        reporting_level=_workday_level_to_int((worker.get("managementLevel") or {}).get("descriptor", "")),
    )


def parse_bamboohr_employee(emp: dict, base: str | None = None) -> dict:
    hire_date = _parse_date(emp.get("hireDate", ""))
    term_date = _parse_date(emp.get("terminationDate") or "")
    try:
        pto = float(emp.get("ptoBalance", 0) or 0)
    except (TypeError, ValueError):
        pto = 0.0
    return _record(
        "bamboohr",
        emp.get("id", ""),
        emp.get("workEmail", ""),
        hire_date,
        term_date,
        termination_kind(emp.get("terminationTypeId")) if term_date else None,
        days_since_promotion=None,
        is_comp_band_max=False,
        pto_days_ytd=int(pto),
        reporting_level=_bamboohr_title_to_level(emp.get("jobTitle", "") or ""),
    )


# ── Sync ──────────────────────────────────────────────────────────────────────


def _find_employee(cur, rec: dict):
    """Return (employee_id, linked_hris_id, left_at) or None.  Identifier match only."""
    if rec["external_id"]:
        cur.execute(
            """
            SELECT id::text, hris_external_id, left_at FROM employees
            WHERE hris_source = %s AND hris_external_id = %s
            """,
            (rec["hris_source"], rec["external_id"]),
        )
        row = cur.fetchone()
        if row:
            return row
    if rec["email"]:
        cur.execute(
            """
            SELECT id::text, hris_external_id, left_at, hris_source FROM employees
            WHERE LOWER(work_email) = %s
            """,
            (rec["email"],),
        )
        row = cur.fetchone()
        if row:
            emp_id, linked, left_at, source = row
            if linked and (source != rec["hris_source"] or linked != rec["external_id"]):
                return ("conflict", linked, left_at)
            return (emp_id, linked, left_at)
    return None


def sync_records(records: Iterable[dict], conn) -> dict:
    """Apply normalised HRIS records.  Returns counts; never matches by name or role."""
    report = {"matched": 0, "linked": 0, "unmatched": 0, "conflicts": 0, "departures": 0, "rehired": 0}
    unmatched_ids: list[str] = []
    today = date.today()

    with conn.cursor() as cur:
        for rec in records:
            if not rec.get("external_id") and not rec.get("email"):
                report["unmatched"] += 1
                continue
            found = _find_employee(cur, rec)
            if found is None:
                report["unmatched"] += 1
                unmatched_ids.append(rec["external_id"] or "<no id>")
                continue
            emp_id, linked, left_at = found[0], found[1], found[2]
            if emp_id == "conflict":
                report["conflicts"] += 1
                logger.warning(
                    "HRIS %s record %s: e-mail belongs to an employee already linked to %s — skipped",
                    rec["hris_source"],
                    rec["external_id"],
                    linked,
                )
                continue
            report["matched"] += 1
            if not linked and rec["external_id"]:
                report["linked"] += 1

            cur.execute(
                """
                UPDATE employees SET
                    hire_date            = COALESCE(%s, hire_date),
                    tenure_months        = %s,
                    days_since_promotion = %s,
                    is_comp_band_max     = %s,
                    pto_days_ytd         = %s,
                    reporting_level      = %s,
                    hris_source          = %s,
                    hris_external_id     = COALESCE(hris_external_id, NULLIF(%s, '')),
                    work_email           = COALESCE(work_email, NULLIF(%s, '')),
                    hris_synced_at       = NOW()
                WHERE id = %s::uuid
                """,
                (
                    rec["hire_date"],
                    rec["tenure_months"],
                    rec["days_since_promotion"],
                    rec["is_comp_band_max"],
                    rec["pto_days_ytd"],
                    rec["reporting_level"],
                    rec["hris_source"],
                    rec["external_id"],
                    rec["email"],
                    emp_id,
                ),
            )

            term = rec.get("termination_date")
            hire = rec.get("hire_date")
            if term and term <= today and not (hire and hire > term):
                kind = rec.get("termination_type") or "unknown"
                cur.execute(
                    """
                    UPDATE employees
                    SET active = false, left_at = %s, departure_type = %s
                    WHERE id = %s::uuid
                      AND (left_at IS NULL OR left_at::date <> %s OR departure_type IS DISTINCT FROM %s)
                    """,
                    (term, kind, emp_id, term, kind),
                )
                changed = cur.rowcount
                cur.execute(
                    """
                    INSERT INTO churn_labels (employee_id, label_date, churned, notes)
                    VALUES (%s::uuid, %s, %s, %s)
                    ON CONFLICT (employee_id, label_date) DO UPDATE
                        SET churned = EXCLUDED.churned, notes = EXCLUDED.notes
                    """,
                    (emp_id, term, kind != "involuntary", f"hris:{rec['hris_source']}:{kind}"),
                )
                report["departures"] += int(changed > 0)
            elif _is_rehire(hire, left_at):
                cur.execute(
                    """
                    UPDATE employees SET active = true, left_at = NULL, departure_type = NULL
                    WHERE id = %s::uuid
                    """,
                    (emp_id,),
                )
                report["rehired"] += 1

    conn.commit()
    if unmatched_ids:
        logger.warning(
            "HRIS sync: %d record(s) match no employee by HRIS ID or work e-mail (first: %s). "
            "Link them with scripts/import_employee_identities.py.",
            len(unmatched_ids),
            ", ".join(unmatched_ids[:5]),
        )
    logger.info("HRIS sync: %s", report)
    return report


def sync_all(conn) -> dict:
    """Connect, fetch, sync, disconnect.  Returns the sync report."""
    if not _BASE_URL and not _BAMBOOHR_SUBDOMAIN:
        logger.warning("HRIS not configured (no HRIS_BASE_URL or BAMBOOHR_SUBDOMAIN). Skipping.")
        return {"skipped": True}
    connector = HRISConnector()
    connector.connect()
    try:
        return sync_records(list(connector.stream_events()), conn)
    finally:
        connector.disconnect()


# ── Helpers ───────────────────────────────────────────────────────────────────


def _parse_date(s: str) -> date | None:
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except (ValueError, TypeError):
            continue
    if isinstance(s, str) and len(s) >= 10:
        try:
            return date.fromisoformat(s[:10])
        except ValueError:
            pass
    return None


def _is_rehire(hire: date | None, left_at) -> bool:
    """A hire date after the recorded departure means the person came back."""
    if hire is None or left_at is None:
        return False
    left = left_at.date() if isinstance(left_at, datetime) else left_at
    return hire > left


def _months_between(start: date, end: date) -> int:
    return max(0, (end.year - start.year) * 12 + (end.month - start.month))


def _days_since(d: date) -> int:
    return (date.today() - d).days


def _extract_email(worker: dict) -> str:
    emails = worker.get("businessEmails", []) or []
    return emails[0] if emails else worker.get("primaryWorkEmail", "")


def _workday_level_to_int(descriptor: str) -> int:
    mapping = {
        "individual contributor": 2,
        "manager": 3,
        "senior manager": 4,
        "director": 5,
        "senior director": 5,
        "vp": 6,
        "svp": 6,
        "evp": 6,
        "c-suite": 7,
        "executive": 7,
    }
    d = descriptor.lower()
    # Longest key first: "senior manager" must win over "manager".
    for key in sorted(mapping, key=len, reverse=True):
        if key in d:
            return mapping[key]
    return 2


def _bamboohr_title_to_level(title: str) -> int:
    t = title.lower()
    if any(x in t for x in ("ceo", "cto", "cfo", "coo", "ciso", "chief")):
        return 7
    if any(x in t for x in ("evp", "svp", "vp ", "vice president")):
        return 6
    if "director" in t:
        return 5
    if any(x in t for x in ("senior manager", "sr. manager", "sr manager")):
        return 4
    if "manager" in t:
        return 3
    if "senior" in t or "staff" in t or "principal" in t:
        return 2
    return 1
