"""Habillages (overrides) : validation à l'écriture, fusion tolérante à la lecture (`overrides.py`, `DocumentService`)."""

from __future__ import annotations

import pytest

from tests.conftest import make_ctx
from xcore_doctype.document import DocumentService
from xcore_doctype.errors import PermissionDenied, ValidationFailed
from xcore_doctype.overrides import (
    apply_form_override,
    validate_form_override,
    validate_workspace_override,
    workspace_from_override,
)
from xcore_doctype.registry import DocTypeRegistry
from xcore_doctype.schema import parse_definition
from xcore_doctype.workspaces import parse_workspace


class Log:
    def info(self, *a, **k): ...
    def error(self, *a, **k): ...


LINE = {
    "schema_version": 1,
    "name": "Line",
    "istable": True,
    "fields": [{"fieldname": "qty", "fieldtype": "Number", "default": 0}],
}
ORDER = {
    "schema_version": 1,
    "name": "Order",
    "module": "shop",
    "fields": [
        {"fieldname": "a", "fieldtype": "Text"},
        {"fieldname": "b", "fieldtype": "Text"},
        {"fieldname": "c", "fieldtype": "Text"},
        {"fieldname": "gone", "fieldtype": "Text", "hidden": True},
        {"fieldname": "lines", "fieldtype": "Table", "options": "Line"},
    ],
}
RETURN = {"schema_version": 1, "name": "Return", "module": "shop", "fields": [{"fieldname": "x", "fieldtype": "Text"}]}


def registry() -> DocTypeRegistry:
    reg = DocTypeRegistry()
    for d in (LINE, ORDER, RETURN):
        reg.register(parse_definition(d))
    reg._workspaces["shop"] = parse_workspace(
        {
            "schema_version": 1,
            "name": "shop",
            "label": "Boutique",
            "blocks": [{"type": "shortcut", "doctype": "Order", "new": True}],
        }
    )
    return reg


def service(reg, db, tables) -> DocumentService:
    return DocumentService(reg, db, tables, Log())


WRITER = make_ctx("acme", "alice", permissions=["shop:order:write", "shop:return:write", "shop:order:read"])
READER = make_ctx("acme", "bob", permissions=["shop:order:read", "shop:return:read"])

WS_BODY = {"label": "Caisse", "icon": "shop", "blocks": [{"type": "shortcut", "doctype": "Order", "new": False}]}


class TestWorkspaceOverrideValidation:
    def test_a_valid_body_is_canonicalised(self):
        got = validate_workspace_override(registry(), "shop", WS_BODY)
        assert got["label"] == "Caisse" and got["order"] == 100 and got["blocks"][0]["doctype"] == "Order"
        assert "schema_version" not in got and "name" not in got

    @pytest.mark.parametrize(
        "patch, fragment",
        [
            ({"blocks": [{"type": "shortcut", "doctype": "Nope"}]}, "inconnu"),
            ({"blocks": [{"type": "shortcut", "doctype": "Line"}]}, "enfant"),
            ({"name": "other"}, "name"),  # la cible vient de l'URL, pas du corps
            ({"label": ""}, "label"),
        ],
    )
    def test_a_body_the_plugin_could_not_have_declared_is_refused(self, patch, fragment):
        with pytest.raises(ValidationFailed) as e:
            validate_workspace_override(registry(), "shop", {**WS_BODY, **patch})
        assert fragment in str(e.value)

    def test_a_measured_block_is_checked_against_its_doctype(self):
        bad = {**WS_BODY, "blocks": [{"type": "number_card", "label": "N", "doctype": "Order", "function": "sum", "field": "a"}]}
        with pytest.raises(ValidationFailed):
            validate_workspace_override(registry(), "shop", bad)

    def test_a_stored_body_that_became_invalid_is_ignored(self):
        assert workspace_from_override("shop", {"blocks": [{"type": "shortcut", "doctype": "Nope"}]}) is None
        assert workspace_from_override("shop", "not an object") is None
        assert workspace_from_override("shop", WS_BODY) is not None


class TestFormOverrideValidation:
    def test_a_valid_layout_is_canonicalised(self):
        got = validate_form_override(
            registry().get("Order"), {"layout": [{"section": "Haut", "columns": [["a"], ["b", "c"]]}]}
        )
        assert got == {"layout": [{"section": "Haut", "collapsible": False, "collapsed": False, "columns": [["a"], ["b", "c"]]}]}
    @pytest.mark.parametrize(
        "layout, fragment",
        [
            ([{"columns": [["nope"]]}], "inconnu"),
            ([{"columns": [["lines"]]}], "table"),
            ([{"columns": [["gone"]]}], "caché"),
            ([{"columns": [["a"], ["a"]]}], "deux fois"),
            ([{"columns": []}], "1 et 4 colonnes"),
            ([{"collapsed": True, "columns": [["a"]]}], "collapsible"),
        ],
    )
    def test_a_layout_the_definition_could_not_have_declared_is_refused(self, layout, fragment):
        with pytest.raises(ValidationFailed) as e:
            validate_form_override(registry().get("Order"), {"layout": layout})
        assert fragment in str(e.value)

    def test_field_tweaks_are_validated(self):
        got = validate_form_override(registry().get("Order"), {"fields": {"a": {"label": "Titre", "description": "d"}, "b": {"hidden": True}}})
        assert got["fields"] == {"a": {"label": "Titre", "description": "d"}, "b": {"hidden": True}}

    @pytest.mark.parametrize(
        "tweaks, fragment",
        [
            ({"nope": {"label": "x"}}, "inconnu"),
            ({"a": {"nope": "x"}}, "clés inconnues"),
            ({"a": {"hidden": "yes"}}, "booléen"),
            ({"a": {"label": ""}}, "texte non vide"),
        ],
    )
    def test_bad_field_tweaks_are_refused(self, tweaks, fragment):
        with pytest.raises(ValidationFailed) as e:
            validate_form_override(registry().get("Order"), {"fields": tweaks})
        assert fragment in str(e.value)

    def test_apply_is_tolerant_of_vanished_fields(self):
        meta = {"fields": [{"fieldname": "a", "label": "A"}, {"fieldname": "b", "hidden": True}]}
        apply_form_override(meta, {"layout": [{"section": None, "columns": [["a"]]}], "fields": {"a": {"label": "AA"}, "zz": {"label": "X"}}})
        assert meta["layout"] == [{"section": None, "columns": [["a"]]}] and meta["fields"][0]["label"] == "AA"

    def test_what_the_organisation_layout_does_not_place_stays_on_the_form(self):
        """Une disposition d'organisation ne place pas les tables (refusé) et ignore les champs ajoutés au YAML après elle : ils ne doivent
        pas disparaître de la fiche (la règle du YAML : un champ ne se cache qu'avec `hidden`). Un champ que l'habillage masque, lui, reste caché."""
        meta = {"fields": [{"fieldname": "a"}, {"fieldname": "later"}, {"fieldname": "lines", "fieldtype": "Table"}, {"fieldname": "secret"}]}
        apply_form_override(meta, {"layout": [{"section": "Tête", "columns": [["a"]]}], "fields": {"secret": {"hidden": True}}})
        assert meta["layout"] == [
            {"section": "Tête", "columns": [["a"]]},
            {"section": None, "collapsible": False, "collapsed": False, "columns": [["later", "lines"]]},
        ]

    def test_a_body_with_an_unknown_key_is_refused(self):
        with pytest.raises(ValidationFailed) as e:
            validate_form_override(registry().get("Order"), {"layout": [], "nope": 1})
        assert "inconnues" in str(e.value)


class TestFormOverrideService:
    async def test_save_then_meta_reflects_it_and_delete_restores(self, db, tables):
        svc = service(registry(), db, tables)
        assert (await svc.fetch_meta(WRITER, "Order"))["layout"] == [
            {"section": None, "collapsible": False, "collapsed": False, "columns": [["a", "b", "c", "lines"]]}
        ]
        await svc.save_form_override(WRITER, "Order", {"layout": [{"section": "Bas", "columns": [["b"]]}], "fields": {"a": {"label": "Champ A"}}})
        meta = await svc.fetch_meta(WRITER, "Order")
        assert meta["layout"][0]["section"] == "Bas" and meta["fields"][0]["label"] == "Champ A"
        assert (await svc.form_override(WRITER, "Order"))["config"]["layout"][0]["section"] == "Bas"
        await svc.delete_form_override(WRITER, "Order")
        assert (await svc.form_override(WRITER, "Order"))["config"] == {}
        assert (await svc.fetch_meta(WRITER, "Order"))["layout"][0]["section"] is None

    async def test_writing_a_form_override_needs_write_rights(self, db, tables):
        svc = service(registry(), db, tables)
        with pytest.raises(PermissionDenied):
            await svc.save_form_override(READER, "Order", {"fields": {"a": {"label": "X"}}})


class TestWorkspaceOverrideService:
    async def test_save_merges_into_workspaces_and_delete_restores(self, db, tables):
        svc = service(registry(), db, tables)
        assert (await svc.fetch_workspaces(WRITER))[0]["label"] == "Boutique"
        await svc.save_workspace_override(WRITER, "shop", WS_BODY)
        assert (await svc.fetch_workspaces(WRITER))[0]["label"] == "Caisse"
        await svc.delete_workspace_override(WRITER, "shop")
        assert (await svc.fetch_workspaces(WRITER))[0]["label"] == "Boutique"

    async def test_writing_a_workspace_override_needs_write_on_its_doctypes(self, db, tables):
        svc = service(registry(), db, tables)
        with pytest.raises(PermissionDenied):
            await svc.save_workspace_override(READER, "shop", WS_BODY)
