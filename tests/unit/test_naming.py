"""Numérotation par série (D7, D13) : le motif, le compteur atomique et sa tenue en concurrence sur les deux bases."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from tests.conftest import make_ctx
from xcore_doctype.document import DocumentService
from xcore_doctype.errors import InvalidDefinition, ValidationFailed
from xcore_doctype.hooks import HookRegistry
from xcore_doctype.naming import SeriesPattern
from xcore_doctype.query import Query
from xcore_doctype.registry import DocTypeRegistry
from xcore_doctype.schema import parse_definition

NOW = datetime(2026, 10, 17, 9, 30, tzinfo=UTC)


class TestPattern:
    @pytest.mark.parametrize(
        ("source", "number", "expected", "key"),
        [
            ("FA-{YYYY}-{#####}", 1, "FA-2026-00001", "FA-2026-{#}"),
            ("FA-{YYYY}-{#####}", 123456, "FA-2026-123456", "FA-2026-{#}"),  # dépasse la largeur : le nombre s'allonge
            ("{YY}{MM}{DD}.{###}", 7, "261017.007", "261017.{#}"),
            ("{#}", 12, "12", "{#}"),
            ("INV_{#####}_{YYYY}", 3, "INV_00003_2026", "INV_{#}_2026"),  # texte après le compteur : dans la clé aussi
            ("a.b-C_9-{####}", 42, "a.b-C_9-0042", "a.b-C_9-{#}"),
        ],
    )
    def test_render_and_key(self, source, number, expected, key):
        p = SeriesPattern.parse(source)
        assert p.render(NOW, number) == expected
        assert p.key(NOW) == key

    def test_the_counter_restarts_with_the_period(self):
        p = SeriesPattern.parse("FA-{YYYY}-{#####}")
        assert p.key(NOW) != p.key(datetime(2027, 1, 1, tzinfo=UTC))

    def test_two_series_never_share_a_counter(self):
        assert SeriesPattern.parse("FA-{#####}").key(NOW) != SeriesPattern.parse("FA-{####}-X").key(NOW)

    @pytest.mark.parametrize(
        ("source", "why"),
        [
            ("", "vide"),
            ("FA-2026", "aucun compteur"),
            ("{#}-{##}", "deux compteurs"),
            ("FA-{foo}-{#}", "jeton inconnu"),
            ("FA-{#}-{", "accolade seule"),
            ("FA {#}", "espace"),
            ("FA/{#}", "barre oblique (segment d'URL)"),
            ("FA-é-{#}", "non ASCII"),
            ("{" + "#" * 11 + "}", "compteur trop large"),
            ("A" * 101 + "{#}", "trop long"),
            ("{yyyy}-{#}", "casse"),
        ],
    )
    def test_invalid(self, source, why):
        with pytest.raises(ValueError):
            SeriesPattern.parse(source)


class TestDefinition:
    BASE = {"schema_version": 1, "name": "Invoice", "fields": [{"fieldname": "label", "fieldtype": "Text"}]}

    def test_series_naming_is_accepted(self):
        d = parse_definition({**self.BASE, "naming": {"series": "FA-{YYYY}-{#####}"}})
        assert d.series is not None and d.series.render(NOW, 5) == "FA-2026-00005"

    def test_hash_is_the_default(self):
        assert parse_definition(self.BASE).series is None

    @pytest.mark.parametrize("naming", ["series", {"series": "FA"}, {"series": "FA-{#}", "extra": 1}, {}, 5])
    def test_refused(self, naming):
        with pytest.raises(InvalidDefinition):
            parse_definition({**self.BASE, "naming": naming})

    def test_the_error_names_the_setting(self):
        with pytest.raises(InvalidDefinition, match=r"naming\.series"):
            parse_definition({**self.BASE, "naming": {"series": "FA-2026"}})


async def _next(db, tables, ctx, doctype, key) -> int:
    async with db.session() as s:
        return await Query(s, ctx, tables).next_number(doctype, key)


class TestCounter:
    async def test_sequence_starts_at_one(self, db, tables):
        ctx = make_ctx("acme")
        assert [await _next(db, tables, ctx, "Invoice", "FA-{#}") for _ in range(4)] == [1, 2, 3, 4]

    async def test_each_key_doctype_and_tenant_counts_alone(self, db, tables):
        a, g = make_ctx("acme"), make_ctx("globex")
        assert await _next(db, tables, a, "Invoice", "FA-2026-{#}") == 1
        assert await _next(db, tables, a, "Invoice", "FA-2026-{#}") == 2
        assert await _next(db, tables, a, "Invoice", "FA-2027-{#}") == 1  # une autre période
        assert await _next(db, tables, a, "Credit", "FA-2026-{#}") == 1  # un autre DocType
        assert await _next(db, tables, g, "Invoice", "FA-2026-{#}") == 1  # un autre tenant
        assert await _next(db, tables, a, "Invoice", "FA-2026-{#}") == 3

    async def test_a_rollback_gives_the_number_back(self, db, tables):
        ctx = make_ctx("acme")
        with pytest.raises(RuntimeError):
            async with db.session() as s:
                assert await Query(s, ctx, tables).next_number("Invoice", "FA-{#}") == 1
                raise RuntimeError("le document n'a pas pu être écrit")
        assert await _next(db, tables, ctx, "Invoice", "FA-{#}") == 1  # pas de trou

    async def test_concurrent_callers_never_get_the_same_number(self, db, tables):
        ctx = make_ctx("acme")
        got = await asyncio.gather(*(_next(db, tables, ctx, "Invoice", "FA-{#}") for _ in range(40)))
        assert sorted(got) == list(range(1, 41))


INVOICE = {
    "schema_version": 1,
    "name": "Invoice",
    "module": "sales",
    "naming": {"series": "FA-{YYYY}-{#####}"},
    "fields": [{"fieldname": "label", "fieldtype": "Text", "required": True}],
    "hooks": {"validate": ["t.seen"], "before_save": ["t.seen"], "after_save": ["t.seen"]},
}
GRANTS = ["sales:invoice:read", "sales:invoice:write", "sales:invoice:delete"]


class Log:
    def info(self, *a, **k): ...
    def error(self, *a, **k): ...


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture
def seen():
    return []


@pytest.fixture
def clock():
    return Clock(NOW)


@pytest.fixture
def svc(db, tables, seen, clock):
    reg = DocTypeRegistry()
    reg.register(parse_definition(INVOICE))
    hooks = HookRegistry()

    async def record(event):
        seen.append((event.event, event.name))

    hooks.register("t.seen", record)
    return DocumentService(reg, db, tables, Log(), hooks=hooks, clock=clock)


def ctx(tenant="acme", user="alice"):
    return make_ctx(tenant, user, permissions=GRANTS)


async def create(svc, label="x", **kw):
    return (await svc.create(kw.pop("ctx", ctx()), "Invoice", {"label": label}))["name"]


class TestService:
    async def test_names_are_sequential_and_readable(self, svc):
        names = [await create(svc) for _ in range(3)]
        assert names == ["FA-2026-00001", "FA-2026-00002", "FA-2026-00003"]
        got = await svc.get(ctx(), "Invoice", "FA-2026-00002")
        assert got["name"] == "FA-2026-00002"

    async def test_hooks_see_no_name_until_the_number_is_assigned_then_the_real_one(self, svc, seen):
        await create(svc)
        assert seen == [("validate", None), ("before_save", None), ("after_save", "FA-2026-00001")]

    async def test_an_update_keeps_the_name_and_does_not_count(self, svc, seen):
        name = await create(svc)
        doc = await svc.get(ctx(), "Invoice", name)
        seen.clear()
        await svc.update(ctx(), "Invoice", name, {"label": "y"}, doc["version"])
        assert seen == [("validate", name), ("before_save", name), ("after_save", name)]
        assert await create(svc) == "FA-2026-00002"

    async def test_deleting_never_reuses_a_number(self, svc):
        assert await create(svc) == "FA-2026-00001"
        await svc.delete(ctx(), "Invoice", "FA-2026-00001")
        assert await create(svc) == "FA-2026-00002"

    async def test_a_refused_document_consumes_no_number(self, svc):
        with pytest.raises(ValidationFailed):
            await svc.create(ctx(), "Invoice", {})  # `label` obligatoire
        with pytest.raises(ValidationFailed):
            await svc.create(ctx(), "Invoice", {"label": 12})
        assert await create(svc) == "FA-2026-00001"

    async def test_a_hook_that_refuses_consumes_no_number(self, db, tables):
        from xcore_doctype.errors import DocumentValidationError

        reg = DocTypeRegistry()
        reg.register(parse_definition(INVOICE | {"hooks": {"validate": ["t.refuse_bad"]}}))
        hooks = HookRegistry()

        async def refuse_bad(event):
            if event.data.get("label") == "bad":
                raise DocumentValidationError("refusé")

        hooks.register("t.refuse_bad", refuse_bad)
        s = DocumentService(reg, db, tables, Log(), hooks=hooks, clock=Clock(NOW))
        assert await create(s, "ok") == "FA-2026-00001"
        for _ in range(3):
            with pytest.raises(DocumentValidationError):
                await create(s, "bad")
        assert await create(s, "ok") == "FA-2026-00002"  # le numéro est attribué en dernier : un refus n'en consomme aucun

    async def test_a_failure_after_the_number_was_taken_gives_it_back(self, svc, monkeypatch):
        """Même transaction que le document : si l'insertion échoue, le compteur revient en arrière (pas de trou)."""
        real = Query.insert

        async def boom(self, *a, **k):
            raise RuntimeError("disque plein")

        monkeypatch.setattr(Query, "insert", boom)
        with pytest.raises(RuntimeError):
            await svc.create(ctx(), "Invoice", {"label": "x"})
        monkeypatch.setattr(Query, "insert", real)
        assert await create(svc) == "FA-2026-00001"

    async def test_the_year_starts_a_new_sequence(self, svc, clock):
        assert await create(svc) == "FA-2026-00001"
        clock.now = datetime(2027, 1, 2, tzinfo=UTC)
        assert await create(svc) == "FA-2027-00001"
        clock.now = NOW
        assert await create(svc) == "FA-2026-00002"

    async def test_tenants_number_independently(self, svc):
        assert await create(svc, ctx=ctx("acme")) == "FA-2026-00001"
        assert await create(svc, ctx=ctx("globex", "bob")) == "FA-2026-00001"
        assert await create(svc, ctx=ctx("acme")) == "FA-2026-00002"
        assert (await svc.list(ctx("globex", "bob"), "Invoice"))["total"] == 1

    async def test_the_date_parts_follow_the_configured_timezone(self, db, tables, clock):
        reg = DocTypeRegistry()
        reg.register(parse_definition(INVOICE | {"hooks": {}}))
        paris = DocumentService(reg, db, tables, Log(), timezone=ZoneInfo("Pacific/Auckland"), clock=clock)
        clock.now = datetime(2026, 12, 31, 23, 30, tzinfo=UTC)  # déjà le 1er janvier 2027 à Auckland (UTC+13)
        assert (await paris.create(ctx(), "Invoice", {"label": "x"}))["name"] == "FA-2027-00001"

    async def test_hash_naming_is_unchanged(self, db, tables, customer):
        reg = DocTypeRegistry()
        # `Customer.country` cite `Country` : sans lui le type est incomplet et l'écriture serait refusée (503).
        reg.register(parse_definition({"schema_version": 1, "name": "Country", "module": "geo",
                                       "fields": [{"fieldname": "country_name", "fieldtype": "Text"}]}))
        reg.register(customer.definition)
        s = DocumentService(reg, db, tables, Log())
        name = (await s.create(make_ctx(), "Customer", {"customer_name": "x"}))["name"]
        assert len(name) == 12 and int(name, 16) >= 0

    async def test_forty_concurrent_creations_give_forty_consecutive_numbers(self, svc):
        """Critère de sortie de la phase 3 : pas de doublon, pas de trou, sur SQLite comme sur PostgreSQL."""
        names = await asyncio.gather(*(create(svc, f"n{i}") for i in range(40)))
        assert len(set(names)) == 40
        assert sorted(names) == [f"FA-2026-{i:05d}" for i in range(1, 41)]
        assert (await svc.list(ctx(), "Invoice", limit=200))["total"] == 40
