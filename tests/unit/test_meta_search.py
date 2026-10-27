"""`/meta/{doctype}` tel que le Desk le consomme, et la recherche des champs Link (process/doctype.md §12)."""

from __future__ import annotations

import json

import pytest

from tests.conftest import make_ctx
from xcore_doctype.context import RequestContext
from xcore_doctype.document import DocumentService
from xcore_doctype.errors import NotFound, PermissionDenied, ValidationFailed
from xcore_doctype.expr import compile_expr, evaluate
from xcore_doctype.registry import DocTypeRegistry
from xcore_doctype.schema import parse_definition

DEF = {
    "schema_version": 1, "name": "Customer", "label": "Client", "module": "sales", "title_field": "customer_name",
    "fields": [
        {"fieldname": "customer_name", "label": "Nom", "fieldtype": "Text", "required": True, "in_list_view": True, "description": "Raison sociale"},
        {"fieldname": "type", "fieldtype": "Select", "options": ["Particulier", "Entreprise"], "in_list_view": True},
        {"fieldname": "company_id", "fieldtype": "Text", "depends_on": "type == 'Entreprise'", "mandatory_depends_on": "credit_limit > 1000"},
        {"fieldname": "credit_limit", "fieldtype": "Number", "default": 0, "read_only_depends_on": "vip"},
        {"fieldname": "vip", "fieldtype": "Boolean"},
        {"fieldname": "internal", "fieldtype": "Text", "hidden": True},
    ],
    "layout": [{"section": "Général", "columns": [["customer_name", "type"], ["credit_limit"]]},
               {"section": "Entreprise", "collapsible": True, "collapsed": True, "columns": [["company_id"]]}],
    "permissions": {"read": ["sales:customer:read", {"grant": "sales:customer:own", "if_owner": True}]},
}


class Log:
    def __init__(self):
        self.infos = []

    def info(self, msg, **kw):
        self.infos.append((msg, kw))

    def error(self, *a, **k): ...


@pytest.fixture
def svc(db, tables):
    reg = DocTypeRegistry()
    reg.register(parse_definition(DEF))
    reg.register(parse_definition({"schema_version": 1, "name": "Note", "module": "sales", "fields": [{"fieldname": "text", "fieldtype": "Text"}]}))
    log = Log()
    s = DocumentService(reg, db, tables, log)
    s.log = log
    return s


ALL = make_ctx("acme", "alice", permissions=["sales:customer:read", "sales:customer:write", "sales:customer:delete",
                                              "sales:note:read", "sales:note:write"])
OWN_ONLY = make_ctx("acme", "bob", permissions=["sales:customer:own"])
BLIND = make_ctx("acme", "eve", permissions=[])


class TestMeta:
    def meta(self, svc, ctx=ALL):
        return svc.get_meta(ctx, "Customer")

    def test_conditions_are_sent_as_trees_never_as_strings(self, svc):
        fields = {f["fieldname"]: f for f in self.meta(svc)["fields"]}
        assert fields["company_id"]["depends_on"] == compile_expr("type == 'Entreprise'", ["type"])
        assert isinstance(fields["company_id"]["mandatory_depends_on"], dict)
        assert fields["credit_limit"]["read_only_depends_on"] == {"t": "field", "name": "vip"}
        assert "depends_on" not in fields["customer_name"]  # pas de clé vide
        for kind in ("depends_on", "mandatory_depends_on"):
            assert not isinstance(fields["company_id"][kind], str)

    def test_the_tree_the_client_receives_evaluates_like_the_server(self, svc):
        tree = {f["fieldname"]: f for f in self.meta(svc)["fields"]}["company_id"]["depends_on"]
        assert evaluate(tree, {"type": "Entreprise"}) is True and evaluate(tree, {"type": "Particulier"}) is False

    def test_it_is_plain_json(self, svc):
        meta = self.meta(svc)
        assert json.loads(json.dumps(meta)) == meta

    def test_layout_is_normalized_and_completed(self, svc):
        lay = self.meta(svc)["layout"]
        assert [s["section"] for s in lay] == ["Général", "Entreprise", None]  # le dernier : champs non placés
        assert lay[1]["collapsible"] and lay[1]["collapsed"]
        assert lay[2]["columns"] == [["vip"]]  # `internal` est caché : il n'y est pas

    def test_list_columns_title_and_descriptions(self, svc):
        meta = self.meta(svc)
        assert meta["list_columns"] == ["customer_name", "type"] and meta["title_field"] == "customer_name"
        assert {f["fieldname"]: f.get("description") for f in meta["fields"]}["customer_name"] == "Raison sociale"
        assert {f["fieldname"]: f["hidden"] for f in meta["fields"]}["internal"] is True

    def test_nothing_internal_leaks(self, svc):
        text = json.dumps(self.meta(svc))
        for secret in ("sales:customer:read", "sales:customer:own", "if_owner", '"permissions"', '"hooks"'):
            assert secret not in text, secret

    def test_rights_depend_on_the_caller(self, svc):
        # `submit` et `cancel` n'existent que pour un DocType soumettable : « none » pour Customer, quels que soient les grants.
        closed = {"submit": "none", "cancel": "none"}
        assert self.meta(svc, ALL)["rights"] == {"read": "all", "write": "all", "create": "all", "delete": "all", **closed}
        assert self.meta(svc, OWN_ONLY)["rights"] == {"read": "restricted", "write": "none", "create": "none", "delete": "none", **closed}
        with pytest.raises(PermissionDenied):
            self.meta(svc, BLIND)


class TestSearch:
    @pytest.fixture
    async def docs(self, svc):
        out = {}
        for who, ctx in (("alice", ALL), ("bob-own", make_ctx("acme", "bob", permissions=["sales:customer:own", "sales:customer:write"]))):
            for n in ("Acme SA", "Zeta SARL"):
                out[(who, n)] = await svc.create(ctx, "Customer", {"customer_name": f"{n} ({who})"})
        return out

    async def test_returns_name_and_title(self, svc, docs):
        got = (await svc.search(ALL, "Customer", "acme", limit=10))["items"]
        assert sorted(i["title"] for i in got) == ["Acme SA (alice)", "Acme SA (bob-own)"]
        assert all(set(i) == {"name", "title"} for i in got)  # rien d'autre que ce qu'il faut pour un lien

    async def test_only_what_the_caller_may_read(self, svc, docs):
        """La recherche d'un champ Link applique les mêmes permissions que la lecture (D6)."""
        bob = make_ctx("acme", "bob", permissions=["sales:customer:own"])
        assert sorted(i["title"] for i in (await svc.search(bob, "Customer", "", limit=10))["items"]) == ["Acme SA (bob-own)", "Zeta SARL (bob-own)"]
        assert (await svc.search(bob, "Customer", "(alice)", limit=10))["items"] == []  # les titres d'alice ne se devinent pas par la recherche

    async def test_tenant_isolation(self, svc, docs):
        other = make_ctx("globex", "mallory", permissions=["sales:customer:read"])
        assert (await svc.search(other, "Customer", "", limit=10))["items"] == []

    async def test_requires_read_access(self, svc, docs):
        with pytest.raises(PermissionDenied):
            await svc.search(BLIND, "Customer", "acme")
        with pytest.raises(PermissionDenied):  # `write` n'est pas `read`
            await svc.search(make_ctx("acme", "w", permissions=["sales:customer:write"]), "Customer", "acme")

    async def test_unknown_doctype(self, svc):
        with pytest.raises(NotFound):
            await svc.search(ALL, "Nope", "x")

    async def test_anonymous_is_refused(self, svc):
        from xcore_doctype.errors import ContextError

        with pytest.raises(ContextError):
            await svc.search(RequestContext("acme", None, "http"), "Customer")

    @pytest.mark.parametrize("kw", [{"limit": 0}, {"limit": 51}, {"limit": True}, {"limit": "5"}, {"limit": 2.0}])
    async def test_bad_limit(self, svc, kw):
        with pytest.raises(ValidationFailed):
            await svc.search(ALL, "Customer", "x", **kw)

    @pytest.mark.parametrize("q", ["x" * 101, 5, None, ["a"]])
    async def test_bad_query(self, svc, q):
        with pytest.raises(ValidationFailed):
            await svc.search(ALL, "Customer", q)

    async def test_a_doctype_without_title_field_gives_null_titles(self, svc):
        await svc.create(ALL, "Note", {"text": "hello"})
        (item,) = (await svc.search(ALL, "Note", ""))["items"]
        assert item["title"] is None and item["name"]

    async def test_system_context_search_is_journaled(self, svc):
        await svc.search(RequestContext.system("acme", "export"), "Customer")
        assert any(kw["op"] == "doc.search" and kw["reason"] == "export" for _, kw in svc.log.infos)
