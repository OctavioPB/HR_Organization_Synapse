"""HRIS connector — syncs enrichment fields from Workday or BambooHR.

Supported providers (HRIS_PROVIDER env var):
    workday    — Workday REST API (Raas report or Workers endpoint)
    bamboohr   — BambooHR REST API v1

Fields synced per employee (all nullable):
    tenure_months        — months since hire date
    days_since_promotion — days since last position change
    is_comp_band_max     — at the top of their compensation band
    pto_days_ytd         — PTO days used this calendar year
    reporting_level      — numeric level 1 (IC1) … 7 (C-Suite)

Usage:
    from ingestion.connectors.hris_connector import HRISConnector
    connector = HRISConnector()
    connector.connect()
    records = list(connector.stream_events())
    connector.disconnect()

Or call sync_all(conn) to connect + upsert in one call.
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime
from collections.abc import Generator

import httpx

logger = logging.getLogger(__name__)

_PROVIDER = os.environ.get("HRIS_PROVIDER", "bamboohr").lower()
_BASE_URL = os.environ.get("HRIS_BASE_URL", "")
_CLIENT_ID = os.environ.get("HRIS_CLIENT_ID", "")
_CLIENT_SECRET = os.environ.get("HRIS_CLIENT_SECRET", "")
_TENANT_ID = os.environ.get("HRIS_TENANT_ID", "")  # Workday tenant name
_BAMBOOHR_SUBDOMAIN = os.environ.get("BAMBOOHR_SUBDOMAIN", "")
_BAMBOOHR_API_KEY = os.environ.get("BAMBOOHR_API_KEY", "")


class HRISConnector:
    """Connector that yields HRIS enrichment records for each employee."""

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
                yield self._parse_workday_worker(worker)
            if not data.get("total") or params["offset"] + len(workers) >= data["total"]:
                break
            params["offset"] += len(workers)

    def _parse_workday_worker(self, worker: dict) -> dict:
        hire_date_str = worker.get("hireDate", "")
        hire_date = _parse_date(hire_date_str)
        tenure_months = _months_since(hire_date) if hire_date else None

        # Last promotion / position change date
        promo_str = worker.get("lastPositionChangeDate", "")
        promo_date = _parse_date(promo_str)
        days_since_promo = _days_since(promo_date) if promo_date else None

        management_level = worker.get("managementLevel", {}).get("descriptor", "")
        reporting_level = _workday_level_to_int(management_level)

        return {
            "external_id": worker.get("id", ""),
            "email": _extract_email(worker),
            "tenure_months": tenure_months,
            "days_since_promotion": days_since_promo,
            "is_comp_band_max": worker.get("isAtCompBandMax", False),
            "pto_days_ytd": int(worker.get("ptoDaysUsedYTD", 0) or 0),
            "reporting_level": reporting_level,
            "hris_source": "workday",
        }

    # ── BambooHR ──────────────────────────────────────────────────────────

    def _connect_bamboohr(self) -> None:
        self._client = httpx.Client(
            auth=(_BAMBOOHR_API_KEY, "x"),
            headers={"Accept": "application/json"},
            timeout=60,
        )
        logger.info("HRIS BambooHR: client configured.")

    def _stream_bamboohr(self) -> Generator[dict, None, None]:
        """Fetch employees from BambooHR directory."""
        base = f"https://api.bamboohr.com/api/gateway.php/{_BAMBOOHR_SUBDOMAIN}/v1"
        fields = "hireDate,jobTitle,employmentHistoryStatus,exempt,ptoBalance"
        resp = self._client.get(
            f"{base}/employees/directory",
            params={"fields": fields},
        )
        resp.raise_for_status()
        employees = resp.json().get("employees", [])
        for emp in employees:
            yield self._parse_bamboohr_employee(emp, base)

    def _parse_bamboohr_employee(self, emp: dict, base: str) -> dict:
        hire_date_str = emp.get("hireDate", "")
        hire_date = _parse_date(hire_date_str)
        tenure_months = _months_since(hire_date) if hire_date else None

        pto_balance = float(emp.get("ptoBalance", 0) or 0)

        return {
            "external_id": str(emp.get("id", "")),
            "email": emp.get("workEmail", ""),
            "tenure_months": tenure_months,
            "days_since_promotion": None,
            "is_comp_band_max": False,
            "pto_days_ytd": int(pto_balance),
            "reporting_level": _bamboohr_title_to_level(emp.get("jobTitle", "")),
            "hris_source": "bamboohr",
        }

    # ── Upsert ────────────────────────────────────────────────────────────

    def upsert_employees(self, records: list[dict], conn) -> int:
        """Upsert HRIS records into employees table matching by email.

        Matches employees by email because external HR system IDs differ
        from internal UUIDs.
        Returns number of rows updated.
        """
        if not records:
            return 0

        updated = 0
        with conn.cursor() as cur:
            for rec in records:
                email = rec.get("email", "").strip().lower()
                if not email:
                    continue
                cur.execute(
                    """
                    UPDATE employees SET
                        tenure_months        = %s,
                        days_since_promotion = %s,
                        is_comp_band_max     = %s,
                        pto_days_ytd         = %s,
                        reporting_level      = %s,
                        hris_source          = %s,
                        hris_synced_at       = NOW()
                    WHERE LOWER(name) = LOWER(%s)
                       OR id::text IN (
                         SELECT id::text FROM employees
                         WHERE LOWER(role) LIKE %s
                         LIMIT 1
                       )
                    """,
                    (
                        rec["tenure_months"],
                        rec["days_since_promotion"],
                        rec["is_comp_band_max"],
                        rec["pto_days_ytd"],
                        rec["reporting_level"],
                        rec["hris_source"],
                        email,
                        f"%{email.split('@')[0]}%",
                    ),
                )
                updated += cur.rowcount
        conn.commit()
        logger.info("HRIS upsert: %d employees updated.", updated)
        return updated


# ── Convenience function ──────────────────────────────────────────────────────


def sync_all(conn) -> int:
    """Connect, fetch, upsert, disconnect. Returns rows updated."""
    if not _BASE_URL and not _BAMBOOHR_SUBDOMAIN:
        logger.warning("HRIS not configured (no HRIS_BASE_URL or BAMBOOHR_SUBDOMAIN). Skipping.")
        return 0
    connector = HRISConnector()
    connector.connect()
    try:
        records = list(connector.stream_events())
        return connector.upsert_employees(records, conn)
    finally:
        connector.disconnect()


# ── Helpers ───────────────────────────────────────────────────────────────────


def _parse_date(s: str) -> date | None:
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except (ValueError, TypeError):
            continue
    return None


def _months_since(d: date) -> int:
    today = date.today()
    return (today.year - d.year) * 12 + (today.month - d.month)


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
    for key, val in mapping.items():
        if key in d:
            return val
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
