"""Documents soumettables (palier 40 %, phase A) : brouillon -> soumis -> annulé -> amendé.

Ce qui est garanti et que ces tests défendent, chacun par une mutation qui le casse :
- un document soumis ou annulé est FIGÉ (ni modification ni suppression), y compris face à une course ;
- soumettre est idempotent : rejouée ou simultanée, la soumission ne passe qu'une fois et ses hooks ne tournent qu'une fois ;
- `on_submit` / `on_cancel` tournent DANS la transaction : un refus défait l'état ET ce que le hook avait écrit ;
- les droits `submit` / `cancel` sont des grants distincts de `write`, et n'existent que pour un DocType soumettable ;
- un amendement est une copie en brouillon d'un document annulé, une seule fois.
"""

from __future__ import annotations

import asyncio

import pytest

from tests.conftest import make_ctx
from xcore_doctype import DocumentValidationError
from xcore_doctype.document import DocumentService
from xcore_doctype.errors import (
    DoctypeError,
    DocumentLocked,
    InvalidDocStatus,
    NotFound,
    PermissionDenied,
    ValidationFailed,
    VersionConflict,
)
from xcore_doctype.hooks import HookRegistry
from xcore_doctype.registry import DocTypeRegistry
from xcore_doctype.schema import parse_definition

VOUCHER = {
    "schema_version": 1,
    "name": "Voucher",
    "module": "acct",
    "submittable": True,
    "fields": [
        {"fieldname": "title", "fieldtype": "Text", "required": True},
        {"fieldname": "amount", "fieldtype": "Number"},
    ],
    "hooks": {"on_submit": ["acct.post"], "on_cancel": ["acct.unpost"]},
}
# Ce que les hooks écrivent. Fermé à tout écrivain humain : seuls les hooks (donc les documents soumis) l'alimentent.
LEDGER = {
    "schema_version": 1,
    "name": "Ledger",
    "module": "acct",
    "track_changes": False,
    "fields": [
        {"fieldname": "voucher", "fieldtype": "Link", "options": "Voucher"},
        {"fieldname": "amount", "fieldtype": "Number"},
    ],
    "permissions": {"write": [], "create": [], "delete": []},
}
# Un compteur que les hooks LISENT puis ÉCRIVENT d'après ce qu'ils ont lu : le cas qui perd des mises à jour sans verrou.
COUNTER = {"schema_version": 1, "name": "Counter", "module": "acct", "track_changes": False, "fields": [{"fieldname": "n", "fieldtype": "Number"}]}
GRANTS = ["acct:voucher:read", "acct:voucher:write", "acct:voucher:delete", "acct:voucher:submit", "acct:voucher:cancel", "acct:ledger:read"]
ALICE = make_ctx("acme", "alice", permissions=GRANTS)
BOB = make_ctx("acme", "bob", permissions=GRANTS)
GLOBEX = make_ctx("globex", "gina", permissions=GRANTS)
WRITER = make_ctx("acme", "wendy", permissions=["acct:voucher:read", "acct:voucher:write", "acct:voucher:delete"])  # sans submit ni cancel


class Bus:
    def __init__(self):
        self.sent = []

    async def emit(self, topic, payload, source=None):
        self.sent.append((topic, payload, source))


class Log:
    def __init__(self):
        self.errors = []

    def info(self, *a, **k): ...
    def error(self, msg, **k):
        self.errors.append((msg, k))


class Env:
    """Le service, les hooks et ce qu'ils ont vu."""

    def __init__(self, db, tables):
        reg = DocTypeRegistry()
        reg.register(parse_definition(VOUCHER))
        reg.register(parse_definition(LEDGER))
        reg.register(parse_definition(COUNTER))
        self.hooks, self.bus, self.log = HookRegistry(), Bus(), Log()
        self.calls: list[tuple] = []
        self.refuse: str | None = None  # un message : `acct.post` refuse APRÈS avoir écrit
        self.hooks.register("acct.post", self.post)
        self.hooks.register("acct.unpost", self.unpost)
        self.svc = DocumentService(reg, db, tables, self.log, hooks=self.hooks, events=self.bus)

    async def post(self, event):
        self.calls.append(("post", event.name, event.event, dict(event.data)))
        await event.tx.create("Ledger", {"voucher": event.name, "amount": event.data.get("amount", 0)}, name=f"{event.name}.in")
        if self.refuse:
            raise DocumentValidationError(self.refuse)

    async def unpost(self, event):
        self.calls.append(("unpost", event.name, event.event, dict(event.data)))
        await event.tx.create("Ledger", {"voucher": event.name, "amount": -event.data.get("amount", 0)}, name=f"{event.name}.out")

    async def voucher(self, ctx=ALICE, **data):
        return await self.svc.create(ctx, "Voucher", {"title": "V", "amount": 10, **data})

    async def ledger(self, ctx=ALICE):
        return sorted(
            (d["name"], d["data"]["amount"]) for d in (await self.svc.list(ctx, "Ledger", limit=200))["items"]
        )


@pytest.fixture
def env(db, tables):
    return Env(db, tables)


class TestStateMachine:
    async def test_a_new_document_is_a_draft_and_the_client_cannot_choose_otherwise(self, env):
        doc = await env.voucher()
        assert (doc["docstatus"], doc["amended_from"]) == (0, None)
        with pytest.raises(ValidationFailed):  # `docstatus` n'est pas un champ : jamais accepté dans un payload
            await env.svc.create(ALICE, "Voucher", {"title": "x", "docstatus": 1})

    async def test_submit_freezes_the_document_and_bumps_the_version(self, env):
        doc = await env.voucher()
        done = await env.svc.submit(ALICE, "Voucher", doc["name"], doc["version"])
        assert (done["docstatus"], done["version"], done["data"]) == (1, doc["version"] + 1, doc["data"])
        assert (await env.svc.get(ALICE, "Voucher", doc["name"]))["docstatus"] == 1

    async def test_a_submitted_document_cannot_be_modified_or_deleted(self, env):
        doc = await env.voucher()
        done = await env.svc.submit(ALICE, "Voucher", doc["name"], 1)
        with pytest.raises(DocumentLocked) as e:
            await env.svc.update(ALICE, "Voucher", doc["name"], {"title": "autre"}, done["version"])
        assert e.value.code == "document_locked" and e.value.status == 409
        with pytest.raises(DocumentLocked):
            await env.svc.delete(ALICE, "Voucher", doc["name"])
        assert (await env.svc.get(ALICE, "Voucher", doc["name"]))["data"]["title"] == "V"

    async def test_a_cancelled_document_stays_frozen_and_is_never_deleted(self, env):
        doc = await env.voucher()
        s = await env.svc.submit(ALICE, "Voucher", doc["name"], 1)
        c = await env.svc.cancel(ALICE, "Voucher", doc["name"], s["version"])
        assert (c["docstatus"], c["version"]) == (2, 3)
        with pytest.raises(DocumentLocked):
            await env.svc.update(ALICE, "Voucher", doc["name"], {"title": "x"}, c["version"])
        with pytest.raises(DocumentLocked):
            await env.svc.delete(ALICE, "Voucher", doc["name"])

    async def test_a_draft_is_still_editable_and_deletable(self, env):
        doc = await env.voucher()
        upd = await env.svc.update(ALICE, "Voucher", doc["name"], {"title": "v2"}, 1)
        assert upd["version"] == 2
        await env.svc.delete(ALICE, "Voucher", doc["name"])
        with pytest.raises(NotFound):
            await env.svc.get(ALICE, "Voucher", doc["name"])

    async def test_the_transitions_only_go_forward_from_the_right_state(self, env):
        doc = await env.voucher()
        with pytest.raises(InvalidDocStatus):  # on n'annule pas un brouillon
            await env.svc.cancel(ALICE, "Voucher", doc["name"], 1)
        s = await env.svc.submit(ALICE, "Voucher", doc["name"], 1)
        with pytest.raises(InvalidDocStatus) as e:  # ni ne soumet deux fois
            await env.svc.submit(ALICE, "Voucher", doc["name"], s["version"])
        assert (e.value.code, e.value.status) == ("invalid_docstatus", 409)
        c = await env.svc.cancel(ALICE, "Voucher", doc["name"], s["version"])
        with pytest.raises(InvalidDocStatus):
            await env.svc.cancel(ALICE, "Voucher", doc["name"], c["version"])
        with pytest.raises(InvalidDocStatus):  # ni ne ressuscite un annulé : on l'amende
            await env.svc.submit(ALICE, "Voucher", doc["name"], c["version"])

    @pytest.mark.parametrize("bad", [None, 0, -1, True, "1", 1.0])
    async def test_the_version_is_required(self, env, bad):
        doc = await env.voucher()
        with pytest.raises(ValidationFailed) as e:
            await env.svc.submit(ALICE, "Voucher", doc["name"], bad)
        assert e.value.code == "version_required"

    async def test_a_stale_version_is_a_conflict(self, env):
        doc = await env.voucher()
        await env.svc.update(ALICE, "Voucher", doc["name"], {"title": "v2"}, 1)
        with pytest.raises(VersionConflict):
            await env.svc.submit(ALICE, "Voucher", doc["name"], 1)
        assert (await env.svc.get(ALICE, "Voucher", doc["name"]))["docstatus"] == 0

    async def test_a_doctype_that_is_not_submittable_refuses(self, env):
        with pytest.raises(ValidationFailed) as e:
            await env.svc.submit(ALICE, "Ledger", "x", 1)
        assert e.value.code == "not_submittable"
        for call in (env.svc.cancel(ALICE, "Ledger", "x", 1), env.svc.amend(ALICE, "Ledger", "x")):
            with pytest.raises(ValidationFailed):
                await call

    async def test_an_unknown_document_is_not_found(self, env):
        with pytest.raises(NotFound):
            await env.svc.submit(ALICE, "Voucher", "nope", 1)

    async def test_another_tenant_cannot_submit_it(self, env):
        doc = await env.voucher()
        with pytest.raises(NotFound):
            await env.svc.submit(GLOBEX, "Voucher", doc["name"], 1)
        assert (await env.svc.get(ALICE, "Voucher", doc["name"]))["docstatus"] == 0

    async def test_a_draft_that_no_longer_fits_its_definition_is_not_frozen(self, env, db, tables):
        """Un brouillon écrit sous une ancienne définition (champ devenu obligatoire) se corrige avant d'être figé."""
        old = {**VOUCHER, "hooks": {}, "fields": [{"fieldname": "title", "fieldtype": "Text"}, {"fieldname": "amount", "fieldtype": "Number"}]}
        r1 = DocTypeRegistry()
        r1.register(parse_definition(old))
        doc = await DocumentService(r1, db, tables, Log()).create(ALICE, "Voucher", {"amount": 5})  # sans titre : valide alors
        with pytest.raises(ValidationFailed) as e:
            await env.svc.submit(ALICE, "Voucher", doc["name"], 1)
        assert any(d["field"] == "title" for d in e.value.details)
        assert (await env.svc.get(ALICE, "Voucher", doc["name"]))["docstatus"] == 0 and env.calls == []


class TestHistory:
    async def test_submit_and_cancel_are_recorded_with_who(self, env):
        doc = await env.voucher()
        s = await env.svc.submit(ALICE, "Voucher", doc["name"], 1)
        await env.svc.cancel(BOB, "Voucher", doc["name"], s["version"])
        items = (await env.svc.history(ALICE, "Voucher", doc["name"]))["items"]
        assert [(i["action"], i["user"], i["version"]) for i in items] == [("cancel", "bob", 3), ("submit", "alice", 2), ("create", "alice", 1)]
        assert items[0]["changes"] == {"docstatus": {"old": 1, "new": 2}}
        assert items[1]["changes"] == {"docstatus": {"old": 0, "new": 1}}


class TestHooksRunInsideTheTransaction:
    async def test_on_submit_writes_what_it_posts_and_sees_the_frozen_document(self, env):
        doc = await env.voucher(amount=25)
        await env.svc.submit(ALICE, "Voucher", doc["name"], 1)
        assert env.calls == [("post", doc["name"], "on_submit", {"title": "V", "amount": 25})]
        assert await env.ledger() == [(f"{doc['name']}.in", 25)]

    async def test_a_refusal_undoes_the_state_and_everything_the_hook_wrote(self, env):
        doc = await env.voucher()
        env.refuse = "Stock insuffisant"
        with pytest.raises(DocumentValidationError) as e:
            await env.svc.submit(ALICE, "Voucher", doc["name"], 1)
        assert e.value.status == 422 and "Stock insuffisant" in e.value.message
        after = await env.svc.get(ALICE, "Voucher", doc["name"])
        assert (after["docstatus"], after["version"]) == (0, 1)  # toujours un brouillon, intact
        assert await env.ledger() == []  # l'écriture du hook a été défaite avec la transaction
        assert (await env.svc.history(ALICE, "Voucher", doc["name"]))["total"] == 1  # et sa trace aussi
        assert env.bus.sent == [("doctype.Voucher.after_save", env.bus.sent[0][1], "xcore-doctype")]  # aucun événement de soumission
        env.refuse = None
        again = await env.svc.submit(ALICE, "Voucher", doc["name"], 1)  # corrigé : la même soumission passe
        assert again["docstatus"] == 1 and await env.ledger() == [(f"{doc['name']}.in", 10)]

    async def test_cancel_runs_its_own_hook_and_the_ledger_keeps_both_lines(self, env):
        doc = await env.voucher(amount=7)
        s = await env.svc.submit(ALICE, "Voucher", doc["name"], 1)
        await env.svc.cancel(ALICE, "Voucher", doc["name"], s["version"])
        assert [c[0] for c in env.calls] == ["post", "unpost"] and env.calls[1][2] == "on_cancel"
        assert await env.ledger() == [(f"{doc['name']}.in", 7), (f"{doc['name']}.out", -7)]

    async def test_a_hook_is_called_once_even_when_the_submission_is_replayed(self, env):
        doc = await env.voucher()
        await env.svc.submit(ALICE, "Voucher", doc["name"], 1)
        for version in (1, 2):
            with pytest.raises(InvalidDocStatus):
                await env.svc.submit(ALICE, "Voucher", doc["name"], version)
        assert len(env.calls) == 1 and len(await env.ledger()) == 1

    async def test_two_simultaneous_submissions_post_once(self, env):
        doc = await env.voucher()
        results = await asyncio.gather(
            *(env.svc.submit(ALICE, "Voucher", doc["name"], 1) for _ in range(4)), return_exceptions=True
        )
        won = [r for r in results if isinstance(r, dict)]
        assert len(won) == 1 and all(isinstance(r, (InvalidDocStatus, VersionConflict)) for r in results if r not in won)
        assert len(env.calls) == 1 and len(await env.ledger()) == 1

    async def test_hooks_that_read_then_write_lose_no_update_under_concurrency(self, env):
        """Lire un compteur puis l'écrire d'après la lecture : sans le verrou, deux soumissions simultanées partiraient de la même valeur.

        PostgreSQL : `tx.get(lock=True)` (FOR UPDATE). SQLite, qui n'a pas de verrou de ligne : le changement d'état est la PREMIÈRE
        écriture de la transaction, il prend le verrou de la base avant que le hook ne lise quoi que ce soit.
        """
        from xcore_doctype.context import RequestContext

        system = RequestContext.system("acme", "test")
        await env.svc.create(system, "Counter", {"n": 0})  # nom aléatoire : on le retrouve
        (counter,) = (await env.svc.list(system, "Counter"))["items"]

        async def bump(event):
            c = await event.tx.get("Counter", counter["name"], lock=True)
            await event.tx.update("Counter", counter["name"], {"n": c["data"]["n"] + 1}, c["version"])

        env.hooks.unregister("acct.post")
        env.hooks.register("acct.post", bump)
        drafts = [await env.voucher() for _ in range(8)]
        results = await asyncio.gather(*(env.svc.submit(ALICE, "Voucher", d["name"], d["version"]) for d in drafts), return_exceptions=True)
        assert all(isinstance(r, dict) for r in results), [repr(r) for r in results if not isinstance(r, dict)]
        assert (await env.svc.get(system, "Counter", counter["name"]))["data"]["n"] == 8  # aucune mise à jour perdue

    async def test_a_missing_hook_refuses_the_submission(self, env):
        doc = await env.voucher()
        env.hooks.unregister("acct.post")
        with pytest.raises(DoctypeError) as e:
            await env.svc.submit(ALICE, "Voucher", doc["name"], 1)
        assert (e.value.code, e.value.status) == ("hook_unavailable", 503)
        assert (await env.svc.get(ALICE, "Voucher", doc["name"]))["docstatus"] == 0

    async def test_a_hook_that_crashes_is_an_opaque_error_and_undoes_the_state(self, env):
        async def boom(event):
            await event.tx.create("Ledger", {"voucher": event.name, "amount": 1}, name="x")
            raise RuntimeError("SELECT secret FROM somewhere")

        env.hooks.unregister("acct.post")
        env.hooks.register("acct.post", boom)
        doc = await env.voucher()
        with pytest.raises(DoctypeError) as e:
            await env.svc.submit(ALICE, "Voucher", doc["name"], 1)
        assert e.value.code == "hook_error" and "secret" not in str(e.value)
        assert (await env.svc.get(ALICE, "Voucher", doc["name"]))["docstatus"] == 0 and await env.ledger() == []

    async def test_events_announce_the_transition_without_content(self, env):
        doc = await env.voucher()
        s = await env.svc.submit(ALICE, "Voucher", doc["name"], 1)
        await env.svc.cancel(ALICE, "Voucher", doc["name"], s["version"])
        topics = [t for t, _, _ in env.bus.sent]
        assert topics == ["doctype.Voucher.after_save", "doctype.Voucher.after_submit", "doctype.Voucher.after_cancel"]
        payload = env.bus.sent[1][1]
        assert payload == {"doctype": "Voucher", "name": doc["name"], "tenant_id": "acme", "version": 2, "user": "alice", "docstatus": 1}


class TestTransactionHandle:
    """Ce que `event.tx` permet, et refuse, à un hook de confiance."""

    async def run(self, env, body):
        async def hook(event):
            await body(event.tx, event)

        env.hooks.unregister("acct.post")
        env.hooks.register("acct.post", hook)
        doc = await env.voucher()
        return doc, await env.svc.submit(ALICE, "Voucher", doc["name"], 1)

    async def test_a_hook_writes_without_the_users_permissions(self, env):
        """`alice` n'a pas `acct:ledger:write` (le DocType est fermé à tous) : le hook de confiance écrit quand même."""
        with pytest.raises(PermissionDenied):
            await env.svc.create(ALICE, "Ledger", {"amount": 1})
        doc, _ = await self.run(env, lambda tx, e: tx.create("Ledger", {"amount": 3}, name="L1"))
        got = await env.svc.get(ALICE, "Ledger", "L1")
        assert got["data"] == {"amount": 3} and got["owner"] == "alice"  # propriétaire : celui qui a soumis

    async def test_what_a_hook_writes_is_validated_like_any_write(self, env):
        with pytest.raises(DoctypeError) as e:  # un défaut du hook (500), pas une faute de l'utilisateur
            await self.run(env, lambda tx, ev: tx.create("Ledger", {"amount": "beaucoup"}, name="L1"))
        assert (e.value.code, e.value.status) == ("hook_invalid_write", 500) and e.value.details[0]["field"] == "amount"
        assert any(msg == "hook wrote an invalid document" for msg, _ in env.log.errors)

    async def test_links_are_checked_on_existence_not_on_the_users_rights(self, env):
        with pytest.raises(DoctypeError) as e:
            await self.run(env, lambda tx, ev: tx.create("Ledger", {"voucher": "absent"}, name="L1"))
        assert e.value.code == "hook_invalid_write" and e.value.details[0]["type"] == "link_not_found"

    async def test_ensure_creates_once_and_never_overwrites(self, env):
        seen = []

        async def body(tx, event):
            seen.append(await tx.ensure("Ledger", "B1", {"amount": 1}))
            seen.append(await tx.ensure("Ledger", "B1", {"amount": 99}))

        await self.run(env, body)
        assert seen == [True, False] and await env.ledger() == [("B1", 1)]

    async def test_update_needs_the_current_version(self, env):
        async def body(tx, event):
            made = await tx.create("Ledger", {"amount": 1}, name="L1")
            upd = await tx.update("Ledger", "L1", {"amount": 2}, made["version"])
            assert upd["version"] == 2
            with pytest.raises(VersionConflict):
                await tx.update("Ledger", "L1", {"amount": 3}, made["version"])
            with pytest.raises(NotFound):
                await tx.update("Ledger", "absent", {"amount": 3}, 1)

        await self.run(env, body)
        assert await env.ledger() == [("L1", 2)]

    async def test_get_reads_without_filter_and_a_name_collision_fails(self, env):
        async def body(tx, event):
            assert (await tx.get("Voucher", event.name, lock=True))["docstatus"] == 1  # l'état est déjà passé
            assert await tx.get("Ledger", "absent") is None
            await tx.create("Ledger", {"amount": 1}, name="L1")
            with pytest.raises(DoctypeError) as e:
                await tx.create("Ledger", {"amount": 1}, name="L1")
            assert e.value.code == "already_exists"
            raise DocumentValidationError("stop")

        with pytest.raises(DocumentValidationError):
            await self.run(env, body)

    async def test_a_doctype_with_hooks_cannot_be_written_from_a_hook(self, env):
        """Ses hooks (totaux, validations…) ne tourneraient pas : on refuse plutôt que d'écrire sans la validation promise."""
        with pytest.raises(DoctypeError) as e:
            await self.run(env, lambda tx, ev: tx.create("Voucher", {"title": "x"}))
        assert e.value.code == "hook_not_supported"

    async def test_the_handle_knows_today_in_the_organisation_timezone(self, env):
        got = []

        async def body(tx, event):
            got.append(tx.today)

        await self.run(env, body)
        assert got[0].isoformat() == env.svc._clock().date().isoformat()  # UTC par défaut


class TestAmend:
    async def cancelled(self, env, **kw):
        doc = await env.voucher(**kw)
        s = await env.svc.submit(ALICE, "Voucher", doc["name"], 1)
        return doc, await env.svc.cancel(ALICE, "Voucher", doc["name"], s["version"])

    async def test_amending_copies_a_cancelled_document_into_a_draft(self, env):
        doc, _ = await self.cancelled(env, amount=42)
        new = await env.svc.amend(BOB, "Voucher", doc["name"])
        assert new["name"] == f"{doc['name']}-1" and new["amended_from"] == doc["name"]
        assert (new["docstatus"], new["version"], new["data"], new["owner"]) == (0, 1, doc["data"] | {"amount": 42}, "bob")
        assert (await env.svc.history(BOB, "Voucher", new["name"]))["items"][0]["action"] == "create"

    async def test_the_amendment_is_a_normal_draft_that_can_be_corrected_and_submitted(self, env):
        doc, _ = await self.cancelled(env, amount=1)
        new = await env.svc.amend(ALICE, "Voucher", doc["name"])
        fixed = await env.svc.update(ALICE, "Voucher", new["name"], {"title": "V", "amount": 2}, new["version"])
        assert fixed["amended_from"] == doc["name"]  # la filiation survit aux modifications
        await env.svc.submit(ALICE, "Voucher", new["name"], fixed["version"])
        assert sorted(a for _, a in await env.ledger()) == [-1, 1, 2]

    async def test_only_a_cancelled_document_can_be_amended(self, env):
        doc = await env.voucher()
        with pytest.raises(InvalidDocStatus):
            await env.svc.amend(ALICE, "Voucher", doc["name"])
        await env.svc.submit(ALICE, "Voucher", doc["name"], 1)
        with pytest.raises(InvalidDocStatus):
            await env.svc.amend(ALICE, "Voucher", doc["name"])

    async def test_a_document_is_amended_once(self, env):
        doc, _ = await self.cancelled(env)
        await env.svc.amend(ALICE, "Voucher", doc["name"])
        with pytest.raises(DoctypeError) as e:
            await env.svc.amend(ALICE, "Voucher", doc["name"])
        assert (e.value.code, e.value.status) == ("already_amended", 409)

    async def test_simultaneous_amendments_make_one_draft(self, env):
        doc, _ = await self.cancelled(env)
        results = await asyncio.gather(*(env.svc.amend(ALICE, "Voucher", doc["name"]) for _ in range(3)), return_exceptions=True)
        assert sum(isinstance(r, dict) for r in results) == 1
        assert all(isinstance(r, DoctypeError) and r.code == "already_amended" for r in results if not isinstance(r, dict))

    async def test_an_amendment_cancelled_in_turn_is_amended_to_the_next_number(self, env):
        doc, _ = await self.cancelled(env)
        first = await env.svc.amend(ALICE, "Voucher", doc["name"])
        s = await env.svc.submit(ALICE, "Voucher", first["name"], 1)
        await env.svc.cancel(ALICE, "Voucher", first["name"], s["version"])
        second = await env.svc.amend(ALICE, "Voucher", first["name"])
        assert (first["name"], second["name"]) == (f"{doc['name']}-1", f"{doc['name']}-2")
        assert second["amended_from"] == first["name"]

    async def test_amending_needs_the_right_to_create_and_to_read(self, env):
        doc, _ = await self.cancelled(env)
        reader = make_ctx("acme", "rita", permissions=["acct:voucher:read"])
        with pytest.raises(PermissionDenied):
            await env.svc.amend(reader, "Voucher", doc["name"])
        blind = make_ctx("acme", "bill", permissions=["acct:voucher:write"])
        with pytest.raises(PermissionDenied):  # pas de lecture : aucune action n'est ouverte, 403 comme ailleurs
            await env.svc.amend(blind, "Voucher", doc["name"])
        with pytest.raises(NotFound):
            await env.svc.amend(GLOBEX, "Voucher", doc["name"])


class TestPermissions:
    async def test_writing_is_not_enough_to_submit_or_cancel(self, env):
        doc = await env.voucher(ctx=WRITER)
        with pytest.raises(PermissionDenied):
            await env.svc.submit(WRITER, "Voucher", doc["name"], 1)
        s = await env.svc.submit(ALICE, "Voucher", doc["name"], 1)
        with pytest.raises(PermissionDenied):
            await env.svc.cancel(WRITER, "Voucher", doc["name"], s["version"])
        assert (await env.svc.get(ALICE, "Voucher", doc["name"]))["docstatus"] == 1

    async def test_submit_does_not_imply_the_right_to_write(self, env):
        """Qui valide n'est pas forcément qui saisit : `submit` seul ne permet pas de modifier le brouillon."""
        approver = make_ctx("acme", "ann", permissions=["acct:voucher:read", "acct:voucher:submit"])
        doc = await env.voucher()
        with pytest.raises(PermissionDenied):
            await env.svc.update(approver, "Voucher", doc["name"], {"title": "x"}, 1)
        assert (await env.svc.submit(approver, "Voucher", doc["name"], 1))["docstatus"] == 1

    async def test_a_submitter_must_be_able_to_read_the_document(self, env):
        doc = await env.voucher()
        blind = make_ctx("acme", "bill", permissions=["acct:voucher:submit"])
        with pytest.raises(PermissionDenied):
            await env.svc.submit(blind, "Voucher", doc["name"], 1)

    async def test_the_rights_are_listed_in_the_meta(self, env):
        meta = env.svc.get_meta(ALICE, "Voucher")
        assert meta["submittable"] is True
        assert meta["rights"] == {"read": "all", "write": "all", "create": "all", "delete": "all", "submit": "all", "cancel": "all"}
        assert env.svc.get_meta(WRITER, "Voucher")["rights"]["submit"] == "none"
        assert env.svc.get_meta(ALICE, "Ledger")["submittable"] is False

    async def test_if_owner_restricts_who_may_submit(self, db, tables):
        defn = {**VOUCHER, "permissions": {"submit": [{"grant": "acct:voucher:own", "if_owner": True}]}}
        reg = DocTypeRegistry()
        reg.register(parse_definition(defn))
        reg.register(parse_definition(LEDGER))
        hooks = HookRegistry()
        for name in ("acct.post", "acct.unpost"):
            async def noop(event): ...
            hooks.register(name, noop)
        svc = DocumentService(reg, db, tables, Log(), hooks=hooks)
        owner = make_ctx("acme", "olga", permissions=["acct:voucher:read", "acct:voucher:write", "acct:voucher:own"])
        other = make_ctx("acme", "otto", permissions=["acct:voucher:read", "acct:voucher:own"])
        doc = await svc.create(owner, "Voucher", {"title": "mine"})
        with pytest.raises(PermissionDenied):
            await svc.submit(other, "Voucher", doc["name"], 1)
        assert (await svc.submit(owner, "Voucher", doc["name"], 1))["docstatus"] == 1


class TestListing:
    async def test_the_status_is_filterable_and_sortable(self, env):
        a, b, c = [await env.voucher(title=t) for t in "abc"]
        sb = await env.svc.submit(ALICE, "Voucher", b["name"], 1)
        await env.svc.cancel(ALICE, "Voucher", b["name"], sb["version"])
        await env.svc.submit(ALICE, "Voucher", c["name"], 1)

        async def names(**kw):
            return {d["name"] for d in (await env.svc.list(ALICE, "Voucher", **kw))["items"]}

        assert await names(filters={"docstatus": 0}) == {a["name"]}
        assert await names(filters={"docstatus": 1}) == {c["name"]}
        assert await names(filters={"docstatus": ["in", [0, 1]]}) == {a["name"], c["name"]}
        assert await names(filters={"docstatus": ["!=", 2]}) == {a["name"], c["name"]}
        listed = await env.svc.list(ALICE, "Voucher", order_by="docstatus", descending=True)
        assert [d["docstatus"] for d in listed["items"]] == [2, 1, 0]

    @pytest.mark.parametrize("bad", [3, -1, "1", True, 1.5, None])
    async def test_an_impossible_status_is_refused(self, env, bad):
        if bad is None:
            pytest.skip("None est un filtre « sans valeur » valide pour les champs, pas pour un état")
        with pytest.raises(ValidationFailed):
            await env.svc.list(ALICE, "Voucher", filters={"docstatus": bad})


class TestDefinition:
    def test_a_submittable_doctype_cites_its_two_grants(self):
        d = parse_definition(VOUCHER)
        specs = d.grant_specs()
        assert {"acct:voucher:submit", "acct:voucher:cancel"} <= set(specs)
        assert (specs["acct:voucher:submit"].description, specs["acct:voucher:cancel"].description) == ("Soumettre Voucher", "Annuler Voucher")

    def test_a_plain_doctype_cites_neither(self):
        d = parse_definition({**VOUCHER, "submittable": False, "hooks": {}})
        assert not {g for g in d.grant_specs() if g.endswith((":submit", ":cancel"))}
        assert d.rules_for("submit") == () and d.rules_for("cancel") == ()

    @pytest.mark.parametrize(
        "patch, fragment",
        [
            ({"submittable": False}, "n'a de sens que pour un DocType `submittable"),  # ses hooks on_submit restent
            ({"track_changes": False}, "exige `track_changes: true`"),
            ({"hooks": {"on_submit": ["action:p.a"]}}, "`action:` interdit"),
            ({"hooks": {"on_cancel": ["action:p.a"]}}, "`action:` interdit"),
        ],
    )
    def test_incoherent_definitions_are_refused(self, patch, fragment):
        with pytest.raises(Exception, match=fragment):
            parse_definition({**VOUCHER, **patch})

    def test_submit_permissions_need_submittable(self):
        with pytest.raises(Exception, match="submit"):
            parse_definition({**VOUCHER, "submittable": False, "hooks": {}, "permissions": {"submit": ["acct:voucher:submit"]}})

    def test_a_child_doctype_cannot_be_submittable(self):
        with pytest.raises(Exception, match="submittable"):
            parse_definition({"schema_version": 1, "name": "Row", "istable": True, "submittable": True, "fields": [{"fieldname": "a", "fieldtype": "Text"}]})

    def test_explicit_submit_grants_replace_the_convention(self):
        d = parse_definition({**VOUCHER, "permissions": {"submit": ["acct:voucher:approve"], "cancel": []}})
        assert [e.grant for e in d.rules_for("submit")] == ["acct:voucher:approve"] and d.rules_for("cancel") == ()
        assert "acct:voucher:approve" in d.grant_specs() and "acct:voucher:cancel" not in d.grant_specs()
