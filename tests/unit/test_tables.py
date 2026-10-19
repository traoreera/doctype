"""Tables enfants (champ `Table`) : les lignes d'un document sont un tableau JSON validé par leur DocType enfant."""

from __future__ import annotations

import pytest

from tests.conftest import make_ctx
from xcore_doctype.builder import CompiledDocType
from xcore_doctype.document import DocumentService
from xcore_doctype.errors import DocumentInUse, InvalidDefinition, NotFound, ValidationFailed
from xcore_doctype.grants import GrantCatalog
from xcore_doctype.hooks import HookRegistry
from xcore_doctype.registry import DocTypeRegistry
from xcore_doctype.schema import MAX_TABLE_ROWS, parse_definition

ITEM = {
    "schema_version": 1, "name": "Item", "label": "Article", "module": "sales", "title_field": "item_name",
    "fields": [{"fieldname": "item_name", "fieldtype": "Text", "required": True}],
}
LINE = {
    "schema_version": 1, "name": "InvoiceLine", "label": "Ligne de facture", "istable": True,
    "fields": [
        {"fieldname": "item", "fieldtype": "Link", "options": "Item", "required": True, "label": "Article"},
        {"fieldname": "qty", "fieldtype": "Number", "required": True, "default": 1},
        {"fieldname": "rate", "fieldtype": "Number", "required": True},
        {"fieldname": "taxed", "fieldtype": "Boolean"},
        {"fieldname": "note", "fieldtype": "Text", "hidden": True},
    ],
}
INVOICE = {
    "schema_version": 1, "name": "Invoice", "label": "Facture", "module": "sales",
    "naming": {"series": "FA-{#####}"},
    "fields": [
        {"fieldname": "customer", "fieldtype": "Text", "required": True},
        {"fieldname": "items", "fieldtype": "Table", "options": "InvoiceLine", "required": True, "label": "Lignes"},
        {"fieldname": "total", "fieldtype": "Number"},
    ],
    "hooks": {"before_save": ["t.totals"]},
}
GRANTS = [f"sales:{t}:{a}" for t in ("item", "invoice") for a in ("read", "write", "delete")]


class Log:
    def info(self, *a, **k): ...
    def error(self, *a, **k): ...


def line(item, qty=1, rate=10, **more):
    return {"item": item, "qty": qty, "rate": rate, **more}


def registry(*docs):
    reg = DocTypeRegistry()
    for d in docs:
        reg.register(parse_definition(d))
    return reg


@pytest.fixture
def reg():
    return registry(ITEM, LINE, INVOICE)


@pytest.fixture
def svc(db, tables, reg):
    hooks = HookRegistry()

    async def totals(event):
        rows = event.data.get("items") or []
        event.data["total"] = sum(r["qty"] * r["rate"] for r in rows)

    hooks.register("t.totals", totals)
    return DocumentService(reg, db, tables, Log(), hooks=hooks)


def ctx(tenant="acme", user="alice", perms=GRANTS):
    return make_ctx(tenant, user, permissions=perms)


async def item(svc, name="Vis", **kw):
    return (await svc.create(kw.pop("ctx", ctx()), "Item", {"item_name": name}))["name"]


async def invoice(svc, items, **kw):
    return await svc.create(kw.pop("ctx", ctx()), "Invoice", {"customer": "ACME", "items": items, **kw})


class TestDefinition:
    BASE = {"schema_version": 1, "name": "P", "fields": [{"fieldname": "a", "fieldtype": "Text"}]}

    def test_a_table_field_names_its_row_doctype(self):
        d = parse_definition(INVOICE)
        assert d.field_map["items"].options == "InvoiceLine"

    @pytest.mark.parametrize(
        "field",
        [
            {"fieldname": "t", "fieldtype": "Table"},  # sans `options`
            {"fieldname": "t", "fieldtype": "Table", "options": "L", "in_list_view": True},
            {"fieldname": "t", "fieldtype": "Table", "options": "L", "default": []},
            {"fieldname": "t", "fieldtype": "Table", "options": ["a"]},
        ],
    )
    def test_invalid_table_fields(self, field):
        with pytest.raises(InvalidDefinition):
            parse_definition({**self.BASE, "fields": [field]})

    def test_a_condition_cannot_read_a_table(self):
        fields = [{"fieldname": "t", "fieldtype": "Table", "options": "L"}, {"fieldname": "a", "fieldtype": "Text", "depends_on": "t"}]
        with pytest.raises(InvalidDefinition, match="champ Table"):
            parse_definition({**self.BASE, "fields": fields})

    def test_a_table_can_itself_be_hidden_by_a_condition(self):
        fields = [{"fieldname": "a", "fieldtype": "Text"}, {"fieldname": "t", "fieldtype": "Table", "options": "L", "depends_on": "a == 'x'"}]
        assert parse_definition({**self.BASE, "fields": fields}).field_map["t"].depends_on

    def test_the_list_never_shows_a_table(self):
        d = parse_definition(INVOICE)
        assert d.list_columns() == ["customer", "total"]

    def test_a_child_keeps_only_fields(self):
        assert parse_definition(LINE).istable is True

    @pytest.mark.parametrize(
        ("extra", "named"),
        [
            ({"module": "sales"}, "module"),
            ({"permissions": {"read": ["a:b:c"]}}, "permissions"),
            ({"hooks": {"validate": ["x.y"]}}, "hooks"),
            ({"layout": [{"columns": [["a"]]}]}, "layout"),
            ({"title_field": "a"}, "title_field"),
            ({"naming": {"series": "L-{#}"}}, "naming"),
        ],
    )
    def test_a_child_refuses_what_it_cannot_apply(self, extra, named):
        with pytest.raises(InvalidDefinition, match=named):
            parse_definition({**self.BASE, "istable": True, **extra})

    def test_no_nested_tables_and_no_read_only_condition_in_rows(self):
        nested = [{"fieldname": "t", "fieldtype": "Table", "options": "L"}]
        with pytest.raises(InvalidDefinition, match="imbriquées"):
            parse_definition({**self.BASE, "istable": True, "fields": nested})
        cond = [{"fieldname": "a", "fieldtype": "Text"}, {"fieldname": "b", "fieldtype": "Text", "read_only_depends_on": "a"}]
        with pytest.raises(InvalidDefinition, match="read_only_depends_on"):
            parse_definition({**self.BASE, "istable": True, "fields": cond})

    def test_a_child_has_no_grants(self):
        assert parse_definition(LINE).grant_specs() == {}


class TestRegistry:
    def test_a_parent_needs_its_child_registered_first(self):
        with pytest.raises(InvalidDefinition, match=r"InvoiceLine.*inconnu"):
            registry(ITEM, INVOICE)

    def test_a_table_must_point_to_a_child_doctype(self):
        with pytest.raises(InvalidDefinition, match="n'est pas un DocType enfant"):
            registry(ITEM, {**INVOICE, "fields": [{"fieldname": "items", "fieldtype": "Table", "options": "Item"}]})

    def test_load_dir_loads_children_first_whatever_the_file_order(self, tmp_path):
        import yaml

        (tmp_path / "a_invoice.yaml").write_text(yaml.safe_dump(INVOICE))  # alphabétiquement AVANT son enfant
        (tmp_path / "b_item.yaml").write_text(yaml.safe_dump(ITEM))
        (tmp_path / "z_line.yaml").write_text(yaml.safe_dump(LINE))
        reg = DocTypeRegistry()
        assert sorted(reg.load_dir(tmp_path)) == ["Invoice", "InvoiceLine", "Item"]
        assert reg.get("Invoice").children["InvoiceLine"].istable
        reg.validate_links()

    def test_a_link_cannot_point_to_a_child(self):
        other = {"schema_version": 1, "name": "Other", "fields": [{"fieldname": "l", "fieldtype": "Link", "options": "InvoiceLine"}]}
        reg = registry(ITEM, LINE, other)
        with pytest.raises(InvalidDefinition, match="DocType enfant"):
            reg.validate_links()

    def test_referrers_include_links_held_by_rows(self, reg):
        from xcore_doctype.registry import Referrer

        assert reg.referrers("Item") == [Referrer("Invoice", "item", "items")]
        assert reg.referrers("Invoice") == []

    def test_no_grants_are_cited_for_a_child(self, reg):
        assert all("invoiceline" not in g for g in GrantCatalog(reg).cited())


class TestValidation:
    @pytest.fixture
    def compiled(self, reg) -> CompiledDocType:
        return reg.get("Invoice")

    def test_valid_rows_are_stored_without_empty_cells(self, compiled):
        out = compiled.validate_data({"customer": "c", "items": [{"item": "a", "qty": 2, "rate": 1.5, "note": None}]})
        assert out["items"] == [{"item": "a", "qty": 2, "rate": 1.5}]

    def test_an_empty_table_is_the_same_as_no_table(self, compiled):
        with pytest.raises(ValidationFailed) as e:
            compiled.validate_data({"customer": "c", "items": []})  # `items` est obligatoire
        assert any(d["field"] == "items" and d["type"] == "missing" for d in e.value.details)

    def test_an_optional_empty_table_is_not_stored(self, db):
        reg = registry(ITEM, LINE, {**INVOICE, "fields": [{"fieldname": "items", "fieldtype": "Table", "options": "InvoiceLine"}], "hooks": {}})
        assert reg.get("Invoice").validate_data({"items": []}) == {}

    def test_errors_name_the_row_and_the_cell(self, compiled):
        with pytest.raises(ValidationFailed) as e:
            compiled.validate_data({"customer": "c", "items": [line("a"), {"item": "a", "qty": "2"}, {"item": "a", "qty": 1, "rate": 1, "x": 1}]})
        got = {(d["field"], d["type"]) for d in e.value.details}
        assert ("items.1.qty", "value_error") in got  # « 2 » n'est pas 2 : pas de coercition, dans les lignes non plus
        assert ("items.1.rate", "missing") in got
        assert ("items.2.x", "extra_forbidden") in got

    @pytest.mark.parametrize("rows", ["abc", {"item": "a"}, [1], [None], [["a"]]])
    def test_the_shape_is_checked(self, compiled, rows):
        with pytest.raises(ValidationFailed):
            compiled.validate_data({"customer": "c", "items": rows})

    def test_a_table_has_a_row_limit(self, compiled):
        ok = [line("a")] * MAX_TABLE_ROWS
        assert len(compiled.validate_data({"customer": "c", "items": ok})["items"]) == MAX_TABLE_ROWS
        with pytest.raises(ValidationFailed):
            compiled.validate_data({"customer": "c", "items": ok + [line("a")]})

    def test_nan_and_infinity_are_refused_in_rows_too(self, compiled):
        for bad in (float("nan"), float("inf")):
            with pytest.raises(ValidationFailed):
                compiled.validate_data({"customer": "c", "items": [line("a", rate=bad)]})


class TestService:
    async def test_create_read_update_and_remove_rows(self, svc):
        vis = await item(svc)
        doc = await invoice(svc, [line(vis, 2, 10), line(vis, 1, 5)])
        assert doc["name"] == "FA-00001"
        assert doc["data"]["items"] == [line(vis, 2, 10), line(vis, 1, 5)]
        assert doc["data"]["total"] == 25  # le hook before_save a lu les lignes

        data = {**doc["data"], "items": [line(vis, 3, 10)]}
        upd = await svc.update(ctx(), "Invoice", doc["name"], data, doc["version"])
        assert upd["data"]["items"] == [line(vis, 3, 10)] and upd["data"]["total"] == 30
        assert (await svc.get(ctx(), "Invoice", doc["name"]))["data"]["items"] == [line(vis, 3, 10)]

    async def test_a_required_table_cannot_be_emptied(self, svc):
        vis = await item(svc)
        doc = await invoice(svc, [line(vis)])
        with pytest.raises(ValidationFailed):
            await svc.update(ctx(), "Invoice", doc["name"], {"customer": "ACME", "items": []}, doc["version"])

    async def test_a_missing_item_in_a_row_is_reported_at_its_cell(self, svc):
        vis = await item(svc)
        with pytest.raises(ValidationFailed) as e:
            await invoice(svc, [line(vis), line("nope"), line("zzz")])
        assert [(d["field"], d["type"]) for d in e.value.details] == [("items.1.item", "link_not_found"), ("items.2.item", "link_not_found")]
        assert e.value.details[0]["msg"] == "Article introuvable : 'nope'"
        assert (await svc.list(ctx(), "Invoice"))["total"] == 0

    async def test_an_unreadable_item_is_refused_like_a_missing_one(self, svc):
        vis = await item(svc)
        no_item_read = ctx(perms=["sales:invoice:read", "sales:invoice:write"])
        with pytest.raises(ValidationFailed) as e:
            await invoice(svc, [line(vis)], ctx=no_item_read)
        assert e.value.details[0]["field"] == "items.0.item"

    async def test_unchanged_lines_are_not_rechecked_even_when_they_move(self, svc, db, tables):
        from xcore_doctype.permissions import Scope
        from xcore_doctype.query import Query

        a, b = await item(svc, "A"), await item(svc, "B")
        doc = await invoice(svc, [line(a), line(b)])
        # L'article B disparaît par un chemin qui contourne le service (import, ancienne donnée) : la ligne devient orpheline.
        async with db.session() as s:
            await Query(s, ctx(), tables).delete("Item", b, scope=Scope.everything("delete"))
        moved = {"customer": "ACME", "items": [line(b), line(a, 5)]}  # B change de place, A change de quantité
        upd = await svc.update(ctx(), "Invoice", doc["name"], moved, doc["version"])
        assert [r["item"] for r in upd["data"]["items"]] == [b, a]
        with pytest.raises(ValidationFailed):  # un NOUVEL article inexistant, lui, est refusé
            await svc.update(ctx(), "Invoice", doc["name"], {**moved, "items": [*moved["items"], line("nope")]}, upd["version"])

    async def test_an_item_cited_by_a_row_cannot_be_deleted(self, svc):
        vis, ecrou = await item(svc, "Vis"), await item(svc, "Écrou")
        doc = await invoice(svc, [line(ecrou), line(vis)])  # `vis` n'est pas dans la première ligne
        with pytest.raises(DocumentInUse) as e:
            await svc.delete(ctx(), "Item", vis)
        assert e.value.details == [{"doctype": "Invoice", "field": "item", "table": "items"}]
        assert "Facture (Lignes › Article)" in e.value.message
        assert doc["name"] not in e.value.message
        upd = await svc.update(ctx(), "Invoice", doc["name"], {"customer": "ACME", "items": [line(ecrou)]}, doc["version"])
        await svc.delete(ctx(), "Item", vis)  # plus aucune ligne ne le cite
        with pytest.raises(DocumentInUse):
            await svc.delete(ctx(), "Item", ecrou)
        assert upd["version"] == 2

    async def test_other_tenants_rows_do_not_block(self, svc):
        mine = await item(svc, "Vis")
        theirs = await item(svc, "Vis", ctx=ctx("globex", "bob"))
        await invoice(svc, [line(theirs)], ctx=ctx("globex", "bob"))
        await svc.delete(ctx("acme"), "Item", mine)
        with pytest.raises(DocumentInUse):
            await svc.delete(ctx("globex", "bob"), "Item", theirs)

    async def test_a_child_doctype_does_not_exist_for_the_api(self, svc):
        c = ctx(perms=GRANTS + ["sales:invoiceline:read", "sales:invoiceline:write"])
        for call in (
            svc.get(c, "InvoiceLine", "x"),
            svc.list(c, "InvoiceLine"),
            svc.search(c, "InvoiceLine"),
            svc.create(c, "InvoiceLine", {"item": "a", "rate": 1}),
            svc.update(c, "InvoiceLine", "x", {}, 1),
            svc.delete(c, "InvoiceLine", "x"),
        ):
            with pytest.raises(NotFound) as e:
                await call
            assert e.value.code == "doctype_not_found"
        with pytest.raises(NotFound):
            svc.get_meta(c, "InvoiceLine")
        assert [m["name"] for m in svc.list_meta(c)] == ["Invoice", "Item"]

    async def test_meta_carries_the_row_fields_for_the_grid(self, svc):
        meta = svc.get_meta(ctx(), "Invoice")
        assert meta["children"]["InvoiceLine"]["label"] == "Ligne de facture"
        names = [f["fieldname"] for f in meta["children"]["InvoiceLine"]["fields"]]
        assert names == ["item", "qty", "rate", "taxed", "note"]
        assert meta["children"]["InvoiceLine"]["fields"][1]["default"] == 1
        assert [f for f in meta["fields"] if f["fieldname"] == "items"][0]["options"] == "InvoiceLine"
        assert "children" not in svc.get_meta(ctx(), "Item")
        assert meta["list_columns"] == ["customer", "total"]

    async def test_a_table_cannot_be_filtered_or_sorted(self, svc):
        with pytest.raises(ValidationFailed, match="Table"):
            await svc.list(ctx(), "Invoice", filters={"items": "x"})
        with pytest.raises(ValidationFailed, match="Table"):
            await svc.list(ctx(), "Invoice", order_by="items")

    async def test_a_hook_may_rewrite_rows_and_they_are_revalidated(self, db, tables, reg):
        hooks = HookRegistry()

        async def bad(event):
            event.data["items"] = [{"item": "a", "qty": "deux", "rate": 1}]

        hooks.register("t.totals", bad)
        s = DocumentService(reg, db, tables, Log(), hooks=hooks)
        vis = await item(s)
        with pytest.raises(ValidationFailed) as e:
            await invoice(s, [line(vis)])
        assert e.value.details[0]["field"] == "items.0.qty"
