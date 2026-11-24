"""Tâches de fond fournies par le moteur, avec reprise (traoreera/doctype#71).

Le cas d'origine : installer le plan comptable d'une société dépasse le délai d'un hook. L'ERP lançait lui-même une tâche, et
si le serveur s'arrêtait au milieu il fallait rouvrir la société pour reprendre. Ce qui se prouve ici, contre une vraie base :
la clé d'unicité tranchée par la base, l'état persistant, la reprise après un arrêt (bail expiré) avec des essais bornés,
le contexte système du module, et le dépôt DANS la transaction d'un hook.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event

from tests.conftest import make_ctx
from xcore_doctype.context import RequestContext
from xcore_doctype.db.rls import RLS_EXEMPT
from xcore_doctype.db.tables import JOB_TABLE
from xcore_doctype.document import DocumentService
from xcore_doctype.errors import DoctypeError, DocumentValidationError, NotFound, PermissionDenied, ValidationFailed
from xcore_doctype.hooks import HookRegistry
from xcore_doctype.jobs import DONE, FAILED, MAX_PAYLOAD_BYTES, PENDING, RUNNING, JobService
from xcore_doctype.registry import DocTypeRegistry
from xcore_doctype.schema import parse_definition
from xcore_doctype.sealing import Sealer
from xcore_doctype.writer import DocumentWriters

ACCOUNT = {
    "schema_version": 1, "name": "Account", "module": "accounts",
    "fields": [{"fieldname": "code", "fieldtype": "Text", "required": True, "unique": True}, {"fieldname": "company", "fieldtype": "Text"}],
}
COMPANY = {
    "schema_version": 1, "name": "Company", "module": "accounts", "submittable": True,
    "hooks": {"on_submit": ["accounts.open"]},
    "fields": [{"fieldname": "title", "fieldtype": "Text", "required": True}, {"fieldname": "refuse", "fieldtype": "Boolean"}],
}
ALL = ["accounts:account:read", "accounts:account:write", "accounts:company:read", "accounts:company:write", "accounts:company:submit"]
ALICE = make_ctx("acme", "alice", permissions=ALL)
BOB = make_ctx("acme", "bob", permissions=[])
ADMIN = make_ctx("acme", "carol", permissions=["accounts:company:write"])
EVE = make_ctx("globex", "eve", permissions=ALL)


class Log:
    def __init__(self):
        self.lines: list[tuple[str, dict]] = []

    def info(self, m, **k): self.lines.append((m, k))
    def warning(self, m, **k): self.lines.append((m, k))
    def error(self, m, **k): self.lines.append((m, k))
    def debug(self, m, **k): pass


class Clock:
    """Une horloge qu'on avance : la reprise se prouve sans attendre un vrai bail."""

    def __init__(self):
        self.now = datetime(2026, 11, 24, 9, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def forward(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


@pytest.fixture
def env(db, tables):
    reg, hooks, log, clock = DocTypeRegistry(), HookRegistry(), Log(), Clock()
    for raw in (ACCOUNT, COMPANY):
        reg.register(parse_definition(raw))
    reg.validate_links()
    docs = DocumentService(reg, db, tables, log, hooks=hooks, sealer=Sealer("k" * 32))
    writers = DocumentWriters(docs, settings=None)  # type: ignore[arg-type]

    def service(**kw) -> JobService:
        return JobService(db, tables, log, writers=writers, clock=clock, backoff_seconds=30, **kw)

    jobs = service()
    docs.jobs = jobs
    return type("Env", (), {"db": db, "tables": tables, "docs": docs, "hooks": hooks, "jobs": jobs, "log": log, "clock": clock, "service": staticmethod(service)})


async def run(jobs: JobService) -> int:
    started = await jobs.run_once()
    await jobs.drain()
    return started


class TestTheOriginalCase:
    async def test_a_long_installation_runs_in_the_background_under_the_module_system_context(self, env):
        seen = {}

        async def install_chart(job):
            seen["attempt"], seen["system"] = job.attempt, job.writer.context.is_system
            for i, code in enumerate(["101", "411", "512"]):
                await job.writer.ensure("Account", {"code": code, "company": job.payload["company"]}, unique="code")
                await job.progress(f"{i + 1}/3 comptes")

        assert env.jobs.register("accounts", "install_chart", install_chart) == "accounts.install_chart"
        job = await env.jobs.enqueue("acme", "accounts.install_chart", {"company": "Kounandy"}, key="chart:Kounandy", owner="alice")
        assert job["status"] == PENDING and job["created"] is True and "payload" not in job
        assert await run(env.jobs) == 1
        done = await env.jobs.get(ALICE, job["id"])
        assert (done["status"], done["attempts"], done["message"]) == (DONE, 1, None)
        assert seen == {"attempt": 1, "system": True}
        page = await env.docs.list(ALICE, "Account")
        assert sorted(d["data"]["code"] for d in page["items"]) == ["101", "411", "512"]
        history = await env.docs.history(ALICE, "Account", page["items"][0]["name"])
        assert history["items"][0]["user"] == "system:job:accounts.install_chart"

    async def test_a_job_writes_only_the_doctypes_of_its_module(self, env):
        async def poach(job):
            await job.writer.create("Account", {"code": "x"})

        env.jobs.register("payroll", "poach", poach, max_attempts=1)
        job = await env.jobs.enqueue("acme", "payroll.poach", owner="alice")
        await run(env.jobs)
        failed = await env.jobs.get(ALICE, job["id"])
        assert failed["status"] == FAILED and "module" in failed["message"]


class TestOneKeyOneActiveJob:
    async def test_a_second_enqueue_returns_the_running_one(self, env):
        env.jobs.register("accounts", "install_chart", _noop)
        first = await env.jobs.enqueue("acme", "accounts.install_chart", key="chart:K", owner="alice")
        second = await env.jobs.enqueue("acme", "accounts.install_chart", key="chart:K", owner="bob")
        assert second["id"] == first["id"] and second["created"] is False

    async def test_the_database_settles_concurrent_enqueues(self, env):
        env.jobs.register("accounts", "install_chart", _noop)
        made = await asyncio.gather(*[env.jobs.enqueue("acme", "accounts.install_chart", key="chart:K") for _ in range(5)])
        assert len({j["id"] for j in made}) == 1 and sum(j["created"] for j in made) == 1

    async def test_the_key_is_free_again_once_the_job_is_over(self, env):
        env.jobs.register("accounts", "install_chart", _noop)
        first = await env.jobs.enqueue("acme", "accounts.install_chart", key="chart:K")
        await run(env.jobs)
        again = await env.jobs.enqueue("acme", "accounts.install_chart", key="chart:K")
        assert again["created"] is True and again["id"] != first["id"]

    async def test_keys_are_per_organisation_and_per_job(self, env):
        env.jobs.register("accounts", "install_chart", _noop)
        env.jobs.register("accounts", "close_year", _noop)
        a = await env.jobs.enqueue("acme", "accounts.install_chart", key="K")
        b = await env.jobs.enqueue("globex", "accounts.install_chart", key="K")
        c = await env.jobs.enqueue("acme", "accounts.close_year", key="K")
        assert len({a["id"], b["id"], c["id"]}) == 3 and all(j["created"] for j in (a, b, c))

    async def test_without_a_key_every_enqueue_is_a_job(self, env):
        env.jobs.register("accounts", "mail", _noop)
        a, b = await env.jobs.enqueue("acme", "accounts.mail"), await env.jobs.enqueue("acme", "accounts.mail")
        assert a["id"] != b["id"]


class TestResume:
    async def test_a_server_that_stops_mid_job_leaves_it_to_the_next_start(self, env):
        """Le défaut d'origine : l'échec était seulement journalisé, il fallait rouvrir la société à la main."""
        runs: list[int] = []

        async def install(job):
            runs.append(job.attempt)

        env.jobs.register("accounts", "install_chart", install)
        job = await env.jobs.enqueue("acme", "accounts.install_chart", key="chart:K", owner="alice")
        # Un processus prend la tâche… et meurt avec elle : aucune écriture de fin.
        dead = env.service(lease_seconds=120)
        dead.register("accounts", "install_chart", install)
        assert await dead.store.claim("acme", job["id"], dead._owner_id, 120) is not None
        assert (await env.jobs.get(ALICE, job["id"]))["status"] == RUNNING
        assert await run(env.jobs) == 0  # son bail tient encore : personne ne la lui prend
        env.clock.forward(121)  # le bail expire : le démarrage suivant la reprend
        assert await run(env.jobs) == 1
        resumed = await env.jobs.get(ALICE, job["id"])
        assert resumed["status"] == DONE and resumed["attempts"] == 2 and runs == [2]

    async def test_the_dead_process_cannot_write_over_the_one_that_resumed(self, env):
        env.jobs.register("accounts", "install_chart", _noop)
        job = await env.jobs.enqueue("acme", "accounts.install_chart")
        store = env.jobs.store
        await store.claim("acme", job["id"], "dead-process", 10)
        env.clock.forward(11)
        await store.claim("acme", job["id"], "survivor", 120)
        assert await store.finish("acme", job["id"], "dead-process", status=FAILED, message="trop tard") is False
        assert await store.note("acme", job["id"], "dead-process", "x") is False
        assert (await store.get("acme", job["id"]))["locked_by"] == "survivor"

    async def test_resumes_are_bounded(self, env):
        """Une tâche qui fait tomber le serveur à chaque essai ne tourne pas en boucle."""
        env.jobs.register("accounts", "install_chart", _noop, max_attempts=2)
        job = await env.jobs.enqueue("acme", "accounts.install_chart", key="K")
        for _ in range(2):  # deux processus la prennent et meurent
            assert await env.jobs.store.claim("acme", job["id"], "crashing", 10) is not None
            env.clock.forward(11)
        await run(env.jobs)
        gone = await env.jobs.store.get("acme", job["id"])
        assert gone["status"] == FAILED and "abandonnée après 2 essais" in gone["message"] and gone["active_key"] is None

    async def test_a_long_job_keeps_its_lease(self, env, db, tables):
        """Le bail est RENOUVELÉ pendant l'exécution : une tâche longue n'est pas « reprise » par un autre processus."""
        log = Log()
        slow = JobService(db, tables, log, lease_seconds=1.0)  # horloge réelle : le battement se mesure
        other = JobService(db, tables, log, lease_seconds=1.0)
        started, release = asyncio.Event(), asyncio.Event()

        async def long_job(job):
            started.set()
            await release.wait()

        for svc in (slow, other):
            svc.register("accounts", "long", long_job)
        job = await slow.enqueue("acme", "accounts.long")
        assert await slow.run_once() == 1
        await started.wait()
        await asyncio.sleep(1.6)  # plus d'un bail : sans battement, `other` la prendrait
        assert await other.run_once() == 0
        release.set()
        await slow.drain()
        assert (await slow.store.get("acme", job["id"]))["status"] == DONE

    async def test_a_clean_shutdown_gives_the_job_back_without_counting_the_attempt(self, env):
        started = asyncio.Event()

        async def long_job(job):
            started.set()
            await asyncio.sleep(60)

        env.jobs.register("accounts", "long", long_job)
        job = await env.jobs.enqueue("acme", "accounts.long", key="K")
        await env.jobs.run_once()
        await started.wait()
        await env.jobs.close()
        back = await env.jobs.store.get("acme", job["id"])
        assert (back["status"], back["attempts"], back["active_key"]) == (PENDING, 0, "K")

    async def test_a_job_waits_for_its_plugin(self, env):
        env.jobs.register("accounts", "install_chart", _noop)
        job = await env.jobs.enqueue("acme", "accounts.install_chart")
        env.jobs.unregister_owner("accounts")
        assert await run(env.jobs) == 0
        env.jobs.register("accounts", "install_chart", _noop)
        assert await run(env.jobs) == 1
        assert (await env.jobs.store.get("acme", job["id"]))["status"] == DONE


class TestFailures:
    async def test_a_failing_attempt_is_retried_later_then_given_up(self, env):
        async def broken(job):
            raise RuntimeError("serveur comptable injoignable")

        env.jobs.register("accounts", "sync", broken, max_attempts=3)
        job = await env.jobs.enqueue("acme", "accounts.sync", key="K", owner="alice")
        await run(env.jobs)
        waiting = await env.jobs.get(ALICE, job["id"])
        assert (waiting["status"], waiting["attempts"], waiting["message"]) == (PENDING, 1, "serveur comptable injoignable")
        assert await run(env.jobs) == 0  # recul : pas avant 30 s
        env.clock.forward(31)
        await run(env.jobs)
        assert await run(env.jobs) == 0  # recul doublé : 60 s
        env.clock.forward(61)
        await run(env.jobs)
        dead = await env.jobs.get(ALICE, job["id"])
        assert (dead["status"], dead["attempts"]) == (FAILED, 3) and dead["finished_at"]
        assert any(m == "job failed, giving up" for m, _ in env.log.lines)

    async def test_a_job_that_never_returns_is_cut(self, env):
        async def stuck(job):
            await asyncio.sleep(60)

        env.jobs.register("accounts", "stuck", stuck, max_attempts=1, timeout=0.05)
        job = await env.jobs.enqueue("acme", "accounts.stuck")
        await run(env.jobs)
        assert (await env.jobs.store.get("acme", job["id"]))["message"] == "délai dépassé"

    async def test_a_failed_job_is_relaunched_by_whoever_holds_its_grant(self, env):
        calls: list[int] = []

        async def flaky(job):
            calls.append(job.attempt)
            if len(calls) == 1:
                raise RuntimeError("une fois")

        env.jobs.register("accounts", "flaky", flaky, max_attempts=1, grant="accounts:company:write")
        job = await env.jobs.enqueue("acme", "accounts.flaky", key="K", owner="alice")
        await run(env.jobs)
        with pytest.raises(PermissionDenied):
            await env.jobs.retry(make_ctx("acme", "alice", permissions=[]), job["id"])  # son auteur la voit, sans pouvoir la relancer
        assert (await env.jobs.retry(ADMIN, job["id"]))["status"] == PENDING
        await run(env.jobs)
        assert (await env.jobs.get(ADMIN, job["id"]))["status"] == DONE and calls == [1, 1]
        with pytest.raises(DoctypeError) as e:
            await env.jobs.retry(ADMIN, job["id"])
        assert e.value.code == "job_not_failed"

    async def test_relaunching_is_refused_while_another_job_holds_the_key(self, env):
        async def broken(job):
            raise RuntimeError("x")

        env.jobs.register("accounts", "sync", broken, max_attempts=1, grant="accounts:company:write")
        failed = await env.jobs.enqueue("acme", "accounts.sync", key="K")
        await run(env.jobs)
        await env.jobs.enqueue("acme", "accounts.sync", key="K")  # une nouvelle a pris la clé
        with pytest.raises(DoctypeError) as e:
            await env.jobs.retry(ADMIN, failed["id"])
        assert e.value.code == "job_already_active"


class TestInsideAHookTransaction:
    """`event.tx.enqueue` : la file d'envoi durable que #59 laissait ouverte."""

    def _hook(self, env, sent):
        async def send_welcome(job):
            sent.append(job.payload["company"])

        async def on_submit(event):
            await event.tx.enqueue("accounts.send_welcome", {"company": event.name}, key=f"welcome:{event.name}")
            if event.data.get("refuse"):
                raise DocumentValidationError("une écriture ultérieure échoue")

        env.jobs.register("accounts", "send_welcome", send_welcome)
        env.hooks.register("accounts.open", on_submit, owner="accounts")

    async def test_a_committed_submission_leaves_the_job(self, env):
        sent: list[str] = []
        self._hook(env, sent)
        doc = await env.docs.create(ALICE, "Company", {"title": "Kounandy"})
        await env.docs.submit(ALICE, "Company", doc["name"], version=doc["version"])
        listed = await env.jobs.list(ALICE, name="accounts.send_welcome")
        assert [j["status"] for j in listed["items"]] == [PENDING] and listed["items"][0]["owner"] == "alice"
        await run(env.jobs)
        assert sent == [doc["name"]]

    async def test_a_refused_submission_leaves_nothing_to_send(self, env):
        """Le cas du ticket #59 : l'écriture suivante échoue, le document reste brouillon — et le courriel ne part pas."""
        sent: list[str] = []
        self._hook(env, sent)
        doc = await env.docs.create(ALICE, "Company", {"title": "Kounandy", "refuse": True})
        with pytest.raises(DocumentValidationError):
            await env.docs.submit(ALICE, "Company", doc["name"], version=doc["version"])
        assert (await env.jobs.list(ALICE))["items"] == []
        assert await run(env.jobs) == 0 and sent == []

    async def test_an_undeclared_job_refuses_the_submission(self, env):
        async def on_submit(event):
            await event.tx.enqueue("accounts.nowhere", {})

        env.hooks.register("accounts.open", on_submit, owner="accounts")
        doc = await env.docs.create(ALICE, "Company", {"title": "K"})
        with pytest.raises(NotFound) as e:
            await env.docs.submit(ALICE, "Company", doc["name"], version=doc["version"])
        assert e.value.code == "job_unknown"


class TestWhoSeesWhat:
    async def _three(self, env):
        env.jobs.register("accounts", "install_chart", _noop, grant="accounts:company:write")
        env.jobs.register("accounts", "private", _noop)
        mine = await env.jobs.enqueue("acme", "accounts.install_chart", {"secret": "s3cret"}, owner="alice")
        private = await env.jobs.enqueue("acme", "accounts.private", owner="alice")
        theirs = await env.jobs.enqueue("globex", "accounts.install_chart", owner="eve")
        return mine, private, theirs

    async def test_the_author_sees_her_jobs_and_never_a_payload(self, env):
        mine, private, _ = await self._three(env)
        listed = (await env.jobs.list(ALICE))["items"]
        assert {j["id"] for j in listed} == {mine["id"], private["id"]}
        assert all("payload" not in j and "s3cret" not in str(j) for j in listed)

    async def test_a_grant_holder_sees_the_jobs_of_that_kind(self, env):
        mine, _private, _ = await self._three(env)
        assert [j["id"] for j in (await env.jobs.list(ADMIN))["items"]] == [mine["id"]]

    async def test_anybody_else_sees_nothing_and_a_hidden_job_is_not_found(self, env):
        mine, _, _ = await self._three(env)
        assert (await env.jobs.list(BOB))["items"] == []
        with pytest.raises(NotFound):
            await env.jobs.get(BOB, mine["id"])

    async def test_another_organisation_sees_nothing(self, env):
        mine, _, theirs = await self._three(env)
        assert [j["id"] for j in (await env.jobs.list(EVE))["items"]] == [theirs["id"]]
        with pytest.raises(NotFound):
            await env.jobs.get(EVE, mine["id"])
        with pytest.raises(NotFound):
            await env.jobs.retry(EVE, mine["id"])

    async def test_filters(self, env):
        mine, _, _ = await self._three(env)
        await run(env.jobs)
        assert [j["id"] for j in (await env.jobs.list(ALICE, name="accounts.install_chart", status=DONE))["items"]] == [mine["id"]]
        with pytest.raises(ValidationFailed):
            await env.jobs.list(ALICE, status="perdu")

    async def test_the_system_context_sees_everything_of_its_tenant(self, env):
        await self._three(env)
        assert len((await env.jobs.list(RequestContext.system("acme", "supervision")))["items"]) == 2


class TestEveryStatementCarriesTheTenant:
    """Le garde-fou de `test_query_leak.py`, pour `JobStore` : le SQL émis est capturé."""

    async def test_only_the_discovery_query_crosses_tenants(self, env):
        env.jobs.register("accounts", "install_chart", _fail_once(), max_attempts=2, grant="accounts:company:write")
        captured: list[tuple[str, dict]] = []

        def listen(conn, cursor, statement, parameters, context, executemany):
            if JOB_TABLE in statement:
                captured.append((statement, parameters if isinstance(parameters, (dict, tuple, list)) else {}))

        engine = env.db.engine.sync_engine
        event.listen(engine, "before_cursor_execute", listen)
        try:
            job = await env.jobs.enqueue("acme", "accounts.install_chart", key="K", owner="alice")
            await env.jobs.enqueue("acme", "accounts.install_chart", key="K")
            await run(env.jobs)
            env.clock.forward(31)
            await run(env.jobs)
            await env.jobs.list(ALICE)
            await env.jobs.get(ALICE, job["id"])
        finally:
            event.remove(engine, "before_cursor_execute", listen)
        assert len(captured) > 8
        unscoped = [s for s, _ in captured if "tenant_id =" not in s and "tenant_id=" not in s and not s.lstrip().upper().startswith("INSERT")]
        assert len(unscoped) >= 1 and all("SELECT" in s.upper() and "payload" not in s for s in unscoped)
        for statement, params in captured:
            values = list(params.values()) if isinstance(params, dict) else list(params)
            assert "globex" not in values, statement

    def test_the_job_table_is_outside_the_optional_rls_on_purpose(self):
        assert JOB_TABLE in RLS_EXEMPT


class TestDeclarations:
    def test_refusals(self, env):
        with pytest.raises(ValueError, match="coroutine"):
            env.jobs.register("accounts", "sync", lambda job: None)
        with pytest.raises(ValueError, match=r"\[a-z\]"):
            env.jobs.register("Accounts", "sync", _noop)
        with pytest.raises(ValueError, match="max_attempts"):
            env.jobs.register("accounts", "sync", _noop, max_attempts=0)
        env.jobs.register("accounts", "sync", _noop)
        with pytest.raises(ValueError, match="déjà déclarée"):
            env.jobs.register("accounts", "sync", _noop)

    async def test_an_enqueue_is_validated_at_once(self, env):
        env.jobs.register("accounts", "sync", _noop)
        with pytest.raises(NotFound):
            await env.jobs.enqueue("acme", "accounts.unknown")
        for bad in ({"payload": ["liste"]}, {"payload": {"x": object()}}, {"payload": {"x": "y" * MAX_PAYLOAD_BYTES}}, {"key": ""}, {"key": "k" * 256}):
            with pytest.raises(ValidationFailed) as e:
                await env.jobs.enqueue("acme", "accounts.sync", bad.get("payload"), key=bad.get("key"))
            assert e.value.code == "job_invalid"
        with pytest.raises(ValidationFailed):
            await env.jobs.enqueue("", "accounts.sync")

    async def test_the_background_loop_picks_jobs_up(self, env, db, tables):
        done = asyncio.Event()

        async def quick(job):
            done.set()

        jobs = JobService(db, tables, Log(), poll_seconds=30)
        jobs.register("accounts", "quick", quick)
        jobs.start()
        jobs.start()  # idempotent
        await asyncio.sleep(0.05)  # la boucle dort pour 30 s
        await jobs.enqueue("acme", "accounts.quick")  # le dépôt la réveille
        await asyncio.wait_for(done.wait(), timeout=5)
        await jobs.drain()
        await jobs.close()


async def _noop(job) -> None:
    return None


def _fail_once():
    state = {"n": 0}

    async def handler(job):
        state["n"] += 1
        if state["n"] == 1:
            raise RuntimeError("une fois")

    return handler
