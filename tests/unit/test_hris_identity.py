"""HRIS sync: identifier-only matching, departures, and the identity import.

Pure parsing tests always run.  TestSyncAgainstPostgres runs sync_records on
a real database inside a transaction that is rolled back; it is skipped when
Postgres (with migration 020) is not reachable.
"""

from __future__ import annotations

import os
import uuid
from datetime import date, timedelta

import pytest

from ingestion.connectors.hris_connector import (
    _is_rehire,
    _workday_level_to_int,
    parse_bamboohr_employee,
    parse_workday_worker,
    sync_records,
    termination_kind,
)
from scripts.import_employee_identities import parse_rows

TODAY = date.today()


class TestParsing:
    @pytest.mark.parametrize(
        "label,kind",
        [
            ("Involuntary", "involuntary"),
            ("Terminate Employee > Voluntary > Resignation", "voluntary"),
            ("Retirement", "voluntary"),
            ("Layoff", "involuntary"),
            ("", "unknown"),
            (None, "unknown"),
        ],
    )
    def test_termination_kind(self, label, kind):
        assert termination_kind(label) == kind

    def test_bamboohr_terminated_employee(self):
        rec = parse_bamboohr_employee(
            {
                "id": "123",
                "workEmail": " Ana.Lopez@Example.com ",
                "hireDate": "2021-03-01",
                "terminationDate": (TODAY - timedelta(days=10)).isoformat(),
                "terminationTypeId": "Voluntary",
                "jobTitle": "Senior Manager",
            }
        )
        assert rec["external_id"] == "123" and rec["email"] == "ana.lopez@example.com"
        assert rec["termination_type"] == "voluntary"
        assert rec["hris_source"] == "bamboohr"
        assert rec["reporting_level"] == 4

    def test_bamboohr_active_employee_has_no_termination(self):
        rec = parse_bamboohr_employee({"id": "9", "workEmail": "x@example.com", "hireDate": "2024-01-01"})
        assert rec["termination_date"] is None and rec["termination_type"] is None

    def test_workday_termination_from_primary_job(self):
        rec = parse_workday_worker(
            {
                "id": "W-77",
                "businessEmails": ["w@example.com"],
                "hireDate": "2020-05-04",
                "primaryJob": {"endDate": "2026-01-15T00:00:00.000Z"},
                "terminationReason": {"descriptor": "Involuntary > Position Eliminated"},
            }
        )
        assert rec["termination_date"] == date(2026, 1, 15)
        assert rec["termination_type"] == "involuntary"

    def test_workday_level_prefers_longest_match(self):
        assert _workday_level_to_int("Senior Manager") == 4
        assert _workday_level_to_int("Manager") == 3

    def test_rehire_detection(self):
        assert _is_rehire(date(2026, 3, 1), date(2025, 12, 31))
        assert not _is_rehire(date(2020, 1, 1), date(2025, 12, 31))
        assert not _is_rehire(None, date(2025, 12, 31))


class TestImportRows:
    E1, E2 = str(uuid.uuid4()), str(uuid.uuid4())

    def test_valid_rows(self):
        clean, errors = parse_rows(
            [
                {"employee_id": self.E1, "work_email": "A@example.com", "hris_source": "", "hris_external_id": ""},
                {"employee_id": self.E2, "work_email": "", "hris_source": "workday", "hris_external_id": "W1"},
            ]
        )
        assert errors == []
        assert clean[0]["work_email"] == "a@example.com"
        assert clean[1]["hris_external_id"] == "W1"

    def test_duplicates_are_rejected_case_insensitively(self):
        _, errors = parse_rows(
            [
                {"employee_id": self.E1, "work_email": "a@example.com"},
                {"employee_id": self.E2, "work_email": "A@EXAMPLE.COM"},
            ]
        )
        assert any("duplicates line 2" in e for e in errors)

    def test_bad_rows(self):
        _, errors = parse_rows(
            [
                {"employee_id": "not-a-uuid", "work_email": "a@example.com"},
                {"employee_id": self.E1, "work_email": "nope"},
                {"employee_id": self.E2, "hris_external_id": "W1", "hris_source": "sap"},
            ]
        )
        assert len(errors) == 3


# ─── Against a real database ──────────────────────────────────────────────────


def _pg():
    import psycopg2

    try:
        conn = psycopg2.connect(
            host=os.environ.get("POSTGRES_HOST", "localhost"),
            port=int(os.environ.get("POSTGRES_PORT", "5432")),
            dbname=os.environ.get("POSTGRES_DB", "org_synapse"),
            user=os.environ.get("POSTGRES_USER", "opb"),
            password=os.environ.get("POSTGRES_PASSWORD", "changeme"),
            connect_timeout=2,
        )
    except Exception:
        return None
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM information_schema.columns WHERE table_name = 'employees' AND column_name = 'work_email'"
        )
        ok = cur.fetchone() is not None
    if not ok:
        conn.close()
        return None
    return conn


class _NoCommit:
    """Wraps a connection so sync_records' commit stays inside the test transaction."""

    def __init__(self, conn):
        self._conn = conn

    def cursor(self, *a, **k):
        return self._conn.cursor(*a, **k)

    def commit(self):
        pass


@pytest.fixture
def db():
    conn = _pg()
    if conn is None:
        pytest.skip("Postgres with migration 020 not reachable")
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


def _employee(cur, name: str, role: str = "Engineer", email: str | None = None, **cols) -> str:
    emp = str(uuid.uuid4())
    cur.execute(
        """
        INSERT INTO employees (id, name, department, role, work_email, hris_source, hris_external_id)
        VALUES (%s, %s, 'Engineering', %s, %s, %s, %s)
        """,
        (emp, name, role, email, cols.get("hris_source"), cols.get("hris_external_id")),
    )
    return emp


def _rec(**kw) -> dict:
    base = {
        "hris_source": "bamboohr",
        "external_id": "",
        "email": "",
        "hire_date": date(2021, 1, 4),
        "tenure_months": 40,
        "termination_date": None,
        "termination_type": None,
        "days_since_promotion": None,
        "is_comp_band_max": False,
        "pto_days_ytd": 3,
        "reporting_level": 2,
    }
    base.update(kw)
    return base


class TestSyncAgainstPostgres:
    def test_never_matches_by_name_or_role(self, db):
        tag = uuid.uuid4().hex[:8]
        with db.cursor() as cur:
            # The old matcher would have picked this person: name equals the e-mail,
            # and the role contains the e-mail prefix.
            decoy = _employee(cur, f"ana.{tag}@example.com", role=f"ana.{tag} lead")
        report = sync_records([_rec(external_id=f"B-{tag}", email=f"ana.{tag}@example.com")], _NoCommit(db))
        assert report["matched"] == 0 and report["unmatched"] == 1
        with db.cursor() as cur:
            cur.execute("SELECT hris_synced_at FROM employees WHERE id = %s", (decoy,))
            assert cur.fetchone()[0] is None

    def test_email_match_links_hris_id_then_id_match_wins(self, db):
        tag = uuid.uuid4().hex[:8]
        with db.cursor() as cur:
            emp = _employee(cur, "Ana", email=f"Ana.{tag}@Example.com")
        r1 = sync_records([_rec(external_id=f"B-{tag}", email=f"ana.{tag}@example.com")], _NoCommit(db))
        assert r1["matched"] == 1 and r1["linked"] == 1
        # Later the e-mail changes in the HRIS: the linked ID still finds her.
        r2 = sync_records([_rec(external_id=f"B-{tag}", email=f"new.{tag}@example.com")], _NoCommit(db))
        assert r2["matched"] == 1 and r2["linked"] == 0
        with db.cursor() as cur:
            cur.execute("SELECT hris_external_id, hire_date FROM employees WHERE id = %s", (emp,))
            assert cur.fetchone() == (f"B-{tag}", date(2021, 1, 4))

    def test_email_already_linked_to_other_hris_id_is_a_conflict(self, db):
        tag = uuid.uuid4().hex[:8]
        with db.cursor() as cur:
            _employee(cur, "Bo", email=f"bo.{tag}@example.com", hris_source="bamboohr", hris_external_id=f"B-{tag}")
        report = sync_records([_rec(external_id=f"OTHER-{tag}", email=f"bo.{tag}@example.com")], _NoCommit(db))
        assert report["conflicts"] == 1 and report["matched"] == 0

    def test_duplicate_email_is_rejected_by_the_schema(self, db):
        import psycopg2

        tag = uuid.uuid4().hex[:8]
        with db.cursor() as cur:
            _employee(cur, "Cy", email=f"cy.{tag}@example.com")
            with pytest.raises(psycopg2.errors.UniqueViolation):
                _employee(cur, "Cy Two", email=f"CY.{tag}@EXAMPLE.COM")

    def test_departure_recorded_and_involuntary_not_a_churn_positive(self, db):
        tag = uuid.uuid4().hex[:8]
        left = TODAY - timedelta(days=20)
        with db.cursor() as cur:
            vol = _employee(cur, "Vo", email=f"vo.{tag}@example.com")
            inv = _employee(cur, "In", email=f"in.{tag}@example.com")
            fut = _employee(cur, "Fu", email=f"fu.{tag}@example.com")
        recs = [
            _rec(
                external_id=f"V-{tag}",
                email=f"vo.{tag}@example.com",
                termination_date=left,
                termination_type="voluntary",
            ),
            _rec(
                external_id=f"I-{tag}",
                email=f"in.{tag}@example.com",
                termination_date=left,
                termination_type="involuntary",
            ),
            _rec(external_id=f"F-{tag}", email=f"fu.{tag}@example.com", termination_date=TODAY + timedelta(days=30)),
        ]
        report = sync_records(recs, _NoCommit(db))
        assert report["departures"] == 2
        with db.cursor() as cur:
            cur.execute(
                """
                SELECT e.id::text, e.active, e.left_at::date, e.departure_type, l.churned
                FROM employees e LEFT JOIN churn_labels l ON l.employee_id = e.id
                WHERE e.id IN (%s, %s, %s)
                """,
                (vol, inv, fut),
            )
            rows = {r[0]: r[1:] for r in cur.fetchall()}
        assert rows[vol] == (False, left, "voluntary", True)
        assert rows[inv] == (False, left, "involuntary", False)
        assert rows[fut] == (True, None, None, None)  # future-dated: not yet

        # Idempotent: a second identical sync records no new departures.
        assert sync_records(recs, _NoCommit(db))["departures"] == 0

    def test_involuntary_exit_is_censored_for_training(self, db):
        from ml.churn.pipeline import load_departures, load_involuntary

        tag = uuid.uuid4().hex[:8]
        with db.cursor() as cur:
            inv = _employee(cur, "In", email=f"in2.{tag}@example.com")
        sync_records(
            [
                _rec(
                    external_id=f"I2-{tag}",
                    email=f"in2.{tag}@example.com",
                    termination_date=TODAY - timedelta(days=5),
                    termination_type="involuntary",
                )
            ],
            _NoCommit(db),
        )
        with db.cursor() as cur:
            assert inv in load_departures(cur)  # departed: out of at-risk sets, visible to peer exposure
            assert inv in load_involuntary(cur)  # but not a voluntary-churn positive

    def test_rehire_reactivates(self, db):
        tag = uuid.uuid4().hex[:8]
        with db.cursor() as cur:
            emp = _employee(cur, "Re", email=f"re.{tag}@example.com")
            cur.execute(
                "UPDATE employees SET active = false, left_at = %s, departure_type = 'voluntary' WHERE id = %s",
                (date(2025, 6, 30), emp),
            )
        report = sync_records(
            [_rec(external_id=f"R-{tag}", email=f"re.{tag}@example.com", hire_date=date(2026, 2, 1))], _NoCommit(db)
        )
        assert report["rehired"] == 1
        with db.cursor() as cur:
            cur.execute("SELECT active, left_at FROM employees WHERE id = %s", (emp,))
            assert cur.fetchone() == (True, None)
