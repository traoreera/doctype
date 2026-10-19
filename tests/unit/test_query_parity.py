"""Parité SQLite / PostgreSQL des filtres, tris et pagination sur la colonne JSON (doc/doctype.md §7.3, §16).

Les mêmes attentes s'appliquent aux deux backends ; les cas PostgreSQL sont SKIPPED sans
`XCORE_DOCTYPE_TEST_PG_URL`. Valeurs de tri en ASCII minuscule : la collation de texte diffère
entre les backends (voir `test_text_sort_is_collation_dependent`).
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

from tests.conftest import make_ctx
from xcore_doctype.errors import AlreadyExists, NotFound, ValidationFailed, VersionConflict
from xcore_doctype.permissions import Scope
from xcore_doctype.query import Query

ROWS = {
    # name:  customer_name, credit_limit, vip,   since,        type
    "r1": ("alpha", 100, True, "2026-01-05", "Entreprise"),
    "r2": ("bravo", 100.5, False, "2026-03-01", "Particulier"),
    "r3": ("charlie", 7, True, "2025-12-31", "Entreprise"),
    "r4": ("delta", None, None, None, None),  # champs optionnels absents
    "r5": ("echo", 100, False, "2026-03-01", "Entreprise"),
    "r6": ("foxtrot", 0, True, "2026-01-05", "Particulier"),
}

ALL = Scope.everything("read")
PAGE = dict(filters={}, order_by="name", descending=False, limit=200, offset=0, scope=ALL)


@pytest.fixture
async def seeded(db, tables, customer):
    @asynccontextmanager
    async def q(tenant="acme"):
        async with db.session() as s:
            yield Query(s, make_ctx(tenant), tables)

    async with q() as query:
        for name, (cn, limit, vip, since, typ) in ROWS.items():
            data = {"customer_name": cn}
            for k, v in (("credit_limit", limit), ("vip", vip), ("since", since), ("type", typ)):
                if v is not None:
                    data[k] = v
            await query.insert("Customer", name, data)
    return q, customer.definition


async def names(q, defn, **kw):
    async with q() as query:
        docs, total = await query.list(defn, **{**PAGE, **kw})
    return [d.name for d in docs], total


class TestFilters:
    @pytest.mark.parametrize(
        "filters, expected",
        [
            ({"customer_name": "alpha"}, ["r1"]),
            ({"type": "Entreprise"}, ["r1", "r3", "r5"]),
            ({"credit_limit": 100}, ["r1", "r5"]),  # 100 (int) et 100.0
            ({"credit_limit": 100.5}, ["r2"]),
            ({"credit_limit": 0}, ["r6"]),  # zéro n'est pas « absent »
            ({"vip": True}, ["r1", "r3", "r6"]),
            ({"vip": False}, ["r2", "r5"]),  # r4 (absent) n'est ni True ni False
            ({"since": "2026-03-01"}, ["r2", "r5"]),
            ({"type": "Entreprise", "credit_limit": 100}, ["r1", "r5"]),  # ET
            ({"type": None}, ["r4"]),  # IS NULL : champ absent
            ({"credit_limit": None}, ["r4"]),
            ({"vip": None}, ["r4"]),
            ({"customer_name": "inconnu"}, []),
        ],
    )
    async def test_equality(self, seeded, filters, expected):
        q, defn = seeded
        got, total = await names(q, defn, filters=filters)
        assert (got, total) == (expected, len(expected))

    @pytest.mark.parametrize("value", ["O'Brien", 'guill"emet', "a\\b", "100%", "x_y", "'; DROP TABLE xdt_document;--", "é à 日本"])
    async def test_awkward_values_are_data_not_syntax(self, db, tables, customer, value):
        async with db.session() as s:
            await Query(s, make_ctx(), tables).insert("Customer", "w1", {"customer_name": value})
        async with db.session() as s:
            docs, total = await Query(s, make_ctx(), tables).list(
                customer.definition, **{**PAGE, "filters": {"customer_name": value}}
            )
        assert [d.data["customer_name"] for d in docs] == [value] and total == 1

    async def test_unknown_filter_field_refused(self, seeded):
        q, defn = seeded
        with pytest.raises(ValidationFailed):
            await names(q, defn, filters={"nope": 1})


class TestOrdering:
    async def test_by_system_column(self, seeded):
        q, defn = seeded
        assert (await names(q, defn, order_by="name", descending=True))[0] == ["r6", "r5", "r4", "r3", "r2", "r1"]

    async def test_number_asc_nulls_last_ties_by_name(self, seeded):
        q, defn = seeded
        got, _ = await names(q, defn, order_by="credit_limit", descending=False)
        # 0, 7, 100, 100 (départagés par name), 100.5, puis l'absent en dernier.
        assert got == ["r6", "r3", "r1", "r5", "r2", "r4"]

    async def test_number_desc_nulls_still_last(self, seeded):
        q, defn = seeded
        got, _ = await names(q, defn, order_by="credit_limit", descending=True)
        assert got == ["r2", "r1", "r5", "r3", "r6", "r4"]  # NULL en dernier aussi en desc, sur les deux backends

    async def test_date_is_chronological(self, seeded):
        q, defn = seeded
        got, _ = await names(q, defn, order_by="since", descending=False)
        assert got == ["r3", "r1", "r6", "r2", "r5", "r4"]

    async def test_text_asc(self, seeded):
        q, defn = seeded
        assert (await names(q, defn, order_by="customer_name", descending=True))[0] == ["r6", "r5", "r4", "r3", "r2", "r1"]

    async def test_unsortable_refused(self, seeded):
        q, defn = seeded
        for bad in ("data", "tenant_id", "nope", "customer_name; DROP TABLE x"):
            with pytest.raises(ValidationFailed):
                await names(q, defn, order_by=bad)

    async def test_text_sort_is_collation_dependent(self, db, tables, customer):
        """Documente un écart connu : l'ordre de texte suit la collation du backend.

        SQLite compare en binaire (« B » < « a »). PostgreSQL suit la collation de la base :
        sous `fr_FR.UTF-8`, « apple » < « Banana ». Un tri sur du texte de casse mixte n'est donc
        pas portable ; à trancher avant la phase 2 (liste triable par colonne texte).
        """
        async with db.session() as s:
            q = Query(s, make_ctx(), tables)
            for n, v in (("m1", "cherry"), ("m2", "Banana"), ("m3", "apple")):
                await q.insert("Customer", n, {"customer_name": v})
        async with db.session() as s:
            docs, _ = await Query(s, make_ctx(), tables).list(
                customer.definition, **{**PAGE, "order_by": "customer_name"}
            )
        got = [d.data["customer_name"] for d in docs]
        binary, localized = ["Banana", "apple", "cherry"], ["apple", "Banana", "cherry"]
        if db.backend == "sqlite":
            assert got == binary
        else:
            # Dépend de la collation du cluster (C => binaire ; fr_FR.UTF-8 => localisé).
            assert got in (binary, localized)
        print(f"\n[collation] {db.backend}: {got}")


class TestPagination:
    async def test_pages_cover_everything_once(self, seeded):
        q, defn = seeded
        seen = []
        for off in (0, 2, 4):
            got, total = await names(q, defn, order_by="credit_limit", limit=2, offset=off)
            assert total == 6
            seen += got
        assert seen == ["r6", "r3", "r1", "r5", "r2", "r4"]  # sans doublon ni trou malgré les ex æquo

    async def test_offset_past_end(self, seeded):
        q, defn = seeded
        assert await names(q, defn, offset=50) == ([], 6)

    async def test_filtered_total_ignores_limit(self, seeded):
        q, defn = seeded
        assert await names(q, defn, filters={"type": "Entreprise"}, limit=1) == (["r1"], 3)


class TestWrites:
    async def test_roundtrip_types(self, seeded):
        q, _ = seeded
        async with q() as query:
            doc = await query.get("Customer", "r2", scope=ALL)
        assert doc.data == {"customer_name": "bravo", "credit_limit": 100.5, "vip": False, "since": "2026-03-01", "type": "Particulier"}
        assert (doc.version, doc.owner) == (1, "alice") and doc.creation.tzinfo is not None

    async def test_update_increments_version_and_replaces_data(self, seeded):
        q, _ = seeded
        async with q() as query:
            doc = await query.update("Customer", "r1", {"customer_name": "alpha2"}, expected_version=1, scope=ALL)
        assert doc.version == 2 and doc.data == {"customer_name": "alpha2"}  # remplacement, pas fusion
        assert doc.modified >= doc.creation

    async def test_stale_version_conflicts_and_changes_nothing(self, seeded):
        q, _ = seeded
        async with q() as query:
            await query.update("Customer", "r1", {"customer_name": "first"}, expected_version=1, scope=ALL)
        with pytest.raises(VersionConflict):
            async with q() as query:
                await query.update("Customer", "r1", {"customer_name": "second"}, expected_version=1, scope=ALL)
        async with q() as query:
            assert (await query.get("Customer", "r1", scope=ALL)).data["customer_name"] == "first"

    async def test_update_missing_is_not_a_conflict(self, seeded):
        q, _ = seeded
        with pytest.raises(NotFound):
            async with q() as query:
                await query.update("Customer", "ghost", {"customer_name": "x"}, expected_version=1, scope=ALL)

    async def test_duplicate_name_same_tenant(self, seeded):
        q, _ = seeded
        with pytest.raises(AlreadyExists):
            async with q() as query:
                await query.insert("Customer", "r1", {"customer_name": "dup"})

    async def test_concurrent_updates_exactly_one_wins(self, seeded):
        """D15 : deux écritures concurrentes avec la même version, une seule réussit."""
        import asyncio

        q, _ = seeded

        async def attempt(label):
            try:
                async with q() as query:
                    await query.update("Customer", "r1", {"customer_name": label}, expected_version=1, scope=ALL)
                return "ok"
            except VersionConflict:
                return "conflict"

        results = await asyncio.gather(*(attempt(f"w{i}") for i in range(8)))
        assert sorted(results) == ["conflict"] * 7 + ["ok"]
        async with q() as query:
            assert (await query.get("Customer", "r1", scope=ALL)).version == 2

    async def test_delete(self, seeded):
        q, _ = seeded
        async with q() as query:
            assert await query.delete("Customer", "r1", scope=ALL) is True
            assert await query.delete("Customer", "r1", scope=ALL) is False


# ── Opérateurs de filtre (phase 2 : barre de filtres de la liste) ────────────────────────────────


class TestOperators:
    """Mêmes résultats sur SQLite et PostgreSQL. Pas d'ordre sur le texte (collation) : voir builder.FILTER_OPS."""

    @pytest.fixture
    def f(self, customer):
        return customer.coerce_filter

    @pytest.mark.parametrize(
        "field, value, expected",
        [
            # != : un document SANS valeur est « différent », comme `!=` dans une condition
            ("type", ["!=", "Entreprise"], ["r2", "r4", "r6"]),
            ("credit_limit", ["!=", 100], ["r2", "r3", "r4", "r6"]),
            ("vip", ["!=", True], ["r2", "r4", "r5"]),
            ("customer_name", ["!=", "alpha"], ["r2", "r3", "r4", "r5", "r6"]),
            # like : « contient », sans casse
            ("customer_name", ["like", "LP"], ["r1"]),
            ("customer_name", ["like", "o"], ["r2", "r5", "r6"]),
            ("customer_name", ["like", "ALPHA"], ["r1"]),
            ("customer_name", ["like", "zzz"], []),
            # ordre sur nombres et dates
            ("credit_limit", [">", 100], ["r2"]),
            ("credit_limit", [">=", 100], ["r1", "r2", "r5"]),
            ("credit_limit", ["<", 100], ["r3", "r6"]),  # r4 (absent) n'est ni plus petit ni plus grand
            ("credit_limit", ["<=", 7], ["r3", "r6"]),
            ("credit_limit", [">", 100.25], ["r2"]),
            ("credit_limit", ["<", 0], []),
            ("since", [">=", "2026-01-05"], ["r1", "r2", "r5", "r6"]),
            ("since", ["<", "2026-01-05"], ["r3"]),
            ("since", [">", "2026-03-01"], []),
            # in
            ("type", ["in", ["Entreprise"]], ["r1", "r3", "r5"]),
            ("type", ["in", ["Entreprise", "Particulier"]], ["r1", "r2", "r3", "r5", "r6"]),
            ("credit_limit", ["in", [100, 7]], ["r1", "r3", "r5"]),
            ("credit_limit", ["in", [100.5]], ["r2"]),
            ("customer_name", ["in", ["alpha", "echo", "inconnu"]], ["r1", "r5"]),
            ("customer_name", ["in", []], []),
            # = explicite
            ("type", ["=", "Entreprise"], ["r1", "r3", "r5"]),
        ],
    )
    async def test_operator(self, seeded, f, field, value, expected):
        q, defn = seeded
        got, total = await names(q, defn, filters={field: f(field, value)})
        assert (got, total) == (expected, len(expected)), (field, value)

    async def test_operators_combine_with_equality_and_each_other(self, seeded, f):
        q, defn = seeded
        flt = {"type": f("type", "Entreprise"), "credit_limit": f("credit_limit", [">=", 100])}
        assert (await names(q, defn, filters=flt))[0] == ["r1", "r5"]
        flt = {"credit_limit": f("credit_limit", [">=", 7]), "since": f("since", ["<", "2026-03-01"])}
        assert (await names(q, defn, filters=flt))[0] == ["r1", "r3"]

    @pytest.mark.parametrize("pattern, expected", [("%", ["w1"]), ("_", ["w2"]), ("\\", ["w3"]), ("0%", ["w1"]), ("a_b", ["w2"])])
    async def test_like_treats_wildcards_as_plain_characters(self, db, tables, customer, pattern, expected):
        async with db.session() as s:
            qq = Query(s, make_ctx(), tables)
            for name, text in (("w1", "100% sûr"), ("w2", "a_b"), ("w3", "back\\slash"), ("w4", "axb"), ("w5", "plain")):
                await qq.insert("Customer", name, {"customer_name": text})
        async with db.session() as s:
            docs, total = await Query(s, make_ctx(), tables).list(
                customer.definition, **{**PAGE, "filters": {"customer_name": customer.coerce_filter("customer_name", ["like", pattern])}}
            )
        assert [d.name for d in docs] == expected and total == len(expected)

    async def test_the_operators_are_part_of_the_total_and_pagination(self, seeded, f):
        q, defn = seeded
        flt = {"credit_limit": f("credit_limit", [">=", 0])}
        assert await names(q, defn, filters=flt, limit=2, offset=0) == (["r1", "r2"], 5)
        assert await names(q, defn, filters=flt, limit=2, offset=4) == (["r6"], 5)

    @pytest.mark.parametrize(
        "field, value",
        [
            ("customer_name", [">", "a"]),  # pas d'ordre sur le texte
            ("type", [">=", "Entreprise"]),
            ("type", ["like", "Ent"]),  # ni de « contient » sur un Select
            ("credit_limit", ["like", "10"]),
            ("vip", ["like", "t"]),
            ("vip", ["in", [True]]),
            ("vip", [">", False]),
            ("since", ["like", "2026"]),
            ("credit_limit", ["between", [1, 2]]),
            ("credit_limit", ["="]),
            ("credit_limit", [">", 1, 2]),
            ("credit_limit", [1, 2]),
            ("credit_limit", [">", "abc"]),  # valeur du mauvais type
            ("credit_limit", [">", True]),
            ("credit_limit", ["in", 5]),
            ("credit_limit", ["in", [1, "x"]]),
            ("credit_limit", ["in", [None]]),
            ("since", [">", "20260101"]),  # date non canonique
            ("type", ["in", ["Autre"]]),
            ("customer_name", ["like", ""]),
            ("customer_name", ["like", 5]),
            ("customer_name", ["like", "x" * 101]),
            ("customer_name", ["in", ["x"] * 101]),
            ("customer_name", ["!=", 5]),
        ],
    )
    def test_invalid_filters_are_refused_before_any_sql(self, customer, field, value):
        with pytest.raises(ValidationFailed):
            customer.coerce_filter(field, value)

    async def test_query_itself_refuses_an_operator_the_type_does_not_allow(self, db, tables, customer):
        """Défense en profondeur : le constructeur SQL ne fait pas confiance à l'appelant."""
        from xcore_doctype.builder import Filter

        async with db.session() as s:
            with pytest.raises(ValidationFailed):
                await Query(s, make_ctx(), tables).list(
                    customer.definition, **{**PAGE, "filters": {"customer_name": Filter(">", "a")}}
                )


class TestSearch:
    async def search(self, db, tables, customer, q, **kw):
        async with db.session() as s:
            return await Query(s, make_ctx(), tables).search(customer.definition, q, **{"limit": 50, "scope": ALL, **kw})

    @pytest.fixture
    async def rows(self, seeded):
        return seeded

    async def test_matches_the_name_or_the_title_case_insensitively(self, rows, db, tables, customer):
        assert [d.name for d in await self.search(db, tables, customer, "ALP")] == ["r1"]
        assert [d.name for d in await self.search(db, tables, customer, "lph")] == ["r1"]
        assert [d.name for d in await self.search(db, tables, customer, "r3")] == ["r3"]  # par le nom du document

    async def test_empty_query_lists_the_first_documents_ordered_by_title(self, rows, db, tables, customer):
        assert [d.data["customer_name"] for d in await self.search(db, tables, customer, "")] == [
            "alpha", "bravo", "charlie", "delta", "echo", "foxtrot"]

    async def test_limit(self, rows, db, tables, customer):
        assert [d.name for d in await self.search(db, tables, customer, "", limit=2)] == ["r1", "r2"]

    @pytest.mark.parametrize("q", ["%", "_", "\\", "a_b", "100%"])
    async def test_wildcards_in_the_query_are_plain_characters(self, rows, db, tables, customer, q):
        assert await self.search(db, tables, customer, q) == []

    async def test_nothing_found(self, rows, db, tables, customer):
        assert await self.search(db, tables, customer, "zzzz") == []

    async def test_respects_the_permission_scope(self, rows, db, tables, customer):
        from xcore_doctype.permissions import Clause, Scope

        mine = Scope("read", (Clause(owner="alice", match=None),))
        theirs = Scope("read", (Clause(owner="bob", match=None),))
        assert len(await self.search(db, tables, customer, "", scope=mine)) == 6  # tous créés par alice
        assert await self.search(db, tables, customer, "", scope=theirs) == []
        assert await self.search(db, tables, customer, "", scope=Scope("read")) == []  # aucun grant : rien


class TestFilterOnName:
    """`name` se filtre comme un texte : le Desk retrouve ainsi les titres de plusieurs liens en une requête."""

    @pytest.mark.parametrize(
        "value, expected",
        [("r2", ["r2"]), (["in", ["r1", "r3", "absent"]], ["r1", "r3"]), (["!=", "r1"], ["r2", "r3", "r4", "r5", "r6"]), (["like", "R"], ["r1", "r2", "r3", "r4", "r5", "r6"]), (["in", []], [])],
    )
    async def test_name_filter(self, seeded, customer, value, expected):
        q, defn = seeded
        got, total = await names(q, defn, filters={"name": customer.coerce_filter("name", value)})
        assert (got, total) == (expected, len(expected))

    @pytest.mark.parametrize("value", [[">", "a"], 5, ["in", [5]], ["like", ""]])
    def test_name_filter_is_validated_like_text(self, customer, value):
        with pytest.raises(ValidationFailed):
            customer.coerce_filter("name", value)

    async def test_combines_with_a_field_filter_and_stays_in_the_tenant(self, seeded, customer):
        q, defn = seeded
        flt = {"name": customer.coerce_filter("name", ["in", ["r1", "r2", "r3"]]), "type": customer.coerce_filter("type", "Entreprise")}
        assert (await names(q, defn, filters=flt))[0] == ["r1", "r3"]
        async with q("globex") as other:  # un autre tenant ne voit aucun de ces noms
            docs, total = await other.list(defn, **{**PAGE, "filters": {"name": customer.coerce_filter("name", ["in", ["r1"]])}})
        assert (docs, total) == ([], 0)
