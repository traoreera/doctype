"""Mode développeur : le constructeur de DocTypes (devtools.py, doc/programme.md §3.5).

Ce que ces tests défendent, chacun par une mutation qui le casse :
- fermé par défaut : sans `developer_mode`, les deux portes répondent comme une route qui n'existe pas ;
- `check` juge une définition COMME AU CHARGEMENT (schéma, compilation, cibles des liens et des tables dans le système réel) et n'enregistre
  RIEN : le type jugé n'existe pas après ;
- son YAML se relit en la même définition (sans les valeurs par défaut), et la liste des grants est celle que le plugin devra déclarer ;
- `definition` rend le YAML D'ORIGINE (sans extension ni champ d'organisation) et exige de pouvoir lire le type.
"""

from __future__ import annotations

import pytest
import yaml

from tests.conftest import make_ctx
from xcore_doctype import devtools
from xcore_doctype.document import DocumentService
from xcore_doctype.errors import DoctypeError, NotFound, PermissionDenied, ValidationFailed
from xcore_doctype.hooks import HookRegistry
from xcore_doctype.registry import DocTypeRegistry
from xcore_doctype.schema import parse_definition

COUNTRY = {"schema_version": 1, "name": "Country", "module": "shop", "fields": [{"fieldname": "label", "fieldtype": "Text"}]}
LINE = {"schema_version": 1, "name": "Order Line", "istable": True, "fields": [{"fieldname": "qty", "fieldtype": "Int"}]}
ORDER = {
    "schema_version": 1, "name": "Order", "label": "Commande", "module": "shop",
    "fields": [
        {"fieldname": "title", "fieldtype": "Text", "label": "Titre", "required": True},
        {"fieldname": "country", "fieldtype": "Link", "options": "Country"},
        {"fieldname": "lines", "fieldtype": "Table", "options": "Order Line"},
    ],
}
ADA = make_ctx("acme", "ada", permissions=["shop:order:read", "shop:order:write", "shop:country:read"])


class _Log:
    def info(self, *a, **k): ...
    def error(self, *a, **k): ...
    def warning(self, *a, **k): ...


@pytest.fixture
def svc(db, tables):
    reg = DocTypeRegistry()
    for d in (COUNTRY, LINE, ORDER):
        reg.register(parse_definition(d), source=f"{d['name']}.yaml", owner=None)
    reg.validate_links()
    return DocumentService(reg, db, tables, _Log(), hooks=HookRegistry())


def new_type(**extra) -> dict:
    return {
        "schema_version": 1, "name": "Ticket", "label": "Ticket", "module": "shop",
        "fields": [{"fieldname": "subject", "fieldtype": "Text", "label": "Sujet", "required": True}, {"fieldname": "order", "fieldtype": "Link", "options": "Order"}],
        **extra,
    }


def refusal(e: pytest.ExceptionInfo[DoctypeError]) -> list[str]:
    return [f"{d['field']}: {d['msg']}" for d in e.value.details]


class TestClosedByDefault:
    def test_both_doors_answer_like_a_missing_route(self, svc):
        for call in (lambda: devtools.definition(svc, ADA, "Order", enabled=False), lambda: devtools.check(svc, ADA, new_type(), enabled=False)):
            with pytest.raises(NotFound) as e:
                call()
            assert e.value.code == "developer_mode_off"


class TestCheck:
    def test_a_valid_new_type_gets_its_yaml_and_grants_and_nothing_is_registered(self, svc):
        got = devtools.check(svc, ADA, new_type(), enabled=True)
        assert got["doctype"] == "Ticket"
        assert parse_definition(yaml.safe_load(got["yaml"])) == parse_definition(new_type())  # le YAML se relit en la même définition
        assert "required: true" in got["yaml"] and "Sujet" in got["yaml"] and "naming" not in got["yaml"]  # sans les valeurs par défaut
        assert got["grants"] == ["shop:ticket:delete", "shop:ticket:read", "shop:ticket:write"]
        with pytest.raises(DoctypeError):
            svc._doctype("Ticket")  # rien n'a été enregistré

    @pytest.mark.parametrize(
        ("definition", "says"),
        [
            (new_type(fields=[{"fieldname": "Sujet!", "fieldtype": "Text"}]), "fields.0.fieldname"),
            (new_type(fields=[{"fieldname": "a", "fieldtype": "Select"}]), "options"),
            (new_type(fields=[{"fieldname": "a", "fieldtype": "Text"}, {"fieldname": "a", "fieldtype": "Text"}]), "a"),
            (new_type(fields=[{"fieldname": "who", "fieldtype": "Link", "options": "Nowhere"}]), "Nowhere"),  # cible inconnue
            (new_type(fields=[{"fieldname": "l", "fieldtype": "Link", "options": "Order Line"}]), "enfant"),  # on ne cite pas une table
            (new_type(fields=[{"fieldname": "t", "fieldtype": "Table", "options": "Order"}]), "Order"),  # une table doit être un type enfant
            (new_type(fields=[{"fieldname": "c", "fieldtype": "Text", "fetch_from": "order.nothing"}, {"fieldname": "order", "fieldtype": "Link", "options": "Order"}]), "nothing"),
        ],
    )
    def test_what_a_plugin_load_would_refuse_is_refused_line_by_line(self, svc, definition, says):
        with pytest.raises(ValidationFailed) as e:
            devtools.check(svc, ADA, definition, enabled=True)
        assert e.value.code == "invalid_definition"
        assert any(says in line for line in refusal(e) + [e.value.message]), refusal(e)

    def test_a_definition_that_is_not_an_object_is_refused(self, svc):
        with pytest.raises(ValidationFailed):
            devtools.check(svc, ADA, ["pas", "un", "objet"], enabled=True)

    def test_editing_a_loaded_type_replaces_it_in_the_trial_registry(self, svc):
        """Modifier `Order` : la version jugée remplace celle chargée, sans « déjà défini » — et `Order` reste intact dans le vrai registre."""
        changed = {**ORDER, "fields": [*ORDER["fields"], {"fieldname": "note", "fieldtype": "LongText"}]}
        got = devtools.check(svc, ADA, changed, enabled=True)
        assert "note" in got["yaml"]
        assert "note" not in svc._doctype("Order").definition.field_map

    def test_child_types_are_loaded_first_whatever_the_order_the_registry_gives(self, svc, monkeypatch):
        """Un plugin rechargé remet ses types enfants APRÈS les parents des autres : le registre d'essai doit quand même charger les enfants
        d'abord (un parent exige ses tables déjà là)."""
        monkeypatch.setattr(svc._registry, "origins", lambda: list(reversed(DocTypeRegistry.origins(svc._registry))))
        assert devtools.check(svc, ADA, new_type(), enabled=True)["doctype"] == "Ticket"

    def test_links_are_judged_against_the_real_system(self, svc):
        ok = new_type(fields=[{"fieldname": "order", "fieldtype": "Link", "options": "Order"}, {"fieldname": "title", "fieldtype": "Text", "fetch_from": "order.title"}])
        assert devtools.check(svc, ADA, ok, enabled=True)["doctype"] == "Ticket"


class TestDefinition:
    def test_it_is_the_original_yaml_and_needs_read_access(self, svc):
        got = devtools.definition(svc, ADA, "Order", enabled=True)
        assert got["definition"]["name"] == "Order" and [f["fieldname"] for f in got["definition"]["fields"]] == ["title", "country", "lines"]
        assert parse_definition(yaml.safe_load(got["yaml"])) == parse_definition(ORDER)
        stranger = make_ctx("acme", "sam", permissions=[])
        with pytest.raises(PermissionDenied):
            devtools.definition(svc, stranger, "Order", enabled=True)

    def test_an_unknown_or_child_type_is_not_found(self, svc):
        for name in ("Nowhere", "Order Line"):
            with pytest.raises(DoctypeError) as e:
                devtools.definition(svc, ADA, name, enabled=True)
            assert e.value.status == 404
