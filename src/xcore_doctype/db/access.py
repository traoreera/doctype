"""Accès base : vérification asynchrone (D11) et création des tables système."""

from __future__ import annotations

from typing import Any

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.schema import CreateColumn

from ..errors import ConfigurationError
from .tables import SystemTables

_HINT = (
    "xcore-doctype exige une base asynchrone : dans integration.yaml, utilisez "
    "`type: sqlite+aio` avec une URL `sqlite+aiosqlite://...` (ou `postgresql+aio` avec "
    "`postgresql+asyncpg://...`). Le type `sqlite`/`postgresql` seul est l'adaptateur synchrone."
)


async def ensure_async_database(db: Any) -> str:
    """Vérifie que `db` est un adaptateur asynchrone et retourne le nom du dialecte.

    Un `isinstance(db, AsyncSQLAdapter)` ne suffit pas : avec la tenancy, `get_service("db")`
    est un `TenantAwareDB` qui enveloppe l'adaptateur. On ouvre donc une session pour de vrai.
    """
    if db is None:
        raise ConfigurationError("aucune base `db` configurée. " + _HINT)
    try:
        async with db.session() as session:
            if not isinstance(session, AsyncSession):
                raise ConfigurationError(f"session de type {type(session).__name__}. " + _HINT)
            await session.execute(text("SELECT 1"))
    except ConfigurationError:
        raise
    except (TypeError, AttributeError) as e:
        # L'adaptateur synchrone fournit un context manager sans `__aenter__`.
        raise ConfigurationError(f"adaptateur synchrone détecté ({e}). " + _HINT) from e
    # Lecture seule : le dialecte, jamais d'écriture via `.engine` (D3).
    return db.engine.dialect.name


async def create_system_tables(db: Any, tables: SystemTables) -> None:
    async with db.session() as session:
        conn = await session.connection()
        await conn.run_sync(tables.metadata.create_all)
        await conn.run_sync(_add_missing_columns, tables)


def _add_missing_columns(conn: Any, tables: SystemTables) -> None:
    """Complète une table système créée par une version antérieure : seules les colonnes AJOUTÉES, jamais autre chose.

    `create_all` ne touche pas à une table qui existe déjà. Une colonne nouvelle doit donc pouvoir s'ajouter à chaud : nullable,
    ou avec une valeur par défaut qui donne à chaque ligne existante son état naturel (`docstatus` : brouillon). Une colonne
    obligatoire sans défaut serait un vrai changement de schéma, qui demande une migration et pas ce rattrapage.
    """
    existing = inspect(conn)
    for table in tables.metadata.sorted_tables:
        have = {c["name"] for c in existing.get_columns(table.name)}
        for column in table.columns:
            if column.name in have:
                continue
            if not column.nullable and column.server_default is None:
                raise ConfigurationError(
                    f"{table.name}.{column.name}: colonne obligatoire sans valeur par défaut, impossible à ajouter à chaud (migration requise)"
                )
            ddl = str(CreateColumn(column).compile(dialect=conn.dialect))
            conn.execute(text(f"ALTER TABLE {table.name} ADD COLUMN {ddl}"))
