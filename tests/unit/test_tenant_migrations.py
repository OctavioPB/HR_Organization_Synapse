"""Tenant schemas are built from data/migrations/, not from an inline DDL copy.

Regression guard: the old inline _TENANT_TABLES_SQL drifted (no migrations
009–017), so new tenants lacked the v2 SPOF band columns and every feature table
added after 008.  These tests pin the replacement behaviour without a database;
the end-to-end check (tenant schema == default schema, idempotent re-run) was
run against Postgres when this was introduced.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import api.tenant as tenant
from api.tenant import apply_tenant_migrations, tenant_migration_files


def _mock_conn() -> tuple[MagicMock, MagicMock]:
    cur = MagicMock()
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur
    return conn, cur


def test_inline_ddl_copy_is_gone():
    assert not hasattr(tenant, "_TENANT_TABLES_SQL")


def test_migration_set_excludes_control_plane_files():
    names = [p.name for p in tenant_migration_files()]
    assert "000_create_airflow_db.sql" not in names  # psql \gexec, creates a database
    assert "008_tenant_schema.sql" not in names  # public.* tenant registry


def test_migration_set_is_ordered_and_includes_latest():
    names = [p.name for p in tenant_migration_files()]
    assert names == sorted(names)
    assert names[0] == "001_app_schema.sql"
    assert "017_model_v2_v3_corrections.sql" in names  # SPOF band columns


def test_every_non_control_migration_is_applied(tmp_path):
    for name in ["000_create_airflow_db.sql", "001_a.sql", "008_tenant_schema.sql", "009_b.sql", "010_c.sql"]:
        (tmp_path / name).write_text(f"-- {name}\nSELECT 1;", encoding="utf-8")
    conn, cur = _mock_conn()

    applied = apply_tenant_migrations(conn, "tenant_acme", migrations_dir=tmp_path)

    assert applied == ["001_a.sql", "009_b.sql", "010_c.sql"]
    executed = [c.args[0] for c in cur.execute.call_args_list]
    assert executed[0] == "SET LOCAL search_path TO tenant_acme"
    assert [sql.splitlines()[0] for sql in executed[1:]] == ["-- 001_a.sql", "-- 009_b.sql", "-- 010_c.sql"]


def test_search_path_has_no_public_fallback(tmp_path):
    """Unqualified DDL must never resolve to a public table while building a tenant."""
    (tmp_path / "001_a.sql").write_text("SELECT 1;", encoding="utf-8")
    conn, cur = _mock_conn()
    apply_tenant_migrations(conn, "tenant_acme", migrations_dir=tmp_path)
    first_sql = cur.execute.call_args_list[0].args[0]
    assert "public" not in first_sql


def test_does_not_commit_leaves_transaction_to_caller(tmp_path):
    (tmp_path / "001_a.sql").write_text("SELECT 1;", encoding="utf-8")
    conn, _ = _mock_conn()
    apply_tenant_migrations(conn, "tenant_acme", migrations_dir=tmp_path)
    conn.commit.assert_not_called()


def test_rejects_unsafe_schema_name(tmp_path):
    conn, _ = _mock_conn()
    with pytest.raises(ValueError):
        apply_tenant_migrations(conn, "tenant_x; DROP SCHEMA public", migrations_dir=tmp_path)


def test_empty_migrations_dir_fails_loudly(tmp_path):
    conn, _ = _mock_conn()
    with pytest.raises(FileNotFoundError):
        apply_tenant_migrations(conn, "tenant_acme", migrations_dir=tmp_path)
