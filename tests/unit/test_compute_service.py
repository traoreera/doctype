"""`compute:` contre une vraie base : ce que le serveur écrit à chaque enregistrement (§3.7, marche 1b des hooks).

Le calcul lui-même est testé sur des dictionnaires (`test_compute.py`). Ce qui se joue ICI : qu'un total soit
écrit SANS que le client l'envoie, qu'il soit recalculé à chaque modification, qu'une valeur envoyée par le
client soit écrasée, et qu'un hook qui change une quantité voie le total suivre.

C'est la raison d'être du mécanisme : sur le jeu d'ERP, 129 champs de montant clés sont `read_only` et rien ne
les remplissait — un hook de comptabilisation aurait posté des totaux vides.
"""

from __future__ import annotations

import pytest

from tests.conftest import make_ctx
from xcore_doctype.document import DocumentService
from xcore_doctype.hooks import HookRegistry
from xcore_doctype.registry import DocTypeRegistry
from xcore_doctype.schema import parse_definition

LINE = {
    "schema_version": 1, "name": "Ligne", "istable": True,
    "fields": [
        {"fieldname": "article", "fieldtype": "Text"},
        {"fieldname": "qty", "fieldtype": "Decimal", "decimals": 3},
        {"fieldname": "rate", "fieldtype": "Currency", "decimals": 2},
        # La forme canonique de l'ERP : le montant d'une ligne n'est pas saisi, il se calcule.
        {"fieldname": "amount", "fieldtype": "Currency", "decimals": 2, "compute": "qty * rate", "read_only": True},
    ],
}
INVOICE = {
    "schema_version": 1, "name": "Facture", "module": "sales",
    "fields": [
        {"fieldname": "client", "fieldtype": "Text", "required": True},
        {"fieldname": "items", "fieldtype": "Table", "options": "Ligne"},
        {"fieldname": "total", "fieldtype": "Currency", "decimals": 2, "compute": "sum(items.amount)", "read_only": True},
        {"fieldname": "taxes", "fieldtype": "Currency", "decimals": 2},
        {"fieldname": "paid", "fieldtype": "Currency", "decimals": 2},
        {"fieldname": "grand_total", "fieldtype": "Currency", "decimals": 2, "compute": "total + taxes", "read_only": True},
        {"fieldname": "outstanding", "fieldtype": "Currency", "decimals": 2, "compute": "grand_total - paid", "read_only": True},
    ],
}

ALL = ["sales:facture:read", "sales:facture:write", "sales:facture:delete"]
ALICE = make_ctx("acme", "alice", permissions=ALL)


class Log:
    def info(self, *a, **k): pass
    def warning(self, *a, **k): pass
    def error(self, *a, **k): pass


@pytest.fixture
async def svc(db, tables):
    reg = DocTypeRegistry()
    for raw in (LINE, INVOICE):
        reg.register(parse_definition(raw))
    reg.validate_links()
    return DocumentService(reg, db, tables, Log())


async def invoice(svc, ctx=ALICE, rows=(("Chaise", "2.000", "250.00"),), **extra):
    data = {
        "client": "Durand",
        "items": [{"article": a, "qty": q, "rate": r} for a, q, r in rows],
        "taxes": "0.00", "paid": "0.00",
        **extra,
    }
    return await svc.create(ctx, "Facture", data)


class TestTheServerFillsTheTotals:
    async def test_the_whole_chain_is_written(self, svc):
        doc = await invoice(svc, rows=(("Chaise", "2.000", "250.00"), ("Table", "1.000", "300.00")), taxes="100.00", paid="50.00")
        data = doc["data"]
        assert [r["amount"] for r in data["items"]] == ["500.00", "300.00"]
        assert data["total"] == "800.00"
        assert data["grand_total"] == "900.00"
        assert data["outstanding"] == "850.00"

    async def test_it_is_STORED_not_just_returned(self, svc):
        doc = await invoice(svc)
        again = await svc.get(ALICE, "Facture", doc["name"])
        assert again["data"]["total"] == "500.00" and again["data"]["items"][0]["amount"] == "500.00"

    async def test_a_value_sent_by_the_client_is_overwritten(self, svc):
        """Comme `fetch_from` : un client qui envoie un total mensonger ne le voit pas enregistré."""
        doc = await invoice(svc, total="99999.00", grand_total="1.00")
        assert doc["data"]["total"] == "500.00" and doc["data"]["grand_total"] == "500.00"

    async def test_it_is_recomputed_at_every_update(self, svc):
        doc = await invoice(svc)
        changed = await svc.update(
            ALICE, "Facture", doc["name"],
            {"client": "Durand", "items": [{"article": "Chaise", "qty": "3.000", "rate": "250.00"}], "taxes": "10.00", "paid": "0.00"},
            doc["version"],
        )
        assert changed["data"]["items"][0]["amount"] == "750.00"
        assert changed["data"]["total"] == "750.00" and changed["data"]["grand_total"] == "760.00"

    async def test_an_invoice_without_a_line_has_a_zero_total_not_a_hole(self, svc):
        doc = await svc.create(ALICE, "Facture", {"client": "Durand", "taxes": "20.00", "paid": "0.00"})
        assert doc["data"]["total"] == "0.00" and doc["data"]["grand_total"] == "20.00"

    async def test_removing_a_line_lowers_the_total(self, svc):
        doc = await invoice(svc, rows=(("Chaise", "2.000", "250.00"), ("Table", "1.000", "300.00")))
        changed = await svc.update(
            ALICE, "Facture", doc["name"],
            {"client": "Durand", "items": [{"article": "Chaise", "qty": "2.000", "rate": "250.00"}], "taxes": "0.00", "paid": "0.00"},
            doc["version"],
        )
        assert changed["data"]["total"] == "500.00"


class TestExactness:
    async def test_the_cents_are_exact(self, svc):
        """0.1 + 0.2 en flottant fait 0.30000000000000004 : c'est tout l'objet du décimal exact."""
        doc = await invoice(svc, rows=(("a", "1.000", "0.10"), ("b", "1.000", "0.20")))
        assert doc["data"]["total"] == "0.30"

    async def test_a_product_rounds_to_the_declared_scale(self, svc):
        """`qty`(3) × `rate`(2) fait 5 décimales ; `amount` en déclare 2, et c'est ce que déclarer veut dire."""
        doc = await invoice(svc, rows=(("a", "0.333", "3.00"),))
        assert doc["data"]["items"][0]["amount"] == "1.00"  # 0.999 arrondi

    async def test_a_hundred_lines_still_add_up_exactly(self, svc):
        doc = await invoice(svc, rows=tuple((f"a{i}", "1.000", "0.01") for i in range(100)))
        assert doc["data"]["total"] == "1.00"


class TestTheClientCannotTypeIt:
    async def test_a_computed_field_is_read_only_in_meta(self, svc):
        meta = svc.fetch_meta  # la route passe par là ; ici on lit la définition compilée
        fields = {f.fieldname: f for f in svc._registry.get("Facture").definition.fields}
        assert fields["total"].compute == "sum(items.amount)" and fields["total"].read_only
        assert meta is not None

    async def test_bulk_refuses_a_computed_field(self, svc):
        """Un champ calculé ne se modifie pas en lot : il ne se modifie pas du tout."""
        from xcore_doctype.errors import ValidationFailed

        doc = await invoice(svc)
        with pytest.raises(ValidationFailed) as e:
            await svc.bulk(ALICE, "Facture", "update", [doc["name"]], data={"total": "1.00"})
        assert any(d.get("type") == "bulk_field" for d in e.value.details)


class TestAHookSeesTheTotals:
    """Un hook de confiance qui change une quantité doit voir le total SUIVRE : c'est ce que les hooks de
    comptabilisation attendront (`programme.md` §3.7 : le calcul précède les hooks)."""

    @pytest.fixture
    async def hooked(self, db, tables):
        reg = DocTypeRegistry()
        for raw in (LINE, {**INVOICE, "hooks": {"before_save": ["sales.double_qty"]}}):
            reg.register(parse_definition(raw))
        reg.validate_links()
        hooks = HookRegistry()

        async def double_qty(event):
            rows = event.data.get("items") or []
            event.data["items"] = [{**r, "qty": "4.000"} for r in rows]

        hooks.register("sales.double_qty", double_qty)
        return DocumentService(reg, db, tables, Log(), hooks=hooks)

    async def test_what_a_hook_changes_is_recomputed(self, hooked):
        doc = await invoice(hooked)
        # Le hook a posé qty=4 : le montant de la ligne ET les totaux doivent le refléter, pas la valeur reçue.
        assert doc["data"]["items"][0]["qty"] == "4.000"
        assert doc["data"]["items"][0]["amount"] == "1000.00"
        assert doc["data"]["total"] == "1000.00" and doc["data"]["grand_total"] == "1000.00"


class TestFiltersAndSorts:
    async def test_a_computed_total_filters_and_sorts_like_any_decimal(self, svc):
        """Rien de spécial : la valeur est stockée, donc le SQL ordinaire la trouve."""
        await invoice(svc, rows=(("a", "1.000", "100.00"),))
        await invoice(svc, rows=(("b", "1.000", "300.00"),))
        big = await svc.list(ALICE, "Facture", filters={"total": [">=", "200.00"]})
        assert big["total"] == 1 and big["items"][0]["data"]["total"] == "300.00"
        ordered = await svc.list(ALICE, "Facture", order_by="total", descending=True)
        assert [d["data"]["total"] for d in ordered["items"]] == ["300.00", "100.00"]
