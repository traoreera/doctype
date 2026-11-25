"""Tâches RÉCURRENTES : le moteur dépose lui-même, une fois par organisation et par période.

Avant, rien ne tournait seul : la file de tâches savait exécuter, pas déclencher, et le moteur ne connaissait aucune liste
d'organisations. Une facture échue le restait jusqu'à ce que quelqu'un la touche. Ce qui se prouve ici, contre une vraie base :
POUR QUI la tâche est déposée (les organisations qui ont des documents du plugin, y compris ceux écrits avant la table de
présence), qu'elle l'est UNE fois par période quel que soit le nombre de processus, qu'un arrêt se rattrape sans empiler, et que
rien de tout cela ne lit le contenu d'un document.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import delete, event, select, text
from xcore.configurations.sections import DatabaseConfig
from xcore.services.database.adapters.async_sql import AsyncSQLAdapter

from tests.conftest import make_ctx
from xcore_doctype import presence as presence_module
from xcore_doctype.db.access import create_system_tables
from xcore_doctype.db.rls import RLS_EXEMPT, ensure_rls
from xcore_doctype.db.tables import DOCUMENT_TABLE, JOB_TABLE, PRESENCE_TABLE, SCHEDULE_TABLE, build_system_tables
from xcore_doctype.document import DocumentService
from xcore_doctype.errors import DocumentValidationError
from xcore_doctype.hooks import HookRegistry
from xcore_doctype.jobs import DONE, FAILED, PENDING, RECURRING_KEY, JobService
from xcore_doctype.plugin import _stored_types
from xcore_doctype.presence import MARK
from xcore_doctype.registry import DocTypeRegistry
from xcore_doctype.schema import parse_definition
from xcore_doctype.sealing import Sealer
from xcore_doctype.writer import DocumentWriters

ACCOUNT = {
    "schema_version": 1, "name": "Account", "module": "accounts",
    "fields": [{"fieldname": "code", "fieldtype": "Text", "required": True, "unique": True}, {"fieldname": "label", "fieldtype": "Text"}],
}
COMPANY = {
    "schema_version": 1, "name": "Company", "module": "accounts", "submittable": True,
    "hooks": {"on_submit": ["accounts.open"]},
    "fields": [{"fieldname": "title", "fieldtype": "Text", "required": True}],
}
LINE = {"schema_version": 1, "name": "Entry Line", "istable": True, "fields": [{"fieldname": "label", "fieldtype": "Text"}]}
MAILBOX = {
    "schema_version": 1, "name": "Mailbox", "module": "mail",
    "fields": [{"fieldname": "address", "fieldtype": "Text", "required": True}],
}
GRANTS = ["accounts:account:read", "accounts:account:write", "accounts:company:read", "accounts:company:write",
          "accounts:company:submit", "mail:mailbox:read", "mail:mailbox:write"]
ACME, GLOBEX, INITECH = (make_ctx(t, "u-" + t, permissions=GRANTS) for t in ("acme", "globex", "initech"))
DAILY = "accounts.daily"


class Log:
    def __init__(self):
        self.lines: list[tuple[str, dict]] = []

    def info(self, m, **k): self.lines.append((m, k))
    def warning(self, m, **k): self.lines.append((m, k))
    def error(self, m, **k): self.lines.append((m, k))
    def debug(self, m, **k): pass


class Clock:
    def __init__(self):
        self.now = datetime(2026, 11, 24, 9, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def forward(self, **delta) -> None:
        self.now += timedelta(**delta)

    def at(self, *when) -> None:
        self.now = datetime(*when, tzinfo=UTC)


class Env:
    """Un moteur (registre, documents, hooks) et de quoi bâtir autant de services de tâches — de PROCESSUS — qu'il en faut."""

    def __init__(self, db, tables, *, rls: bool = False):
        self.db, self.tables, self.rls = db, tables, rls
        self.registry, self.hooks, self.log, self.clock = DocTypeRegistry(), HookRegistry(), Log(), Clock()
        for raw in (LINE, ACCOUNT, COMPANY, MAILBOX):
            self.registry.register(parse_definition(raw))
        self.registry.validate_links()
        self.docs = self.documents()
        self.jobs = self.process()

    def documents(self) -> DocumentService:
        """Un service de documents neuf : sa mémoire de présence est vide, comme celle d'un processus qui démarre."""
        return DocumentService(self.registry, self.db, self.tables, self.log, hooks=self.hooks, sealer=Sealer("k" * 32), rls=self.rls)

    def process(self, docs: DocumentService | None = None, **kw) -> JobService:
        """Un autre processus : la même base, le même registre, une mémoire à lui."""
        docs = docs or self.docs
        return JobService(self.db, self.tables, self.log, writers=DocumentWriters(docs, settings=None),  # type: ignore[arg-type]
                          clock=self.clock, presence=docs.presence, types_of=_stored_types(self.registry), **kw)

    async def deposited(self, tenant: str, name: str = DAILY) -> list[dict]:
        return await self.jobs.store.list(tenant, name=name, limit=100)

    async def rows(self, table: str) -> list[tuple]:
        async with self.db.session() as s:
            return [tuple(r) for r in await s.execute(text(f"SELECT * FROM {table} ORDER BY 1, 2"))]


@pytest.fixture
def env(db, tables):
    return Env(db, tables)


async def noop(job) -> None:
    return None


async def run(jobs: JobService) -> int:
    started = await jobs.run_once()
    await jobs.drain()
    return started


async def account(env: Env, ctx, code: str = "411") -> dict:
    return await env.docs.create(ctx, "Account", {"code": code})


class TestForWhom:
    async def test_one_job_per_organisation_that_has_documents_of_the_plugin(self, env):
        await account(env, ACME)
        await account(env, GLOBEX)
        await env.docs.create(INITECH, "Mailbox", {"address": "contact@initech.test"})  # un autre plugin : pas concernée
        seen = []

        async def daily(job):
            seen.append((job.tenant_id, job.period, job.writer.context.is_system, job.owner))

        env.jobs.register("accounts", "daily", daily, daily_at="02:30")
        assert await env.jobs.schedule_once() == 2
        for tenant in ("acme", "globex"):
            (job,) = await env.deposited(tenant)
            assert (job["status"], job["key"], job["owner"], job["payload"]) == (PENDING, RECURRING_KEY, "system", {"period": "2026-11-24"})
        assert await env.deposited("initech") == []
        assert await run(env.jobs) == 2
        assert sorted(seen) == [("acme", "2026-11-24", True, "system"), ("globex", "2026-11-24", True, "system")]

    async def test_each_plugin_has_its_own_organisations(self, env):
        await account(env, ACME)
        await env.docs.create(INITECH, "Mailbox", {"address": "contact@initech.test"})
        env.jobs.register("accounts", "daily", noop, daily_at="02:30")
        env.jobs.register("mail", "fetch", noop, every=900)
        assert await env.jobs.schedule_once() == 2
        assert [len(await env.deposited(t)) for t in ("acme", "initech")] == [1, 0]
        assert [len(await env.deposited(t, "mail.fetch")) for t in ("acme", "initech")] == [0, 1]

    async def test_an_organisation_joins_at_the_next_period(self, env):
        await account(env, ACME)
        env.jobs.register("accounts", "daily", noop, daily_at="02:30")
        assert await env.jobs.schedule_once() == 1
        await run(env.jobs)
        await account(env, GLOBEX)  # son premier document, en cours de période
        assert await env.jobs.schedule_once() == 0
        env.clock.forward(days=1)
        assert await env.jobs.schedule_once() == 2
        assert [len(await env.deposited(t)) for t in ("acme", "globex")] == [2, 1]

    async def test_documents_written_before_the_presence_table_are_taken_up_once(self, env):
        """La mise à jour d'une installation existante : ses documents n'ont aucune ligne de présence. La première période les
        reprend d'une instruction, et ne recommence pas (la ligne (`*`, `*`) le marque)."""
        await account(env, ACME)
        await account(env, GLOBEX)
        async with env.db.session() as s:
            await s.execute(delete(env.tables.presence))  # comme si ces documents dataient d'avant la table
        docs = env.documents()
        jobs = env.process(docs)
        jobs.register("accounts", "daily", noop, daily_at="02:30")
        assert await jobs.schedule_once() == 2
        await run(jobs)
        assert (MARK, MARK) in {(r[0], r[1]) for r in await env.rows(PRESENCE_TABLE)}
        # Une présence perdue APRÈS la reprise ne revient pas par un second balayage de la table des documents…
        async with env.db.session() as s:
            await s.execute(delete(env.tables.presence).where(env.tables.presence.c.tenant_id == "globex"))
        env.clock.forward(days=1)
        again = env.process(env.documents())
        again.register("accounts", "daily", noop, daily_at="02:30")
        assert await again.schedule_once() == 1
        assert len(await env.deposited("globex")) == 1

    async def test_a_modification_is_enough_to_be_known_again(self, env):
        """…elle revient par la prochaine ÉCRITURE de l'organisation, création ou modification : c'est ce qui répare une ligne qu'un
        arrêt du processus n'a pas laissé le temps de poser (elle s'écrit après le commit, à part)."""
        doc = await account(env, ACME)
        env.jobs.register("accounts", "daily", noop, daily_at="02:30")
        assert await env.jobs.schedule_once() == 1  # la reprise est marquée
        await run(env.jobs)
        async with env.db.session() as s:
            await s.execute(delete(env.tables.presence).where(env.tables.presence.c.tenant_id == "acme"))
        docs = env.documents()  # le processus a redémarré : il ne « sait » plus rien
        await docs.update(ACME, "Account", doc["name"], {"code": "411", "label": "Clients"}, doc["version"])
        env.clock.forward(days=1)
        jobs = env.process(docs)
        jobs.register("accounts", "daily", noop, daily_at="02:30")
        assert await jobs.schedule_once() == 1

    async def test_a_refused_write_teaches_nothing(self, env):
        """La présence se note APRÈS le commit : un document écrit par un hook dans une transaction finalement refusée n'a jamais
        existé, et son organisation n'est pas « connue » pour ce type."""
        async def open_books(event):
            await event.tx.create("Account", {"code": "101"})
            raise DocumentValidationError("refusé après avoir écrit")

        env.hooks.register("accounts.open", open_books, owner="accounts")
        company = await env.docs.create(ACME, "Company", {"title": "Kounandy"})
        with pytest.raises(DocumentValidationError):
            await env.docs.submit(ACME, "Company", company["name"], company["version"])
        assert {(r[0], r[1]) for r in await env.rows(PRESENCE_TABLE)} == {("acme", "Company")}

    async def test_presence_is_per_organisation_and_per_type(self, env):
        await account(env, ACME)
        await account(env, ACME, "512")
        await env.docs.create(GLOBEX, "Mailbox", {"address": "a@globex.test"})
        assert {(r[0], r[1]) for r in await env.rows(PRESENCE_TABLE)} == {("acme", "Account"), ("globex", "Mailbox")}
        assert await env.docs.presence.tenants(["Account"]) == ["acme"]
        assert await env.docs.presence.tenants(["Account", "Mailbox"]) == ["acme", "globex"]
        assert await env.docs.presence.tenants([]) == []

    async def test_a_presence_that_cannot_be_written_never_fails_the_document(self, env, monkeypatch):
        def broken(session):
            raise RuntimeError("base de présence injoignable")

        monkeypatch.setattr(presence_module, "_insert_for", broken)
        doc = await account(env, ACME)
        assert doc["data"]["code"] == "411" and await env.rows(PRESENCE_TABLE) == []
        assert any(m == "presence not recorded" and k["tenant"] == "acme" for m, k in env.log.lines)
        monkeypatch.undo()
        await account(env, ACME, "512")  # rien n'a été retenu de l'échec : la ligne se pose à l'écriture suivante
        assert {(r[0], r[1]) for r in await env.rows(PRESENCE_TABLE)} == {("acme", "Account")}

    def test_only_stored_types_of_the_plugin_count(self, env):
        """Ni une table de lignes (elle vit dans son parent), ni le type d'un autre plugin."""
        types_of = _stored_types(env.registry)
        assert types_of("accounts") == ("Account", "Company") and types_of("mail") == ("Mailbox",) and types_of("nobody") == ()


class TestOncePerPeriod:
    async def test_nothing_more_in_the_same_period_even_once_the_job_is_over(self, env):
        """La clé d'unicité d'une tâche ne tient que tant qu'elle est active : sans l'échéancier, un second processus redéposerait
        la tâche du jour dès la première finie."""
        await account(env, ACME)
        env.jobs.register("accounts", "daily", noop, daily_at="02:30")
        assert await env.jobs.schedule_once() == 1
        assert await run(env.jobs) == 1
        assert (await env.deposited("acme"))[0]["status"] == DONE
        assert await env.jobs.schedule_once() == 0
        other = env.process()  # un autre processus, qui n'a rien en mémoire
        other.register("accounts", "daily", noop, daily_at="02:30")
        assert await other.schedule_once() == 0
        assert len(await env.deposited("acme")) == 1

    async def test_the_database_settles_processes_that_tick_together(self, env):
        for ctx in (ACME, GLOBEX, INITECH):
            await account(env, ctx)
        processes = [env.process() for _ in range(5)]
        for p in processes:
            p.register("accounts", "daily", noop, daily_at="02:30")
        made = await asyncio.gather(*(p.schedule_once() for p in processes))
        assert sum(made) == 3
        assert [len(await env.deposited(t)) for t in ("acme", "globex", "initech")] == [1, 1, 1]

    async def test_the_next_day_brings_one_more(self, env):
        await account(env, ACME)
        env.jobs.register("accounts", "daily", noop, daily_at="02:30")
        await env.jobs.schedule_once()
        await run(env.jobs)
        env.clock.forward(days=1)
        assert await env.jobs.schedule_once() == 1
        periods = sorted(j["payload"]["period"] for j in await env.deposited("acme"))
        assert periods == ["2026-11-24", "2026-11-25"]

    async def test_before_the_hour_it_is_still_yesterday(self, env):
        await account(env, ACME)
        env.jobs.register("accounts", "daily", noop, daily_at="02:30")
        env.clock.at(2026, 11, 25, 2, 29)
        assert await env.jobs.schedule_once() == 1  # la période due est celle du 24 : jamais déposée, elle part maintenant
        assert (await env.deposited("acme"))[0]["payload"] == {"period": "2026-11-24"}
        await run(env.jobs)
        env.clock.at(2026, 11, 25, 2, 30)
        assert await env.jobs.schedule_once() == 1
        assert sorted(j["payload"]["period"] for j in await env.deposited("acme")) == ["2026-11-24", "2026-11-25"]

    async def test_the_hour_is_read_in_the_engine_timezone(self, env):
        """`doctype.timezone` : aux Comores (UTC+3), 23 h 45 UTC le 24 est déjà le 25 à 2 h 45 — l'heure est passée."""
        await account(env, ACME)
        jobs = env.process(timezone=ZoneInfo("Indian/Comoro"))
        jobs.register("accounts", "daily", noop, daily_at="02:30")
        env.clock.at(2026, 11, 24, 23, 45)
        assert await jobs.schedule_once() == 1
        assert (await env.deposited("acme"))[0]["payload"] == {"period": "2026-11-25"}

    async def test_a_server_down_for_days_deposits_one_job_not_one_per_missed_day(self, env):
        await account(env, ACME)
        env.jobs.register("accounts", "daily", noop, daily_at="02:30")
        await env.jobs.schedule_once()
        await run(env.jobs)
        env.clock.forward(days=3)  # personne n'a tourné pendant trois nuits
        back = env.process()
        back.register("accounts", "daily", noop, daily_at="02:30")
        assert await back.schedule_once() == 1
        assert sorted(j["payload"]["period"] for j in await env.deposited("acme")) == ["2026-11-24", "2026-11-27"]

    async def test_a_job_still_running_is_not_doubled(self, env):
        """La tâche d'hier n'a pas fini quand la période d'aujourd'hui arrive : on ne l'empile pas (même clé, la base rend celle qui
        tourne). C'est pourquoi une tâche récurrente se lit sur l'état, pas sur sa période."""
        await account(env, ACME)
        env.jobs.register("accounts", "daily", noop, daily_at="02:30")
        assert await env.jobs.schedule_once() == 1  # déposée, pas encore exécutée
        env.clock.forward(days=1)
        assert await env.jobs.schedule_once() == 0
        assert len(await env.deposited("acme")) == 1
        await run(env.jobs)
        env.clock.forward(days=1)
        assert await env.jobs.schedule_once() == 1

    async def test_the_period_is_taken_with_its_job_or_not_at_all(self, env, monkeypatch):
        """Un processus qui meurt entre « la période est prise » et « la tâche est déposée » ne doit pas laisser une période prise
        sans tâche : elle ne serait plus jamais déposée. Les deux écritures sont une seule transaction."""
        await account(env, ACME)
        env.jobs.register("accounts", "daily", noop, daily_at="02:30")
        real = env.jobs.store.insert

        async def dies(*a, **k):
            raise RuntimeError("le processus meurt ici")

        monkeypatch.setattr(env.jobs.store, "insert", dies)
        with pytest.raises(RuntimeError):
            await env.jobs.schedule_once()
        assert await env.rows(SCHEDULE_TABLE) == []
        monkeypatch.setattr(env.jobs.store, "insert", real)
        assert await env.jobs.schedule_once() == 1


class TestEvery:
    async def test_slots_are_aligned_on_the_clock(self, env):
        await env.docs.create(ACME, "Mailbox", {"address": "a@acme.test"})
        seen = []

        async def fetch(job):
            seen.append(job.period)

        env.jobs.register("mail", "fetch", fetch, every=900)
        env.clock.at(2026, 11, 24, 9, 7)
        assert await env.jobs.schedule_once() == 1
        await run(env.jobs)
        env.clock.at(2026, 11, 24, 9, 14, 59)
        assert await env.jobs.schedule_once() == 0
        env.clock.at(2026, 11, 24, 9, 15)
        assert await env.jobs.schedule_once() == 1
        await run(env.jobs)
        assert seen == ["2026-11-24T09:00:00Z", "2026-11-24T09:15:00Z"]

    async def test_a_handmade_run_carries_no_period_and_does_not_double_the_scheduled_one(self, env):
        """Un bouton « lancer maintenant » dépose la tâche avec la clé de l'ordonnanceur : elle ne double pas celle qui attend."""
        await env.docs.create(ACME, "Mailbox", {"address": "a@acme.test"})
        periods = []

        async def fetch(job):
            periods.append(job.period)

        env.jobs.register("mail", "fetch", fetch, every=900)
        by_hand = await env.jobs.enqueue("acme", "mail.fetch", key=RECURRING_KEY, owner="alice")
        assert by_hand["created"] is True
        assert await env.jobs.schedule_once() == 0  # celle du bouton tient la clé
        await env.jobs.enqueue("acme", "mail.fetch", {"period": "ce n'est pas l'ordonnanceur"}, owner="alice")  # sans la clé : une tâche ordinaire
        await run(env.jobs)
        assert periods == [None, None]


class TestJournal:
    async def test_finished_recurring_jobs_are_kept_for_a_while_then_removed(self, env):
        await account(env, ACME)
        async def daily(job):
            if job.period == "2026-11-24":
                raise RuntimeError("le premier jour échoue")

        env.jobs.register("accounts", "daily", daily, daily_at="02:30", keep_days=7, max_attempts=1)
        env.jobs.register("accounts", "export", noop)
        by_hand = await env.jobs.enqueue("acme", "accounts.export", owner="alice")  # une tâche ordinaire : le journal de quelqu'un
        same_name = await env.jobs.enqueue("acme", "accounts.daily", key="reprise-2025", owner="alice")  # déposée à la main, sous SA clé
        await run(env.jobs)
        for _ in range(3):
            await env.jobs.schedule_once()
            await run(env.jobs)
            env.clock.forward(days=1)
        scheduled = [j for j in await env.deposited("acme") if j["key"] == RECURRING_KEY]
        assert sorted(j["status"] for j in scheduled) == [DONE, DONE, FAILED]
        env.clock.forward(days=4, hours=1)  # le 1er décembre : la première (échouée le 24) a plus de 7 jours, les deux autres non
        await env.jobs.schedule_once()
        left = [j for j in await env.deposited("acme") if j["key"] == RECURRING_KEY]
        assert sorted(j["payload"]["period"] for j in left) == ["2026-11-25", "2026-11-26", "2026-12-01"]
        for kept in (by_hand, same_name):  # aussi anciennes que la première, et toujours là : seul l'ordonnanceur retire les siennes
            assert (await env.jobs.store.get("acme", kept["id"]))["status"] in (DONE, FAILED)


class TestDeclarations:
    def test_refusals(self, env):
        for bad, why in (
            ({"every": 900, "daily_at": "02:30"}, "pas les deux"),
            ({"every": 59}, "every"), ({"every": True}, "every"), ({"every": "900"}, "every"), ({"every": 40 * 86400}, "every"),
            ({"daily_at": "24:00"}, "daily_at"), ({"daily_at": "2:30"}, "daily_at"), ({"daily_at": 230}, "daily_at"),
            ({"daily_at": "02:30", "keep_days": 0}, "keep_days"), ({"every": 900, "keep_days": 1.5}, "keep_days"),
        ):
            with pytest.raises(ValueError, match=why):
                env.jobs.register("accounts", "daily", noop, **bad)
        assert env.jobs.recurring() == []  # un refus ne laisse rien de déclaré

    def test_what_the_engine_triggers_by_itself_is_listed(self, env):
        env.jobs.register("accounts", "daily", noop, daily_at="02:30")
        env.jobs.register("mail", "fetch", noop, every=900, keep_days=3)
        env.jobs.register("accounts", "install_chart", noop)
        assert env.jobs.recurring() == [
            {"name": "accounts.daily", "every": None, "daily_at": "02:30", "keep_days": 30},
            {"name": "mail.fetch", "every": 900.0, "daily_at": None, "keep_days": 3},
        ]

    async def test_a_plugin_that_leaves_takes_its_rhythm_with_it(self, env):
        await account(env, ACME)
        env.jobs.register("accounts", "daily", noop, daily_at="02:30")
        env.jobs.unregister_owner("accounts")
        assert env.jobs.recurring() == [] and await env.jobs.schedule_once() == 0
        env.jobs.register("accounts", "daily", noop, daily_at="02:30")  # il revient : la période se rejuge, la base tranche
        assert await env.jobs.schedule_once() == 1
        env.jobs.unregister_owner("accounts")
        env.jobs.register("accounts", "daily", noop, daily_at="02:30")
        assert await env.jobs.schedule_once() == 0

    async def test_a_plugin_without_stored_types_serves_nobody_and_forgets_nothing(self, env):
        """Ses types ne sont pas (encore) chargés : rien n'est déposé, et la période n'est pas tenue pour faite."""
        await account(env, ACME)
        types = {"accounts": ()}
        jobs = JobService(env.db, env.tables, env.log, clock=env.clock, presence=env.docs.presence, types_of=lambda plugin: types[plugin])
        jobs.register("accounts", "daily", noop, daily_at="02:30")
        assert await jobs.schedule_once() == 0
        types["accounts"] = ("Account",)
        assert await jobs.schedule_once() == 1

    async def test_the_background_loop_schedules_then_runs(self, env):
        await account(env, ACME)
        done = asyncio.Event()

        async def daily(job):
            done.set()

        jobs = env.process(poll_seconds=0.05)
        jobs.register("accounts", "daily", daily, daily_at="02:30")
        jobs.start()
        try:
            await asyncio.wait_for(done.wait(), timeout=5)
            await jobs.drain()
        finally:
            await jobs.close()

    async def test_recurring_jobs_can_be_suspended(self, env):
        """`doctype.jobs.recurring: false` : la boucle exécute ce qu'on dépose, elle ne déclenche plus rien d'elle-même."""
        await account(env, ACME)
        ran = asyncio.Event()

        async def export(job):
            ran.set()

        jobs = env.process(poll_seconds=0.05, recurring=False)
        jobs.register("accounts", "daily", noop, daily_at="02:30")
        jobs.register("accounts", "export", export)
        jobs.start()
        try:
            await jobs.enqueue("acme", "accounts.export")
            await asyncio.wait_for(ran.wait(), timeout=5)
            await jobs.drain()
        finally:
            await jobs.close()
        assert await env.deposited("acme") == []


class TestNothingIsReadThatShouldNotBe:
    async def test_the_scheduler_reads_no_document_content_and_no_payload(self, env):
        """Le garde-fou de `test_query_leak.py`, pour l'ordonnanceur. Il lit `xdt_document` tous tenants confondus UNE fois (la
        reprise) : ce ne sont que deux colonnes, jamais `data`. Et aucune instruction qui franchit les tenants ne lit ni ne retire
        d'après un `payload` : seule la lecture d'UNE tâche, sous son tenant, le porte."""
        await account(env, ACME)
        await account(env, GLOBEX)
        async with env.db.session() as s:
            await s.execute(delete(env.tables.presence))
        jobs = env.process(env.documents())
        jobs.register("accounts", "daily", noop, daily_at="02:30")
        captured: list[str] = []

        def listen(conn, cursor, statement, parameters, context, executemany):
            captured.append(statement)

        engine = env.db.engine.sync_engine
        event.listen(engine, "before_cursor_execute", listen)
        try:
            assert await jobs.schedule_once() == 2
            env.clock.forward(days=1)
            await jobs.schedule_once()
        finally:
            event.remove(engine, "before_cursor_execute", listen)
        on_documents = [s for s in captured if DOCUMENT_TABLE in s]
        assert len(on_documents) == 1, on_documents  # la reprise, une fois
        columns = on_documents[0].split("SELECT", 1)[1].split("FROM", 1)[0]
        assert "tenant_id" in columns and "doctype" in columns and "data" not in columns and "name" not in columns
        crossing = [s for s in captured if DOCUMENT_TABLE not in s and "tenant_id =" not in s and not s.lstrip().upper().startswith("INSERT")]
        assert len(crossing) >= 2 and all("payload" not in s and ".data" not in s for s in crossing), crossing  # qui a quoi, puis la purge
        assert {s.lstrip().split()[0].upper() for s in crossing} == {"SELECT", "DELETE"}

    def test_the_scheduler_tables_are_outside_the_optional_rls_on_purpose(self):
        assert RLS_EXEMPT == {JOB_TABLE, PRESENCE_TABLE, SCHEDULE_TABLE}

    async def test_every_scheduler_table_carries_the_tenant_in_its_key(self, tables):
        for table in (tables.presence, tables.schedules):
            assert "tenant_id" in {c.name for c in table.primary_key.columns}


@pytest.mark.postgres
class TestUnderRls:
    """Avec la RLS optionnelle, un rôle ORDINAIRE : la table des documents ne laisse rien lire sans tenant. Les tâches récurrentes
    doivent tourner quand même — c'est pour cela que la présence et l'échéancier sont hors de la RLS."""

    @pytest.fixture
    async def rls_env(self, rls_app_url):
        adapter = AsyncSQLAdapter("rls", DatabaseConfig(name="rls", type="postgresql+aio", url=rls_app_url))
        await adapter.connect()
        tables = build_system_tables()
        await create_system_tables(adapter, tables)
        await ensure_rls(adapter, tables, "postgresql")
        try:
            yield Env(adapter, tables, rls=True)
        finally:
            await adapter.disconnect()

    async def test_organisations_are_learnt_from_their_writes(self, rls_env):
        env = rls_env
        await account(env, ACME)
        await account(env, GLOBEX)
        env.jobs.register("accounts", "daily", noop, daily_at="02:30")
        assert await env.jobs.schedule_once() == 2
        assert [len(await env.deposited(t)) for t in ("acme", "globex")] == [1, 1]
        assert await run(env.jobs) == 2

    async def test_the_takeover_is_not_attempted_and_not_marked(self, rls_env):
        """Sans tenant posé, la politique ne laisserait lire aucun document : on ne balaie pas pour rien, et on ne marque pas une
        reprise qui n'a pas eu lieu (une base dont on retire la RLS sera reprise alors)."""
        env = rls_env
        await account(env, ACME)
        async with env.db.session() as s:
            await s.execute(delete(env.tables.presence))
        jobs = env.process(env.documents())
        jobs.register("accounts", "daily", noop, daily_at="02:30")
        assert await jobs.schedule_once() == 0
        assert await env.rows(PRESENCE_TABLE) == []
        async with env.db.session() as s:  # et la table des documents reste fermée à qui ne pose pas de tenant
            assert (await s.execute(select(env.tables.documents.c.name))).all() == []
