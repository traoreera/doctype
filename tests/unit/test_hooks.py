"""Hooks : validate/before_save directs, after_save après commit, hooks `action:` avant transaction (D2, D4, D16, D21).

Critère 6 de 14 bis.8 : un `validate` qui lève annule la sauvegarde ; un `after_save` qui lève ne défait rien.
Critère 8 (niveau service) : un document trop gros pour un hook sandboxé échoue tout de suite, sans appeler le plugin.
"""

from __future__ import annotations

import asyncio

import pytest

from tests.conftest import make_ctx
from xcore_doctype import DocumentValidationError, HookEvent, HookRegistrationError, HookRegistry
from xcore_doctype.context import RequestContext
from xcore_doctype.document import DocumentService
from xcore_doctype.errors import DoctypeError, PermissionDenied
from xcore_doctype.hooks import ActionHooks, HookSettings
from xcore_doctype.registry import DocTypeRegistry
from xcore_doctype.schema import parse_definition

ORDER_MANAGER, ORDER_FR = "orders:order:manage", "orders:order:fr"
FR_MATCH = {"field": "country", "principal_attr": "country"}
ACME = make_ctx("acme", "alice", permissions=[ORDER_MANAGER])
GLOBEX = make_ctx("globex", "bob", permissions=[ORDER_MANAGER])


@pytest.fixture(autouse=True)
def _match_enabled(monkeypatch):
    """`match` est gelé en production ; ces tests s'en servent pour vérifier le périmètre APRÈS les hooks."""
    from xcore_doctype import schema

    monkeypatch.setattr(schema, "MATCH_ENABLED", True)


def fr_only() -> RequestContext:
    """Un utilisateur dont le périmètre est country == FR (attribut `country` du principal)."""
    return RequestContext("acme", {"sub": "rep", "permissions": [ORDER_FR], "user": {"country": "FR"}}, "http")


def order_def(**hooks):
    return parse_definition(
        {
            "schema_version": 1,
            "name": "Order",
            "fields": [
                {"fieldname": "title", "fieldtype": "Text", "required": True},
                {"fieldname": "total", "fieldtype": "Number", "default": 0},
                {"fieldname": "stamp", "fieldtype": "Text"},
                {"fieldname": "country", "fieldtype": "Text"},
            ],
            # Syntaxe compacte : une action liste les grants qui l'ouvrent ; ORDER_FR est borné par `match` (gelé en production).
            "permissions": {
                "read": [ORDER_MANAGER, {"grant": ORDER_FR, "match": FR_MATCH}],
                "write": [ORDER_MANAGER, {"grant": ORDER_FR, "match": FR_MATCH}],
                "create": [ORDER_MANAGER, {"grant": ORDER_FR, "match": FR_MATCH}],
                "delete": [ORDER_MANAGER],
            },
            "hooks": hooks,
        }
    )


class Log:
    def __init__(self):
        self.infos, self.errors = [], []

    def info(self, msg, **kw):
        self.infos.append((msg, kw))

    def error(self, msg, **kw):
        self.errors.append((msg, kw))


class Events:
    def __init__(self, fail=False):
        self.emitted, self.fail = [], fail

    async def emit(self, name, data=None, source=None, gather=True):
        if self.fail:
            raise RuntimeError("bus down")
        self.emitted.append((name, data, source))


class TrackingDB:
    """Compte les sessions ouvertes : D21 veut aucune connexion tenue pendant un appel de plugin."""

    def __init__(self, db):
        self._db, self.open = db, 0

    def session(self):
        outer = self

        class _Ctx:
            async def __aenter__(self_inner):
                self_inner._cm = outer._db.session()
                outer.open += 1
                return await self_inner._cm.__aenter__()

            async def __aexit__(self_inner, *exc):
                try:
                    return await self_inner._cm.__aexit__(*exc)
                finally:
                    outer.open -= 1

        return _Ctx()


class Env:
    """Un service avec des hooks, un journal, un bus et un appelant de plugins programmables."""

    def __init__(self, db, tables, definition, *, settings=None, events=None):
        self.tracking = TrackingDB(db)
        self.log, self.events = Log(), events or Events()
        self.hooks = HookRegistry()
        self.calls: list[tuple] = []  # (plugin, action, payload, kwargs, sessions_ouvertes)
        self.responses: list = []  # file de réponses pour le faux appelant
        self.settings = settings or HookSettings(timeout_seconds=1.0)
        reg = DocTypeRegistry()
        reg.register(definition)
        self.actions = ActionHooks(self._caller, "xcore-doctype", self.settings, self.log)
        self.svc = DocumentService(
            reg, self.tracking, tables, self.log,
            hooks=self.hooks, actions=self.actions, events=self.events, hook_settings=self.settings,
        )
        self.db, self.tables = db, tables
        self.trace: list[str] = []

    async def _caller(self, plugin, action, payload, **kw):
        self.calls.append((plugin, action, payload, kw, self.tracking.open))
        r = self.responses.pop(0) if self.responses else {"status": "ok"}
        if isinstance(r, BaseException):
            raise r
        if callable(r):
            return await r()
        return r

    def hook(self, name, fn=None, *, record=True):
        async def default(event: HookEvent):
            return None

        inner = fn or default

        async def wrapped(event: HookEvent):
            if record:
                self.trace.append(f"{name}@{self.tracking.open}")  # @1 = dans la transaction, @0 = hors transaction
            await inner(event)

        self.hooks.register(name, wrapped, owner="test")

    async def stored(self, ctx=ACME):
        return (await self.svc.list(RequestContext.system(ctx.tenant_id, "test"), "Order", limit=200))["items"]


@pytest.fixture
def make_env(db, tables):
    def make(**hooks):
        return Env(db, tables, order_def(**hooks))

    return make


# ── Critère 6 : un validate qui lève annule ─────────────────────────────────────────────────────


async def test_c6_validate_that_raises_cancels_a_create(make_env):
    env = make_env(validate=["o.check"], after_save=["o.notify"])

    async def refuse(event):
        raise DocumentValidationError("Plafond dépassé", details={"field": "total"})

    env.hook("o.check", refuse)
    env.hook("o.notify")
    with pytest.raises(DocumentValidationError) as e:
        await env.svc.create(ACME, "Order", {"title": "t", "total": 10**9})
    assert (e.value.status, e.value.code, e.value.message) == (422, "hook_rejected", "Plafond dépassé")
    assert await env.stored() == []  # rien d'écrit
    assert "o.notify" not in " ".join(env.trace) and env.events.emitted == []  # ni after_save ni événement


async def test_c6_validate_that_raises_cancels_an_update_and_keeps_the_version(make_env):
    env = make_env(validate=["o.check"])
    env.hook("o.check", _refuse_if(lambda e: e.data.get("title") == "bad"))
    doc = await env.svc.create(ACME, "Order", {"title": "good"})
    with pytest.raises(DocumentValidationError):
        await env.svc.update(ACME, "Order", doc["name"], {"title": "bad"}, 1)
    assert await env.svc.get(ACME, "Order", doc["name"]) == doc  # même version, mêmes données


def _refuse_if(predicate, message="refusé"):
    async def hook(event):
        if predicate(event):
            raise DocumentValidationError(message)

    return hook


async def test_c6_after_save_that_raises_does_not_undo_the_write(make_env):
    env = make_env(after_save=["o.notify"])

    async def boom(event):
        raise RuntimeError("smtp down: password=hunter2")

    env.hook("o.notify", boom)
    doc = await env.svc.create(ACME, "Order", {"title": "t"})  # ne lève pas
    assert (await env.svc.get(ACME, "Order", doc["name"]))["data"]["title"] == "t"
    assert any("after_save hook failed" in m for m, _ in env.log.errors)  # journalisé côté serveur
    assert [n for n, _, _ in env.events.emitted] == ["doctype.Order.after_save"]  # et l'événement part quand même


async def test_c6_after_save_runs_after_commit_and_outside_the_transaction(make_env):
    env = make_env(after_save=["o.notify"])
    seen = {}

    async def notify(event):
        seen["open_sessions"] = env.tracking.open
        seen["committed_row"] = await event.get("Order", event.name)  # lue par une AUTRE session : déjà commité

    env.hook("o.notify", notify, record=False)
    doc = await env.svc.create(ACME, "Order", {"title": "t"})
    assert seen["open_sessions"] == 0 and seen["committed_row"]["name"] == doc["name"]


async def test_after_save_timeout_is_logged_not_raised(db, tables):
    env = Env(db, tables, order_def(after_save=["o.slow"]), settings=HookSettings(timeout_seconds=0.05))

    async def slow(event):
        await asyncio.sleep(5)

    env.hook("o.slow", slow)
    await env.svc.create(ACME, "Order", {"title": "t"})
    assert any(kw.get("hook") == "o.slow" for _, kw in env.log.errors)


# ── before_save, revalidation, périmètre ────────────────────────────────────────────────────────


async def test_before_save_can_modify_the_data_and_the_result_is_stored(make_env):
    env = make_env(before_save=["o.stamp"])

    async def stamp(event):
        event.data["stamp"] = f"by-{event.ctx.user_id}"
        event.data["total"] = event.data.get("total", 0) * 2

    env.hook("o.stamp", stamp)
    doc = await env.svc.create(ACME, "Order", {"title": "t", "total": 5})
    assert doc["data"] == {"title": "t", "total": 10, "stamp": "by-alice"}


async def test_before_save_output_is_revalidated_by_the_schema(make_env):
    env = make_env(before_save=["o.bad"])

    async def bad(event):
        event.data["total"] = "beaucoup"  # un hook de confiance se trompe : le schéma fait foi

    env.hook("o.bad", bad)
    with pytest.raises(DoctypeError) as e:
        await env.svc.create(ACME, "Order", {"title": "t"})
    assert e.value.status == 422 and e.value.code == "validation_failed"
    assert await env.stored() == []


async def test_before_save_cannot_move_a_document_out_of_the_users_perimeter(make_env):
    env = make_env(before_save=["o.move"])

    async def move(event):
        event.data["country"] = "DE"

    env.hook("o.move", move)
    with pytest.raises(PermissionDenied):
        await env.svc.create(fr_only(), "Order", {"title": "t", "country": "FR"})
    assert await env.stored() == []


async def test_validate_receives_read_only_data(make_env):
    """Un validate ne doit pas pouvoir modifier la donnée : l'échec est bruyant, pas ignoré."""
    env = make_env(validate=["o.sneaky"])

    async def sneaky(event):
        event.data["total"] = 999

    env.hook("o.sneaky", sneaky)
    with pytest.raises(DoctypeError) as e:
        await env.svc.create(ACME, "Order", {"title": "t"})
    assert (e.value.status, e.value.code) == (500, "hook_error")
    assert await env.stored() == []


async def test_event_carries_old_values_and_a_tenant_scoped_getter(make_env):
    env = make_env(before_save=["o.inspect"])
    seen = []

    async def inspect(event):
        other = await event.get("Order", seen_other["name"])
        seen.append((event.old, event.event, event.doctype, other))

    env.hook("o.inspect", inspect)
    seen_other = {"name": "ghost"}
    created = await env.svc.create(ACME, "Order", {"title": "v1"})
    seen_other["name"] = created["name"]
    # GLOBEX met à jour un document qui n'est pas à lui : introuvable. Le getter ne voit pas non plus chez ACME.
    await env.svc.create(GLOBEX, "Order", {"title": "g"})
    assert seen[0][0] is None and seen[0][1:3] == ("before_save", "Order")  # création : pas d'ancienne valeur
    await env.svc.update(ACME, "Order", created["name"], {"title": "v2"}, 1)
    assert dict(seen[-1][0]) == {"title": "v1", "total": 0}  # mise à jour : valeur précédente
    assert seen[-1][3]["data"]["title"] == "v1"  # le getter lit le document courant du MÊME tenant
    # Le getter de GLOBEX ne lit pas ACME : le test précédent a créé le document de GLOBEX avec le nom d'ACME en cible.
    assert seen[1][3] is None


async def test_hook_order_validate_then_before_save_and_actions_first(make_env):
    env = make_env(validate=["o.v", "action:rules.v"], before_save=["o.b", "action:rules.b"])
    env.hook("o.v")
    env.hook("o.b")
    await env.svc.create(ACME, "Order", {"title": "t"})
    assert [c[1] for c in env.calls] == ["v", "b"]  # actions : validate puis before_save, avant la transaction
    assert env.trace == ["o.v@1", "o.b@1"]  # hooks de confiance : dans la transaction, validate puis before_save
    assert all(c[4] == 0 for c in env.calls)  # D21 : aucune session ouverte pendant un appel de plugin


async def test_a_hook_that_crashes_aborts_the_save_without_leaking_its_error(make_env):
    env = make_env(validate=["o.crash"])

    async def crash(event):
        raise RuntimeError("SELECT secret FROM t WHERE password='hunter2'")

    env.hook("o.crash", crash)
    with pytest.raises(DoctypeError) as e:
        await env.svc.create(ACME, "Order", {"title": "t"})
    assert (e.value.status, e.value.code) == (500, "hook_error") and "hunter2" not in repr(e.value.as_detail())
    assert any("hunter2" in repr(kw) for _, kw in env.log.errors)  # le détail va au journal serveur seulement
    assert await env.stored() == []


async def test_trusted_hook_timeout_rolls_back(db, tables):
    env = Env(db, tables, order_def(before_save=["o.slow"]), settings=HookSettings(timeout_seconds=0.05))

    async def slow(event):
        await asyncio.sleep(5)

    env.hook("o.slow", slow)
    with pytest.raises(DoctypeError) as e:
        await env.svc.create(ACME, "Order", {"title": "t"})
    assert (e.value.status, e.value.code) == (504, "hook_timeout")
    assert await env.stored() == [] and env.tracking.open == 0


async def test_system_context_runs_hooks_too(make_env):
    env = make_env(validate=["o.check"])
    env.hook("o.check", _refuse_if(lambda e: True, "même le système"))
    with pytest.raises(DocumentValidationError):
        await env.svc.create(RequestContext.system("acme", "import"), "Order", {"title": "t"})


# ── Événement ───────────────────────────────────────────────────────────────────────────────────


async def test_event_is_emitted_after_commit_without_document_content(make_env):
    env = make_env()
    doc = await env.svc.create(ACME, "Order", {"title": "secret title"})
    await env.svc.update(ACME, "Order", doc["name"], {"title": "x"}, 1)
    (n1, d1, s1), (n2, d2, _) = env.events.emitted
    assert n1 == n2 == "doctype.Order.after_save" and s1 == "xcore-doctype"
    assert d1 == {"doctype": "Order", "name": doc["name"], "tenant_id": "acme", "version": 1, "user": "alice", "created": True}
    assert (d2["version"], d2["created"]) == (2, False)
    assert "secret title" not in repr(env.events.emitted)  # le bus est global au processus, pas filtré par tenant


async def test_event_bus_failure_does_not_fail_the_save(db, tables):
    env = Env(db, tables, order_def(), events=Events(fail=True))
    assert (await env.svc.create(ACME, "Order", {"title": "t"}))["version"] == 1
    assert any("event failed" in m for m, _ in env.log.errors)


# ── Échec fermé (D16) ───────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("where", ["validate", "before_save", "after_save"])
async def test_missing_trusted_hook_refuses_the_save(make_env, where):
    """Après un reload d'xcore-doctype, le registre est vide : sauvegarder sans la validation promise serait pire que refuser."""
    env = make_env(**{where: ["sales.not_registered_yet"]})
    with pytest.raises(DoctypeError) as e:
        await env.svc.create(ACME, "Order", {"title": "t"})
    assert (e.value.status, e.value.code) == (503, "hook_unavailable")
    assert "sales.not_registered_yet" not in repr(e.value.as_detail())  # noms internes : journal seulement
    assert any("sales.not_registered_yet" in repr(kw) for _, kw in env.log.errors)
    assert await env.stored() == []


async def test_missing_hook_also_blocks_updates_but_not_reads_or_deletes(make_env):
    env = make_env(validate=["o.check"])
    env.hook("o.check")
    doc = await env.svc.create(ACME, "Order", {"title": "t"})
    env.hooks.clear()  # simule un reload
    with pytest.raises(DoctypeError) as e:
        await env.svc.update(ACME, "Order", doc["name"], {"title": "u"}, 1)
    assert e.value.code == "hook_unavailable"
    assert (await env.svc.get(ACME, "Order", doc["name"]))["data"]["title"] == "t"
    await env.svc.delete(ACME, "Order", doc["name"])


async def test_action_hook_without_a_caller_refuses_the_save(db, tables):
    reg = DocTypeRegistry()
    reg.register(order_def(validate=["action:rules.check"]))
    svc = DocumentService(reg, db, tables, Log())  # pas d'ActionHooks
    with pytest.raises(DoctypeError) as e:
        await svc.create(ACME, "Order", {"title": "t"})
    assert e.value.code == "hook_unavailable"


# ── Hooks `action:` (D4, D21) ───────────────────────────────────────────────────────────────────


async def test_action_hook_receives_explicit_tenant_principal_and_caller(make_env):
    env = make_env(before_save=["action:rules.check_credit"])
    ctx = make_ctx("acme", "alice", permissions=[ORDER_MANAGER], roles=["Sales Manager"])
    doc = await env.svc.create(ctx, "Order", {"title": "t", "total": 7})
    (plugin, action, payload, kw, sessions), = env.calls
    assert (plugin, action, sessions) == ("rules", "check_credit", 0)
    assert kw == {"caller": "xcore-doctype", "tenant_id": "acme", "principal": ctx.principal}  # jamais les ContextVar
    assert payload == {"event": "before_save", "doctype": "Order", "name": doc["name"], "doc": {"title": "t", "total": 7},
                       "old": None, "user": "alice", "roles": ["Sales Manager"]}


async def test_action_hook_update_payload_includes_the_old_document(make_env):
    env = make_env(validate=["action:rules.check"])
    doc = await env.svc.create(ACME, "Order", {"title": "v1"})
    env.calls.clear()
    await env.svc.update(ACME, "Order", doc["name"], {"title": "v2"}, 1)
    assert env.calls[0][2]["old"] == {"title": "v1", "total": 0} and env.calls[0][2]["doc"] == {"title": "v2", "total": 0}


async def test_action_hook_can_modify_the_document_and_the_result_is_revalidated(make_env):
    env = make_env(before_save=["action:rules.enrich"])
    env.responses.append({"status": "ok", "doc": {"title": "t", "total": 42, "stamp": "from-sandbox"}})
    doc = await env.svc.create(ACME, "Order", {"title": "t"})
    assert doc["data"] == {"title": "t", "total": 42, "stamp": "from-sandbox"}


async def test_action_hook_ok_without_doc_leaves_the_document_unchanged(make_env):
    env = make_env(validate=["action:rules.check"])
    env.responses.append({"status": "ok"})
    assert (await env.svc.create(ACME, "Order", {"title": "t"}))["data"] == {"title": "t", "total": 0}


@pytest.mark.parametrize(
    "returned",
    [
        {"title": "t", "owner": "mallory"},  # champ système : refusé par le schéma
        {"title": "t", "unknown": 1},
        {"title": "t", "total": "beaucoup"},
        {"title": "t", "total": float("nan")},
        {},  # champ requis supprimé
    ],
)
async def test_untrusted_hook_output_is_validated_like_client_input(make_env, returned):
    env = make_env(before_save=["action:rules.evil"])
    env.responses.append({"status": "ok", "doc": returned})
    with pytest.raises(DoctypeError) as e:
        await env.svc.create(ACME, "Order", {"title": "t"})
    assert (e.value.status, e.value.code) == (502, "hook_invalid_result")
    assert await env.stored() == []


async def test_action_hook_output_cannot_widen_beyond_the_users_perimeter(make_env):
    env = make_env(before_save=["action:rules.reroute"])
    env.responses.append({"status": "ok", "doc": {"title": "t", "country": "DE"}})
    with pytest.raises(PermissionDenied):
        await env.svc.create(fr_only(), "Order", {"title": "t", "country": "FR"})


async def test_action_hook_rejection_is_422_with_the_hooks_message(make_env):
    env = make_env(before_save=["action:rules.check_credit"])
    env.responses.append({"status": "error", "code": "credit_exceeded", "msg": "Crédit dépassé de 40 €"})
    with pytest.raises(DoctypeError) as e:
        await env.svc.create(ACME, "Order", {"title": "t"})
    assert (e.value.status, e.value.code, e.value.message) == (422, "hook_rejected", "Crédit dépassé de 40 €")
    assert e.value.details == {"hook": "action:rules.check_credit", "code": "credit_exceeded"}
    assert await env.stored() == []


async def test_action_hook_rejection_message_is_bounded(make_env):
    env = make_env(before_save=["action:rules.noisy"])
    env.responses.append({"status": "error", "msg": "x" * 100_000})
    with pytest.raises(DoctypeError) as e:
        await env.svc.create(ACME, "Order", {"title": "t"})
    assert len(e.value.message) == 500


@pytest.mark.parametrize("code", ["permission_denied", "ipc_denied", "not_found", "unknown_action", "handler_error",
                                  "all_retries_failed", "rate_limit_exceeded", "action_permission_denied"])
async def test_infrastructure_failures_are_503_and_never_expose_the_plugins_message(make_env, code):
    """Un hook mal déployé (permissions, allowed_callers, crash) n'est pas une erreur de saisie de l'utilisateur."""
    env = make_env(before_save=["action:rules.check"])
    env.responses.append({"status": "error", "code": code, "msg": "SELECT * FROM secrets -- /srv/plugins/rules/src/main.py"})
    with pytest.raises(DoctypeError) as e:
        await env.svc.create(ACME, "Order", {"title": "t"})
    assert (e.value.status, e.value.code) == (503, "hook_unavailable")
    assert "secrets" not in repr(e.value.as_detail()) and "/srv" not in repr(e.value.as_detail())
    assert await env.stored() == []


async def test_action_hook_timeout_reported_by_xcore_or_by_us(db, tables):
    env = Env(db, tables, order_def(before_save=["action:rules.slow"]), settings=HookSettings(timeout_seconds=0.05))

    async def never():
        await asyncio.sleep(5)

    env.responses.append(never)
    with pytest.raises(DoctypeError) as ours:
        await env.svc.create(ACME, "Order", {"title": "t"})
    env.responses.append({"status": "error", "code": "timeout", "msg": "Timeout après 10s"})
    with pytest.raises(DoctypeError) as theirs:
        await env.svc.create(ACME, "Order", {"title": "t"})
    assert (ours.value.status, ours.value.code) == (theirs.value.status, theirs.value.code) == (504, "hook_timeout")


async def test_action_hook_call_exception_is_503_and_logged(make_env):
    env = make_env(before_save=["action:rules.check"])
    env.responses.append(RuntimeError("connection reset: token=abc123"))
    with pytest.raises(DoctypeError) as e:
        await env.svc.create(ACME, "Order", {"title": "t"})
    assert (e.value.status, e.value.code) == (503, "hook_unavailable") and "abc123" not in repr(e.value.as_detail())


@pytest.mark.parametrize("bad", ["ok", None, ["ok"], {"status": "weird"}, {"status": "ok", "doc": "x"}, {"status": "ok", "doc": [1]}])
async def test_malformed_action_hook_responses_fail_closed(make_env, bad):
    env = make_env(before_save=["action:rules.check"])
    env.responses.append(bad)
    with pytest.raises(DoctypeError) as e:
        await env.svc.create(ACME, "Order", {"title": "t"})
    assert (e.value.status, e.value.code) == (502, "hook_invalid_result")
    assert await env.stored() == []


# ── Critère 8 (niveau service) : document trop gros ─────────────────────────────────────────────


async def test_c8_oversized_document_fails_immediately_without_calling_the_plugin(make_env):
    env = make_env(before_save=["action:rules.check"])
    with pytest.raises(DoctypeError) as e:
        await env.svc.create(ACME, "Order", {"title": "x" * 70_000})
    assert (e.value.status, e.value.code) == (413, "hook_payload_too_large")
    assert env.calls == []  # jamais envoyé : pas d'attente du timeout, pas de worker recyclé
    assert await env.stored() == []


async def test_c8_the_limit_counts_the_wire_size_with_escaped_characters(make_env):
    """Le canal IPC écrit en ASCII échappé : « é » pèse 6 octets sur la ligne, pas 2."""
    env = make_env(before_save=["action:rules.check"])
    with pytest.raises(DoctypeError) as e:
        await env.svc.create(ACME, "Order", {"title": "é" * 12_000})  # 24 Ko en UTF-8, ~72 Ko échappé
    assert e.value.code == "hook_payload_too_large" and env.calls == []


async def test_c8_a_document_just_under_the_limit_goes_through(make_env):
    env = make_env(before_save=["action:rules.check"])
    await env.svc.create(ACME, "Order", {"title": "x" * 50_000})
    assert len(env.calls) == 1


async def test_the_oversize_rule_applies_to_the_old_document_too(make_env):
    """`old` double la charge utile d'une mise à jour : la limite s'applique au total envoyé."""
    env = make_env(before_save=["action:rules.check"])
    doc = await env.svc.create(ACME, "Order", {"title": "x" * 40_000})
    with pytest.raises(DoctypeError) as e:
        await env.svc.update(ACME, "Order", doc["name"], {"title": "y" * 40_000}, 1)
    assert e.value.code == "hook_payload_too_large"
    assert (await env.svc.get(ACME, "Order", doc["name"]))["data"]["title"] == "x" * 40_000


# ── Registre ────────────────────────────────────────────────────────────────────────────────────


class TestHookRegistry:
    async def hook(self, event): ...

    def test_register_and_lookup(self):
        r, fn = HookRegistry(), self.hook  # une méthode liée est recréée à chaque accès : on garde la même
        r.register("sales.check", fn, owner="sales")
        assert r.get("sales.check") is fn and r.names() == ["sales.check"] and r.get("absent") is None

    @pytest.mark.parametrize("name", ["", "1abc", "a..b", "a b", "../x", "a;b", "action:p.a", "os.system('x')", None, 5])
    def test_invalid_names_rejected(self, name):
        with pytest.raises(HookRegistrationError):
            HookRegistry().register(name, self.hook)

    def test_blocking_functions_rejected(self):
        with pytest.raises(HookRegistrationError):
            HookRegistry().register("a.b", lambda event: None)

    def test_duplicate_rejected_and_unregister_owner(self):
        r = HookRegistry()
        r.register("a.b", self.hook, owner="p1")
        r.register("a.c", self.hook, owner="p1")
        r.register("a.d", self.hook, owner="p2")
        with pytest.raises(HookRegistrationError, match="p1"):
            r.register("a.b", self.hook, owner="p2")
        assert r.unregister_owner("p1") == 2 and r.names() == ["a.d"]
        r.unregister("a.d")
        r.unregister("never.registered")  # sans effet
        assert r.names() == []


class TestSettings:
    def test_defaults_leave_headroom_under_the_64kib_worker_limit(self):
        assert HookSettings().max_payload_bytes < 65_536

    @pytest.mark.parametrize("block", [{"hook_timeout_seconds": 0}, {"hook_timeout_seconds": True}, {"hook_timeout_seconds": "5"},
                                       {"hook_timeout_seconds": 1000}, {"hook_max_payload_bytes": 70_000},
                                       {"hook_max_payload_bytes": 5.5}, {"hook_max_payload_bytes": True}])
    def test_rejects_bad_values(self, block):
        from xcore_doctype.errors import ConfigurationError

        with pytest.raises(ConfigurationError):
            HookSettings.from_plugin_config({"doctype": block})

    def test_reads_values(self):
        s = HookSettings.from_plugin_config({"doctype": {"hook_timeout_seconds": 2, "hook_max_payload_bytes": 10_000}})
        assert (s.timeout_seconds, s.max_payload_bytes) == (2.0, 10_000)
