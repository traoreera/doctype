"""Défaut `today` des champs Date, et `unit_from` (l'unité affichée à côté d'une quantité)."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from tests.conftest import make_ctx
from xcore_doctype.document import DocumentService
from xcore_doctype.errors import InvalidDefinition, ValidationFailed
from xcore_doctype.registry import DocTypeRegistry
from xcore_doctype.schema import parse_definition


class Log:
    def info(self, *a, **k): ...
    def error(self, *a, **k): ...


NOTE = {
    "schema_version": 1,
    "name": "Note",
    "module": "n",
    "fields": [
        {"fieldname": "title", "fieldtype": "Text"},
        {"fieldname": "day", "fieldtype": "Date", "default": "today", "required": True},
        {"fieldname": "due", "fieldtype": "Date"},
    ],
}
CTX = make_ctx("acme", "alice", permissions=["n:note:read", "n:note:write"])


def service(db, tables, clock, tz="UTC", *defs):
    reg = DocTypeRegistry()
    for d in defs or (NOTE,):
        reg.register(parse_definition(d))
    return DocumentService(reg, db, tables, Log(), clock=lambda: clock, timezone=ZoneInfo(tz))


class TestToday:
    async def test_an_absent_date_gets_today_in_the_organisation_timezone(self, db, tables):
        # 23 h 30 à Paris le 15 est encore le 15 ; en UTC l'horloge dit 21 h 30 le 15 : même jour. Puis 00 h 30 à Paris le 16 = 22 h 30 UTC le 15.
        late = datetime(2026, 6, 15, 22, 30, tzinfo=UTC)
        utc = service(db, tables, late, "UTC")
        paris = service(db, tables, late, "Europe/Paris")
        assert (await utc.create(CTX, "Note", {"title": "a"}))["data"]["day"] == "2026-06-15"
        assert (await paris.create(CTX, "Note", {"title": "b"}))["data"]["day"] == "2026-06-16"  # le jour de l'organisation, pas celui d'UTC

    async def test_a_value_the_client_sent_is_never_replaced(self, db, tables):
        svc = service(db, tables, datetime(2026, 6, 15, 12, tzinfo=UTC))
        assert (await svc.create(CTX, "Note", {"day": "2026-01-02"}))["data"]["day"] == "2026-01-02"

    async def test_only_the_absent_field_is_filled(self, db, tables):
        svc = service(db, tables, datetime(2026, 6, 15, 12, tzinfo=UTC))
        doc = (await svc.create(CTX, "Note", {}))["data"]
        assert doc == {"day": "2026-06-15"}  # `due` n'a pas de défaut : il reste absent

    async def test_an_update_does_not_re_apply_it(self, db, tables):
        svc = service(db, tables, datetime(2026, 6, 15, 12, tzinfo=UTC))
        doc = await svc.create(CTX, "Note", {"day": "2026-01-02"})
        with pytest.raises(ValidationFailed):  # `day` est obligatoire : l'omettre à la modification n'est pas « prendre aujourd'hui »
            await svc.update(CTX, "Note", doc["name"], {"title": "x"}, doc["version"])

    def test_the_meta_keeps_the_marker_and_gives_the_timezone(self, db, tables):
        svc = service(db, tables, datetime(2026, 6, 15, 12, tzinfo=UTC), "Europe/Paris")
        meta = svc.get_meta(CTX, "Note")
        assert {f["fieldname"]: f.get("default") for f in meta["fields"]}["day"] == "today" and meta["timezone"] == "Europe/Paris"

    def test_a_default_that_is_neither_a_date_nor_today_is_refused(self):
        with pytest.raises(InvalidDefinition, match="incompatible"):
            parse_definition({**NOTE, "fields": [{"fieldname": "d", "fieldtype": "Date", "default": "tomorrow"}]})

    def test_rows_cannot_default_to_today(self):
        row = {"schema_version": 1, "name": "R", "istable": True, "fields": [{"fieldname": "d", "fieldtype": "Date", "default": "today"}]}
        with pytest.raises(InvalidDefinition, match="today"):
            parse_definition(row)


class TestSummary:
    def test_the_home_page_knows_where_the_user_may_create(self, db, tables):
        svc = service(db, tables, datetime(2026, 6, 15, tzinfo=UTC))
        writer = make_ctx("acme", "w", permissions=["n:note:read", "n:note:write"])
        reader = make_ctx("acme", "r", permissions=["n:note:read"])
        assert [m["create"] for m in svc.list_meta(writer)] == [True]
        assert [m["create"] for m in svc.list_meta(reader)] == [False]


ITEM = {"schema_version": 1, "name": "Item", "module": "w", "fields": [{"fieldname": "uom", "fieldtype": "Select", "options": ["kg", "m"]}, {"fieldname": "n", "fieldtype": "Text"}]}
BIN = {
    "schema_version": 1,
    "name": "Bin",
    "module": "w",
    "fields": [
        {"fieldname": "item", "fieldtype": "Link", "options": "Item"},
        {"fieldname": "unit", "fieldtype": "Text"},
        {"fieldname": "qty", "fieldtype": "Decimal", "decimals": 3, "unit_from": "item.uom"},
        {"fieldname": "own", "fieldtype": "Number", "unit_from": "unit"},
    ],
}


def reg(*defs):
    r = DocTypeRegistry()
    for d in defs:
        r.register(parse_definition(d))
    r.validate_links()
    return r


class TestUnitFrom:
    def test_a_valid_unit_reaches_the_browser(self, db, tables):
        svc = DocumentService(reg(ITEM, BIN), db, tables, Log())
        meta = svc.get_meta(make_ctx("acme", "a", permissions=["w:bin:read"]), "Bin")
        assert {f["fieldname"]: f.get("unit_from") for f in meta["fields"]} == {"item": None, "unit": None, "qty": "item.uom", "own": "unit"}

    @pytest.mark.parametrize(
        "unit_from, fragment",
        [
            ("ghost.uom", "champ inconnu 'ghost'"),
            ("unit.uom", "n'est pas un champ Link"),
            ("item.ghost", "n'a pas de champ 'ghost'"),
            ("ghost", "champ inconnu"),
        ],
    )
    def test_a_dangling_unit_fails_at_load(self, unit_from, fragment):
        bad = {**BIN, "fields": [*BIN["fields"][:2], {"fieldname": "qty", "fieldtype": "Decimal", "decimals": 3, "unit_from": unit_from}]}
        with pytest.raises(InvalidDefinition, match=fragment):
            reg(ITEM, bad)

    def test_the_unit_must_be_text(self):
        bad_item = {**ITEM, "fields": [{"fieldname": "uom", "fieldtype": "Number"}]}
        with pytest.raises(InvalidDefinition, match="doit être un texte"):
            reg(bad_item, BIN)

    @pytest.mark.parametrize("ftype", ["Text", "Date", "Boolean", "Select"])
    def test_only_numbers_carry_a_unit(self, ftype):
        extra = {"options": ["a"]} if ftype == "Select" else {}
        with pytest.raises(InvalidDefinition, match="unit_from"):
            parse_definition({**BIN, "fields": [{"fieldname": "x", "fieldtype": ftype, "unit_from": "unit", **extra}]})

    def test_a_row_may_take_its_unit_from_its_own_link(self):
        line = {"schema_version": 1, "name": "BinLine", "istable": True, "fields": [
            {"fieldname": "item", "fieldtype": "Link", "options": "Item"},
            {"fieldname": "qty", "fieldtype": "Decimal", "decimals": 3, "unit_from": "item.uom"},
        ]}
        reg(ITEM, line)  # accepté
        bad = {**line, "fields": [line["fields"][0], {**line["fields"][1], "unit_from": "item.ghost"}]}
        with pytest.raises(InvalidDefinition, match="ghost"):
            reg(ITEM, bad)
