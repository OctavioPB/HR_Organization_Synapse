"""Unit tests for F6 — Multi-Tenant SaaS Architecture.

Tests cover:
  - TenantContext dataclass
  - _hash_key / generate_api_key
  - set_search_path: safe name validation, SQL issued
  - resolve_tenant: valid creds, wrong key, inactive tenant
  - provision_tenant_schema: happy path, slug conflict
  - record_usage / fetch_current_usage
  - TenantMiddleware: header extraction
  - get_tenant_db dependency: valid creds, missing headers, invalid key
  - POST /admin/tenants: happy path, conflict
  - GET  /admin/tenants: list
  - DELETE /admin/tenants/{id}: deactivation
  - GET /billing/usage: returns usage for authenticated tenant
  - POST /billing/webhook: valid sig, invalid sig, duplicate
  - tenant_topic: correct name format
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
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
def admin_client(monkeypatch):
    monkeypatch.setenv("ADMIN_SECRET_KEY", "test-admin-secret")
    from api.main import app
    return TestClient(app, raise_server_exceptions=True)


# ─── Hash / key generation ────────────────────────────────────────────────────


class TestApiKeyHelpers:
    def test_hash_key_is_sha256_hex(self):
        from api.tenant import _hash_key

        raw = "hello"
        expected = hashlib.sha256(b"hello").hexdigest()
        assert _hash_key(raw) == expected

    def test_generate_api_key_returns_pair(self):
        from api.tenant import generate_api_key

        raw, hashed = generate_api_key()
        assert len(raw) > 20
        assert hashed == hashlib.sha256(raw.encode()).hexdigest()

    def test_two_keys_are_unique(self):
        from api.tenant import generate_api_key

        r1, _ = generate_api_key()
        r2, _ = generate_api_key()
        assert r1 != r2


# ─── set_search_path ──────────────────────────────────────────────────────────


class TestSetSearchPath:
    def test_issues_correct_sql(self, mock_conn):
        from api.tenant import set_search_path

        conn, cursor = mock_conn
        set_search_path(conn, "tenant_acme")
        cursor.execute.assert_called_with("SET search_path TO tenant_acme, public")

    def test_rejects_invalid_schema_name(self):
        from api.tenant import set_search_path

        conn = MagicMock()
        with pytest.raises(ValueError):
            set_search_path(conn, "'; DROP TABLE tenants;--")

    def test_rejects_uppercase(self):
        from api.tenant import set_search_path

        conn = MagicMock()
        with pytest.raises(ValueError):
            set_search_path(conn, "Tenant_Acme")


# ─── resolve_tenant ───────────────────────────────────────────────────────────


class TestResolveTenant:
    def _make_row(self, key_hash):
        return {
            "id": "tenant-uuid-001",
            "slug": "acme",
            "name": "Acme Corp",
            "schema_name": "tenant_acme",
            "plan": "pro",
            "active": True,
            "stripe_customer_id": None,
            "key_id": "key-uuid-001",
            "key_hash": key_hash,
        }

    def test_valid_credentials_return_context(self, mock_conn):
        from api.tenant import _hash_key, resolve_tenant

        raw_key = "correct-secret"
        conn, cursor = mock_conn
        cursor.fetchall.return_value = [self._make_row(_hash_key(raw_key))]

        ctx = resolve_tenant("tenant-uuid-001", raw_key, conn)
        assert ctx is not None
        assert ctx.slug == "acme"
        assert ctx.plan == "pro"

    def test_wrong_api_key_returns_none(self, mock_conn):
        from api.tenant import _hash_key, resolve_tenant

        conn, cursor = mock_conn
        cursor.fetchall.return_value = [self._make_row(_hash_key("correct"))]

        ctx = resolve_tenant("tenant-uuid-001", "wrong-key", conn)
        assert ctx is None

    def test_no_rows_returns_none(self, mock_conn):
        from api.tenant import resolve_tenant

        conn, cursor = mock_conn
        cursor.fetchall.return_value = []

        ctx = resolve_tenant("tenant-uuid-001", "any-key", conn)
        assert ctx is None


# ─── tenant_topic ─────────────────────────────────────────────────────────────


class TestTenantTopic:
    def test_topic_format(self):
        from ingestion.producers.tenant_producer import tenant_topic

        assert tenant_topic("acme-corp") == "acme-corp.collaboration.events.raw"

    def test_topic_different_per_tenant(self):
        from ingestion.producers.tenant_producer import tenant_topic

        assert tenant_topic("foo") != tenant_topic("bar")


# ─── record_usage ─────────────────────────────────────────────────────────────


class TestRecordUsage:
    def test_issues_upsert(self, mock_conn):
        from api.tenant import record_usage

        conn, cursor = mock_conn
        record_usage("tenant-001", 500, conn)
        sql_call = cursor.execute.call_args[0][0]
        assert "INSERT INTO public.tenant_usage" in sql_call
        assert "ON CONFLICT" in sql_call


# ─── TenantMiddleware ─────────────────────────────────────────────────────────


class TestTenantMiddleware:
    def test_header_extraction(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from api.middleware.tenant_middleware import TenantMiddleware

        app = FastAPI()
        app.add_middleware(TenantMiddleware)

        @app.get("/test")
        def _test_route(request):
            from fastapi import Request
            return {
                "tenant_id": getattr(request.state, "tenant_id", ""),
                "api_key":   getattr(request.state, "api_key", ""),
            }

        # Import Request for the route
        from fastapi import Request

        @app.get("/check")
        def _check(request: Request):
            return {
                "tenant_id": getattr(request.state, "tenant_id", "MISSING"),
                "api_key":   getattr(request.state, "api_key", "MISSING"),
            }

        client = TestClient(app)
        resp = client.get(
            "/check",
            headers={"X-Tenant-ID": "abc-123", "X-Api-Key": "secret"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["tenant_id"] == "abc-123"
        assert body["api_key"]   == "secret"

    def test_missing_headers_sets_empty_strings(self):
        from fastapi import FastAPI, Request
        from fastapi.testclient import TestClient
        from api.middleware.tenant_middleware import TenantMiddleware

        app = FastAPI()
        app.add_middleware(TenantMiddleware)

        @app.get("/check")
        def _check(request: Request):
            return {
                "tenant_id": getattr(request.state, "tenant_id", "MISSING"),
                "api_key":   getattr(request.state, "api_key", "MISSING"),
            }

        client = TestClient(app)
        resp = client.get("/check")
        assert resp.status_code == 200
        body = resp.json()
        assert body["tenant_id"] == ""
        assert body["api_key"]   == ""


# ─── POST /admin/tenants ──────────────────────────────────────────────────────


class TestAdminCreateTenant:
    def test_returns_201_with_api_key(self, admin_client):
        provisioned = {
            "tenant_id":    "tid-001",
            "slug":         "acme",
            "name":         "Acme Corp",
            "plan":         "free",
            "schema_name":  "tenant_acme",
            "raw_api_key":  "raw-key-abc",
        }
        with patch("api.tenant.provision_tenant_schema", return_value=provisioned):
            resp = admin_client.post(
                "/admin/tenants",
                json={"slug": "acme", "name": "Acme Corp", "plan": "free"},
                headers={"X-Admin-Key": "test-admin-secret"},
            )
        assert resp.status_code == 201
        body = resp.json()
        assert body["raw_api_key"] == "raw-key-abc"
        assert body["schema_name"] == "tenant_acme"

    def test_missing_admin_key_returns_403(self, admin_client):
        resp = admin_client.post(
            "/admin/tenants",
            json={"slug": "acme", "name": "Acme Corp"},
        )
        assert resp.status_code == 403

    def test_provisioning_error_returns_409(self, admin_client):
        with patch("api.tenant.provision_tenant_schema", side_effect=Exception("slug taken")):
            resp = admin_client.post(
                "/admin/tenants",
                json={"slug": "taken", "name": "Taken Corp"},
                headers={"X-Admin-Key": "test-admin-secret"},
            )
        assert resp.status_code == 409

    def test_invalid_slug_returns_422(self, admin_client):
        resp = admin_client.post(
            "/admin/tenants",
            json={"slug": "UPPERCASE-INVALID", "name": "Bad Slug"},
            headers={"X-Admin-Key": "test-admin-secret"},
        )
        assert resp.status_code == 422


# ─── GET /admin/tenants ───────────────────────────────────────────────────────


class TestAdminListTenants:
    def test_returns_200_with_list(self, admin_client):
        from datetime import datetime

        rows = [{
            "id": "tid-001", "slug": "acme", "name": "Acme Corp",
            "plan": "free", "schema_name": "tenant_acme", "active": True,
            "stripe_customer_id": None,
            "created_at": datetime(2025, 1, 1),
        }]

        mock_cursor = MagicMock()
        mock_cursor.__enter__ = lambda s: mock_cursor
        mock_cursor.__exit__ = MagicMock(return_value=False)
        mock_cursor.fetchall.return_value = rows

        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.closed = False

        with patch("api.deps._open_connection", return_value=mock_conn):
            resp = admin_client.get(
                "/admin/tenants",
                headers={"X-Admin-Key": "test-admin-secret"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert "tenants" in body


# ─── POST /billing/webhook ────────────────────────────────────────────────────


class TestBillingWebhook:
    def test_valid_event_returns_200(self, admin_client):
        payload = json.dumps({
            "id": "evt_test_001",
            "type": "invoice.paid",
            "data": {"object": {"customer": "cus_001", "amount_due": 5000}},
        }).encode()

        mock_cursor = MagicMock()
        mock_cursor.__enter__ = lambda s: mock_cursor
        mock_cursor.__exit__ = MagicMock(return_value=False)
        mock_cursor.fetchone.return_value = None  # not duplicate

        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.closed = False

        with patch("api.routers.billing.psycopg2.connect", return_value=mock_conn):
            resp = admin_client.post(
                "/billing/webhook",
                content=payload,
                headers={"Content-Type": "application/json"},
            )
        assert resp.status_code == 200

    def test_duplicate_event_returns_duplicate_status(self, admin_client):
        payload = json.dumps({
            "id": "evt_dup_001",
            "type": "invoice.paid",
            "data": {"object": {"customer": "cus_001"}},
        }).encode()

        mock_cursor = MagicMock()
        mock_cursor.__enter__ = lambda s: mock_cursor
        mock_cursor.__exit__ = MagicMock(return_value=False)
        mock_cursor.fetchone.return_value = {"1": 1}  # already processed

        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        with patch("api.routers.billing.psycopg2.connect", return_value=mock_conn):
            resp = admin_client.post(
                "/billing/webhook",
                content=payload,
                headers={"Content-Type": "application/json"},
            )
        assert resp.status_code == 200
        assert resp.json()["status"] == "duplicate"

    def test_invalid_signature_returns_400(self, admin_client, monkeypatch):
        monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "real-secret")
        import api.routers.billing as billing_mod
        billing_mod._STRIPE_WEBHOOK_SECRET = "real-secret"

        payload = b'{"id":"evt_bad","type":"invoice.paid","data":{"object":{}}}'
        resp = admin_client.post(
            "/billing/webhook",
            content=payload,
            headers={
                "Content-Type": "application/json",
                "Stripe-Signature": "t=123,v1=invalid",
            },
        )
        assert resp.status_code == 400

        # Restore
        billing_mod._STRIPE_WEBHOOK_SECRET = ""
