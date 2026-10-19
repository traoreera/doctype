"""Row Level Security PostgreSQL (D14) : le filet sous la couche de requêtes, avec un VRAI rôle ordinaire.

Un superutilisateur n'est pas soumis aux politiques : ces tests créent un rôle applicatif (fixture `rls_app_url`), y créent les tables
puis activent la RLS. Ignorés sans `XCORE_DOCTYPE_TEST_PG_URL`.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from xcore.configurations.sections import DatabaseConfig
from xcore.services.database.adapters.async_sql import AsyncSQLAdapter

from tests.conftest import CUSTOMER_GRANTS, PG_URL, make_ctx
from tests.unit.test_query_leak import SCENARIOS, Env, check_isolation
from xcore_doctype.db.access import create_system_tables
from xcore_doctype.db.rls import POLICY, bind_tenant, ensure_rls
from xcore_doctype.db.tables import NAMING_TABLE, VERSION_TABLE, build_system_tables
from xcore_doctype.document import DocumentService
from xcore_doctype.errors import ConfigurationError, DocumentInUse
from xcore_doctype.query import Query
from xcore_doctype.registry import DocTypeRegistry
from xcore_doctype.schema import parse_definition

pytestmark = pytest.mark.postgres


async def connect(url: str) -> AsyncSQLAdapter:
    adapter = AsyncSQLAdapter("rls", DatabaseConfig(name="rls", type="postgresql+aio", url=url))
    await adapter.connect()
    return adapter


@pytest.fixture
async def app(rls_app_url):
    """(adaptateur du rôle ordinaire, tables) avec la RLS active."""
    adapter = await connect(rls_app_url)
    tables = build_system_tables()
    await create_system_tables(adapter, tables)
    await ensure_rls(adapter, tables, "postgresql")
    try:
        yield adapter, tables
    finally:
        await adapter.disconnect()


class RlsEnv(Env):
    def session(self, tenant: str):
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def bound():
            async with self.db.session() as s:
                await bind_tenant(s, tenant)
                yield s

        return bound()


class TestStartup:
    async def test_every_system_table_gets_the_policy_and_is_forced(self, app):
        adapter, tables = app
        async with adapter.session() as s:
            policies = {r[0] for r in (await s.execute(text("SELECT tablename FROM pg_policies WHERE policyname = :p"), {"p": POLICY})).all()}
            flags = (await s.execute(text("SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname LIKE 'xdt\\_%' AND relkind = 'r'"))).all()
        assert policies == {t.name for t in tables.metadata.sorted_tables} >= {NAMING_TABLE, VERSION_TABLE}
        assert flags and all(enabled and forced for _, enabled, forced in flags)

    async def test_it_is_idempotent(self, app):
        adapter, tables = app
        await ensure_rls(adapter, tables, "postgresql")
        await ensure_rls(adapter, tables, "postgresql")
        async with adapter.session() as s:
            n = (await s.execute(text("SELECT count(*) FROM pg_policies WHERE policyname = :p"), {"p": POLICY})).scalar_one()
        assert n == len(tables.metadata.sorted_tables)

    async def test_a_superuser_connection_is_refused_because_it_would_protect_nothing(self, db, tables):
        if db.backend != "postgres":
            pytest.skip("PostgreSQL seulement")
        with pytest.raises(ConfigurationError, match="superutilisateur.*ne protégerait rien"):
            await ensure_rls(db, tables, "postgresql")

    async def test_a_role_with_bypassrls_is_refused_too(self, rls_app_url):
        admin = await connect(PG_URL)
        try:
            async with admin.session() as s:
                await s.execute(text("ALTER ROLE xdt_rls_app BYPASSRLS"))
            adapter = await connect(rls_app_url)
            try:
                with pytest.raises(ConfigurationError, match="BYPASSRLS"):
                    await ensure_rls(adapter, build_system_tables(), "postgresql")
            finally:
                await adapter.disconnect()
        finally:
            await admin.disconnect()

    @pytest.mark.parametrize("dialect", ["sqlite", "mysql", ""])
    async def test_a_non_postgres_database_is_refused_rather_than_silently_unprotected(self, dialect):
        with pytest.raises(ConfigurationError, match="n'existe que sur PostgreSQL"):
            await ensure_rls(None, build_system_tables(), dialect)

    async def test_a_role_that_does_not_own_the_tables_gets_a_clear_message(self, rls_app_url, db, tables):
        if db.backend != "postgres":
            pytest.skip("PostgreSQL seulement")
        # Les tables du fixture `tables` appartiennent au superutilisateur ; le rôle ordinaire ne peut pas les modifier.
        adapter = await connect(rls_app_url)
        try:
            with pytest.raises(ConfigurationError, match="propriétaire des tables"):
                await ensure_rls(adapter, tables, "postgresql")
        finally:
            await adapter.disconnect()


class TestIsolationEnforcedByTheDatabase:
    @pytest.fixture
    async def seeded(self, app):
        adapter, tables = app
        for tenant in ("acme", "globex"):
            async with adapter.session() as s:
                await bind_tenant(s, tenant)
                q = Query(s, make_ctx(tenant), tables)
                await q.insert("Customer", f"{tenant}-1", {"customer_name": tenant})
                await q.next_number("Customer", "C-{#}")
                await q.add_version("Customer", f"{tenant}-1", version=1, action="create", user="u", doc_owner="u", changes={})
        return adapter, tables

    async def count(self, adapter, sql, tenant=None):
        async with adapter.session() as s:
            if tenant:
                await bind_tenant(s, tenant)
            return (await s.execute(text(sql))).scalar_one()

    async def test_without_a_tenant_nothing_is_visible_even_to_a_query_with_no_filter(self, seeded):
        adapter, _ = seeded
        for table in ("xdt_document", NAMING_TABLE, VERSION_TABLE):
            assert await self.count(adapter, f"SELECT count(*) FROM {table}") == 0, table

    async def test_a_query_that_forgets_its_tenant_filter_still_sees_only_its_own_rows(self, seeded):
        adapter, _ = seeded
        for table in ("xdt_document", NAMING_TABLE, VERSION_TABLE):
            assert await self.count(adapter, f"SELECT count(*) FROM {table}", "acme") == 1, table
        async with adapter.session() as s:
            await bind_tenant(s, "globex")
            assert (await s.execute(text("SELECT DISTINCT tenant_id FROM xdt_document"))).scalars().all() == ["globex"]

    async def test_writing_a_row_for_another_tenant_is_refused_by_the_database(self, seeded):
        adapter, _ = seeded
        with pytest.raises(DBAPIError) as refused:
            async with adapter.session() as s:
                await bind_tenant(s, "acme")
                await s.execute(text(
                    "INSERT INTO xdt_document (tenant_id, doctype, name, data, owner, creation, modified, version) "
                    "VALUES ('globex', 'Customer', 'sneaky', '{}', 'u', now(), now(), 1)"
                ))
        # SQLSTATE 42501 (insufficient_privilege) : le texte du message dépend de la langue du serveur.
        orig = refused.value.orig
        assert (getattr(orig, "sqlstate", None) or getattr(orig.__cause__, "sqlstate", None)) == "42501"

    async def test_changing_or_deleting_another_tenants_rows_touches_nothing(self, seeded):
        adapter, _ = seeded
        async with adapter.session() as s:
            await bind_tenant(s, "acme")
            assert (await s.execute(text("UPDATE xdt_document SET owner = 'pwn'"))).rowcount == 1  # la sienne seulement
            assert (await s.execute(text("DELETE FROM xdt_document WHERE tenant_id = 'globex'"))).rowcount == 0
        assert await self.count(adapter, "SELECT count(*) FROM xdt_document", "globex") == 1

    async def test_the_tenant_does_not_outlive_its_transaction_on_a_pooled_connection(self, seeded):
        """La fuite classique d'un pool : le tenant de la requête précédente reste posé sur la connexion rendue."""
        adapter, _ = seeded
        async with adapter.session() as s:
            await bind_tenant(s, "acme")
            first = (await s.execute(text("SELECT pg_backend_pid()"))).scalar_one()
            assert (await s.execute(text("SELECT count(*) FROM xdt_document"))).scalar_one() == 1
        async with adapter.session() as s:  # la même connexion : le pool en a une seule disponible
            second = (await s.execute(text("SELECT pg_backend_pid()"))).scalar_one()
            seen = (await s.execute(text("SELECT count(*) FROM xdt_document"))).scalar_one()
        assert first == second, "le test n'a pas réutilisé la connexion : il ne prouve rien"
        assert seen == 0

    async def test_a_buggy_query_that_drops_the_tenant_condition_cannot_leak(self, seeded, monkeypatch):
        """Le scénario pour lequel la RLS existe : un bug dans `Query._scope`."""
        adapter, tables = seeded
        monkeypatch.setattr(Query, "_scope", lambda self, *conds, scope=None: __import__("sqlalchemy").and_(*conds, self._permission_clause(scope) if scope else True))
        async with adapter.session() as s:
            await bind_tenant(s, "acme")
            from xcore_doctype.permissions import Scope

            docs, total = await Query(s, make_ctx("acme"), tables).list(
                parse_definition({"schema_version": 1, "name": "Customer", "fields": [{"fieldname": "customer_name", "fieldtype": "Text"}]}),
                filters={}, order_by="name", descending=False, limit=50, offset=0, scope=Scope.everything("read"),
            )
        assert [d.name for d in docs] == ["acme-1"] and total == 1  # sans RLS : les deux tenants


class TestEveryQueryScenarioUnderRls:
    @pytest.mark.parametrize("method", sorted(SCENARIOS))
    async def test_no_cross_tenant_access(self, method, app):
        from sqlalchemy import event

        from tests.conftest import CUSTOMER
        from xcore_doctype.builder import CompiledDocType

        adapter, tables = app
        env = RlsEnv(adapter, tables, CompiledDocType.compile(parse_definition(CUSTOMER)))
        try:
            await env.seed()
            env.snapshot_a = await env.rows("acme")
            env.recording = True
            await check_isolation(method, env)
        finally:
            env.recording = False
            env.close()
            assert event  # (importé pour que l'écouteur d'Env soit retiré proprement même en cas d'échec)


SERVICE_DEFS = [
    {"schema_version": 1, "name": "Item", "module": "sales", "fields": [{"fieldname": "n", "fieldtype": "Text"}]},
    {"schema_version": 1, "name": "Line", "istable": True, "fields": [{"fieldname": "item", "fieldtype": "Link", "options": "Item"}]},
    {"schema_version": 1, "name": "Invoice", "module": "sales", "naming": {"series": "FA-{#####}"},
     "fields": [{"fieldname": "lines", "fieldtype": "Table", "options": "Line"}]},
]
SALES = [f"sales:{t}:{a}" for t in ("item", "invoice") for a in ("read", "write", "delete")]


class Log:
    def info(self, *a, **k): ...
    def error(self, *a, **k): ...


class TestServiceUnderRls:
    """Le service complet fonctionne avec la RLS : série, liens verrouillés, historique, deux tenants."""

    @pytest.fixture
    def svc(self, app):
        adapter, tables = app
        reg = DocTypeRegistry()
        for d in SERVICE_DEFS:
            reg.register(parse_definition(d))
        return DocumentService(reg, adapter, tables, Log(), rls=True)

    async def test_the_whole_lifecycle_for_two_tenants(self, svc):
        a, b = make_ctx("acme", permissions=SALES), make_ctx("globex", "bob", permissions=SALES)
        item = (await svc.create(a, "Item", {"n": "Vis"}))["name"]
        inv = await svc.create(a, "Invoice", {"lines": [{"item": item}]})
        assert inv["name"] == "FA-00001"
        assert (await svc.create(b, "Invoice", {}))["name"] == "FA-00001"  # un compteur par tenant
        assert (await svc.list(a, "Invoice"))["total"] == 1 and (await svc.list(b, "Invoice"))["total"] == 1
        with pytest.raises(DocumentInUse):
            await svc.delete(a, "Item", item)  # le contrôle de référence (FOR UPDATE) passe par la RLS
        assert (await svc.history(a, "Invoice", inv["name"]))["total"] == 1
        updated = await svc.update(a, "Invoice", inv["name"], {}, 1)
        assert updated["version"] == 2

    async def test_concurrent_creations_stay_gapless_under_rls(self, svc):
        ctx = make_ctx("acme", permissions=SALES)
        names = await asyncio.gather(*(svc.create(ctx, "Invoice", {}) for _ in range(20)))
        assert sorted(d["name"] for d in names) == [f"FA-{i:05d}" for i in range(1, 21)]

    async def test_a_service_without_the_binding_sees_nothing_and_writes_nothing(self, app):
        """`rls=False` sur une base protégée : échec fermé, pas une fuite."""
        adapter, tables = app
        reg = DocTypeRegistry()
        for d in SERVICE_DEFS:
            reg.register(parse_definition(d))
        loose = DocumentService(reg, adapter, tables, Log(), rls=False)
        ctx = make_ctx("acme", permissions=SALES)
        with pytest.raises(DBAPIError):
            await loose.create(ctx, "Item", {"n": "x"})
        assert (await loose.list(ctx, "Item"))["total"] == 0
