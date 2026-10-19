"""Critère 10 de 14 bis.8 : adaptateur synchrone -> échec clair au on_load (D11)."""

from __future__ import annotations

import pytest
from xcore.configurations.sections import DatabaseConfig
from xcore.kernel.tenancy.services import TenantAwareDB
from xcore.services.database.adapters.async_sql import AsyncSQLAdapter
from xcore.services.database.adapters.sql import SQLAdapter

from xcore_doctype.db.access import ensure_async_database
from xcore_doctype.errors import ConfigurationError


@pytest.fixture
async def sync_adapter(tmp_path):
    a = SQLAdapter("s", DatabaseConfig(name="s", type="sqlite", url=f"sqlite:///{tmp_path}/sync.db"))
    await a.connect()
    yield a
    await a.disconnect()


@pytest.fixture
async def async_adapter(tmp_path):
    a = AsyncSQLAdapter("a", DatabaseConfig(name="a", type="sqlite+aio", url=f"sqlite+aiosqlite:///{tmp_path}/a.db"))
    await a.connect()
    yield a
    await a.disconnect()


async def test_sync_adapter_refused_with_actionable_message(sync_adapter):
    with pytest.raises(ConfigurationError) as e:
        await ensure_async_database(sync_adapter)
    assert "sqlite+aio" in str(e.value) and "aiosqlite" in str(e.value)


async def test_sync_adapter_refused_even_when_wrapped_by_tenancy(sync_adapter):
    """Avec tenancy.isolate_db, `get_service("db")` est un TenantAwareDB : un isinstance ne verrait rien."""
    with pytest.raises(ConfigurationError) as e:
        await ensure_async_database(TenantAwareDB(sync_adapter))
    assert "sqlite+aio" in str(e.value)


async def test_missing_database_refused():
    with pytest.raises(ConfigurationError):
        await ensure_async_database(None)


async def test_async_adapter_accepted_and_dialect_returned(async_adapter):
    assert await ensure_async_database(async_adapter) == "sqlite"
    assert await ensure_async_database(TenantAwareDB(async_adapter)) == "sqlite"
