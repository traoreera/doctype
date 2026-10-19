"""Fixtures partagées.

Les tests de base de données tournent sur SQLite toujours, et sur PostgreSQL quand
`XCORE_DOCTYPE_TEST_PG_URL` est défini (ex. `postgresql+asyncpg://postgres@/xdt?host=/tmp/pgsock`).
Sans cette variable, les cas PostgreSQL apparaissent comme SKIPPED : jamais absents en silence.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy import text
from xcore.configurations.sections import DatabaseConfig
from xcore.services.database.adapters.async_sql import AsyncSQLAdapter

from xcore_doctype.builder import CompiledDocType
from xcore_doctype.context import RequestContext
from xcore_doctype.db.access import create_system_tables
from xcore_doctype.db.tables import SystemTables, build_system_tables
from xcore_doctype.schema import parse_definition

ROOT = Path(__file__).resolve().parent.parent
PG_URL = os.environ.get("XCORE_DOCTYPE_TEST_PG_URL")

BACKENDS = [
    pytest.param("sqlite", id="sqlite"),
    pytest.param("postgres", id="postgres", marks=pytest.mark.postgres),
]

CUSTOMER = {
    "schema_version": 1,
    "name": "Customer",
    "module": "sales",
    "title_field": "customer_name",
    "fields": [
        {"fieldname": "customer_name", "fieldtype": "Text", "required": True},
        {"fieldname": "type", "fieldtype": "Select", "options": ["Particulier", "Entreprise"]},
        {"fieldname": "credit_limit", "fieldtype": "Number", "default": 0},
        {"fieldname": "vip", "fieldtype": "Boolean"},
        {"fieldname": "since", "fieldtype": "Date"},
        {"fieldname": "country", "fieldtype": "Link", "options": "Country"},
    ],
    # Aucun bloc `permissions` : la convention donne sales:customer:read | write | delete (module + nom du DocType).
}
CUSTOMER_GRANTS = ["sales:customer:read", "sales:customer:write", "sales:customer:delete"]


@pytest.fixture
def customer() -> CompiledDocType:
    return CompiledDocType.compile(parse_definition(CUSTOMER))


def make_ctx(tenant: str = "acme", user: str | None = "alice", **principal) -> RequestContext:
    """Contexte HTTP. Par défaut : tous les grants Customer, sauf `permissions=` explicite."""
    principal.setdefault("permissions", CUSTOMER_GRANTS)
    p = {"sub": user, **principal} if user is not None else None
    return RequestContext(tenant, p, "http")


@pytest.fixture
def ctx_factory():
    return make_ctx


async def _drop_system_tables(adapter) -> None:
    """Repart de rien sur PostgreSQL (base partagée entre les tests) : toutes les tables système, pas seulement `Document`."""
    async with adapter.session() as s:
        for name in build_system_tables().metadata.tables:
            await s.execute(text(f"DROP TABLE IF EXISTS {name}"))


@pytest.fixture(params=BACKENDS)
async def db(request, tmp_path):
    """Un `AsyncSQLAdapter` xcore réel (celui que reçoit un plugin), sur chaque backend."""
    if request.param == "postgres":
        if not PG_URL:
            pytest.skip("XCORE_DOCTYPE_TEST_PG_URL non défini")
        cfg = DatabaseConfig(name="test", type="postgresql+aio", url=PG_URL)
    else:
        cfg = DatabaseConfig(name="test", type="sqlite+aio", url=f"sqlite+aiosqlite:///{tmp_path}/t.db")
    adapter = AsyncSQLAdapter("test", cfg)
    await adapter.connect()
    if request.param == "postgres":
        await _drop_system_tables(adapter)
    adapter.backend = request.param  # type: ignore[attr-defined]
    try:
        yield adapter
    finally:
        if request.param == "postgres":
            await _drop_system_tables(adapter)
        await adapter.disconnect()


RLS_ROLE, RLS_PASSWORD = "xdt_rls_app", "xdt_rls_pw"


@pytest.fixture
async def rls_app_url():
    """URL PostgreSQL d'un rôle ORDINAIRE (ni superutilisateur ni BYPASSRLS), le genre de rôle que la RLS exige.

    La base de test appartient à un superutilisateur, que la RLS ne contraint pas : il faut un vrai rôle applicatif. Il est créé ici et
    supprimé avec tout ce qu'il possède (les tables xdt_* créées par le test).
    """
    if not PG_URL:
        pytest.skip("XCORE_DOCTYPE_TEST_PG_URL non défini")
    from sqlalchemy.engine import make_url

    admin = AsyncSQLAdapter("admin", DatabaseConfig(name="admin", type="postgresql+aio", url=PG_URL))
    await admin.connect()
    try:
        await _drop_system_tables(admin)  # celles d'un autre test appartiennent au superutilisateur
        async with admin.session() as s:
            exists = (await s.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": RLS_ROLE})).first()
            if exists:
                await s.execute(text(f"DROP OWNED BY {RLS_ROLE}"))
                await s.execute(text(f"DROP ROLE {RLS_ROLE}"))
            await s.execute(text(f"CREATE ROLE {RLS_ROLE} LOGIN PASSWORD '{RLS_PASSWORD}' NOSUPERUSER NOBYPASSRLS"))
            await s.execute(text(f"GRANT ALL ON SCHEMA public TO {RLS_ROLE}"))
        yield make_url(PG_URL).set(username=RLS_ROLE, password=RLS_PASSWORD).render_as_string(hide_password=False)
    finally:
        async with admin.session() as s:
            await s.execute(text(f"DROP OWNED BY {RLS_ROLE}"))
            await s.execute(text(f"DROP ROLE IF EXISTS {RLS_ROLE}"))
        await admin.disconnect()


@pytest.fixture
async def tables(db) -> SystemTables:
    t = build_system_tables()
    await create_system_tables(db, t)
    return t
