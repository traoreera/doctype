"""Historique (versions) : écrit avec le document, lu avec le droit de lire le document.

Rétention (D24) : les `max_versions` entrées les plus récentes d'un document survivent à chacune de ses écritures, dans la même
transaction ; qui a soumis et qui a annulé ne se purge jamais ainsi. L'opérateur purge par âge (`purge_history`) : sa date est un instant, quel que soit son décalage horaire.
Un nom se réutilise : chaque suppression clôt une génération, un document ne montre (et la rétention ne compte) que la sienne.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy import update

from tests.conftest import CUSTOMER, CUSTOMER_GRANTS, make_ctx
from xcore_doctype.context import RequestContext
from xcore_doctype.document import DocumentService
from xcore_doctype.errors import InvalidDefinition, NotFound, PermissionDenied, ValidationFailed, VersionConflict
from xcore_doctype.query import Query
from xcore_doctype.registry import DocTypeRegistry
from xcore_doctype.schema import parse_definition
from xcore_doctype.versions import diff, snapshot


class Log:
    def info(self, *a, **k): ...
    def error(self, *a, **k): ...


class TestDiff:
    def test_only_what_changed(self):
        assert diff({"a": 1, "b": 2, "c": 3}, {"a": 1, "b": 5, "c": 3}) == {"b": {"old": 2, "new": 5}}

    def test_creation_is_a_difference_with_nothing(self):
        assert diff(None, {"a": 1, "b": "x"}) == {"a": {"old": None, "new": 1}, "b": {"old": None, "new": "x"}}

    def test_deletion_is_a_difference_with_no_more(self):
        assert diff({"a": 1}, None) == {"a": {"old": 1, "new": None}}

    def test_added_and_removed_fields(self):
        assert diff({"a": 1}, {"b": 2}) == {"a": {"old": 1, "new": None}, "b": {"old": None, "new": 2}}

    def test_no_change_is_empty_and_zero_or_false_are_values(self):
        assert diff({"a": 0, "b": False}, {"a": 0, "b": False}) == {}
        assert diff({"a": 0}, {"a": None}) == {"a": {"old": 0, "new": None}}
        assert diff({"a": None}, {"a": 0}) == {"a": {"old": None, "new": 0}}

    def test_a_table_is_compared_as_a_whole(self):
        old, new = {"items": [{"q": 1}, {"q": 2}]}, {"items": [{"q": 1}, {"q": 3}]}
        assert diff(old, new) == {"items": {"old": old["items"], "new": new["items"]}}

    def test_snapshot_rebuilds_a_deleted_document(self):
        gone = diff({"a": 1, "b": "x", "c": 0}, None)
        assert snapshot(gone) == {"a": 1, "b": "x", "c": 0}


@pytest.fixture
def svc(db, tables, customer):
    reg = DocTypeRegistry()
    reg.register(parse_definition({"schema_version": 1, "name": "Country", "module": "sales", "fields": [{"fieldname": "n", "fieldtype": "Text"}]}))
    reg.register(customer.definition)
    return DocumentService(reg, db, tables, Log())


ALICE = make_ctx("acme", "alice")


async def create(svc, ctx=ALICE, **data):
    return await svc.create(ctx, "Customer", {"customer_name": "ACME", **data})


async def hist(svc, doc, ctx=ALICE, **kw):
    return await svc.history(ctx, "Customer", doc["name"], **kw)


class TestRecording:
    async def test_a_creation_is_recorded_with_who_and_what(self, svc):
        doc = await create(svc, credit_limit=50)
        (entry,) = (await hist(svc, doc))["items"]
        assert (entry["action"], entry["user"], entry["version"], entry["owner"]) == ("create", "alice", 1, "alice")
        assert entry["changes"]["customer_name"] == {"old": None, "new": "ACME"}
        assert entry["changes"]["credit_limit"] == {"old": None, "new": 50}
        assert entry["creation"].endswith("+00:00")

    async def test_an_update_keeps_only_the_difference_and_the_new_version(self, svc):
        doc = await create(svc, credit_limit=50)
        upd = await svc.update(ALICE, "Customer", doc["name"], {"customer_name": "ACME", "credit_limit": 80}, 1)
        assert upd["version"] == 2
        page = await hist(svc, doc)
        assert page["total"] == 2
        newest = page["items"][0]  # la plus récente d'abord
        assert (newest["action"], newest["version"]) == ("update", 2)
        assert newest["changes"] == {"credit_limit": {"old": 50, "new": 80}}

    async def test_an_update_that_changes_nothing_leaves_no_entry(self, svc):
        doc = await create(svc, credit_limit=50)
        await svc.update(ALICE, "Customer", doc["name"], {"customer_name": "ACME", "credit_limit": 50}, 1)
        assert (await hist(svc, doc))["total"] == 1

    async def test_a_deletion_keeps_the_content(self, svc):
        doc = await create(svc, credit_limit=50)
        await svc.delete(ALICE, "Customer", doc["name"])
        newest = (await hist(svc, doc))["items"][0]
        assert (newest["action"], newest["user"]) == ("delete", "alice")
        assert snapshot(newest["changes"]) == {"customer_name": "ACME", "credit_limit": 50}

    async def test_a_removed_value_is_a_change(self, svc):
        doc = await create(svc, credit_limit=50, vip=True)
        await svc.update(ALICE, "Customer", doc["name"], {"customer_name": "ACME", "credit_limit": 50}, 1)
        assert (await hist(svc, doc))["items"][0]["changes"] == {"vip": {"old": True, "new": None}}

    async def test_the_system_context_is_named_by_its_reason(self, svc):
        system = RequestContext.system("acme", "nightly-import")
        doc = await create(svc, ctx=system)
        assert (await svc.history(system, "Customer", doc["name"]))["items"][0]["user"] == "system:nightly-import"

    async def test_tracking_can_be_turned_off(self, db, tables):
        reg = DocTypeRegistry()
        reg.register(parse_definition({**CUSTOMER, "track_changes": False, "fields": [f for f in CUSTOMER["fields"] if f["fieldtype"] != "Link"]}))
        s = DocumentService(reg, db, tables, Log())
        doc = await s.create(ALICE, "Customer", {"customer_name": "x"})
        await s.update(ALICE, "Customer", doc["name"], {"customer_name": "y"}, 1)
        await s.delete(ALICE, "Customer", doc["name"])
        async with db.session() as session:
            assert await Query(session, ALICE, tables).versions("Customer", doc["name"], limit=10, offset=0) == ([], 0)

    async def test_a_series_name_is_the_one_in_the_history(self, db, tables):
        reg = DocTypeRegistry()
        reg.register(parse_definition({"schema_version": 1, "name": "Invoice", "module": "sales", "naming": {"series": "FA-{#####}"},
                                       "fields": [{"fieldname": "x", "fieldtype": "Text"}]}))
        s = DocumentService(reg, db, tables, Log())
        ctx = make_ctx(permissions=["sales:invoice:read", "sales:invoice:write"])
        doc = await s.create(ctx, "Invoice", {"x": "1"})
        assert doc["name"] == "FA-00001"
        assert (await s.history(ctx, "Invoice", "FA-00001"))["total"] == 1

    async def test_a_table_change_stores_both_arrays(self, db, tables):
        reg = DocTypeRegistry()
        reg.register(parse_definition({"schema_version": 1, "name": "Line", "istable": True, "fields": [{"fieldname": "q", "fieldtype": "Number"}]}))
        reg.register(parse_definition({"schema_version": 1, "name": "Doc", "module": "sales", "fields": [{"fieldname": "lines", "fieldtype": "Table", "options": "Line"}]}))
        s = DocumentService(reg, db, tables, Log())
        ctx = make_ctx(permissions=["sales:doc:read", "sales:doc:write"])
        doc = await s.create(ctx, "Doc", {"lines": [{"q": 1}]})
        await s.update(ctx, "Doc", doc["name"], {"lines": [{"q": 1}, {"q": 2}]}, 1)
        assert (await s.history(ctx, "Doc", doc["name"]))["items"][0]["changes"] == {"lines": {"old": [{"q": 1}], "new": [{"q": 1}, {"q": 2}]}}


class TestAtomicity:
    async def test_an_entry_that_cannot_be_written_undoes_the_document_change(self, svc, monkeypatch):
        doc = await create(svc, credit_limit=1)

        async def boom(self, *a, **k):
            raise RuntimeError("journal plein")

        monkeypatch.setattr(Query, "add_version", boom)
        with pytest.raises(RuntimeError):
            await svc.update(ALICE, "Customer", doc["name"], {"customer_name": "ACME", "credit_limit": 2}, 1)
        with pytest.raises(RuntimeError):
            await svc.delete(ALICE, "Customer", doc["name"])
        with pytest.raises(RuntimeError):
            await create(svc, customer_name="ghost")
        monkeypatch.undo()
        got = await svc.get(ALICE, "Customer", doc["name"])
        assert got["data"]["credit_limit"] == 1 and got["version"] == 1  # ni modifié, ni supprimé
        assert (await svc.list(ALICE, "Customer"))["total"] == 1  # le « ghost » n'existe pas
        assert (await hist(svc, doc))["total"] == 1

    async def test_a_document_that_cannot_be_written_leaves_no_entry(self, svc, db, tables, monkeypatch):
        async def boom(self, *a, **k):
            raise RuntimeError("disque plein")

        monkeypatch.setattr(Query, "insert", boom)
        with pytest.raises(RuntimeError):
            await create(svc)
        monkeypatch.undo()
        async with db.session() as s:
            from sqlalchemy import func, select

            assert (await s.execute(select(func.count()).select_from(tables.versions))).scalar_one() == 0

    async def test_concurrent_updates_leave_one_entry_for_the_one_that_won(self, svc):
        doc = await create(svc, credit_limit=0)

        async def attempt(n):
            return await svc.update(ALICE, "Customer", doc["name"], {"customer_name": "ACME", "credit_limit": n}, 1)

        results = await asyncio.gather(*(attempt(n) for n in range(1, 9)), return_exceptions=True)
        assert sum(not isinstance(r, BaseException) for r in results) == 1
        assert all(isinstance(r, VersionConflict) for r in results if isinstance(r, BaseException))
        page = await hist(svc, doc)
        assert [i["action"] for i in page["items"]] == ["update", "create"]


class TestReading:
    async def test_newest_first_with_paging(self, svc):
        doc = await create(svc, credit_limit=0)
        for n in range(1, 6):
            await svc.update(ALICE, "Customer", doc["name"], {"customer_name": "ACME", "credit_limit": n}, n)
        first = await hist(svc, doc, limit=2)
        assert first["total"] == 6 and [i["version"] for i in first["items"]] == [6, 5]
        assert [i["version"] for i in (await hist(svc, doc, limit=2, offset=4))["items"]] == [2, 1]
        assert (await hist(svc, doc, limit=2, offset=10))["items"] == []

    @pytest.mark.parametrize("kw", [{"limit": 0}, {"limit": 201}, {"limit": True}, {"offset": -1}, {"offset": "1"}])
    async def test_bad_paging(self, svc, kw):
        doc = await create(svc)
        with pytest.raises(ValidationFailed):
            await hist(svc, doc, **kw)

    async def test_it_needs_the_right_to_read(self, svc):
        """L'historique d'UN document nommé : un partage pourrait l'ouvrir sans rôle (permissions.py) ; sans lecture ni partage, il est
        « introuvable », comme un document qui n'existe pas."""
        doc = await create(svc)
        with pytest.raises(NotFound):
            await hist(svc, doc, ctx=make_ctx(permissions=["sales:customer:write"]))
        with pytest.raises(NotFound):
            await hist(svc, doc, ctx=make_ctx(permissions=[]))

    async def test_other_tenants_and_missing_documents_are_not_found(self, svc):
        doc = await create(svc)
        for ctx, name in ((make_ctx("globex", "bob"), doc["name"]), (ALICE, "nope")):
            with pytest.raises(NotFound):
                await svc.history(ctx, "Customer", name)

    async def test_the_row_scope_applies_to_live_and_deleted_documents(self, db, tables):
        reg = DocTypeRegistry()
        reg.register(parse_definition({**CUSTOMER, "fields": [f for f in CUSTOMER["fields"] if f["fieldtype"] != "Link"],
                                       "permissions": {"read": [{"grant": "sales:customer:own", "if_owner": True}, "sales:customer:read"],
                                                       "write": ["sales:customer:write"], "create": ["sales:customer:write"], "delete": ["sales:customer:delete"]}}))
        s = DocumentService(reg, db, tables, Log())
        admin = make_ctx(permissions=CUSTOMER_GRANTS)
        mine_only = lambda who: make_ctx("acme", who, permissions=["sales:customer:own", "sales:customer:write", "sales:customer:delete"])  # noqa: E731
        theirs = await s.create(admin, "Customer", {"customer_name": "theirs"})  # propriétaire : alice
        mine = await s.create(mine_only("bob"), "Customer", {"customer_name": "mine"})

        assert (await s.history(mine_only("bob"), "Customer", mine["name"]))["total"] == 1
        with pytest.raises(NotFound):
            await s.history(mine_only("bob"), "Customer", theirs["name"])  # hors de son périmètre : introuvable

        await s.delete(admin, "Customer", theirs["name"])
        await s.delete(mine_only("bob"), "Customer", mine["name"])
        # Après suppression, le même périmètre juge sur le contenu et le propriétaire conservés.
        assert (await s.history(mine_only("bob"), "Customer", mine["name"]))["items"][0]["action"] == "delete"
        with pytest.raises(NotFound):
            await s.history(mine_only("bob"), "Customer", theirs["name"])
        assert (await s.history(admin, "Customer", theirs["name"]))["total"] == 2

    async def test_a_child_doctype_has_no_history_route(self, db, tables):
        reg = DocTypeRegistry()
        reg.register(parse_definition({"schema_version": 1, "name": "Line", "istable": True, "fields": [{"fieldname": "q", "fieldtype": "Number"}]}))
        s = DocumentService(reg, db, tables, Log())
        with pytest.raises(NotFound):
            await s.history(ALICE, "Line", "x")


# ── Rétention (`max_versions`, D24) ─────────────────────────────────────────

PLAIN = {**CUSTOMER, "fields": [f for f in CUSTOMER["fields"] if f["fieldtype"] != "Link"]}


def service(db, tables, *defs) -> DocumentService:
    reg = DocTypeRegistry()
    for d in defs or (PLAIN,):
        reg.register(parse_definition(d))
    return DocumentService(reg, db, tables, Log())


async def updates(s, doc, count, ctx=ALICE):
    """`count` modifications réelles (chacune laisse une entrée) ; renvoie le document à jour."""
    for _ in range(count):
        doc = await s.update(ctx, "Customer", doc["name"], {"customer_name": "ACME", "credit_limit": doc["version"] * 10}, doc["version"])
    return doc


async def all_entries(s, doc, ctx=ALICE):
    return (await s.history(ctx, "Customer", doc["name"], limit=200))["items"]


class TestRetention:
    async def test_only_the_most_recent_entries_survive(self, db, tables):
        s = service(db, tables, {**PLAIN, "max_versions": 3})
        doc = await updates(s, await create(s), 5)  # 6 entrées écrites : create puis 5 updates
        assert [e["version"] for e in await all_entries(s, doc)] == [6, 5, 4]  # les plus récentes, la création est partie

    async def test_the_default_keeps_twenty_five(self, db, tables):
        s = service(db, tables)
        doc = await updates(s, await create(s), 29)
        entries = await all_entries(s, doc)
        assert len(entries) == 25 and (entries[0]["version"], entries[-1]["version"]) == (30, 6)

    async def test_each_document_keeps_its_own_entries(self, db, tables):
        s = service(db, tables, {**PLAIN, "max_versions": 2})
        quiet = await create(s, customer_name="Calme")
        busy = await updates(s, await create(s), 4)
        assert [e["version"] for e in await all_entries(s, busy)] == [5, 4]
        assert [e["action"] for e in await all_entries(s, quiet)] == ["create"]  # la purge de l'un ne touche pas l'autre

    async def test_another_tenant_keeps_the_entries_of_its_homonym(self, db, tables):
        """Le même nom chez globex et chez acme : les écritures d'acme ne purgent que l'historique d'acme."""
        s = service(db, tables, {**PLAIN, "naming": "prompt", "max_versions": 1})
        globex = make_ctx("globex", "gina")
        theirs = await s.create(globex, "Customer", {"customer_name": "Globex"}, name="C-1")
        mine = await updates(s, await s.create(ALICE, "Customer", {"customer_name": "ACME"}, name="C-1"), 2)
        assert [e["version"] for e in await all_entries(s, mine)] == [3]
        assert [e["action"] for e in await all_entries(s, theirs, ctx=globex)] == ["create"]

    async def test_a_lowered_limit_applies_at_the_next_write(self, db, tables):
        """La purge est un effet de l'écriture : baisser `max_versions` ne purge rien au chargement, la prochaine écriture rattrape."""
        before = service(db, tables)  # le défaut : 25 entrées
        doc = await updates(before, await create(before), 5)
        s = service(db, tables, {**PLAIN, "max_versions": 2})
        assert len(await all_entries(s, doc)) == 6
        doc = await s.update(ALICE, "Customer", doc["name"], dict(doc["data"]), doc["version"])  # mêmes valeurs : version 7, sans entrée
        assert len(await all_entries(s, doc)) == 6  # une écriture sans changement n'écrit (ni ne purge) rien
        doc = await updates(s, doc, 1)
        assert [e["version"] for e in await all_entries(s, doc)] == [8, 6]

    async def test_the_deletion_entry_survives_and_keeps_the_content(self, db, tables):
        s = service(db, tables, {**PLAIN, "max_versions": 2})
        doc = await updates(s, await create(s, credit_limit=1), 3)
        await s.delete(ALICE, "Customer", doc["name"])
        newest, older = await all_entries(s, doc)
        assert (newest["action"], older["action"]) == ("delete", "update")
        assert snapshot(newest["changes"]) == {"customer_name": "ACME", "credit_limit": 30}  # la création est purgée, le contenu reste

    async def test_the_purge_is_undone_with_the_write(self, db, tables, monkeypatch):
        """Dans la transaction du document : une écriture qui échoue après la purge ne perd aucune entrée."""
        s = service(db, tables, {**PLAIN, "max_versions": 2})
        doc = await updates(s, await create(s), 1)
        purge = Query.purge_versions

        async def purge_then_fail(self, *a, **k):
            assert await purge(self, *a, **k) == 1  # la plus vieille entrée est bien supprimée…
            raise RuntimeError("panne après la purge")

        monkeypatch.setattr(Query, "purge_versions", purge_then_fail)
        with pytest.raises(RuntimeError):
            await updates(s, doc, 1)
        monkeypatch.undo()
        assert [e["version"] for e in await all_entries(s, doc)] == [2, 1]  # … mais rien n'est perdu : la transaction est défaite
        assert (await s.get(ALICE, "Customer", doc["name"]))["version"] == 2

    async def test_who_submitted_and_cancelled_survives_the_retention(self, db, tables):
        """Un DocType soumettable garde son historique pour dire QUI a soumis et annulé : la rétention ne purge jamais ces deux entrées."""
        deal = {"schema_version": 1, "name": "Deal", "module": "sales", "submittable": True, "max_versions": 1,
                "fields": [{"fieldname": "title", "fieldtype": "Text"}, {"fieldname": "note", "fieldtype": "Text", "allow_on_submit": True}]}
        s = service(db, tables, deal)
        ctx = make_ctx(permissions=["sales:deal:read", "sales:deal:write", "sales:deal:submit", "sales:deal:cancel"])
        bob = make_ctx("acme", "bob", permissions=["sales:deal:read", "sales:deal:cancel"])
        doc = await s.create(ctx, "Deal", {"title": "D"})
        doc = await s.submit(ctx, "Deal", doc["name"], doc["version"])
        for n in range(3):  # des modifications après soumission (`allow_on_submit`) ne chassent pas l'entrée de soumission
            doc = await s.update(ctx, "Deal", doc["name"], {"title": "D", "note": f"suivi {n}"}, doc["version"])
        await s.cancel(bob, "Deal", doc["name"], doc["version"])
        entries = (await s.history(ctx, "Deal", doc["name"]))["items"]
        assert [(e["action"], e["user"]) for e in entries] == [("cancel", "bob"), ("submit", "alice")]


    async def test_the_cancel_entry_survives_a_later_rewrite_of_a_cancelled_document(self, db, tables):
        """Un document ANNULÉ reçoit encore des entrées (le renommage d'un document qu'il cite le réécrit) : la rétention garde la sienne."""
        tag = {"schema_version": 1, "name": "Tag", "module": "sales", "allow_rename": True, "fields": [{"fieldname": "t", "fieldtype": "Text"}]}
        deal = {"schema_version": 1, "name": "Deal", "module": "sales", "submittable": True, "max_versions": 1,
                "fields": [{"fieldname": "tag", "fieldtype": "Link", "options": "Tag"}]}
        s = service(db, tables, tag, deal)
        ctx = make_ctx(permissions=[f"sales:{d}:{a}" for d in ("tag", "deal") for a in ("read", "write", "submit", "cancel")])
        t = await s.create(ctx, "Tag", {"t": "x"})
        doc = await s.create(ctx, "Deal", {"tag": t["name"]})
        doc = await s.submit(ctx, "Deal", doc["name"], doc["version"])
        doc = await s.cancel(ctx, "Deal", doc["name"], doc["version"])
        await s.rename(ctx, "Tag", t["name"], "renomme")
        assert [e["action"] for e in (await s.history(ctx, "Deal", doc["name"]))["items"]] == ["update", "cancel", "submit"]


# ── Un nom réutilisé : chaque suppression clôt une génération ───────────────

# `naming: prompt` (un nom se réutilise), `allow_rename`, et la lecture `if_owner` : chacun ne lit que SES notes.
NOTE = {
    "schema_version": 1, "name": "Note", "module": "acct", "naming": "prompt", "allow_rename": True, "max_versions": 2,
    "fields": [{"fieldname": "text", "fieldtype": "Text"}],
    "permissions": {"read": [{"grant": "acct:note:own", "if_owner": True}], "write": [{"grant": "acct:note:own", "if_owner": True}],
                    "create": ["acct:note:own"], "delete": [{"grant": "acct:note:own", "if_owner": True}]},
}
LONG_NOTE = {k: v for k, v in NOTE.items() if k != "max_versions"}  # la rétention par défaut : rien ne part dans ces tests-là
BOB = make_ctx("acme", "bob", permissions=["acct:note:own"])
OLGA = make_ctx("acme", "olga", permissions=["acct:note:own"])


async def note_history(s, ctx, name):
    return [(e["action"], e["user"]) for e in (await s.history(ctx, "Note", name, limit=200))["items"]]


class TestNameReuse:
    async def test_a_new_document_never_shows_the_history_of_a_deleted_homonym(self, db, tables):
        """Bob crée N-1 puis le supprime ; Olga crée N-1 : elle ne voit ni l'entrée de suppression de Bob ni son contenu."""
        s = service(db, tables, LONG_NOTE)
        await s.create(BOB, "Note", {"text": "le mémo salaire de Bob"}, name="N-1")
        await s.delete(BOB, "Note", "N-1")
        await s.create(OLGA, "Note", {"text": "bonjour"}, name="N-1")
        assert await note_history(s, OLGA, "N-1") == [("create", "olga")]

    async def test_the_retention_counts_and_purges_only_the_living_generation(self, db, tables):
        """`max_versions: 2` : les écritures d'Olga ne purgent pas la génération de Bob (l'âge seul la purge), qui ne compte pas dans les siennes."""
        s = service(db, tables, NOTE)
        bob = await s.create(BOB, "Note", {"text": "a"}, name="N-1")
        await s.update(BOB, "Note", "N-1", {"text": "b"}, bob["version"])
        await s.delete(BOB, "Note", "N-1")  # sa génération close : les 2 plus récentes, la suppression et la modification
        mine = await s.create(OLGA, "Note", {"text": "c"}, name="N-1")
        assert await note_history(s, OLGA, "N-1") == [("create", "olga")]  # pas de place prise par celles de Bob
        mine = await s.update(OLGA, "Note", "N-1", {"text": "d"}, mine["version"])
        await s.update(OLGA, "Note", "N-1", {"text": "e"}, mine["version"])
        assert await note_history(s, OLGA, "N-1") == [("update", "olga"), ("update", "olga")]
        # Bob ne lit pas la note d'Olga : seule SA génération (supprimée) peut lui revenir, intacte, jamais celle d'Olga.
        assert await note_history(s, BOB, "N-1") == [("delete", "bob"), ("update", "bob")]
        assert await s.purge_history("acme", datetime.now(UTC) + timedelta(minutes=1)) == 4  # l'âge, lui, purge toutes les générations

    async def test_a_deleted_name_shows_its_last_generation_judged_on_its_snapshot(self, db, tables):
        s = service(db, tables, LONG_NOTE)
        for who in (BOB, OLGA):
            await s.create(who, "Note", {"text": who.user_id}, name="N-1")
            await s.delete(who, "Note", "N-1")
        assert await note_history(s, OLGA, "N-1") == [("delete", "olga"), ("create", "olga")]
        with pytest.raises(NotFound):  # la dernière génération est celle d'Olga : Bob ne la lit pas, et la sienne, plus vieille, ne revient pas
            await s.history(BOB, "Note", "N-1")

    async def test_a_renamed_document_takes_its_own_generation_only(self, db, tables):
        """Le renommage emporte la génération vivante : celle de Bob reste au nom qu'il a supprimé."""
        s = service(db, tables, LONG_NOTE)
        await s.create(BOB, "Note", {"text": "a"}, name="X")
        await s.delete(BOB, "Note", "X")
        await s.create(OLGA, "Note", {"text": "b"}, name="X")
        await s.rename(OLGA, "Note", "X", "Y")
        assert await note_history(s, OLGA, "Y") == [("rename", "olga"), ("create", "olga")]
        assert await note_history(s, BOB, "X") == [("delete", "bob"), ("create", "bob")]

    async def test_a_document_renamed_to_a_reused_name_keeps_its_whole_history(self, db, tables):
        """Y a servi (créé puis supprimé APRÈS la création de X) : X renommé Y garde toutes ses entrées, sans celles de l'ancien Y."""
        s = service(db, tables, LONG_NOTE)
        x = await s.create(OLGA, "Note", {"text": "a"}, name="X")
        await s.create(BOB, "Note", {"text": "b"}, name="Y")
        await s.delete(BOB, "Note", "Y")
        await s.update(OLGA, "Note", "X", {"text": "c"}, x["version"])
        await s.rename(OLGA, "Note", "X", "Y")
        assert await note_history(s, OLGA, "Y") == [("rename", "olga"), ("update", "olga"), ("create", "olga")]
        assert await note_history(s, BOB, "Y") == [("delete", "bob"), ("create", "bob")]  # sa génération supprimée, toujours à lui
        with pytest.raises(NotFound):
            await s.history(OLGA, "Note", "X")  # X n'a jamais été supprimé : plus rien à ce nom


class TestRetentionDefinition:
    @pytest.mark.parametrize("value", [0, 1001, -1, True, "5", 2.0])
    def test_bounds_and_strict_int(self, value):
        with pytest.raises(InvalidDefinition):
            parse_definition({**PLAIN, "max_versions": value})

    @pytest.mark.parametrize("value", [1, 1000])
    def test_the_bounds_are_included(self, value):
        assert parse_definition({**PLAIN, "max_versions": value}).max_versions == value

    def test_it_needs_track_changes(self):
        with pytest.raises(InvalidDefinition, match="max_versions: exige `track_changes: true`"):
            parse_definition({**PLAIN, "track_changes": False, "max_versions": 5})

    def test_a_virtual_doctype_refuses_it(self):
        with pytest.raises(InvalidDefinition, match="max_versions"):
            parse_definition({**PLAIN, "source": "people", "max_versions": 5})


# ── Purge par âge (`purge_history`, action opérateur `ops.history.purge`) ───

async def age(db, tables, ctx, doctype, name, *, days):
    """Vieillit les entrées d'un document de `days` jours (l'horloge des entrées est celle de la base : on la recule à la main)."""
    v = tables.versions
    async with db.session() as session:
        await session.execute(
            update(v).where(v.c.tenant_id == ctx.tenant_id, v.c.doctype == doctype, v.c.docname == name)
            .values(creation=datetime.now(UTC) - timedelta(days=days))
        )
        await session.commit()


class TestPurgeByAge:
    async def test_entries_older_than_the_date_go_and_the_newer_stay(self, db, tables):
        s = service(db, tables)
        old, recent = await create(s, customer_name="Ancien"), await create(s, customer_name="Récent")
        await age(db, tables, ALICE, "Customer", old["name"], days=40)
        assert await s.purge_history("acme", datetime.now(UTC) - timedelta(days=30)) == 1
        assert await all_entries(s, old) == []
        assert [e["action"] for e in await all_entries(s, recent)] == ["create"]

    async def test_one_doctype_and_one_tenant_only(self, db, tables):
        country = {"schema_version": 1, "name": "Country", "module": "sales", "fields": [{"fieldname": "n", "fieldtype": "Text"}]}
        s = service(db, tables, PLAIN, country)
        globex = make_ctx("globex", "gina")
        mine, theirs = await create(s), await create(s, ctx=globex)
        fr = await s.create(make_ctx(permissions=["sales:country:read", "sales:country:write"]), "Country", {"n": "France"})
        for ctx, doctype, name in ((ALICE, "Customer", mine["name"]), (globex, "Customer", theirs["name"]), (ALICE, "Country", fr["name"])):
            await age(db, tables, ctx, doctype, name, days=10)
        cutoff = datetime.now(UTC) - timedelta(days=1)
        assert await s.purge_history("acme", cutoff, doctype="Country") == 1  # seul le pays d'acme
        assert len(await all_entries(s, mine)) == 1
        assert await s.purge_history("acme", cutoff) == 1  # puis le client d'acme ; celui de globex reste
        assert len(await all_entries(s, theirs, ctx=globex)) == 1

    async def test_the_date_is_an_instant_whatever_its_offset(self, db, tables):
        """Une date écrite avec un autre décalage horaire désigne le même instant : la base compare des instants, pas des heures d'horloge.

        (SQLite range les dates sans fuseau : une date en +05:00 comparée telle quelle purgeait des entrées PLUS RÉCENTES qu'elle.)
        """
        s = service(db, tables)
        doc = await create(s)
        east, west = timezone(timedelta(hours=5)), timezone(timedelta(hours=-5))
        an_hour_ago = (datetime.now(UTC) - timedelta(hours=1)).astimezone(east)  # 5 h de plus à l'horloge, mais une heure AVANT l'entrée
        assert await s.purge_history("acme", an_hour_ago) == 0
        assert len(await all_entries(s, doc)) == 1
        in_a_minute = (datetime.now(UTC) + timedelta(minutes=1)).astimezone(west)  # 5 h de moins à l'horloge, mais APRÈS l'entrée
        assert await s.purge_history("acme", in_a_minute) == 1

    async def test_a_purged_deleted_document_is_gone_for_good(self, db, tables):
        s = service(db, tables)
        doc = await create(s)
        await s.delete(ALICE, "Customer", doc["name"])
        assert await s.purge_history("acme", datetime.now(UTC) + timedelta(minutes=1)) == 2
        with pytest.raises(NotFound):  # plus de trace : introuvable, comme un document qui n'a jamais existé
            await s.history(ALICE, "Customer", doc["name"])
