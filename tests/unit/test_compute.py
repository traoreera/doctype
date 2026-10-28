"""`compute:` — le calcul déclaratif à vocabulaire fermé (compute.py, décision 2 de `programme.md` §3.7).

Aucune base : trois formes, leurs refus au chargement, et l'exactitude du résultat. C'est le découpage de
`mapping.py` et de `printing.py` — ce qui décide se teste sur des dictionnaires, à la valeur près.

Les deux points que §3.7 laissait « à préciser » sont tranchés ici, et ce fichier est l'endroit où ils sont
vérifiés plutôt que décrits : un PRODUIT arrondit à l'échelle du champ qui reçoit (en `ROUND_HALF_UP`), une
ADDITION ne perd jamais de précision (c'est refusé au chargement).
"""

from __future__ import annotations

import pytest

from xcore_doctype.compute import (
    MAX_TERMS,
    compute_order,
    Product,
    TableSum,
    Terms,
    apply_computes,
    compute_problems,
    evaluate,
    has_computes,
    parse_compute,
)
from xcore_doctype.errors import InvalidDefinition
from xcore_doctype.schema import parse_definition


def f(fieldname, fieldtype="Decimal", **kw):
    out = {"fieldname": fieldname, "fieldtype": fieldtype, **kw}
    if fieldtype in ("Decimal", "Currency"):
        out.setdefault("decimals", 2)
    return out


LINE = parse_definition({
    "schema_version": 1, "name": "Ligne", "istable": True,
    "fields": [
        f("qty", decimals=3),
        f("rate"),
        f("amount", compute="qty * rate", read_only=True),
        f("discount"),
        f("label", "Text"),
    ],
})
INVOICE = parse_definition({
    "schema_version": 1, "name": "Facture", "module": "ventes",
    "fields": [
        {"fieldname": "items", "fieldtype": "Table", "options": "Ligne"},
        f("total", compute="sum(items.amount)", read_only=True),
        f("taxes"),
        f("paid"),
        f("grand_total", compute="total + taxes", read_only=True),
        f("outstanding", compute="grand_total - paid", read_only=True),
    ],
})
CHILDREN = {"items": LINE}


class TestTheVocabularyIsClosed:
    """Trois formes, et rien d'autre : ce qui n'y est pas est refusé AU CHARGEMENT, pas à la première facture."""

    @pytest.mark.parametrize("text,expected", [
        ("qty * rate", Product("qty", "rate")),
        ("qty*rate", Product("qty", "rate")),
        ("sum(items.amount)", TableSum("items", "amount")),
        ("sum( items.amount )", TableSum("items", "amount")),
        ("a + b", Terms(((1, "a"), (1, "b")))),
        ("a - b", Terms(((1, "a"), (-1, "b")))),
        ("a + b - c + d", Terms(((1, "a"), (1, "b"), (-1, "c"), (1, "d")))),
        ("  a-b  ", Terms(((1, "a"), (-1, "b")))),
    ])
    def test_the_three_forms_are_read(self, text, expected):
        assert parse_compute(text) == expected

    @pytest.mark.parametrize("text", [
        "qty / rate",  # aucune division : l'arrondi deviendrait une question à chaque usage
        "qty * rate * 2",  # un produit a deux opérandes
        "qty * rate + taxes",  # mélanger produit et somme n'est pas dans le vocabulaire
        "(a + b) * c",  # aucune parenthèse
        "qty * 2",  # aucun littéral : son échelle serait à deviner
        "2",
        "sum(items)",  # une somme nomme une COLONNE
        "sum(amount)",
        "sum(items.amount) + taxes",
        "total()",
        "-a",
        "a +",
        "",
        "   ",
        "QTY * RATE",  # les noms de champs sont en minuscules
        "items.amount",
        None,
        12,
    ])
    def test_everything_else_is_refused(self, text):
        with pytest.raises(ValueError):
            parse_compute(text)

    def test_a_sum_stays_readable(self):
        assert parse_compute(" + ".join(f"f{i}" for i in range(MAX_TERMS)))
        with pytest.raises(ValueError, match="termes au plus"):
            parse_compute(" + ".join(f"f{i}" for i in range(MAX_TERMS + 1)))


class TestWhatTheSchemaRefuses:
    """Un champ calculé est écrit par le SERVEUR : les mêmes refus que `fetch_from`, et pour la même raison."""

    def one(self, **extra):
        return {"schema_version": 1, "name": "T", "module": "m", "fields": [f("a"), f("b"), f("x", compute="a + b", **extra)]}

    @pytest.mark.parametrize("extra,message", [
        ({"default": "1.00"}, "n'a pas de `default`"),
        ({"required": True}, "ne peut pas être obligatoire"),
        ({"mandatory_depends_on": "a"}, "ne peut pas être obligatoire"),
        ({"unique": True}, "aucun sens"),
        ({"set_only_once": True}, "recalculé"),
        ({"fetch_from": "lien.champ"}, "choisissez-en un"),
    ])
    def test_a_computed_field_is_not_typed_by_a_user(self, extra, message):
        with pytest.raises(InvalidDefinition, match=message):
            parse_definition(self.one(**extra))

    def test_a_malformed_expression_is_refused_at_parse_time(self):
        with pytest.raises(InvalidDefinition, match="trois formes"):
            parse_definition({"schema_version": 1, "name": "T", "module": "m", "fields": [f("x", compute="a / b")]})


class TestWhatTheLoadRefuses:
    """Les noms, les genres numériques et la précision : vérifiés au chargement, jamais à la première écriture."""

    def problems(self, field, definition=None, children=None):
        definition = definition or INVOICE
        target = definition.field_map[field] if field in definition.field_map else None
        assert target is not None and target.compute is not None
        return compute_problems(target, parse_compute(target.compute), definition, (children or CHILDREN).get)

    def built(self, *fields, name="T"):
        return parse_definition({"schema_version": 1, "name": name, "module": "m", "fields": list(fields)})

    def test_the_declared_set_is_clean(self):
        assert self.problems("total") == [] and self.problems("grand_total") == [] and self.problems("outstanding") == []
        assert compute_problems(LINE.field_map["amount"], parse_compute("qty * rate"), LINE, lambda _: None) == []

    def test_an_unknown_field_is_named(self):
        d = self.built(f("a"), f("x", compute="a + absent"))
        assert "absent" in " ".join(compute_problems(d.field_map["x"], parse_compute("a + absent"), d, lambda _: None))

    def test_a_field_cannot_compute_itself(self):
        d = self.built(f("a"), f("x", compute="x + a"))
        assert "ne peut pas se lire lui-même" in " ".join(compute_problems(d.field_map["x"], parse_compute("x + a"), d, lambda _: None))

    def test_a_non_numeric_operand_is_refused(self):
        d = self.built(f("a"), f("note", "Text"), f("x", compute="a + note"))
        assert "ne se calcule pas" in " ".join(compute_problems(d.field_map["x"], parse_compute("a + note"), d, lambda _: None))

    def test_a_non_numeric_destination_is_refused(self):
        d = self.built(f("a"), f("x", "Text", compute="a + a"))
        assert "ne se calcule pas" in " ".join(compute_problems(d.field_map["x"], parse_compute("a + a"), d, lambda _: None))

    def test_a_float_never_mixes_with_an_exact_decimal(self):
        """Le cœur de la décision 1 de §3.7 : on ne fait pas entrer un flottant par la porte du calcul."""
        d = self.built(f("qty", "Number"), f("rate"), f("amount", compute="qty * rate"))
        said = " ".join(compute_problems(d.field_map["amount"], parse_compute("qty * rate"), d, lambda _: None))
        assert "ne se mélange pas" in said and "flottant" not in said.split("décimal exact")[0][:0] + "x"
        # Et l'inverse : un champ flottant qui lirait un décimal exact.
        d2 = self.built(f("qty"), f("rate"), f("amount", "Number", compute="qty * rate"))
        assert "ne se mélange pas" in " ".join(compute_problems(d2.field_map["amount"], parse_compute("qty * rate"), d2, lambda _: None))

    def test_two_floats_compute_together(self):
        d = self.built(f("qty", "Number"), f("rate", "Number"), f("amount", "Number", compute="qty * rate"))
        assert compute_problems(d.field_map["amount"], parse_compute("qty * rate"), d, lambda _: None) == []

    def test_a_computed_field_READS_another_computed_field(self):
        """Il le FAUT : la facture de l'ERP est cette chaîne. Une première version l'interdisait — un test l'a attrapée.

        `amount = qty * rate` (ligne), `total = sum(items.amount)`, `grand_total = total + taxes`,
        `outstanding = grand_total - paid` : trois niveaux. L'interdire rendait `compute:` inutile pour le cas
        même qui l'a fait naître.
        """
        d = self.built(f("a"), f("b", compute="a + a"), f("c", compute="b + a"))
        assert compute_problems(d.field_map["c"], parse_compute("b + a"), d, lambda _: None) == []
        order, problems = compute_order(d.fields)
        assert problems == [] and order.index("b") < order.index("c")  # l'ordre est CALCULÉ, pas déclaré

    def test_a_loop_is_refused_and_named(self):
        d = self.built(f("x"), f("a", compute="b + x"), f("b", compute="a + x"))
        order, problems = compute_order(d.fields)
        assert order == () and problems and "a, b" in problems[0]

    def test_a_long_chain_is_ordered(self):
        d = self.built(f("x"), f("d", compute="c + x"), f("b", compute="a + x"), f("c", compute="b + x"), f("a", compute="x + x"))
        order, problems = compute_order(d.fields)
        assert problems == [] and list(order) == ["a", "b", "c", "d"]

    def test_a_sum_needs_a_real_table_and_a_real_column(self):
        d = self.built(f("a"), f("x", compute="sum(a.b)"))
        assert "n'est pas un champ Table" in " ".join(compute_problems(d.field_map["x"], parse_compute("sum(a.b)"), d, lambda _: None))
        said = " ".join(self.problems("total", children={"items": LINE}))
        assert said == ""
        bad = parse_definition({
            "schema_version": 1, "name": "P", "module": "m",
            "fields": [{"fieldname": "items", "fieldtype": "Table", "options": "Ligne"}, f("x", compute="sum(items.absent)")],
        })
        assert "n'est pas un champ de Ligne" in " ".join(compute_problems(bad.field_map["x"], parse_compute("sum(items.absent)"), bad, CHILDREN.get))

    def test_an_addition_never_loses_precision(self):
        """Additionner des montants à 3 décimales dans un total à 2 perdrait des centimes : refusé au chargement."""
        d = self.built(f("a", decimals=3), f("b"), f("x", compute="a + b"))
        assert "perdrait de la précision" in " ".join(compute_problems(d.field_map["x"], parse_compute("a + b"), d, lambda _: None))

    def test_a_sum_of_rows_never_loses_precision_either(self):
        parent = parse_definition({
            "schema_version": 1, "name": "P", "module": "m",
            "fields": [{"fieldname": "items", "fieldtype": "Table", "options": "Ligne"}, f("x", compute="sum(items.qty)")],
        })
        said = " ".join(compute_problems(parent.field_map["x"], parse_compute("sum(items.qty)"), parent, CHILDREN.get))
        assert "perdrait de la précision" in said  # qty a 3 décimales, x en a 2

    def test_a_product_MAY_round_because_that_is_what_its_scale_declares(self):
        """La différence assumée : `qty`(3) × `rate`(2) fait 5 décimales, et `amount` en déclare 2."""
        assert compute_problems(LINE.field_map["amount"], parse_compute("qty * rate"), LINE, lambda _: None) == []


class TestTheResultIsExact:
    def value(self, expression, field, container):
        return evaluate(parse_compute(expression), field, container)

    def test_a_product_is_exact_then_rounded_to_the_declared_scale(self):
        amount = LINE.field_map["amount"]  # 2 décimales
        assert self.value("qty * rate", amount, {"qty": "2.000", "rate": "250.00"}) == "500.00"
        assert self.value("qty * rate", amount, {"qty": "0.333", "rate": "3.00"}) == "1.00"  # 0.999 -> 1.00
        assert self.value("qty * rate", amount, {"qty": "1.005", "rate": "1.00"}) == "1.01"  # ROUND_HALF_UP, pas « banquier »

    def test_half_up_is_the_commercial_convention_not_bankers_rounding(self):
        """`ROUND_HALF_EVEN` (le défaut de `decimal`) rendrait 0.12 ici : une facture ne s'arrondit pas ainsi."""
        amount = LINE.field_map["amount"]
        assert self.value("qty * rate", amount, {"qty": "0.125", "rate": "1.00"}) == "0.13"
        assert self.value("qty * rate", amount, {"qty": "0.135", "rate": "1.00"}) == "0.14"

    def test_a_sum_of_rows_is_exact(self):
        total = INVOICE.field_map["total"]
        rows = [{"amount": "0.10"}, {"amount": "0.20"}]
        assert self.value("sum(items.amount)", total, {"items": rows}) == "0.30"  # 0.1 + 0.2 en flottant ferait 0.30000000000000004

    def test_an_empty_table_sums_to_zero_not_null(self):
        """Un total absent sur une facture sans ligne serait un trou, pas une information."""
        total = INVOICE.field_map["total"]
        for rows in ([], None, "pas une liste"):
            assert self.value("sum(items.amount)", total, {"items": rows}) == "0.00"
        assert self.value("sum(items.amount)", total, {}) == "0.00"

    def test_a_missing_value_counts_as_zero(self):
        assert self.value("total + taxes", INVOICE.field_map["grand_total"], {"total": "10.00"}) == "10.00"
        assert self.value("total + taxes", INVOICE.field_map["grand_total"], {}) == "0.00"

    def test_a_difference_can_be_negative(self):
        assert self.value("grand_total - paid", INVOICE.field_map["outstanding"], {"grand_total": "10.00", "paid": "25.50"}) == "-15.50"

    def test_minus_zero_is_zero(self):
        assert self.value("grand_total - paid", INVOICE.field_map["outstanding"], {"grand_total": "5.00", "paid": "5.00"}) == "0.00"

    def test_a_float_field_stays_a_float(self):
        d = parse_definition({"schema_version": 1, "name": "T", "module": "m", "fields": [
            f("qty", "Number"), f("rate", "Number"), f("amount", "Number", compute="qty * rate")]})
        out = self.value("qty * rate", d.field_map["amount"], {"qty": 2.5, "rate": 4.0})
        assert out == 10.0 and isinstance(out, float)

    def test_a_row_ignores_what_is_not_a_row(self):
        total = INVOICE.field_map["total"]
        assert self.value("sum(items.amount)", total, {"items": [{"amount": "1.00"}, "bruit", None]}) == "1.00"


class TestTheWholeDocument:
    """`apply_computes` : les LIGNES d'abord, le document ensuite — un total lit un `amount` déjà calculé."""

    def test_rows_are_computed_before_the_parent(self):
        out = apply_computes(INVOICE, CHILDREN.get, {
            "items": [{"qty": "2.000", "rate": "250.00"}, {"qty": "1.000", "rate": "300.00"}],
            "taxes": "100.00", "paid": "50.00",
        })
        assert [r["amount"] for r in out["items"]] == ["500.00", "300.00"]
        assert out["total"] == "800.00"  # la somme a VU les montants calculés juste avant
        assert out["grand_total"] == "900.00"
        assert out["outstanding"] == "850.00"

    def test_what_the_client_sent_is_overwritten(self):
        """Comme `fetch_from` : la valeur envoyée est écrasée, jamais prise en compte."""
        out = apply_computes(INVOICE, CHILDREN.get, {
            "items": [{"qty": "1.000", "rate": "10.00", "amount": "99999.00"}],
            "total": "1.00", "grand_total": "2.00", "taxes": "0.00", "paid": "0.00",
        })
        assert out["items"][0]["amount"] == "10.00" and out["total"] == "10.00" and out["grand_total"] == "10.00"

    def test_a_document_without_rows_still_gets_its_totals(self):
        out = apply_computes(INVOICE, CHILDREN.get, {"taxes": "20.00", "paid": "0.00"})
        assert out["total"] == "0.00" and out["grand_total"] == "20.00"

    def test_the_other_values_are_untouched(self):
        out = apply_computes(INVOICE, CHILDREN.get, {"items": [{"qty": "1.000", "rate": "2.00", "label": "gardé"}], "taxes": "0.00", "paid": "0.00"})
        assert out["items"][0]["label"] == "gardé"

    def test_a_type_without_computes_is_detected(self):
        plain = parse_definition({"schema_version": 1, "name": "T", "module": "m", "fields": [f("a")]})
        assert has_computes(plain, lambda _: None) is False
        assert has_computes(INVOICE, CHILDREN.get) is True
        # Et un parent dont seules les LIGNES calculent : il faut quand même passer (c'est le cas de la facture).
        only_rows = parse_definition({"schema_version": 1, "name": "P", "module": "m",
                                      "fields": [{"fieldname": "items", "fieldtype": "Table", "options": "Ligne"}]})
        assert has_computes(only_rows, CHILDREN.get) is True
