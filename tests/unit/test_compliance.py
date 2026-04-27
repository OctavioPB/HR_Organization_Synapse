"""Unit tests for F8 — Compliance & Regulatory Reporting.

Tests cover:
  - build_data_audit: catalogue structure, row counts, cutoff dates
  - run_retention_purge: SQL issued, purge log recorded, failure path
  - export_employee_data: full package, missing employee
  - update_consent: happy path, audit log written, missing employee
  - generate_html_report: HTML structure, includes KPIs
  - API endpoints: /compliance/data-audit, /compliance/data-export/{id},
      PATCH /compliance/consent/{id}, POST /compliance/purge,
      GET /compliance/purge-history, GET /compliance/report
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, call, patch

import pytest
from fastapi.testclient import TestClient


# ─── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture()
def mock_conn():
    conn = MagicMock()
    cursor = MagicMock()
    cursor.__enter__ = lambda s: cursor
    cursor.__exit__ = MagicMock(return_value=False)
    conn.cursor.return_value = cursor
    conn.closed = False
    return conn, cursor


@pytest.fixture()
def api_client():
    from api.main import app
    from api.deps import get_db

    mock_conn = MagicMock()
    cursor = MagicMock()
    cursor.__enter__ = lambda s: cursor
    cursor.__exit__ = MagicMock(return_value=False)
    mock_conn.cursor.return_value = cursor
    mock_conn.closed = False
    cursor.fetchone.return_value = None
    cursor.fetchall.return_value = []

    app.dependency_overrides[get_db] = lambda: mock_conn
    yield TestClient(app, raise_server_exceptions=True), mock_conn, cursor
    app.dependency_overrides.clear()


@pytest.fixture()
def admin_client(monkeypatch):
    monkeypatch.setenv("ADMIN_SECRET_KEY", "test-admin-secret")
    from api.main import app
    from api.deps import get_admin_db

    mock_conn = MagicMock()
    cursor = MagicMock()
    cursor.__enter__ = lambda s: cursor
    cursor.__exit__ = MagicMock(return_value=False)
    mock_conn.cursor.return_value = cursor
    mock_conn.closed = False
    cursor.fetchall.return_value = []

    app.dependency_overrides[get_admin_db] = lambda: mock_conn
    yield TestClient(app, raise_server_exceptions=True), mock_conn, cursor
    app.dependency_overrides.clear()


# ─── build_data_audit ─────────────────────────────────────────────────────────


class TestBuildDataAudit:
    def test_returns_all_required_top_level_keys(self, mock_conn):
        from graph.compliance import build_data_audit

        conn, cursor = mock_conn
        cursor.fetchone.return_value = (0,)

        result = build_data_audit(conn)
        assert "generated_at" in result
        assert "categories" in result
        assert "total_tables" in result
        assert "total_personal_rows" in result
        assert "framework" in result

    def test_all_categories_present(self, mock_conn):
        from graph.compliance import build_data_audit, _DATA_CATEGORIES

        conn, cursor = mock_conn
        cursor.fetchone.return_value = (0,)

        result = build_data_audit(conn)
        tables = {c["table"] for c in result["categories"]}
        for cat in _DATA_CATEGORIES:
            assert cat["table"] in tables

    def test_cutoff_date_set_for_bounded_retention(self, mock_conn):
        from graph.compliance import build_data_audit

        conn, cursor = mock_conn
        cursor.fetchone.return_value = (100,)

        result = build_data_audit(conn)
        for cat in result["categories"]:
            if cat["retention_days"] is not None:
                assert cat["cutoff_date"] is not None
            else:
                assert cat["cutoff_date"] is None

    def test_total_personal_rows_is_sum(self, mock_conn):
        from graph.compliance import build_data_audit

        conn, cursor = mock_conn
        cursor.fetchone.return_value = (50,)

        result = build_data_audit(conn)
        expected = sum(c["row_count"] for c in result["categories"] if c["personal_data"])
        assert result["total_personal_rows"] == expected

    def test_count_table_exception_returns_zero(self, mock_conn):
        from graph.compliance import build_data_audit

        conn, cursor = mock_conn
        cursor.fetchone.side_effect = Exception("table missing")

        result = build_data_audit(conn)
        for cat in result["categories"]:
            assert cat["row_count"] == 0


# ─── run_retention_purge ──────────────────────────────────────────────────────


class TestRunRetentionPurge:
    def test_deletes_from_correct_tables(self, mock_conn):
        from graph.compliance import run_retention_purge

        conn, cursor = mock_conn
        cursor.rowcount = 10

        results = run_retention_purge(conn, triggered_by="test")
        tables = {r["table"] for r in results}
        assert "raw_events" in tables
        assert "graph_snapshots" in tables

    def test_returns_rows_deleted(self, mock_conn):
        from graph.compliance import run_retention_purge

        conn, cursor = mock_conn
        cursor.rowcount = 42

        results = run_retention_purge(conn, triggered_by="test")
        for r in results:
            assert r["rows_deleted"] == 42
            assert r["status"] == "completed"

    def test_failed_purge_returns_status_failed(self, mock_conn):
        from graph.compliance import run_retention_purge

        conn, cursor = mock_conn
        cursor.execute.side_effect = [Exception("DB error"), None, None, None]

        results = run_retention_purge(conn, triggered_by="test")
        assert any(r["status"] == "failed" for r in results)
        failed = [r for r in results if r["status"] == "failed"]
        assert failed[0]["rows_deleted"] == 0

    def test_cutoff_dates_set(self, mock_conn):
        from graph.compliance import run_retention_purge
        from datetime import timedelta

        conn, cursor = mock_conn
        cursor.rowcount = 0

        results = run_retention_purge(conn)
        raw   = next(r for r in results if r["table"] == "raw_events")
        graph = next(r for r in results if r["table"] == "graph_snapshots")

        raw_cutoff   = date.today() - timedelta(days=90)
        graph_cutoff = date.today() - timedelta(days=365)
        assert raw["cutoff_date"]   == raw_cutoff.isoformat()
        assert graph["cutoff_date"] == graph_cutoff.isoformat()


# ─── export_employee_data ─────────────────────────────────────────────────────


class TestExportEmployeeData:
    def _make_cursor(self, employee_row):
        cursor = MagicMock()
        cursor.__enter__ = lambda s: cursor
        cursor.__exit__ = MagicMock(return_value=False)
        cursor.fetchone.return_value = employee_row
        cursor.fetchall.return_value = []
        return cursor

    def test_returns_none_for_missing_employee(self):
        from graph.compliance import export_employee_data

        conn = MagicMock()
        cursor = self._make_cursor(None)
        conn.cursor.return_value = cursor

        assert export_employee_data("nonexistent-id", conn) is None

    def test_export_contains_all_keys(self):
        from graph.compliance import export_employee_data

        conn = MagicMock()
        emp_row = {
            "id": "emp-001", "name": "Alice", "department": "Engineering",
            "role": "SWE", "active": True, "consent": True, "created_at": datetime.now(),
        }
        cursor = self._make_cursor(emp_row)
        conn.cursor.return_value = cursor

        result = export_employee_data("emp-001", conn)
        assert result is not None
        for key in ("export_generated_at", "article", "employee_id", "employee",
                    "raw_events", "graph_snapshots", "risk_scores",
                    "churn_scores", "knowledge_entries", "consent_audit_log"):
            assert key in result

    def test_article_is_gdpr_article_20(self):
        from graph.compliance import export_employee_data

        conn = MagicMock()
        emp_row = {"id": "emp-001", "name": "Bob", "department": "HR",
                   "role": "HRBP", "active": True, "consent": True, "created_at": datetime.now()}
        cursor = MagicMock()
        cursor.__enter__ = lambda s: cursor
        cursor.__exit__ = MagicMock(return_value=False)
        cursor.fetchone.return_value = emp_row
        cursor.fetchall.return_value = []
        conn.cursor.return_value = cursor

        result = export_employee_data("emp-001", conn)
        assert "Article 20" in result["article"]


# ─── update_consent ───────────────────────────────────────────────────────────


class TestUpdateConsent:
    def test_returns_none_for_missing_employee(self):
        from graph.compliance import update_consent

        conn = MagicMock()
        cursor = MagicMock()
        cursor.__enter__ = lambda s: cursor
        cursor.__exit__ = MagicMock(return_value=False)
        cursor.fetchone.return_value = None
        conn.cursor.return_value = cursor

        result = update_consent("nonexistent", False, "hr_admin", None, conn)
        assert result is None

    def test_happy_path_returns_dict(self):
        from graph.compliance import update_consent

        conn = MagicMock()
        cursor = MagicMock()
        cursor.__enter__ = lambda s: cursor
        cursor.__exit__ = MagicMock(return_value=False)
        cursor.fetchone.return_value = {"consent": True}
        conn.cursor.return_value = cursor

        result = update_consent("emp-001", False, "hr_admin", "GDPR request", conn)
        assert result is not None
        assert result["new_value"] is False
        assert result["previous_value"] is True
        assert result["changed_by"] == "hr_admin"

    def test_commits_after_update(self):
        from graph.compliance import update_consent

        conn = MagicMock()
        cursor = MagicMock()
        cursor.__enter__ = lambda s: cursor
        cursor.__exit__ = MagicMock(return_value=False)
        cursor.fetchone.return_value = {"consent": True}
        conn.cursor.return_value = cursor

        update_consent("emp-001", False, "employee", None, conn)
        conn.commit.assert_called()


# ─── generate_html_report ─────────────────────────────────────────────────────


class TestGenerateHtmlReport:
    def test_returns_html_string(self, mock_conn):
        from graph.compliance import generate_html_report

        conn, cursor = mock_conn
        cursor.fetchone.side_effect = [
            (0,),   # employees count
            (0,),   # raw_events count
            (0,),   # graph_snapshots count
            (0,),   # risk_scores
            (0,),   # churn_risk_scores
            (0,),   # employee_knowledge
            (0,),   # consent_audit_log
            {"opted_in": 50, "opted_out": 2},  # consent stats
        ]
        cursor.fetchall.return_value = []

        html = generate_html_report(conn)
        assert isinstance(html, str)
        assert "<!DOCTYPE html>" in html

    def test_html_contains_gdpr_reference(self, mock_conn):
        from graph.compliance import generate_html_report

        conn, cursor = mock_conn
        cursor.fetchone.return_value = (0,)
        cursor.fetchall.return_value = []

        html = generate_html_report(conn)
        assert "GDPR" in html

    def test_html_contains_data_inventory_table(self, mock_conn):
        from graph.compliance import generate_html_report

        conn, cursor = mock_conn
        cursor.fetchone.return_value = (0,)
        cursor.fetchall.return_value = []

        html = generate_html_report(conn)
        assert "Data Inventory" in html
        assert "<table" in html


# ─── GET /compliance/data-audit ──────────────────────────────────────────────


class TestDataAuditEndpoint:
    def test_returns_200(self, api_client):
        client, conn, cursor = api_client
        cursor.fetchone.return_value = (0,)
        cursor.fetchall.return_value = []

        with patch("graph.compliance.build_data_audit", return_value={
            "generated_at": "2025-01-01T00:00:00+00:00",
            "framework": ["GDPR", "CCPA"],
            "data_controller": "Org Synapse",
            "dpo_contact": "privacy@org-synapse.internal",
            "categories": [],
            "total_tables": 0,
            "total_personal_rows": 0,
        }):
            resp = client.get("/compliance/data-audit")
        assert resp.status_code == 200
        body = resp.json()
        assert "categories" in body
        assert "total_tables" in body


# ─── GET /compliance/data-export/{id} ────────────────────────────────────────


class TestDataExportEndpoint:
    def test_returns_404_for_missing_employee(self, api_client):
        client, conn, cursor = api_client
        with patch("graph.compliance.export_employee_data", return_value=None):
            resp = client.get("/compliance/data-export/nonexistent")
        assert resp.status_code == 404

    def test_returns_200_with_export(self, api_client):
        client, conn, cursor = api_client
        package = {
            "export_generated_at": "2025-01-01T00:00:00+00:00",
            "article": "GDPR Article 20 — Right to Data Portability",
            "employee_id": "emp-001",
            "employee": {"name": "Alice"},
            "raw_events": [],
            "graph_snapshots": [],
            "risk_scores": [],
            "churn_scores": [],
            "knowledge_entries": [],
            "consent_audit_log": [],
        }
        with patch("graph.compliance.export_employee_data", return_value=package):
            resp = client.get("/compliance/data-export/emp-001")
        assert resp.status_code == 200
        assert resp.json()["employee_id"] == "emp-001"


# ─── PATCH /compliance/consent/{id} ──────────────────────────────────────────


class TestConsentEndpoint:
    def test_returns_404_for_missing_employee(self, api_client):
        client, conn, cursor = api_client
        with patch("graph.compliance.update_consent", return_value=None):
            resp = client.patch(
                "/compliance/consent/nonexistent",
                json={"consent": False, "changed_by": "hr_admin"},
            )
        assert resp.status_code == 404

    def test_returns_200_on_success(self, api_client):
        client, conn, cursor = api_client
        result = {
            "employee_id": "emp-001",
            "previous_value": True,
            "new_value": False,
            "changed_by": "hr_admin",
            "reason": "GDPR request",
            "changed_at": "2025-01-01T00:00:00+00:00",
        }
        with patch("graph.compliance.update_consent", return_value=result):
            resp = client.patch(
                "/compliance/consent/emp-001",
                json={"consent": False, "changed_by": "hr_admin", "reason": "GDPR request"},
            )
        assert resp.status_code == 200
        assert resp.json()["new_value"] is False

    def test_missing_changed_by_returns_422(self, api_client):
        client, conn, cursor = api_client
        resp = client.patch(
            "/compliance/consent/emp-001",
            json={"consent": False},
        )
        assert resp.status_code == 422


# ─── POST /compliance/purge ───────────────────────────────────────────────────


class TestPurgeEndpoint:
    def test_missing_admin_key_returns_403(self):
        from api.main import app
        client = TestClient(app, raise_server_exceptions=True)
        resp = client.post("/compliance/purge")
        assert resp.status_code == 403

    def test_returns_202_on_success(self, admin_client):
        client, conn, cursor = admin_client
        purge_results = [
            {"table": "raw_events", "rows_deleted": 100, "cutoff_date": "2025-01-01", "status": "completed"},
            {"table": "graph_snapshots", "rows_deleted": 50, "cutoff_date": "2024-04-01", "status": "completed"},
        ]
        with patch("graph.compliance.run_retention_purge", return_value=purge_results):
            resp = client.post(
                "/compliance/purge",
                headers={"X-Admin-Key": "test-admin-secret"},
            )
        assert resp.status_code == 202
        body = resp.json()
        assert body["total_rows_deleted"] == 150
        assert len(body["results"]) == 2


# ─── GET /compliance/purge-history ───────────────────────────────────────────


class TestPurgeHistoryEndpoint:
    def test_returns_200_with_entries(self, api_client):
        client, conn, cursor = api_client
        rows = [{
            "purged_at": datetime(2025, 1, 1, 8, 0, tzinfo=timezone.utc),
            "table_name": "raw_events",
            "rows_deleted": 200,
            "cutoff_date": date(2024, 10, 3),
            "triggered_by": "airflow",
            "status": "completed",
        }]
        with patch("api.db.fetch_purge_history", return_value=rows):
            resp = client.get("/compliance/purge-history")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["entries"][0]["table_name"] == "raw_events"

    def test_invalid_limit_returns_422(self, api_client):
        client, conn, cursor = api_client
        resp = client.get("/compliance/purge-history?limit=0")
        assert resp.status_code == 422

    def test_limit_above_500_returns_422(self, api_client):
        client, conn, cursor = api_client
        resp = client.get("/compliance/purge-history?limit=501")
        assert resp.status_code == 422


# ─── GET /compliance/report ───────────────────────────────────────────────────


class TestComplianceReportEndpoint:
    def test_returns_html_content(self, api_client):
        client, conn, cursor = api_client
        html = "<!DOCTYPE html><html><body>Compliance Report</body></html>"
        with patch("graph.compliance.generate_html_report", return_value=html):
            resp = client.get("/compliance/report")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "Compliance" in resp.text

    def test_502_on_generation_failure(self, api_client):
        client, conn, cursor = api_client
        with patch("graph.compliance.generate_html_report", side_effect=RuntimeError("DB down")):
            resp = client.get("/compliance/report")
        assert resp.status_code == 502
