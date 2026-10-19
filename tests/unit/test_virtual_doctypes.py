"""DocTypes VIRTUELS (`source: <nom>`, sources.py) : leurs documents vivent chez une source, le moteur applique droits, filtres, tri, recherche, liens et agrégats.

Le point central est la PARITÉ : un DocType ordinaire (`Sale`, table générique, SQL) et un DocType virtuel de même définition (`SaleV`, source en mémoire)
doivent répondre PAREIL à la même question (`list`, `aggregate`, `search`) sur les mêmes documents. Les deux interprétations des filtres (SQL / Python) ne
peuvent plus diverger sans qu'un test échoue. Chaque test tourne sur SQLite et, si configuré, sur PostgreSQL.
"""

from __future__ import annotations

import itertools
from datetime import UTC, datetime
from typing import Any

import pytest

from tests.conftest import make_ctx
from xcore_doctype.context import RequestContext
from xcore_doctype.document import DocumentService
from xcore_doctype.errors import DoctypeError, DocumentInUse, InvalidDefinition, NotFound, PermissionDenied, ValidationFailed, VersionConflict
from xcore_doctype.query import StoredDoc
from xcore_doctype.registry import DocTypeRegistry
from xcore_doctype.schema import parse_definition
from xcore_doctype.sources import DocumentSources, ReadOnlySource, content_version

NOW = datetime(2026, 10, 17, 9, 0, tzinfo=UTC)
FIELDS = [
    {"fieldname": "client", "fieldtype": "Link", "options": "Client"},
    {"fieldname": "status", "fieldtype": "Select", "options": ["Ouverte", "Payée", "Perdue"]},
    {"fieldname": "amount", "fieldtype": "Decimal", "decimals": 2},
    {"fieldname": "qty", "fieldtype": "Number"},
    {"fieldname": "paid", "fieldtype": "Boolean"},
    {"fieldname": "due", "fieldtype": "Date"},
    {"fieldname": "note", "fieldtype": "Text"},
    {"fieldname": "locked", "fieldtype": "Text", "read_only": True},
    {"fieldname": "n", "fieldtype": "Int"},
    {"fieldname": "pct", "fieldtype": "Percent"},
    {"fieldname": "at", "fieldtype": "Time"},
    {"fieldname": "dur", "fieldtype": "Duration"},
]
PERMS = {"read": ["shop:sale:read", {"grant": "shop:sale:read_own", "if_owner": True}], "write": ["shop:sale:write"], "create": ["shop:sale:write"], "delete": ["shop:sale:write"]}
CLIENT = {"schema_version": 1, "name": "Client", "module": "shop", "fields": [{"fieldname": "client_name", "fieldtype": "Text"}]}
SALE = {"schema_version": 1, "name": "Sale", "module": "shop", "fields": FIELDS, "title_field": "note", "permissions": PERMS}
SALE_V = {**SALE, "name": "SaleV", "source": "memory", "track_changes": False}
CLIENT_V = {"schema_version": 1, "name": "ClientV", "module": "shop", "source": "memory", "fields": [{"fieldname": "client_name", "fieldtype": "Text"}], "title_field": "client_name"}
ORDER = {"schema_version": 1, "name": "Order", "module": "shop", "fields": [{"fieldname": "client", "fieldtype": "Link", "options": "ClientV"}]}

ALICE = make_ctx("acme", "alice", permissions=["shop:sale:read", "shop:sale:write", "shop:client:read", "shop:client:write", "shop:clientv:read", "shop:clientv:write", "shop:clientv:delete", "shop:order:read", "shop:order:write", "shop:order:delete"])
BOB_OWN = make_ctx("acme", "bob", permissions=["shop:sale:read_own", "shop:sale:write"])
NOBODY = make_ctx("acme", "nobody", permissions=[])
SYSTEM = RequestContext.system("acme", "test")


class _Log:
    def __init__(self) -> None:
        self.errors: list[tuple[Any, Any]] = []

    def info(self, *a: Any, **k: Any) -> None: ...

    def error(self, *a: Any, **k: Any) -> None:
        self.errors.append((a, k))


class MemorySource(ReadOnlySource):
    """Une source qui garde ses documents dans un dict (par tenant) : de quoi tester le moteur sans plugin ni réseau."""

    def __init__(self) -> None:
        self.docs: dict[str, dict[str, StoredDoc]] = {}
        self.calls: list[tuple[str, str]] = []
        self._ids = itertools.count(1)
        self.boom: Exception | None = None

    def put(self, tenant: str, doc: StoredDoc) -> None:
        self.docs.setdefault(tenant, {})[doc.name] = doc

    async def fetch_all(self, ctx, doctype):
        self.calls.append(("fetch_all", doctype.name))
        if self.boom:
            raise self.boom
        return [d for d in self.docs.get(ctx.tenant_id, {}).values() if d.doctype == doctype.name]

    async def insert(self, ctx, doctype, data):
        self.calls.append(("insert", doctype.name))
        name = f"{doctype.name.lower()}-{next(self._ids)}"
        doc = StoredDoc(doctype.name, name, dict(data), ctx.owner_id, NOW, NOW, content_version(data))
        self.put(ctx.tenant_id, doc)
        return doc

    async def update(self, ctx, doctype, name, data, version):
        self.calls.append(("update", doctype.name))
        held = self.docs.get(ctx.tenant_id, {}).get(name)
        if held is None:
            raise NotFound("document_not_found", name)
        if held.version != version:
            raise VersionConflict("périmé")
        doc = StoredDoc(doctype.name, name, dict(data), held.owner, held.creation, NOW, content_version(data))
        self.put(ctx.tenant_id, doc)
        return doc

    async def remove(self, ctx, doctype, name):
        self.calls.append(("remove", doctype.name))
        self.docs.get(ctx.tenant_id, {}).pop(name, None)


@pytest.fixture
def source():
    return MemorySource()


@pytest.fixture
def svc(db, tables, source):
    reg = DocTypeRegistry()
    for raw in (CLIENT, SALE, SALE_V, CLIENT_V, ORDER):
        reg.register(parse_definition(raw))
    sources = DocumentSources()
    sources.register("shop", "memory", source)
    log = _Log()
    service = DocumentService(reg, db, tables, log, clock=lambda: NOW, sources=sources)
    service.log = log  # type: ignore[attr-defined]
    return service


SALES = [
    # owner, status, amount, qty, paid, due, note, n, pct, at, dur
    ("alice", "Ouverte", "10.50", 3, False, "2026-01-05", "alpha", 5, 10.5, "08:00:00", 3600),
    ("alice", "Payée", "99.99", 7, True, "2026-01-20", "Bravo", 12, 50, "13:30:00", 5400),
    ("bob", "Payée", "5.00", 1, True, "2026-02-01", "charlie", -3, 100, "23:59:59", 60),
    ("bob", "Perdue", "0.00", 0, False, "2025-12-31", None, 0, 0, "00:00:00", 0),
    ("alice", "Ouverte", "10.50", None, None, None, "alpha 2", None, None, None, None),
    ("bob", "Ouverte", "123.45", 12, False, "2026-02-01", "100% déjà", 12, 33.3, "13:30:00", 7200),
    ("alice", None, None, 3, True, "2026-03-15", "x_y", 5, 10.5, "08:00:00", 3600),
]


@pytest.fixture
async def twin(svc, source):
    """Les MÊMES sept ventes dans `Sale` (table générique) et dans `SaleV` (source), sous les mêmes noms, propriétaires et dates."""
    for i, (owner, status, amount, qty, paid, due, note, n, pct, at, dur) in enumerate(SALES, 1):
        data = {k: v for k, v in {"status": status, "amount": amount, "qty": qty, "paid": paid, "due": due, "note": note, "n": n, "pct": pct, "at": at, "dur": dur}.items() if v is not None}
        ctx = make_ctx("acme", owner, permissions=ALICE.permissions)
        made = await svc.create(ctx, "Sale", data)
        source.put("acme", StoredDoc("SaleV", made["name"], dict(made["data"]), made["owner"], datetime.fromisoformat(made["creation"]), datetime.fromisoformat(made["modified"]), made["version"]))
    return svc


async def both(svc, method: str, ctx, *args, **kw):
    """La même question aux deux DocTypes ; le nom du type est la seule chose qui diffère de la réponse."""
    real = await getattr(svc, method)(ctx, "Sale", *args, **kw)
    virtual = await getattr(svc, method)(ctx, "SaleV", *args, **kw)
    return real, virtual


def strip(result):
    """Retire ce qui nomme le type, pour comparer le reste."""
    if isinstance(result, dict):
        return {k: strip(v) for k, v in result.items() if k not in ("doctype", "doctype_label")}
    if isinstance(result, list):
        return [strip(v) for v in result]
    return result


class TestParity:
    @pytest.mark.parametrize(
        "filters",
        [
            {},
            {"status": "Payée"},
            {"status": ["!=", "Payée"]},  # `!=` retient aussi ce qui n'a pas de statut
            {"status": ["in", ["Ouverte", "Perdue"]]},
            {"status": ["in", []]},
            {"note": ["like", "alpha"]},
            {"note": ["like", "ALPHA"]},  # sans casse
            {"note": ["like", "100%"]},  # `%` littéral, pas un joker
            {"note": ["like", "x_y"]},  # `_` littéral
            {"amount": ["=", "10.50"]},
            {"amount": [">=", "10.50"]},
            {"amount": ["<", "10.50"]},
            {"amount": ["in", ["0.00", "5.00"]]},
            {"qty": 3},
            {"qty": [">", 3]},
            {"qty": ["<=", 3]},
            {"paid": True},
            {"paid": False},
            {"paid": ["!=", True]},
            {"due": "2026-02-01"},
            {"due": [">=", "2026-01-20"]},
            {"due": ["<", "2026-01-20"]},
            {"name": ["like", "-"]},
            {"status": "Ouverte", "paid": False},  # ET
            {"status": "Payée", "qty": [">", 5]},
            {"n": 5}, {"n": [">", 4]}, {"n": ["in", [12, -3]]}, {"n": ["!=", 12]},
            {"pct": [">=", 50]}, {"pct": 10.5}, {"pct": ["<", 33.3]},
            {"at": [">=", "13:00:00"]}, {"at": "08:00:00"}, {"at": ["!=", "08:00:00"]},
            {"dur": [">", 3600]}, {"dur": ["in", [0, 60]]},
        ],
    )
    @pytest.mark.parametrize("order_by,descending", [("modified", True), ("name", False), ("amount", True), ("amount", False), ("due", False), ("qty", True), ("status", False), ("note", True), ("owner", False), ("n", True), ("pct", False), ("at", True), ("dur", False)])
    async def test_list(self, twin, filters, order_by, descending):
        # `modified` et `name` : les ventes de `Sale` ont des noms aléatoires, ceux de `SaleV` les LEURS : même nom des deux côtés (voir `twin`).
        real, virtual = await both(twin, "list", ALICE, filters=filters, order_by=order_by, descending=descending, limit=200)
        assert strip(virtual) == strip(real)

    @pytest.mark.parametrize("limit,offset", [(2, 0), (2, 2), (3, 6), (200, 5), (1, 50)])
    async def test_pages_and_total(self, twin, limit, offset):
        real, virtual = await both(twin, "list", ALICE, order_by="name", descending=False, limit=limit, offset=offset)
        assert strip(virtual) == strip(real)
        assert real["total"] == len(SALES)

    async def test_the_read_scope_hides_other_owners_in_both(self, twin):
        real, virtual = await both(twin, "list", BOB_OWN, order_by="name", descending=False)
        assert strip(virtual) == strip(real)
        assert {i["owner"] for i in real["items"]} == {"bob"} and real["total"] == 3
        r, v = await both(twin, "list", BOB_OWN, filters={"status": "Payée"}, order_by="name", descending=False)
        assert strip(v) == strip(r) and r["total"] == 1

    @pytest.mark.parametrize("q", ["alpha", "ALPHA", "bra", "100", "x_y", "-", "zzz", "é"])
    async def test_search(self, twin, q):
        real, virtual = await both(twin, "search", ALICE, q, limit=10)
        assert strip(virtual) == strip(real)

    @pytest.mark.parametrize(
        "kw",
        [
            {},  # un compte
            {"metrics": ["count", "sum:amount"]},
            {"metrics": ["sum:amount", "avg:qty", "min:qty", "max:qty"]},
            {"group_by": "status"},
            {"group_by": "status", "metrics": ["count", "sum:amount"]},
            {"group_by": "status", "metrics": ["sum:amount", "count"]},
            {"group_by": "paid"},
            {"group_by": "client"},
            {"group_by": "owner"},
            {"group_by": "docstatus"},
            {"group_by": "due", "bucket": "month"},
            {"group_by": "due", "bucket": "year", "metrics": ["count", "sum:amount"]},
            {"group_by": "due", "bucket": "day", "periods": 30},
            {"group_by": "status", "limit": 2},
            {"group_by": "status", "filters": {"paid": True}},
            {"metrics": ["sum:dur", "avg:pct", "max:n", "min:n", "sum:n"]},
            {"group_by": "status", "metrics": ["sum:dur", "avg:pct"]},
            {"filters": {"status": ["!=", "Payée"]}, "metrics": ["sum:amount", "avg:qty"]},
            {"filters": {"note": "zzz"}, "metrics": ["sum:amount", "min:qty"]},  # rien : les sommes valent 0, les autres rien
        ],
    )
    async def test_aggregate(self, twin, kw):
        real, virtual = await both(twin, "aggregate", ALICE, **kw)
        assert strip(virtual) == strip(real)

    async def test_aggregates_respect_the_read_scope(self, twin):
        real, virtual = await both(twin, "aggregate", BOB_OWN, group_by="status", metrics=["count", "sum:amount"])
        assert strip(virtual) == strip(real)
        assert sum(r["values"][0] for r in real["rows"]) == 3


class TestOperations:
    async def test_get_and_the_404_of_a_document_outside_the_scope(self, twin):
        mine = (await twin.list(BOB_OWN, "SaleV", order_by="name", descending=False))["items"][0]
        assert (await twin.get(BOB_OWN, "SaleV", mine["name"]))["data"] == mine["data"]
        other = next(i for i in (await twin.list(ALICE, "SaleV"))["items"] if i["owner"] == "alice")
        with pytest.raises(NotFound):
            await twin.get(BOB_OWN, "SaleV", other["name"])  # hors périmètre : « introuvable », comme pour un document absent
        with pytest.raises(NotFound):
            await twin.get(ALICE, "SaleV", "n-existe-pas")

    async def test_no_grant_no_access_and_the_source_is_never_called(self, svc, source):
        for call in (svc.list(NOBODY, "SaleV"), svc.get(NOBODY, "SaleV", "x"), svc.create(NOBODY, "SaleV", {}), svc.aggregate(NOBODY, "SaleV")):
            with pytest.raises(PermissionDenied):
                await call
        assert source.calls == []

    async def test_create_validates_strictly_before_the_source_sees_anything(self, svc, source):
        with pytest.raises(ValidationFailed):
            await svc.create(ALICE, "SaleV", {"qty": "12"})  # pas de coercition : un nombre n'est pas un texte
        with pytest.raises(ValidationFailed):
            await svc.create(ALICE, "SaleV", {"status": "Inconnue"})
        with pytest.raises(ValidationFailed) as e:
            await svc.create(ALICE, "SaleV", {"client": "pas-un-client"})  # le Link est vérifié comme partout
        assert e.value.details[0]["type"] == "link_not_found"
        assert source.calls == []
        made = await svc.create(ALICE, "SaleV", {"note": "ok", "qty": 2})
        assert made["owner"] == "alice" and made["data"] == {"note": "ok", "qty": 2} and source.calls == [("insert", "SaleV")]
        assert (await svc.get(ALICE, "SaleV", made["name"]))["data"]["note"] == "ok"

    async def test_update_checks_the_version_the_scope_and_read_only_fields(self, svc, source):
        made = await svc.create(ALICE, "SaleV", {"note": "v1", "locked": "fixe"})
        with pytest.raises(ValidationFailed) as e:
            await svc.update(ALICE, "SaleV", made["name"], {"note": "v2", "locked": "changé"}, made["version"])
        assert e.value.details == [{"field": "locked", "msg": "Champ en lecture seule", "type": "read_only"}]  # un client ne change pas un champ en lecture seule
        done = await svc.update(ALICE, "SaleV", made["name"], {"note": "v2", "locked": "fixe"}, made["version"])
        assert done["data"]["note"] == "v2" and done["version"] != made["version"]
        with pytest.raises(VersionConflict):
            await svc.update(ALICE, "SaleV", made["name"], {"note": "v3", "locked": "fixe"}, made["version"])  # périmée
        with pytest.raises(ValidationFailed) as e2:
            await svc.update(ALICE, "SaleV", made["name"], {"note": "v3"}, 0)
        assert e2.value.code == "version_required"
        read_only_user = make_ctx("acme", "carl", permissions=["shop:sale:read"])
        with pytest.raises(PermissionDenied):
            await svc.update(read_only_user, "SaleV", made["name"], {"note": "x"}, done["version"])
        with pytest.raises(NotFound):  # il peut écrire mais ne LIT que ses documents : celui d'Alice lui est introuvable, donc intouchable
            await svc.update(BOB_OWN, "SaleV", made["name"], {"note": "x"}, done["version"])
        with pytest.raises(PermissionDenied):
            await svc.update(NOBODY, "SaleV", made["name"], {"note": "x"}, done["version"])

    async def test_delete(self, twin, source):
        victim = (await twin.list(ALICE, "SaleV"))["items"][0]["name"]
        await twin.delete(ALICE, "SaleV", victim)
        assert ("remove", "SaleV") in source.calls
        with pytest.raises(NotFound):
            await twin.get(ALICE, "SaleV", victim)
        with pytest.raises(NotFound):
            await twin.delete(ALICE, "SaleV", victim)

    async def test_history_is_empty_but_the_document_must_be_readable(self, twin):
        mine = (await twin.list(BOB_OWN, "SaleV"))["items"][0]["name"]
        assert (await twin.history(BOB_OWN, "SaleV", mine))["items"] == []
        other = next(i for i in (await twin.list(ALICE, "SaleV"))["items"] if i["owner"] == "alice")["name"]
        with pytest.raises(NotFound):
            await twin.history(BOB_OWN, "SaleV", other)

    async def test_tenants_are_separated_by_the_source_context(self, twin, source):
        elsewhere = make_ctx("globex", "gina", permissions=ALICE.permissions)
        assert (await twin.list(elsewhere, "SaleV"))["total"] == 0

    async def test_meta_says_virtual_and_has_no_history(self, svc):
        meta = svc.get_meta(ALICE, "SaleV")
        assert meta["virtual"] is True and meta["track_changes"] is False and meta["source"] == "memory"
        assert next(f for f in meta["fields"] if f["fieldname"] == "locked")["read_only"] is True
        listed = {m["name"]: m for m in svc.list_meta(ALICE)}
        assert listed["SaleV"]["virtual"] is True and "virtual" not in listed["Sale"]


class TestLinks:
    async def test_an_ordinary_document_can_link_to_a_virtual_one_and_the_target_must_exist(self, svc, source):
        client = await svc.create(ALICE, "ClientV", {"client_name": "Acme"})
        order = await svc.create(ALICE, "Order", {"client": client["name"]})
        assert order["data"]["client"] == client["name"]
        with pytest.raises(ValidationFailed) as e:
            await svc.create(ALICE, "Order", {"client": "clientv-999"})
        assert e.value.details[0]["type"] == "link_not_found"
        hidden = make_ctx("acme", "dora", permissions=["shop:order:read", "shop:order:write"])  # elle ne lit pas ClientV : introuvable pour elle aussi
        with pytest.raises(ValidationFailed):
            await svc.create(hidden, "Order", {"client": client["name"]})

    async def test_a_virtual_document_cited_by_a_stored_one_cannot_be_deleted(self, svc, source):
        client = await svc.create(ALICE, "ClientV", {"client_name": "Acme"})
        order = await svc.create(ALICE, "Order", {"client": client["name"]})
        with pytest.raises(DocumentInUse) as e:
            await svc.delete(ALICE, "ClientV", client["name"])
        assert e.value.details == [{"doctype": "Order", "field": "client"}]
        assert ("remove", "ClientV") not in source.calls
        await svc.delete(ALICE, "Order", order["name"])
        await svc.delete(ALICE, "ClientV", client["name"])  # plus cité : il part

    async def test_link_titles_are_resolved_through_the_list(self, svc):
        client = await svc.create(ALICE, "ClientV", {"client_name": "Acme"})
        found = await svc.list(ALICE, "ClientV", filters={"name": ["in", [client["name"], "autre"]]})
        assert [i["data"]["client_name"] for i in found["items"]] == ["Acme"]  # ce que fait le Desk pour afficher « Acme » plutôt qu'un identifiant
        assert (await svc.search(ALICE, "ClientV", "acm"))["items"] == [{"name": client["name"], "title": "Acme"}]


class TestFailures:
    async def test_an_unregistered_source_refuses_instead_of_answering_empty(self, db, tables):
        reg = DocTypeRegistry()
        reg.register(parse_definition(SALE_V))
        svc = DocumentService(reg, db, tables, _Log(), sources=DocumentSources())
        for call in (svc.list(ALICE, "SaleV"), svc.get(ALICE, "SaleV", "x"), svc.create(ALICE, "SaleV", {}), svc.aggregate(ALICE, "SaleV")):
            with pytest.raises(DoctypeError) as e:
                await call
            assert (e.value.status, e.value.code) == (503, "source_unavailable")

    async def test_a_crashing_source_is_a_502_that_leaks_nothing(self, svc, source):
        source.boom = RuntimeError("postgres://admin:secret@db/xauth password authentication failed")
        with pytest.raises(DoctypeError) as e:
            await svc.list(ALICE, "SaleV")
        assert (e.value.status, e.value.code) == (502, "source_failed")
        assert "secret" not in e.value.message and "postgres" not in str(e.value.as_detail())
        assert any("secret" in str(k) for _, k in svc.log.errors)  # le détail va au journal, pas au client

    async def test_a_source_answering_the_wrong_thing_is_refused(self, svc, source):
        source.fetch_all = lambda ctx, doctype: _return([StoredDoc("AutreType", "x", {}, "alice", NOW, NOW, 1)])  # type: ignore[method-assign]
        with pytest.raises(DoctypeError) as e:
            await svc.list(ALICE, "SaleV")
        assert (e.value.status, e.value.code) == (502, "source_invalid")

    async def test_a_read_only_source_refuses_writes_with_a_clear_code(self, db, tables):
        class Fixed(ReadOnlySource):
            async def fetch_all(self, ctx, doctype):
                return []

        reg = DocTypeRegistry()
        reg.register(parse_definition(SALE_V))
        sources = DocumentSources()
        sources.register("shop", "memory", Fixed())
        svc = DocumentService(reg, db, tables, _Log(), sources=sources)
        with pytest.raises(ValidationFailed) as e:
            await svc.create(ALICE, "SaleV", {"note": "x"})
        assert e.value.code == "source_read_only"


async def _return(value):
    return value


class TestRegistry:
    def test_a_source_has_one_owner(self):
        sources = DocumentSources()
        a = MemorySource()
        sources.register("iam", "xauth", a)
        sources.register("iam", "xauth", a)  # le même propriétaire peut la remplacer
        with pytest.raises(ValueError, match="appartient au plugin 'iam'"):
            sources.register("shop", "xauth", MemorySource())
        assert sources.get("xauth") is a and sources.names() == ["xauth"]
        assert sources.unregister_owner("shop") == 0 and sources.unregister_owner("iam") == 1 and sources.get("xauth") is None

    @pytest.mark.parametrize(
        "patch,message",
        [
            ({"single": True}, "single"),
            ({"submittable": True}, "submittable"),
            ({"naming": {"series": "S-{#####}"}}, "naming"),
            ({"hooks": {"validate": ["shop.check"]}}, "hooks"),
            ({"track_changes": True}, "track_changes"),
            ({"fields": [{"fieldname": "photo", "fieldtype": "Image"}]}, "Image"),
            ({"fields": [{"fieldname": "d", "fieldtype": "Date", "default": "today"}]}, "today"),
        ],
    )
    def test_what_the_engine_cannot_apply_to_a_document_it_does_not_store_is_refused(self, patch, message):
        base = {"schema_version": 1, "name": "V", "module": "shop", "source": "memory", "fields": [{"fieldname": "a", "fieldtype": "Text"}]}
        with pytest.raises(InvalidDefinition, match=message):
            parse_definition({**base, **patch})

    def test_the_source_name_is_a_closed_shape(self):
        for bad in ("Xauth", "x auth", "1x", "x;drop", ""):
            with pytest.raises(InvalidDefinition):
                parse_definition({"schema_version": 1, "name": "V", "module": "shop", "source": bad, "fields": [{"fieldname": "a", "fieldtype": "Text"}]})

    def test_content_version_changes_with_the_content_and_is_never_zero(self):
        a, b = content_version({"x": 1}), content_version({"x": 2})
        assert a != b and a == content_version({"x": 1}) and content_version({"y": [1, 2], "x": 1}) == content_version({"x": 1, "y": [1, 2]})
        assert all(v >= 1 for v in (a, b, content_version({})))


class TestHooksCannotWriteThere:
    async def test_a_transition_hook_reads_a_virtual_document_but_never_writes_it(self, svc, source):
        from xcore_doctype.document import _Transaction  # noqa: PLC0415

        made = await svc.create(ALICE, "SaleV", {"note": "lu"})
        tx = _Transaction(svc, ALICE, None, "on_submit")  # type: ignore[arg-type]
        assert (await tx.get("SaleV", made["name"]))["data"]["note"] == "lu"
        assert await tx.get("SaleV", "absent") is None
        for call in (tx.create("SaleV", {"note": "x"}), tx.ensure("SaleV", "n", {"note": "x"}), tx.update("SaleV", made["name"], {"note": "y"}, 1)):
            with pytest.raises(DoctypeError) as e:
                await call
            assert e.value.code == "hook_not_supported"
