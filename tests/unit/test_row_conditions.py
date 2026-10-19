"""Conditions dans les LIGNES d'une table (palier 40 %, formulaires) : une cellule peut dépendre de sa ligne ET de son parent.

Le cas qui l'a demandé : un mouvement de stock. « Entrepôt source » n'existe que pour une sortie ou un transfert, « Entrepôt cible »
que pour une réception ou un transfert ; la nature est un champ du PARENT, lu par `parent_purpose`.
"""

from __future__ import annotations

import pytest

from tests.conftest import make_ctx
from xcore_doctype.builder import CompiledDocType
from xcore_doctype.document import DocumentService
from xcore_doctype.errors import InvalidDefinition, ValidationFailed
from xcore_doctype.registry import DocTypeRegistry
from xcore_doctype.schema import parse_definition

LINE = {
    "schema_version": 1,
    "name": "MoveLine",
    "istable": True,
    "fields": [
        {"fieldname": "item", "fieldtype": "Text", "required": True},
        {"fieldname": "source", "fieldtype": "Text", "depends_on": "parent_purpose != 'In'", "mandatory_depends_on": "parent_purpose in ['Out', 'Move']"},
        {"fieldname": "target", "fieldtype": "Text", "depends_on": "parent_purpose != 'Out'", "mandatory_depends_on": "parent_purpose in ['In', 'Move']"},
        # Une condition peut aussi lire un autre champ de la MÊME ligne.
        {"fieldname": "reason", "fieldtype": "Text", "required": True, "depends_on": "damaged"},
        {"fieldname": "damaged", "fieldtype": "Boolean"},
    ],
}
MOVE = {
    "schema_version": 1,
    "name": "Move",
    "module": "wh",
    "fields": [
        {"fieldname": "purpose", "fieldtype": "Select", "options": ["In", "Out", "Move"], "required": True},
        {"fieldname": "lines", "fieldtype": "Table", "options": "MoveLine"},
    ],
}


def registry(*defs):
    reg = DocTypeRegistry()
    for d in defs:
        reg.register(parse_definition(d))
    return reg


def compiled() -> CompiledDocType:
    return registry(LINE, MOVE).get("Move")


def errors(c, payload):
    with pytest.raises(ValidationFailed) as e:
        c.validate_data(payload)
    return {d["field"]: d["type"] for d in e.value.details}


def doc(purpose, *lines):
    return {"purpose": purpose, "lines": [{"item": "x", **line} for line in lines]}


class TestServerSide:
    def test_a_cell_made_mandatory_by_the_parent_cannot_be_missing(self):
        c = compiled()
        assert errors(c, doc("Out", {})) == {"lines.0.source": "missing"}
        assert errors(c, doc("In", {})) == {"lines.0.target": "missing"}
        assert errors(c, doc("Move", {"source": "A"})) == {"lines.0.target": "missing"}  # les deux, pour un transfert
        assert errors(c, doc("Move", {})) == {"lines.0.source": "missing", "lines.0.target": "missing"}

    def test_the_cell_a_nature_does_not_need_is_not_required(self):
        c = compiled()
        assert c.validate_data(doc("Out", {"source": "A"}))["lines"][0]["source"] == "A"
        assert c.validate_data(doc("In", {"target": "B"}))["lines"][0]["target"] == "B"

    def test_each_row_is_judged_on_its_own_and_the_error_names_its_row(self):
        c = compiled()
        assert errors(c, doc("Out", {"source": "A"}, {}, {"source": "C"}, {})) == {"lines.1.source": "missing", "lines.3.source": "missing"}

    def test_a_required_cell_that_depends_on_a_sibling_is_required_only_when_visible(self):
        c = compiled()
        ok = doc("In", {"target": "B"})  # `damaged` absent : `reason` masqué, donc pas obligatoire
        assert c.validate_data(ok)
        assert errors(c, doc("In", {"target": "B", "damaged": True})) == {"lines.0.reason": "missing"}
        assert c.validate_data(doc("In", {"target": "B", "damaged": True, "reason": "casse"}))

    def test_a_hidden_value_is_kept_not_stripped(self):
        """Comme pour un champ du document : le serveur n'invente ni n'efface rien (c'est la règle métier, un hook, qui refuse)."""
        out = compiled().validate_data(doc("In", {"target": "B", "source": "A"}))
        assert out["lines"][0]["source"] == "A"

    def test_a_missing_parent_field_reads_as_null(self):
        assert errors(compiled(), {"lines": [{"item": "x"}]}) == {"purpose": "missing"}  # la nature manque : seule elle est signalée


class TestDefinition:
    def test_the_conditions_reach_the_browser_as_trees_never_strings(self, db, tables):
        svc = DocumentService(registry(LINE, MOVE), db, tables, type("L", (), {"info": lambda *a, **k: None, "error": lambda *a, **k: None})())
        meta = svc.get_meta(make_ctx("acme", "a", permissions=["wh:move:read"]), "Move")
        fields = {f["fieldname"]: f for f in meta["children"]["MoveLine"]["fields"]}
        assert isinstance(fields["source"]["depends_on"], dict) and isinstance(fields["source"]["mandatory_depends_on"], dict)
        assert fields["source"]["depends_on"]["t"] == "cmp"
        assert "depends_on" not in fields["item"]

    def test_a_condition_citing_an_unknown_parent_field_fails_at_registration(self):
        bad = {**LINE, "fields": [{"fieldname": "a", "fieldtype": "Text", "depends_on": "parent_nope"}]}
        with pytest.raises(InvalidDefinition, match="parent_nope"):
            registry(bad, MOVE)

    def test_a_condition_citing_an_unknown_own_field_fails_at_load(self):
        bad = {**LINE, "fields": [{"fieldname": "a", "fieldtype": "Text", "depends_on": "ghost"}]}
        with pytest.raises(InvalidDefinition, match="ghost"):
            parse_definition(bad)

    def test_the_parent_prefix_is_reserved_in_rows(self):
        bad = {**LINE, "fields": [{"fieldname": "parent_x", "fieldtype": "Text"}]}
        with pytest.raises(InvalidDefinition, match="réservé"):
            parse_definition(bad)

    def test_a_top_level_doctype_cannot_use_the_parent_prefix(self):
        bad = {**MOVE, "fields": [{"fieldname": "a", "fieldtype": "Text", "depends_on": "parent_b"}]}
        with pytest.raises(InvalidDefinition, match="parent_b"):
            parse_definition(bad)

    def test_a_parent_decimal_or_table_cannot_be_read_by_a_row(self):
        parent = {**MOVE, "fields": [*MOVE["fields"], {"fieldname": "qty", "fieldtype": "Decimal", "decimals": 2}]}
        bad = {**LINE, "fields": [{"fieldname": "a", "fieldtype": "Text", "depends_on": "parent_qty"}]}
        with pytest.raises(InvalidDefinition, match="parent_qty"):
            registry(bad, parent)

    def test_a_row_cannot_read_its_own_decimal(self):
        bad = {**LINE, "fields": [{"fieldname": "q", "fieldtype": "Decimal", "decimals": 2}, {"fieldname": "a", "fieldtype": "Text", "depends_on": "q > 1"}]}
        with pytest.raises(InvalidDefinition, match="Decimal"):
            parse_definition(bad)
