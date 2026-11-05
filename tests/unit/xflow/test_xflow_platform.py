"""xflow livré AVEC le moteur (`xcore_doctype/platform/xflow`) : ce qui changeait en le sortant de `app/xflows`.

- Sa chaîne Alembic tourne d'ailleurs que `app/xflows` (son `env.py` importait `app.xflows.src` : hors de ce chemin la migration
  échouait, et en production le plugin refusait de démarrer) — prouvé sur une base NEUVE en `MODE=production`, puis rejouée.
- Une action IPC refuse une organisation de charge utile qui n'est pas celle de l'appel.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from xcore.kernel.tenancy import _current_tenant_id
from xcore_doctype.platform.xflow.src.main import Plugin


async def test_the_alembic_chain_runs_from_the_packaged_plugin_on_a_fresh_database_in_production(tmp_path, monkeypatch):
    monkeypatch.setenv("MODE", "production")  # un échec de migration y LÈVE : c'est ce qui rend le test probant
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'x.db'}")
    try:
        db = SimpleNamespace(engine=engine)
        await Plugin._initialize_tables(SimpleNamespace(), db)
        await Plugin._initialize_tables(SimpleNamespace(), db)  # rejouée : idempotente
        async with engine.connect() as c:
            versions = (await c.execute(text("SELECT version_num FROM alembic_version_xflows"))).scalars().all()
        assert len(versions) == 1
    finally:
        await engine.dispose()


async def test_an_ipc_action_refuses_another_organisation_than_the_call_s():
    plugin = Plugin.__new__(Plugin)
    seen = []

    async def dispatched(self, action, payload):
        seen.append(payload["tenant_id"])
        return {"status": "ok"}

    token = _current_tenant_id.set("acme")
    try:
        from xcore.sdk import AutoDispatchMixin

        original = AutoDispatchMixin.handle
        AutoDispatchMixin.handle = dispatched  # le répartiteur d'xcore, remplacé : on regarde seulement ce qui passe la garde
        try:
            refused = await plugin.handle("trigger", {"tenant_id": "globex", "workflow_name": "x"})
            assert refused["status"] == "error" and refused["code"] == "tenant_mismatch"
            assert (await plugin.handle("trigger", {"tenant_id": "acme", "workflow_name": "x"}))["status"] == "ok"
        finally:
            AutoDispatchMixin.handle = original
    finally:
        _current_tenant_id.reset(token)
    assert seen == ["acme"]


class _Sched:
    """Le SchedulerService d'xcore, réduit à ce qu'xflow appelle : des jobs par identifiant."""

    def __init__(self):
        self.jobs = {}

    def add_job(self, func, trigger, job_id, **kw):
        self.jobs[job_id] = trigger

    def remove_job(self, job_id):
        del self.jobs[job_id]


class _Store:
    def __init__(self):
        self.saved = {}

    async def save_definition(self, tenant_id, definition):
        self.saved[(tenant_id, definition.name)] = definition
        return definition

    async def get_definition(self, tenant_id, name):
        return self.saved.get((tenant_id, name))

    async def delete_definition(self, tenant_id, name):
        self.saved.pop((tenant_id, name), None)


async def test_a_scheduled_flow_s_job_stops_when_it_is_deleted_or_no_longer_scheduled():
    """Le job s'appelle `wf_sched_<organisation>_<nom>` ; la suppression cherchait `wf_sched_<nom>` : un flux planifié supprimé
    depuis le Desk continuait de tourner. Et un flux redéployé « à la demande » gardait son ancien cron."""
    from xcore_doctype.platform.xflow.src.schemas import WorkflowDefinition
    from xcore_doctype.platform.xflow.src.services import WorkflowRegistryService, WorkflowScheduler

    sched = _Sched()
    registry = WorkflowRegistryService(_Store(), WorkflowScheduler(sched, engine=None))
    step = [{"id": "a", "type": "action", "plugin": "p", "action": "x"}]
    planned = {"name": "relance", "trigger": {"type": "schedule", "cron": "0 9 * * *"}, "steps": step}
    await registry.register("acme", WorkflowDefinition(**planned))
    await registry.register("globex", WorkflowDefinition(**planned))
    assert set(sched.jobs) == {"wf_sched_acme_relance", "wf_sched_globex_relance"}
    await registry.unregister("acme", "relance")
    assert set(sched.jobs) == {"wf_sched_globex_relance"}  # l'autre organisation garde le sien
    await registry.register("globex", WorkflowDefinition(**{**planned, "trigger": {"type": "manual"}}))
    assert sched.jobs == {}
