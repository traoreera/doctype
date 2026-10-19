"""Une base créée avant `docstatus` / `amended_from` se met à niveau toute seule, sans perdre une ligne."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import Column, Integer, MetaData, String, Table, text

from tests.conftest import make_ctx
from xcore_doctype.db.access import create_system_tables
from xcore_doctype.db.tables import DOCUMENT_TABLE, build_system_tables
from xcore_doctype.errors import ConfigurationError
from xcore_doctype.permissions import Scope
from xcore_doctype.query import Query

CTX = make_ctx("acme", "alice")


async def columns(db, table: str) -> set[str]:
    async with db.session() as s:
        conn = await s.connection()
        from sqlalchemy import inspect

        return {c["name"] for c in await conn.run_sync(lambda c: inspect(c).get_columns(table))}


async def test_an_old_table_gains_the_columns_and_its_rows_become_drafts(db):
    tables = build_system_tables()
    await create_system_tables(db, tables)
    async with db.session() as s:
        q = Query(s, CTX, tables)
        await q.insert("Customer", "old", {"customer_name": "avant"})
    async with db.session() as s:  # la table telle qu'une version antérieure l'avait créée
        await s.execute(text(f"ALTER TABLE {DOCUMENT_TABLE} DROP COLUMN docstatus"))
        await s.execute(text(f"ALTER TABLE {DOCUMENT_TABLE} DROP COLUMN amended_from"))
    assert not {"docstatus", "amended_from"} & await columns(db, DOCUMENT_TABLE)

    await create_system_tables(db, build_system_tables())

    assert {"docstatus", "amended_from"} <= await columns(db, DOCUMENT_TABLE)
    async with db.session() as s:
        old = await Query(s, CTX, tables).get("Customer", "old", scope=Scope.everything("read"))
    assert (old.docstatus, old.amended_from, old.data) == (0, None, {"customer_name": "avant"})
    async with db.session() as s:  # et le document se soumet comme un autre
        done = await Query(s, CTX, tables).transition(
            "Customer", "old", source=0, target=1, expected_version=1, scope=Scope.everything("submit")
        )
    assert done.docstatus == 1


async def test_the_upgrade_is_idempotent(db):
    tables = build_system_tables()
    for _ in range(3):
        await create_system_tables(db, tables)
    assert {"docstatus", "amended_from"} <= await columns(db, DOCUMENT_TABLE)


async def test_a_required_column_without_default_is_not_added_blindly(db):
    """Ajouter à chaud une colonne obligatoire sans défaut ne peut pas donner de valeur aux lignes existantes : une migration."""
    md = MetaData()
    Table("xdt_probe", md, Column("id", Integer, primary_key=True))
    probe = SimpleNamespace(metadata=md)
    await create_system_tables(db, probe)
    md2 = MetaData()
    Table("xdt_probe", md2, Column("id", Integer, primary_key=True), Column("must", String(5), nullable=False))
    try:
        with pytest.raises(ConfigurationError, match="xdt_probe.must"):
            await create_system_tables(db, SimpleNamespace(metadata=md2))
    finally:
        async with db.session() as s:
            await s.execute(text("DROP TABLE IF EXISTS xdt_probe"))
