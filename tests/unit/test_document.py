"""Service `Document` : identité obligatoire (critère 5), contexte système journalisé, CRUD, validation."""

from __future__ import annotations

import re

import pytest

from tests.conftest import make_ctx
from xcore_doctype.context import RequestContext
from xcore_doctype.document import DocumentService
from xcore_doctype.errors import ContextError, NotFound, ValidationFailed, VersionConflict
from xcore_doctype.registry import DocTypeRegistry
from xcore_doctype.schema import parse_definition


class FakeLog:
    def __init__(self):
        self.records: list[tuple[str, dict]] = []

    def info(self, msg, **kw):
        self.records.append((msg, kw))

    # Le logger d'xcore est `error(msg, **fields)` / `warning(...)` : un double qui ne les a pas transforme un refus
    # du moteur en AttributeError, ce qui masque la vraie raison (ici : un DocType incomplet).
    def error(self, msg, **kw):
        self.records.append((msg, kw))

    def warning(self, msg, **kw):
        self.records.append((msg, kw))


@pytest.fixture
def log():
    return FakeLog()


@pytest.fixture
def svc(db, tables, customer, log):
    reg = DocTypeRegistry()
    # `Customer.country` cite `Country` : sans lui le type est INCOMPLET et toute écriture est refusée (503,
    # `load_dir(…, peers=True)`). Ces tests parlent de cloisonnement et de contexte système, pas de types incomplets.
    reg.register(parse_definition({"schema_version": 1, "name": "Country", "module": "geo",
                                   "fields": [{"fieldname": "country_name", "fieldtype": "Text"}]}))
    reg.register(customer.definition)
    return DocumentService(reg, db, tables, log)


ACME = make_ctx("acme", "alice")
GLOBEX = make_ctx("globex", "bob")
ANON = RequestContext("acme", None, "http")


async def make(svc, ctx=ACME, **data):
    return await svc.create(ctx, "Customer", {"customer_name": "Acme SA", **data})


class TestIdentityRequired:
    """Critère 5 : appel sans principal -> refus, et rien n'est écrit."""

    @pytest.mark.parametrize(
        "op",
        [
            lambda s, c: s.get(c, "Customer", "x"),
            lambda s, c: s.list(c, "Customer"),
            lambda s, c: s.create(c, "Customer", {"customer_name": "x"}),
            lambda s, c: s.update(c, "Customer", "x", {"customer_name": "x"}, 1),
            lambda s, c: s.delete(c, "Customer", "x"),
            lambda s, c: _async(s.list_meta, c),
            lambda s, c: _async(s.get_meta, c, "Customer"),
        ],
        ids=["get", "list", "create", "update", "delete", "list_meta", "get_meta"],
    )
    async def test_anonymous_refused(self, svc, op):
        with pytest.raises(ContextError) as e:
            await op(svc, ANON)
        assert (e.value.code, e.value.status) == ("unauthenticated", 401)

    async def test_nothing_written_by_refused_create(self, svc):
        with pytest.raises(ContextError):
            await svc.create(ANON, "Customer", {"customer_name": "ghost"})
        assert (await svc.list(ACME, "Customer"))["total"] == 0


async def _async(fn, *a):
    return fn(*a)


class TestSystemContext:
    SYS = RequestContext.system("acme", "nightly-import")

    async def test_allowed_and_journaled(self, svc, log):
        doc = await make(svc, self.SYS)
        assert doc["owner"] == "system"
        (msg, kw), = log.records
        assert msg == "system context operation"
        assert (kw["op"], kw["tenant"], kw["reason"], kw["doctype"]) == ("doc.create", "acme", "nightly-import", "Customer")

    async def test_every_system_operation_is_journaled(self, svc, log):
        doc = await make(svc, self.SYS)
        await svc.get(self.SYS, "Customer", doc["name"])
        await svc.list(self.SYS, "Customer")
        await svc.update(self.SYS, "Customer", doc["name"], {"customer_name": "y"}, 1)
        await svc.delete(self.SYS, "Customer", doc["name"])
        assert [kw["op"] for _, kw in log.records] == ["doc.create", "doc.get", "doc.list", "doc.update", "doc.delete"]

    async def test_user_operations_are_not_journaled_as_system(self, svc, log):
        await make(svc)
        assert log.records == []

    async def test_system_context_stays_tenant_scoped(self, svc):
        doc = await make(svc, ACME)
        with pytest.raises(NotFound):
            await svc.get(RequestContext.system("globex", "x"), "Customer", doc["name"])


class TestCrud:
    async def test_lifecycle(self, svc):
        created = await make(svc, credit_limit=10)
        assert re.fullmatch(r"[0-9a-f]{12}", created["name"])
        assert (created["version"], created["owner"]) == (1, "alice")
        assert created["data"] == {"customer_name": "Acme SA", "credit_limit": 10}

        assert await svc.get(ACME, "Customer", created["name"]) == created

        updated = await svc.update(ACME, "Customer", created["name"], {"customer_name": "Acme 2"}, created["version"])
        assert updated["version"] == 2 and updated["data"] == {"customer_name": "Acme 2", "credit_limit": 0}

        page = await svc.list(ACME, "Customer")
        assert (page["total"], [d["name"] for d in page["items"]]) == (1, [created["name"]])

        await svc.delete(ACME, "Customer", created["name"])
        with pytest.raises(NotFound) as e:
            await svc.get(ACME, "Customer", created["name"])
        assert e.value.code == "document_not_found"

    async def test_names_are_unique(self, svc):
        names = {(await make(svc))["name"] for _ in range(20)}
        assert len(names) == 20

    async def test_invalid_update_changes_nothing(self, svc):
        doc = await make(svc)
        with pytest.raises(ValidationFailed):
            await svc.update(ACME, "Customer", doc["name"], {"customer_name": "x", "credit_limit": "oops"}, 1)
        assert await svc.get(ACME, "Customer", doc["name"]) == doc

    async def test_stale_version(self, svc):
        doc = await make(svc)
        await svc.update(ACME, "Customer", doc["name"], {"customer_name": "a"}, 1)
        with pytest.raises(VersionConflict) as e:
            await svc.update(ACME, "Customer", doc["name"], {"customer_name": "b"}, 1)
        assert e.value.status == 409

    @pytest.mark.parametrize("version", [None, 0, -1, True, "1", 1.0])
    async def test_update_requires_a_real_version(self, svc, version):
        doc = await make(svc)
        with pytest.raises(ValidationFailed) as e:
            await svc.update(ACME, "Customer", doc["name"], {"customer_name": "x"}, version)
        assert e.value.code == "version_required"

    async def test_unknown_doctype(self, svc):
        for call in (
            svc.get(ACME, "Nope", "x"),
            svc.list(ACME, "Nope"),
            svc.create(ACME, "Nope", {}),
            svc.update(ACME, "Nope", "x", {}, 1),
            svc.delete(ACME, "Nope", "x"),
        ):
            with pytest.raises(NotFound) as e:
                await call
            assert e.value.code == "doctype_not_found"

    async def test_meta(self, svc):
        assert svc.list_meta(ACME) == [
            {"name": "Customer", "label": "Customer", "module": "sales", "icon": None, "single": False, "create": True}
        ]
        meta = svc.get_meta(ACME, "Customer")
        assert [f["fieldname"] for f in meta["fields"]][:2] == ["customer_name", "type"]


class TestTenantIsolationThroughService:
    async def test_other_tenant_cannot_read_write_or_delete(self, svc):
        doc = await make(svc, ACME)
        n = doc["name"]
        with pytest.raises(NotFound):
            await svc.get(GLOBEX, "Customer", n)
        with pytest.raises(NotFound):
            await svc.update(GLOBEX, "Customer", n, {"customer_name": "pwned"}, 1)
        with pytest.raises(NotFound):
            await svc.delete(GLOBEX, "Customer", n)
        assert (await svc.list(GLOBEX, "Customer"))["total"] == 0
        assert await svc.get(ACME, "Customer", n) == doc


class TestListParameters:
    @pytest.mark.parametrize("kw", [{"limit": 0}, {"limit": 201}, {"limit": True}, {"limit": "5"}, {"offset": -1}, {"offset": "0"}])
    async def test_bad_paging(self, svc, kw):
        with pytest.raises(ValidationFailed):
            await svc.list(ACME, "Customer", **kw)

    @pytest.mark.parametrize("order_by", ["nope", "data", "tenant_id", "version; DROP TABLE x"])
    async def test_bad_order(self, svc, order_by):
        with pytest.raises(ValidationFailed):
            await svc.list(ACME, "Customer", order_by=order_by)

    @pytest.mark.parametrize("filters", [{"nope": 1}, {"credit_limit": "12"}, {"type": "Autre"}, ["a"], "x"])
    async def test_bad_filters(self, svc, filters):
        with pytest.raises(ValidationFailed):
            await svc.list(ACME, "Customer", filters=filters)

    async def test_filter_and_sort(self, svc):
        for n, limit in (("a", 3), ("b", 1), ("c", 2)):
            await svc.create(ACME, "Customer", {"customer_name": n, "credit_limit": limit, "type": "Entreprise"})
        await svc.create(ACME, "Customer", {"customer_name": "d", "type": "Particulier"})
        page = await svc.list(ACME, "Customer", filters={"type": "Entreprise"}, order_by="credit_limit", descending=False)
        assert [d["data"]["customer_name"] for d in page["items"]] == ["b", "c", "a"] and page["total"] == 3
