"""`doctype.rls` sur un vrai Xcore : il échoue fermé quand la protection n'existerait pas, et fonctionne avec un rôle ordinaire."""

from __future__ import annotations

import os

import asyncpg
import pytest

from tests.integration.conftest import boot_host
from xcore_doctype.db.tables import DOCUMENT_TABLE

PG_URL = os.environ.get("XCORE_DOCTYPE_TEST_PG_URL")


async def state_of(host, name: str) -> str | None:
    plugins = (await host.ipc("GET", "/status")).json()["plugins"]
    return next((p["state"] for p in plugins if p["name"] == name), None)


class TestConfiguration:
    @pytest.mark.parametrize("value", ["yes", 1, "true"])
    async def test_rls_must_be_a_boolean(self, tmp_path, value):
        async with boot_host(tmp_path, doctype_config={"rls": value}) as h:
            assert await state_of(h, "xcore-doctype") != "ready"

    async def test_rls_on_sqlite_refuses_to_start_rather_than_pretend(self, tmp_path):
        async with boot_host(tmp_path, doctype_config={"rls": True}) as h:
            assert await state_of(h, "xcore-doctype") != "ready"

    async def test_the_default_is_off_and_nothing_changes(self, tmp_path):
        async with boot_host(tmp_path) as h:
            assert await state_of(h, "xcore-doctype") == "ready"
            r = await h.http("POST", "/doc/Customer", token=h.token(), json={"data": {"customer_name": "x"}})
            assert r.status_code == 201


pg = pytest.mark.skipif(not PG_URL, reason="XCORE_DOCTYPE_TEST_PG_URL non défini")


@pg
@pytest.mark.postgres
class TestOnPostgres:
    async def test_a_superuser_connection_makes_the_plugin_refuse_to_start(self, tmp_path):
        conn = await asyncpg.connect(PG_URL.replace("postgresql+asyncpg", "postgresql"))
        try:
            await conn.execute(f"DROP TABLE IF EXISTS public.{DOCUMENT_TABLE}, public.xdt_naming_series, public.xdt_version")
        finally:
            await conn.close()
        async with boot_host(tmp_path, db_type="postgresql+aio", db_url=PG_URL, doctype_config={"rls": True}) as h:
            assert await state_of(h, "xcore-doctype") != "ready"  # le superutilisateur contournerait les politiques

    async def test_an_ordinary_role_gets_a_protected_and_working_plugin(self, tmp_path, rls_app_url):
        async with boot_host(tmp_path, db_type="postgresql+aio", db_url=rls_app_url, doctype_config={"rls": True}) as h:
            assert await state_of(h, "xcore-doctype") == "ready"
            for tenant in ("acme", "globex"):
                t = h.token(sub=f"user-{tenant}", tenant=tenant)
                r = await h.http("POST", "/doc/Customer", token=t, tenant=tenant, json={"data": {"customer_name": f"{tenant} SA"}})
                assert r.status_code == 201, r.text
            a = (await h.http("GET", "/list/Customer", token=h.token(tenant="acme"), tenant="acme")).json()
            assert [d["data"]["customer_name"] for d in a["items"]] == ["acme SA"]

            conn = await asyncpg.connect(rls_app_url.replace("postgresql+asyncpg", "postgresql"))
            try:
                # Hors du plugin, la même connexion (même rôle) ne voit rien tant qu'elle ne dit pas pour quel tenant elle travaille.
                assert await conn.fetchval(f"SELECT count(*) FROM {DOCUMENT_TABLE}") == 0
                async with conn.transaction():
                    await conn.execute("SELECT set_config('xdt.tenant_id', 'globex', true)")
                    assert await conn.fetchval(f"SELECT count(*) FROM {DOCUMENT_TABLE}") == 1
            finally:
                await conn.close()
