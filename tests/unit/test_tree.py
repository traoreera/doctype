"""La forêt d'un type arborescent : rattachement, racines de secours (parent hors page), boucles, refus."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from tests.conftest import make_ctx
from xcore_doctype.document import DocumentService, _tree
from xcore_doctype.errors import PermissionDenied, ValidationFailed
from xcore_doctype.query import StoredDoc
from xcore_doctype.registry import DocTypeRegistry
from xcore_doctype.schema import parse_definition

NOW = datetime(2026, 10, 17, 9, 30, tzinfo=UTC)

NODE = {
    "schema_version": 1,
    "name": "Node",
    "module": "shop",
    "title_field": "title",
    "fields": [
        {"fieldname": "title", "fieldtype": "Text", "required": True},
        {"fieldname": "parent_node", "fieldtype": "Link", "options": "Node"},
    ],
    "views": {"tree": {"parent": "parent_node"}},
}
LEAF = {"schema_version": 1, "name": "Leaf", "module": "shop", "fields": [{"fieldname": "title", "fieldtype": "Text", "required": True}]}

READ = make_ctx("acme", "alice", permissions=["shop:node:read"])
WRITE = make_ctx("acme", "alice", permissions=["shop:node:read", "shop:node:write"])


class Log:
    def info(self, *a, **k): ...
    def error(self, *a, **k): ...


def registry():
    reg = DocTypeRegistry()
    reg.register(parse_definition(NODE))
    reg.register(parse_definition(LEAF))
    return reg


def service(db, tables):
    return DocumentService(registry(), db, tables, Log())


async def node(svc, title, parent=None):
    data: dict = {"title": title}
    if parent is not None:
        data["parent_node"] = parent
    return (await svc.create(WRITE, "Node", data))["name"]


def descendants(nodes):
    for item in nodes:
        yield item["name"]
        yield from descendants(item["children"])


def stored_doc(name, parent):
    return StoredDoc(
        doctype="Node",
        name=name,
        data={"title": name, **({"parent_node": parent} if parent is not None else {})},
        owner="alice",
        creation=NOW,
        modified=NOW,
        version=1,
    )


class TestService:
    async def test_nests_each_document_under_its_parent(self, db, tables):
        svc = service(db, tables)
        root = await node(svc, "Racine")
        child = await node(svc, "Enfant", root)
        grand = await node(svc, "Petit-enfant", child)
        other = await node(svc, "Autre")
        body = await svc.tree(READ, "Node")
        assert body["parent"] == "parent_node" and body["total"] == 4 and body["truncated"] is False
        assert {n["name"] for n in body["items"]} == {root, other}
        top = next(n for n in body["items"] if n["name"] == root)
        assert top["title"] == "Racine"
        assert list(descendants(top["children"])) == [child, grand]

    async def test_a_cycle_is_rendered_as_roots_without_looping(self, db, tables):
        svc = service(db, tables)
        a = await node(svc, "A")
        b = await node(svc, "B", a)
        # `a` devient l'enfant de `b` : b -> a -> b. Un Link se vérifie à l'écriture, pas l'acyclicité de l'arbre.
        version = (await svc.get(READ, "Node", a))["version"]
        await svc.update(WRITE, "Node", a, {"title": "A", "parent_node": b}, version)
        body = await svc.tree(READ, "Node")
        assert {n["name"] for n in body["items"]} == {a, b}
        assert all(not n["children"] for n in body["items"])

    async def test_a_non_tree_type_is_refused(self, db, tables):
        with pytest.raises(ValidationFailed, match="arborescent"):
            await service(db, tables).tree(make_ctx("acme", "alice", permissions=["shop:leaf:read"]), "Leaf")

    async def test_reading_the_tree_needs_the_read_grant(self, db, tables):
        with pytest.raises(PermissionDenied):
            await service(db, tables).tree(make_ctx("acme", "bob", permissions=["shop:node:write"]), "Node")


class TestForest:
    """La mise en forêt elle-même : ce qu'on fait d'un parent hors page, d'un cycle, d'un nœud isolé."""

    def test_orders_the_children_under_the_parent(self):
        body = self._forest([stored_doc("a", None), stored_doc("b", "a"), stored_doc("c", "b")])
        assert [n["name"] for n in body["items"]] == ["a"]
        assert body["items"][0]["children"][0]["children"][0]["name"] == "c"

    def test_a_parent_out_of_the_page_becomes_a_root(self):
        body = self._forest([stored_doc("a", None), stored_doc("b", None), stored_doc("c", "z")])
        assert {n["name"] for n in body["items"]} == {"a", "b", "c"}

    def test_a_cycle_falls_back_to_roots(self):
        # b -> a et a -> b : les deux se réclament ; rattacher l'un à l'autre boucle. Les deux restent visibles en racines.
        body = self._forest([stored_doc("a", "b"), stored_doc("b", "a")])
        assert {n["name"] for n in body["items"]} == {"a", "b"}
        assert all(not n["children"] for n in body["items"])

    def test_a_self_parent_is_a_root(self):
        body = self._forest([stored_doc("a", "a")])
        assert [n["name"] for n in body["items"]] == ["a"] and body["items"][0]["children"] == []

    def test_a_blank_parent_is_a_root(self):
        body = self._forest([stored_doc("a", "")])
        assert [n["name"] for n in body["items"]] == ["a"]

    def test_reports_truncation_when_the_page_is_smaller_than_the_total(self):
        body = _tree(parse_definition(NODE), "parent_node", [stored_doc("a", None)], 500)
        assert body["truncated"] is True and body["total"] == 500

    def _forest(self, docs):
        return _tree(parse_definition(NODE), "parent_node", docs, len(docs))
