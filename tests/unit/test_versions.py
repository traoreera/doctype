"""Historique (versions) : écrit avec le document, lu avec le droit de lire le document."""

from __future__ import annotations

import asyncio

import pytest

from tests.conftest import CUSTOMER, CUSTOMER_GRANTS, make_ctx
from xcore_doctype.context import RequestContext
from xcore_doctype.document import DocumentService
from xcore_doctype.errors import NotFound, PermissionDenied, ValidationFailed, VersionConflict
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
        doc = await create(svc)
        with pytest.raises(PermissionDenied):
            await hist(svc, doc, ctx=make_ctx(permissions=["sales:customer:write"]))
        with pytest.raises(PermissionDenied):
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
