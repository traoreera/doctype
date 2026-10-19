"""Types de champ du palier 70 % (phase H) : Datetime, Currency, Rating, Color, LongText et le `format` d'un Text.

Écriture stricte (rien n'est corrigé en silence), même comportement sur SQLite et PostgreSQL pour les filtres, tris et agrégats.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from tests.conftest import make_ctx
from xcore_doctype import fieldtypes
from xcore_doctype.builder import CompiledDocType
from xcore_doctype.document import DocumentService
from xcore_doctype.errors import InvalidDefinition, ValidationFailed
from xcore_doctype.registry import DocTypeRegistry
from xcore_doctype.schema import parse_definition

CTX = make_ctx("acme", "alice", permissions=["shop:contact:read", "shop:contact:write"])
NOW = datetime(2026, 10, 18, 14, 30, 5, tzinfo=UTC)

CONTACT = {
    "schema_version": 1,
    "name": "Contact",
    "module": "shop",
    "fields": [
        {"fieldname": "email", "fieldtype": "Text", "format": "email"},
        {"fieldname": "phone", "fieldtype": "Text", "format": "phone"},
        {"fieldname": "site", "fieldtype": "Text", "format": "url"},
        {"fieldname": "bio", "fieldtype": "LongText"},
        {"fieldname": "salary", "fieldtype": "Currency", "options": "EUR"},
        {"fieldname": "level", "fieldtype": "Rating"},
        {"fieldname": "mood", "fieldtype": "Rating", "stars": 3},
        {"fieldname": "tint", "fieldtype": "Color"},
        {"fieldname": "met_at", "fieldtype": "Datetime"},
        {"fieldname": "created_at", "fieldtype": "Datetime", "default": "now"},
    ],
}


def compiled(**over):
    return CompiledDocType.compile(parse_definition({**CONTACT, **over}))


def ok(data):
    return compiled().validate_data(data)


def refused(data):
    with pytest.raises(ValidationFailed) as e:
        compiled().validate_data(data)
    return {d["field"] for d in e.value.details}


class TestDefinition:
    def test_type_defaults(self):
        f = {x.fieldname: x for x in parse_definition(CONTACT).fields}
        assert f["salary"].decimals == 2 and f["salary"].options == "EUR"
        assert (f["level"].stars, f["mood"].stars) == (5, 3)

    @pytest.mark.parametrize(
        "field, fragment",
        [
            ({"fieldname": "x", "fieldtype": "Text", "format": "fax"}, "format"),
            ({"fieldname": "x", "fieldtype": "Number", "format": "email"}, "`format` n'a de sens que pour un Text"),
            ({"fieldname": "x", "fieldtype": "Number", "stars": 3}, "`stars` n'a de sens que pour un Rating"),
            ({"fieldname": "x", "fieldtype": "Rating", "stars": 11}, "stars"),
            ({"fieldname": "x", "fieldtype": "Rating", "stars": 0}, "stars"),
            ({"fieldname": "x", "fieldtype": "Currency", "options": "euro"}, "ISO 4217"),
            ({"fieldname": "x", "fieldtype": "Currency", "decimals": 10}, "decimals"),
            ({"fieldname": "x", "fieldtype": "Date", "decimals": 2}, "n'a de sens que pour Decimal et Currency"),
            ({"fieldname": "x", "fieldtype": "Color", "options": ["a"]}, "options"),
            ({"fieldname": "x", "fieldtype": "Datetime", "default": "2026-10-18"}, "incompatible"),
            ({"fieldname": "x", "fieldtype": "Datetime", "default": "demain"}, "incompatible"),
            ({"fieldname": "x", "fieldtype": "Rating", "default": 9}, "incompatible"),
            ({"fieldname": "x", "fieldtype": "Color", "default": "red"}, "incompatible"),
            ({"fieldname": "x", "fieldtype": "Text", "format": "email", "default": "pas un courriel"}, "incompatible"),
            ({"fieldname": "x", "fieldtype": "Currency", "default": 1.5}, "incompatible"),  # un flottant est refusé, comme pour un Decimal
        ],
    )
    def test_a_bad_field_is_refused_at_load(self, field, fragment):
        with pytest.raises(InvalidDefinition, match=fragment):
            parse_definition({**CONTACT, "fields": [field]})

    def test_good_defaults(self):
        d = parse_definition({**CONTACT, "fields": [
            {"fieldname": "a", "fieldtype": "Currency", "default": 0},
            {"fieldname": "b", "fieldtype": "Rating", "default": 3},
            {"fieldname": "c", "fieldtype": "Color", "default": "#336699"},
            {"fieldname": "d", "fieldtype": "Datetime", "default": "2026-01-01T00:00:00Z"},
        ]})
        assert [f.default for f in d.fields] == [0, 3, "#336699", "2026-01-01T00:00:00Z"]


class TestWriteIsStrict:
    def test_good_values(self):
        clean = ok({
            "email": "alice@example.org", "phone": "+33 6 12 34 56 78", "site": "https://exemple.fr/profil?a=1", "bio": "ligne 1\nligne 2",
            "salary": "2500.5", "level": 4, "mood": 3, "tint": "#a1b2c3", "met_at": "2026-10-18T14:30:05Z",
        })
        assert clean["salary"] == "2500.50" and clean["level"] == 4 and clean["bio"] == "ligne 1\nligne 2"

    @pytest.mark.parametrize(
        "field, value",
        [
            ("email", "pas-un-courriel"), ("email", "a@b"), ("email", "a b@c.fr"), ("email", "a@c.fr, b@c.fr"), ("email", "x" * 250 + "@a.fr"),
            ("phone", "abc"), ("phone", "12"), ("phone", "+33 (0)1; DROP"), ("phone", "1" * 30),
            ("site", "javascript:alert(1)"), ("site", "data:text/html,x"), ("site", "ftp://x.fr"), ("site", "https://a b.fr"), ("site", "//x.fr"),
            ("salary", 12.5), ("salary", "1.234"), ("salary", "abc"), ("salary", True),
            ("level", 6), ("level", -1), ("level", 2.5), ("level", "3"), ("level", True), ("mood", 4),
            ("tint", "#ABCDEF"), ("tint", "#abc"), ("tint", "rouge"), ("tint", "#12345g"),
            ("met_at", "2026-10-18"), ("met_at", "2026-10-18 14:30:05"), ("met_at", "2026-10-18T14:30:05"), ("met_at", "2026-10-18T14:30:05+02:00"),
            ("met_at", "2026-02-30T10:00:00Z"), ("met_at", "2026-10-18T25:00:00Z"), ("met_at", 1760797805),
            ("bio", "x" * (fieldtypes.MAX_LONG_TEXT + 1)), ("bio", 12),
        ],
    )
    def test_a_bad_value_is_refused_not_corrected(self, field, value):
        assert refused({field: value}) == {field}

    def test_a_url_scheme_is_case_insensitive_but_only_http(self):
        assert ok({"site": "HTTP://EXEMPLE.FR"})["site"] == "HTTP://EXEMPLE.FR"  # on ne réécrit rien

    def test_the_longest_accepted_values(self):
        assert ok({"bio": "x" * fieldtypes.MAX_LONG_TEXT})["bio"]
        assert ok({"level": 0, "mood": 0})["level"] == 0


@pytest.fixture
def svc(db, tables):
    reg = DocTypeRegistry()
    reg.register(parse_definition(CONTACT))
    return DocumentService(reg, db, tables, _Log(), clock=lambda: NOW)


class _Log:
    def info(self, *a, **k): ...
    def error(self, *a, **k): ...


async def add(svc, **data):
    return await svc.create(CTX, "Contact", data)


class TestDatabase:
    async def test_now_is_resolved_at_creation_in_utc_and_never_overrides_a_value(self, svc):
        auto = await add(svc, email="a@b.fr")
        assert auto["data"]["created_at"] == "2026-10-18T14:30:05Z"
        given = await add(svc, created_at="2020-01-01T00:00:00Z")
        assert given["data"]["created_at"] == "2020-01-01T00:00:00Z"

    async def test_datetimes_sort_and_filter_chronologically(self, svc):
        for t in ("2026-10-18T09:00:00Z", "2026-10-17T23:59:59Z", "2026-10-18T14:00:00Z"):
            await add(svc, met_at=t)
        got = await svc.list(CTX, "Contact", order_by="met_at", descending=False)
        assert [d["data"]["met_at"] for d in got["items"]] == ["2026-10-17T23:59:59Z", "2026-10-18T09:00:00Z", "2026-10-18T14:00:00Z"]
        after = await svc.list(CTX, "Contact", filters={"met_at": [">=", "2026-10-18T09:00:00Z"]})
        assert after["total"] == 2
        assert (await svc.list(CTX, "Contact", filters={"met_at": ["in", ["2026-10-18T09:00:00Z"]]}))["total"] == 1
        with pytest.raises(ValidationFailed):
            await svc.list(CTX, "Contact", filters={"met_at": [">=", "hier"]})

    async def test_currency_sums_exactly_and_ratings_average(self, svc):
        for salary, level in (("0.10", 5), ("0.20", 3), ("0.30", 4)):
            await add(svc, salary=salary, level=level)
        res = await svc.aggregate(CTX, "Contact", metrics=["sum:salary", "avg:level", "max:level"])
        assert res["rows"][0]["values"] == ["0.60", 4, 5]
        assert res["metrics"][0]["decimals"] == 2

    async def test_currency_filters_and_sorts_as_a_number(self, svc):
        for salary in ("9.00", "10.00", "100.00"):
            await add(svc, salary=salary)
        got = await svc.list(CTX, "Contact", order_by="salary", descending=False)
        assert [d["data"]["salary"] for d in got["items"]] == ["9.00", "10.00", "100.00"]  # pas l'ordre des chaînes
        assert (await svc.list(CTX, "Contact", filters={"salary": [">", "9.50"]}))["total"] == 2

    async def test_group_by_datetime_month_and_day(self, svc):
        for t in ("2026-09-02T10:00:00Z", "2026-10-02T10:00:00Z", "2026-10-02T23:00:00Z"):
            await add(svc, met_at=t)
        month = await svc.aggregate(CTX, "Contact", group_by="met_at")
        assert [(r["key"], r["values"]) for r in month["rows"]] == [("2026-09", [1]), ("2026-10", [2])]
        day = await svc.aggregate(CTX, "Contact", group_by="met_at", bucket="day", periods=3)
        assert [(r["key"], r["values"]) for r in day["rows"]][-1] == ("2026-10-18", [0])

    async def test_long_text_only_filters_by_contains(self, svc):
        await add(svc, bio="Aime la randonnée")
        assert (await svc.list(CTX, "Contact", filters={"bio": ["like", "RANDO"]}))["total"] == 1
        with pytest.raises(ValidationFailed):
            await svc.list(CTX, "Contact", filters={"bio": "exact"})  # une égalité sur un texte long n'a pas de sens
        with pytest.raises(ValidationFailed, match="regroupement impossible"):
            await svc.aggregate(CTX, "Contact", group_by="bio")

    async def test_color_and_rating_can_group(self, svc):
        await add(svc, tint="#336699", level=2)
        with pytest.raises(ValidationFailed, match="regroupement impossible"):
            await svc.aggregate(CTX, "Contact", group_by="tint")  # une couleur n'est pas une catégorie de plus de quelques valeurs

    async def test_meta_carries_the_new_properties(self, svc):
        meta = svc.get_meta(CTX, "Contact")
        f = {x["fieldname"]: x for x in meta["fields"]}
        assert f["email"]["format"] == "email" and f["mood"]["stars"] == 3
        assert f["salary"]["decimals"] == 2 and f["salary"]["options"] == "EUR"


class TestHelpers:
    def test_format_instant_is_utc(self):
        from datetime import timedelta, timezone

        local = datetime(2026, 10, 18, 16, 30, tzinfo=timezone(timedelta(hours=2)))
        assert fieldtypes.format_instant(local) == "2026-10-18T14:30:00Z"

    @pytest.mark.parametrize("phone", ["+33 1 23 45 67 89", "01.23.45.67.89", "(555) 123-4567", "1234"])
    def test_phones(self, phone):
        assert fieldtypes.text_format("phone", phone) == phone
