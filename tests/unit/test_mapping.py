"""« Créer depuis » : la logique pure du préremplissage (doc/mapper.md).

Aucune base, aucun droit : `mapping.py` reçoit un document source et ce qui a déjà été fait, il rend des
valeurs. C'est ce découpage qui rend la recopie testable sur des dictionnaires.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from xcore_doctype.mapping import (
    copied_fields,
    copied_row_fields,
    map_document,
    map_rows,
    mappable_types,
    nothing_left,
    remaining_quantity,
    same_number_kind,
)
from xcore_doctype.schema import CreatedFrom, MappingLines, MappingQuantity, parse_definition


def dt(name, fields, **extra):
    return parse_definition({"schema_version": 1, "name": name, "module": "sales", "fields": fields, **extra})


def child(name, fields):
    return parse_definition({"schema_version": 1, "name": name, "istable": True, "fields": fields})


def f(fieldname, fieldtype="Text", **kw):
    return {"fieldname": fieldname, "fieldtype": fieldtype, **kw}


ORDER_LINE = child("Order Line", [f("item", "Link", options="Item"), f("qty", "Number"), f("note")])
MOVE_LINE = child(
    "Move Line",
    [f("item", "Link", options="Item"), f("qty", "Number"), f("note"), f("src_row", read_only=True), f("src_doc", "Link", options="Order", read_only=True)],
)
ORDER = dt("Order", [f("client", "Link", options="Customer"), f("items", "Table", options="Order Line")], submittable=True)
MOVE = dt("Move", [f("client", "Link", options="Customer"), f("items", "Table", options="Move Line")])

LINES = MappingLines(**{"from": "items", "reference": "src_row", "document": "src_doc", "quantity": MappingQuantity(source="qty", target="qty")})
MAPPING = CreatedFrom(source="Order", lines={"items": LINES})


class TestWhatTheConventionCopies:
    def test_a_field_of_the_same_name_and_type_is_copied(self):
        assert copied_fields(MOVE, ORDER, CreatedFrom(source="Order")) == {"client": "client"}

    def test_a_table_is_never_copied_as_a_field(self):
        # Les tables passent par `lines`, jamais par la recopie de champs.
        assert "items" not in copied_fields(MOVE, ORDER, CreatedFrom(source="Order"))

    @pytest.mark.parametrize(
        "extra",
        [
            {"no_copy": True},
            {"unique": True},
            {"read_only": True},
            {"fetch_from": "client.name"},
        ],
    )
    def test_what_never_travels(self, extra):
        """La même liste que « Dupliquer » : un `no_copy`, un `unique`, un `read_only` (le serveur le refuserait
        au retour), un champ que le serveur recalcule."""
        target = dt("T", [f("client", "Link", options="Customer"), f("ref", **extra)])
        source = dt("Order", [f("client", "Link", options="Customer"), f("ref")], submittable=True)
        assert copied_fields(target, source, CreatedFrom(source="Order")) == {"client": "client"}

    def test_a_secret_and_a_file_never_travel(self):
        target = dt("T", [f("pwd", "Password"), f("pic", "Image"), f("doc", "Attach"), f("ok")])
        source = dt("Order", [f("pwd", "Password"), f("pic", "Image"), f("doc", "Attach"), f("ok")], submittable=True)
        assert copied_fields(target, source, CreatedFrom(source="Order")) == {"ok": "ok"}

    def test_the_naming_field_never_travels(self):
        """Il désigne un autre document : le copier donnerait deux documents du même nom."""
        target = dt("T", [f("code", required=True), f("ok")], naming={"field": "code"})
        source = dt("Order", [f("code"), f("ok")], submittable=True)
        assert copied_fields(target, source, CreatedFrom(source="Order")) == {"ok": "ok"}

    def test_an_exception_renames_and_an_exclusion_removes(self):
        target = dt("T", [f("buyer", "Link", options="Customer"), f("note")])
        source = dt("Order", [f("client", "Link", options="Customer"), f("note")], submittable=True)
        m = CreatedFrom(source="Order", fields={"buyer": "client"}, exclude=["note"])
        assert copied_fields(target, source, m) == {"buyer": "client"}

    def test_the_trace_fields_are_not_part_of_the_row_copy(self):
        """Le service les pose lui-même, puis les vérifie : ils ne sont pas une recopie."""
        assert copied_row_fields(MOVE_LINE, ORDER_LINE, LINES) == {"item": "item", "qty": "qty", "note": "note"}


class TestTypesThatMayFillEachOther:
    def test_a_decimal_needs_the_same_number_of_decimals(self):
        two = f("x", "Decimal", decimals=2)
        three = f("x", "Decimal", decimals=3)
        d = dt("A", [two, f("y")])
        e = dt("B", [three, f("y")])
        assert not mappable_types(d.field_map["x"], e.field_map["x"])
        assert mappable_types(d.field_map["x"], d.field_map["x"])

    def test_a_link_needs_the_same_target(self):
        a = dt("A", [f("l", "Link", options="Item")])
        b = dt("B", [f("l", "Link", options="Warehouse")])
        assert not mappable_types(a.field_map["l"], b.field_map["l"])

    def test_a_currency_fills_a_decimal_of_the_same_scale(self):
        a = dt("A", [f("m", "Currency", decimals=2)])
        b = dt("B", [f("m", "Decimal", decimals=2)])
        assert mappable_types(a.field_map["m"], b.field_map["m"])

    def test_a_quantity_pair_must_keep_subtraction_exact(self):
        num = dt("A", [f("q", "Number"), f("i", "Int"), f("d", "Decimal", decimals=3)])
        assert same_number_kind(num.field_map["q"], num.field_map["i"])
        assert same_number_kind(num.field_map["d"], num.field_map["d"])
        # Un flottant et une valeur exacte : la soustraction passerait par un double.
        assert not same_number_kind(num.field_map["q"], num.field_map["d"])


class TestWhatIsLeftToDo:
    def test_it_is_the_difference(self):
        field = MOVE_LINE.field_map["qty"]
        assert remaining_quantity(120, Decimal(40), field) == 80.0

    def test_it_is_never_negative(self):
        """Servi au-delà du demandé, il ne reste RIEN — pas « −5 », qui préremplirait un mouvement inverse."""
        field = MOVE_LINE.field_map["qty"]
        assert remaining_quantity(10, Decimal(15), field) == 0.0

    def test_a_decimal_stays_exact_and_canonical(self):
        line = child("L", [f("q", "Decimal", decimals=3)])
        got = remaining_quantity("0.300", Decimal("0.100"), line.field_map["q"])
        assert got == "0.200"  # une chaîne canonique, jamais 0.19999999999999998

    def test_an_int_stays_an_int(self):
        line = child("L", [f("q", "Int")])
        got = remaining_quantity(7, Decimal(2), line.field_map["q"])
        assert got == 5 and isinstance(got, int)


class TestTheRowsOfTheDraft:
    def test_a_fresh_source_is_copied_whole_with_its_trace(self):
        rows = map_rows(MOVE_LINE, ORDER_LINE, LINES, [{"item": "ITEM-1", "qty": 120.0, "note": "urgent"}], source_name="SO-1")
        assert rows == [{"item": "ITEM-1", "qty": 120.0, "note": "urgent", "src_row": "0", "src_doc": "SO-1"}]

    def test_what_is_already_done_is_deducted(self):
        rows = map_rows(
            MOVE_LINE, ORDER_LINE, LINES,
            [{"item": "ITEM-1", "qty": 120.0}],
            source_name="SO-1", done={0: Decimal(40)},
        )
        assert rows[0]["qty"] == 80.0

    def test_a_settled_line_is_not_offered(self):
        rows = map_rows(
            MOVE_LINE, ORDER_LINE, LINES,
            [{"item": "A", "qty": 10.0}, {"item": "B", "qty": 5.0}],
            source_name="SO-1", done={0: Decimal(10)},
        )
        assert [r["item"] for r in rows] == ["B"]

    def test_the_reference_is_the_index_in_the_SOURCE_table(self):
        """Et non le rang dans le brouillon : une ligne soldée décale l'un sans décaler l'autre."""
        rows = map_rows(
            MOVE_LINE, ORDER_LINE, LINES,
            [{"item": "A", "qty": 1.0}, {"item": "B", "qty": 1.0}, {"item": "C", "qty": 1.0}],
            source_name="SO-1", done={0: Decimal(1)},
        )
        assert [(r["item"], r["src_row"]) for r in rows] == [("B", "1"), ("C", "2")]

    def test_without_a_quantity_pair_every_row_comes_at_full_measure(self):
        lines = MappingLines(**{"from": "items", "reference": "src_row"})
        rows = map_rows(MOVE_LINE, ORDER_LINE, lines, [{"item": "A", "qty": 9.0}], source_name="SO-1", done={0: Decimal(9)})
        assert rows == [{"item": "A", "qty": 9.0, "src_row": "0"}]


class TestTheWholeDraft:
    def test_header_and_rows_together(self):
        values = map_document(
            MOVE, ORDER, MAPPING,
            {"client": "CUST-1", "items": [{"item": "ITEM-1", "qty": 120.0}]},
            source_name="SO-1",
            children={"items": (MOVE_LINE, ORDER_LINE)},
            done={"items": {0: Decimal(100)}},
        )
        assert values["client"] == "CUST-1"
        assert values["items"] == [{"item": "ITEM-1", "qty": 20.0, "src_row": "0", "src_doc": "SO-1"}]

    def test_a_fully_served_source_has_nothing_left(self):
        values = map_document(
            MOVE, ORDER, MAPPING,
            {"client": "CUST-1", "items": [{"item": "ITEM-1", "qty": 120.0}]},
            source_name="SO-1",
            children={"items": (MOVE_LINE, ORDER_LINE)},
            done={"items": {0: Decimal(120)}},
        )
        assert values["items"] == []
        assert nothing_left(values, MAPPING)  # le bouton ne doit plus être offert

    def test_a_mapping_without_quantities_is_never_considered_settled(self):
        m = CreatedFrom(source="Order", lines={"items": MappingLines(**{"from": "items"})})
        assert not nothing_left({"items": []}, m)


class TestDeclarationsRefusedOutright:
    def test_a_quantity_without_a_reference_is_refused(self):
        """Sans référence, on ne sait pas quelle ligne source a déjà été servie."""
        with pytest.raises(ValueError, match="exige `reference`"):
            MappingLines(**{"from": "items", "quantity": {"source": "qty", "target": "qty"}})

    def test_the_trace_fields_cannot_be_the_same(self):
        with pytest.raises(ValueError, match="ne peuvent pas être le même champ"):
            MappingLines(**{"from": "items", "reference": "x", "document": "x"})

    def test_two_declarations_for_one_source_are_refused(self):
        with pytest.raises(Exception, match="même source"):
            dt("T", [f("x")], created_from=[{"source": "Order"}, {"source": "Order"}])

    def test_a_child_and_a_single_cannot_be_created_from_anything(self):
        with pytest.raises(Exception, match="ligne de son parent"):
            parse_definition({"schema_version": 1, "name": "L", "istable": True, "fields": [f("x")], "created_from": [{"source": "Order"}]})
        with pytest.raises(Exception, match="document unique"):
            dt("S", [f("x")], single=True, created_from=[{"source": "Order"}])
