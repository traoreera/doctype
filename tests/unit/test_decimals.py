"""Champs Decimal : exacts à l'écriture, stockés en chaîne canonique, comparables en NUMERIC (palier 40 %, phase A)."""

from __future__ import annotations

from decimal import Decimal

import json
from pathlib import Path

import pytest

from tests.conftest import make_ctx
from xcore_doctype.decimals import MAX_DIGITS, canonical
from xcore_doctype.document import DocumentService
from xcore_doctype.errors import ValidationFailed
from xcore_doctype.registry import DocTypeRegistry
from xcore_doctype.schema import parse_definition


VECTORS = json.loads((Path(__file__).resolve().parent.parent / "vectors" / "decimal_cases.json").read_text())["cases"]


@pytest.mark.parametrize("case", VECTORS, ids=lambda c: f"{c['raw']!r}/{c['decimals']}")
def test_shared_vectors_with_the_desk(case):
    """Les mêmes cas que `ui/tests/decimal.spec.ts` : le navigateur et le serveur ne peuvent pas diverger sur ce qui est valide."""
    if case["expect"] is None:
        with pytest.raises(ValueError):
            canonical(case["raw"], case["decimals"])
    else:
        assert canonical(case["raw"], case["decimals"]) == case["expect"]


class TestCanonical:
    @pytest.mark.parametrize(
        "raw, decimals, expected",
        [
            ("12.5", 3, "12.500"),
            (12, 3, "12.000"),
            ("12", 0, "12"),
            ("0", 3, "0.000"),
            ("-0", 3, "0.000"),  # « -0.000 » n'est pas une autre valeur
            ("-0.000", 3, "0.000"),
            ("-7.25", 2, "-7.25"),
            ("1.50000", 3, "1.500"),  # des zéros en trop ne changent pas la valeur
            ("0.001", 3, "0.001"),
            ("123456789012345.678", 3, "123456789012345.678"),  # 18 chiffres : le maximum
            (0, 2, "0.00"),
            (-5, 2, "-5.00"),
        ],
    )
    def test_accepted_values_have_one_canonical_form(self, raw, decimals, expected):
        assert canonical(raw, decimals) == expected

    @pytest.mark.parametrize(
        "raw, decimals",
        [
            (12.5, 3),  # un flottant JSON est déjà inexact : refusé, on n'arrondit pas en silence
            (0.1, 3),
            (True, 3),
            (None, 3),
            ([1], 3),
            ("1.2345", 3),  # trop de décimales : pas d'arrondi
            ("0.0005", 3),
            ("1.5", 0),
            ("1e3", 3),
            ("1,5", 3),
            (" 1", 3),
            ("+1", 3),
            ("01", 3),
            (".5", 3),
            ("5.", 3),
            ("", 3),
            ("NaN", 3),
            ("Infinity", 3),
            ("-", 3),
            ("1234567890123456.789", 3),  # 19 chiffres
            (10**16, 3),
        ],
    )
    def test_everything_else_is_refused(self, raw, decimals):
        with pytest.raises(ValueError):
            canonical(raw, decimals)

    def test_the_largest_value_fits_numeric_18(self):
        assert canonical("9" * 15 + ".999", 3) == "999999999999999.999" and len("9" * 15 + "999") == MAX_DIGITS

    def test_the_canonical_form_round_trips_without_loss(self):
        for raw in ("0.1", "0.2", "0.3", "1234.567"):
            assert Decimal(canonical(raw, 3)) == Decimal(raw)
        assert Decimal(canonical("0.1", 3)) + Decimal(canonical("0.2", 3)) == Decimal(canonical("0.3", 3))  # ce qu'un float rate


QTY = {
    "schema_version": 1,
    "name": "Stock",
    "module": "wh",
    "fields": [
        {"fieldname": "label", "fieldtype": "Text"},
        {"fieldname": "qty", "fieldtype": "Decimal", "decimals": 3, "required": True},
        {"fieldname": "price", "fieldtype": "Decimal", "decimals": 2, "default": 0},
        {"fieldname": "lines", "fieldtype": "Table", "options": "StockLine"},
    ],
}
LINE = {
    "schema_version": 1,
    "name": "StockLine",
    "istable": True,
    "fields": [{"fieldname": "qty", "fieldtype": "Decimal", "decimals": 3, "required": True}],
}
CTX = make_ctx("acme", "alice", permissions=["wh:stock:read", "wh:stock:write", "wh:stock:delete"])


class Log:
    def info(self, *a, **k): ...
    def error(self, *a, **k): ...


@pytest.fixture
def svc(db, tables):
    reg = DocTypeRegistry()
    reg.register(parse_definition(LINE))
    reg.register(parse_definition(QTY))
    return DocumentService(reg, db, tables, Log())


async def put(svc, qty, **extra):
    return await svc.create(CTX, "Stock", {"qty": qty, **extra})


class TestWrites:
    async def test_the_stored_value_is_the_canonical_string(self, svc):
        doc = await put(svc, "12.5", label="a")
        assert doc["data"] == {"label": "a", "qty": "12.500", "price": "0.00"}  # le défaut est canonique aussi
        assert (await svc.get(CTX, "Stock", doc["name"]))["data"]["qty"] == "12.500"

    async def test_an_integer_is_accepted_and_a_float_is_not(self, svc):
        assert (await put(svc, 4))["data"]["qty"] == "4.000"
        with pytest.raises(ValidationFailed) as e:
            await put(svc, 4.5)
        assert e.value.details[0]["field"] == "qty"
        assert "inexact" in e.value.details[0]["msg"]

    async def test_too_many_decimals_is_refused_not_rounded(self, svc):
        with pytest.raises(ValidationFailed):
            await put(svc, "0.0005")

    async def test_rows_of_a_table_are_validated_the_same_way(self, svc):
        ok = await put(svc, 1, lines=[{"qty": "2.5"}, {"qty": 3}])
        assert ok["data"]["lines"] == [{"qty": "2.500"}, {"qty": "3.000"}]
        with pytest.raises(ValidationFailed) as e:
            await put(svc, 1, lines=[{"qty": "2.5"}, {"qty": 0.5}])
        assert e.value.details[0]["field"] == "lines.1.qty"

    async def test_the_history_compares_canonical_strings(self, svc):
        doc = await put(svc, "1")
        same = await svc.update(CTX, "Stock", doc["name"], {"qty": "1.000"}, 1)  # la même valeur, écrite autrement : rien n'a changé
        assert same["version"] == 2
        assert (await svc.history(CTX, "Stock", doc["name"]))["total"] == 1
        await svc.update(CTX, "Stock", doc["name"], {"qty": "1.001"}, 2)
        assert (await svc.history(CTX, "Stock", doc["name"]))["items"][0]["changes"] == {"qty": {"old": "1.000", "new": "1.001"}}


class TestMeta:
    async def test_defaults_reach_the_desk_as_canonical_strings_even_in_rows(self, svc):
        """`default: 0` écrit en YAML : le Desk reçoit « 0.00 », une chaîne comme toute valeur du champ (et pas un nombre qu'il refuserait)."""
        meta = svc.get_meta(CTX, "Stock")
        assert {f["fieldname"]: f.get("default") for f in meta["fields"]}["price"] == "0.00"
        line = {"schema_version": 1, "name": "StockLine2", "istable": True,
                "fields": [{"fieldname": "q", "fieldtype": "Decimal", "decimals": 1, "default": 5}]}
        reg = DocTypeRegistry()
        reg.register(parse_definition(line))
        reg.register(parse_definition({**QTY, "name": "Stock2", "fields": [{"fieldname": "rows", "fieldtype": "Table", "options": "StockLine2"}]}))
        got = DocumentService(reg, svc._db, svc._tables, Log()).get_meta(make_ctx("acme", "alice", permissions=["wh:stock2:read"]), "Stock2")
        assert got["children"]["StockLine2"]["fields"][0]["default"] == "5.0"


class TestQueries:
    async def seed(self, svc, *values):
        return [(await put(svc, v, label=str(v)))["name"] for v in values]

    async def labels(self, svc, **kw):
        return [d["data"]["label"] for d in (await svc.list(CTX, "Stock", **kw))["items"]]

    async def test_filters_compare_as_numbers_not_as_text(self, svc):
        await self.seed(svc, "9", "10", "100", "2.5", "0.001")
        by = dict(order_by="qty", descending=False)
        assert await self.labels(svc, **by) == ["0.001", "2.5", "9", "10", "100"]  # « 100 » après « 9 » : un tri numérique
        assert await self.labels(svc, filters={"qty": [">", "9"]}, **by) == ["10", "100"]
        assert await self.labels(svc, filters={"qty": [">=", "9.000"]}, **by) == ["9", "10", "100"]
        assert await self.labels(svc, filters={"qty": ["<", "2.5"]}, **by) == ["0.001"]
        assert await self.labels(svc, filters={"qty": "2.5"}) == ["2.5"]
        assert await self.labels(svc, filters={"qty": ["!=", "2.5"]}, **by) == ["0.001", "9", "10", "100"]
        assert await self.labels(svc, filters={"qty": ["in", ["9", "100"]]}, **by) == ["9", "100"]

    async def test_an_equal_value_is_found_whatever_its_spelling(self, svc):
        await self.seed(svc, "1.5")
        assert await self.labels(svc, filters={"qty": "1.50"}) == ["1.5"]
        assert await self.labels(svc, filters={"qty": 2}) == []

    async def test_a_filter_value_is_validated_like_a_written_one(self, svc):
        for bad in (1.5, ["like", "1"], ["between", 1], "x", "1.2345"):
            with pytest.raises(ValidationFailed):
                await svc.list(CTX, "Stock", filters={"qty": bad})


class TestDefinition:
    def test_decimals_is_required_and_bounded(self):
        base = {"schema_version": 1, "name": "T", "fields": [{"fieldname": "q", "fieldtype": "Decimal"}]}
        with pytest.raises(Exception, match="Decimal exige `decimals`"):
            parse_definition(base)
        for bad in (-1, 10, 2.5, "2", True):
            with pytest.raises(Exception):
                parse_definition({**base, "fields": [{"fieldname": "q", "fieldtype": "Decimal", "decimals": bad}]})
        parse_definition({**base, "fields": [{"fieldname": "q", "fieldtype": "Decimal", "decimals": 0}]})

    def test_decimals_means_nothing_for_other_types(self):
        with pytest.raises(Exception, match="n'a de sens que pour Decimal"):
            parse_definition({"schema_version": 1, "name": "T", "fields": [{"fieldname": "q", "fieldtype": "Number", "decimals": 2}]})

    @pytest.mark.parametrize("default", [1.5, "abc", "1.2345", True])
    def test_a_default_must_be_a_valid_decimal(self, default):
        with pytest.raises(Exception, match="incompatible"):
            parse_definition({"schema_version": 1, "name": "T", "fields": [{"fieldname": "q", "fieldtype": "Decimal", "decimals": 2, "default": default}]})

    def test_a_decimal_cannot_be_used_in_a_condition(self):
        with pytest.raises(Exception, match="Decimal"):
            parse_definition(
                {"schema_version": 1, "name": "T", "fields": [
                    {"fieldname": "q", "fieldtype": "Decimal", "decimals": 2},
                    {"fieldname": "n", "fieldtype": "Text", "depends_on": "q > 0"},
                ]}
            )
