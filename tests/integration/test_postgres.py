"""D3 / D14 sur PostgreSQL réel : ce que fait `tenancy.isolate_db: true` avec nos tables.

SKIPPED sans `XCORE_DOCTYPE_TEST_PG_URL`. Chaque test recrée un état propre (schémas et table).
"""

from __future__ import annotations

import os

import asyncpg
import pytest

from tests.integration.conftest import boot_host
from xcore_doctype.db.tables import DOCUMENT_TABLE

PG_URL = os.environ.get("XCORE_DOCTYPE_TEST_PG_URL")
pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not PG_URL, reason="XCORE_DOCTYPE_TEST_PG_URL non défini"),
]


@pytest.fixture
async def pg():
    dsn = PG_URL.replace("postgresql+asyncpg", "postgresql")
    conn = await asyncpg.connect(dsn)

    async def clean():
        await conn.execute(f"DROP TABLE IF EXISTS public.{DOCUMENT_TABLE}")
        for schema in ("default", "acme", "globex"):
            await conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')

    await clean()
    yield conn
    await clean()
    await conn.close()


async def tables_in(conn) -> list[str]:
    rows = await conn.fetch("SELECT table_schema FROM information_schema.tables WHERE table_name = $1", DOCUMENT_TABLE)
    return sorted(r["table_schema"] for r in rows)


async def roundtrip(h):
    for tenant in ("acme", "globex"):
        t = h.token(sub=f"user-{tenant}", tenant=tenant)
        r = await h.http("POST", "/doc/Customer", token=t, tenant=tenant, json={"data": {"customer_name": f"{tenant} SA"}})
        assert r.status_code == 201, r.text
    a = (await h.http("GET", "/list/Customer", token=h.token(tenant="acme"), tenant="acme")).json()
    g = (await h.http("GET", "/list/Customer", token=h.token(sub="b", tenant="globex"), tenant="globex")).json()
    assert [d["data"]["customer_name"] for d in a["items"]] == ["acme SA"]
    assert [d["data"]["customer_name"] for d in g["items"]] == ["globex SA"]


async def test_pg_end_to_end_with_isolate_db_false(tmp_path, pg):
    """Configuration recommandée (D3) : isolation par colonne, pas de search_path."""
    async with boot_host(tmp_path, db_type="postgresql+aio", db_url=PG_URL, isolate_db=False) as h:
        await roundtrip(h)
    assert await tables_in(pg) == ["public"]


async def test_pg_with_isolate_db_true_falls_back_to_public_when_tenant_schemas_are_absent(tmp_path, pg, caplog):
    """D3 : « le search_path d'xcore retombe sur `public` quand le schéma du tenant n'existe pas »."""
    async with boot_host(tmp_path, db_type="postgresql+aio", db_url=PG_URL, isolate_db=True) as h:
        await roundtrip(h)
    assert await tables_in(pg) == ["public"]
    # Le déploiement est averti : cette configuration marche par accident (voir le test suivant).
    assert "tenancy.isolate_db est actif sur PostgreSQL" in caplog.text


async def test_pg_isolate_db_false_gives_no_warning(tmp_path, pg, caplog):
    async with boot_host(tmp_path, db_type="postgresql+aio", db_url=PG_URL, isolate_db=False):
        pass
    assert "tenancy.isolate_db est actif" not in caplog.text


async def test_pg_isolate_db_true_with_tenant_schemas_makes_http_and_ipc_diverge(tmp_path, pg):
    """Risque de D3, mesuré : avec `isolate_db: true` et des schémas de tenant existants.

    - Le DDL du `on_load` passe par `TenantAwareDB` sous le tenant par défaut : la table est créée
      dans le schéma `default` (premier du search_path), pas dans `public`.
    - Les routes HTTP ne posent jamais `_current_tenant_id` (seul `Supervisor._dispatch` le fait) :
      `TenantAwareDB` y résout toujours `default` et retrouve la table, par accident.
    - Le chemin IPC pose le vrai tenant : `search_path = "acme", public`, la table est introuvable.
    Conclusion : `isolate_db: false` (D3) n'est pas une option de confort, c'est une condition de cohérence.
    """
    for schema in ("default", "acme", "globex"):
        await pg.execute(f'CREATE SCHEMA "{schema}"')
    async with boot_host(tmp_path, db_type="postgresql+aio", db_url=PG_URL, isolate_db=True) as h:
        landed = await tables_in(pg)
        t = h.token(tenant="acme")
        http = await h.http("GET", "/list/Customer", token=t, tenant="acme")
        ipc = await h.action("doc.list", {"doctype": "Customer"}, token=t, tenant="acme")
    print(f"\n[D3] schémas contenant la table : {landed} ; HTTP : {http.status_code} ; IPC : {ipc}")
    assert landed == ["default"]
    assert http.status_code == 200
    assert ipc["status"] == "error"  # HTTP et IPC divergent : la table n'est visible que par le tenant « default »
