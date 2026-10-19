"""Agrégats (`DocumentService.aggregate`, `Query.aggregate`) : comptes, sommes exactes, regroupements, fenêtres de temps, périmètre.

Chaque test tourne sur SQLite et, si configuré, sur PostgreSQL (fixture `db`) : les deux bases doivent répondre pareil.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from tests.conftest import make_ctx
from xcore_doctype import aggregation
from xcore_doctype.aggregation import Metric
from xcore_doctype.document import DocumentService
from xcore_doctype.errors import NotFound, PermissionDenied, ValidationFailed
from xcore_doctype.registry import DocTypeRegistry
from xcore_doctype.schema import parse_definition

ALL = ["shop:sale:read", "shop:sale:write", "shop:client:read", "shop:client:write"]
OWN = ["shop:sale:read_own", "shop:sale:write", "shop:client:read", "shop:client:write"]
ALICE = make_ctx("acme", "alice", permissions=ALL)
GLOBEX = make_ctx("globex", "gina", permissions=ALL)

CLIENT = {"schema_version": 1, "name": "Client", "module": "shop", "fields": [{"fieldname": "client_name", "fieldtype": "Text"}]}
SALE = {
    "schema_version": 1,
    "name": "Sale",
    "module": "shop",
    "fields": [
        {"fieldname": "client", "fieldtype": "Link", "options": "Client"},
        {"fieldname": "status", "fieldtype": "Select", "options": ["Ouverte", "Payée", "Perdue"]},
        {"fieldname": "amount", "fieldtype": "Decimal", "decimals": 2},
        {"fieldname": "qty", "fieldtype": "Number"},
        {"fieldname": "paid", "fieldtype": "Boolean"},
        {"fieldname": "due", "fieldtype": "Date"},
        {"fieldname": "note", "fieldtype": "Text"},
    ],
    "permissions": {"read": ["shop:sale:read", {"grant": "shop:sale:read_own", "if_owner": True}]},
}
NOW = datetime(2026, 10, 17, 9, 0, tzinfo=UTC)


@pytest.fixture
def svc(db, tables):
    reg = DocTypeRegistry()
    for raw in (CLIENT, SALE):
        reg.register(parse_definition(raw))
    return DocumentService(reg, db, tables, _Log(), clock=lambda: NOW)


class _Log:
    def info(self, *a, **k): ...
    def error(self, *a, **k): ...


async def sale(svc, ctx=ALICE, **data):
    return await svc.create(ctx, "Sale", data)


async def seed(svc):
    for name in ("c1", "c2", "c3"):
        await svc.create(ALICE, "Client", {"client_name": name})
        await svc.create(GLOBEX, "Client", {"client_name": name})
    clients = {n: d["name"] for n, d in zip(("c1", "c2", "c3"), (await svc.list(ALICE, "Client", order_by="creation", descending=False))["items"], strict=True)}
    rows = [
        dict(client=clients["c1"], status="Ouverte", amount="10.10", qty=1, paid=False, due="2026-10-03"),
        dict(client=clients["c1"], status="Payée", amount="0.20", qty=2, paid=True, due="2026-10-20"),
        dict(client=clients["c2"], status="Payée", amount="5.00", qty=3, paid=True, due="2026-08-15"),
        dict(client=clients["c3"], status="Perdue", amount="7.75", qty=4, paid=False, due="2025-12-31"),
        dict(status="Ouverte", qty=10),  # ni client, ni montant, ni échéance
    ]
    for r in rows:
        await sale(svc, **r)
    await sale(svc, GLOBEX, status="Ouverte", amount="1000.00", qty=99, due="2026-10-01")  # un autre tenant : jamais compté
    return clients


async def agg(svc, ctx=ALICE, **kw):
    return await svc.aggregate(ctx, "Sale", **kw)


def table(res):
    return {r["key"]: r["values"] for r in res["rows"]}


class TestCountsAndMetrics:
    async def test_a_plain_count(self, svc):
        await seed(svc)
        res = await agg(svc)
        assert res["rows"] == [{"key": None, "values": [5]}] and res["group"] is None and res["truncated"] is False

    async def test_an_empty_set_counts_zero_and_sums_zero_but_has_no_average(self, svc):
        res = await agg(svc, metrics=["count", "sum:amount", "sum:qty", "avg:qty", "min:amount", "max:qty"])
        assert res["rows"][0]["values"] == [0, "0.00", 0, None, None, None]

    async def test_decimal_sums_are_exact_not_floats(self, svc):
        # 0.1 + 0.2 + 0.3 est 0.6000000000000001 en flottant : une somme de montants doit rester exacte.
        for amount in ("0.10", "0.20", "0.30"):
            await sale(svc, amount=amount)
        res = await agg(svc, metrics=["sum:amount"])
        assert res["rows"][0]["values"] == ["0.60"]
        assert res["metrics"][0]["decimals"] == 2  # le client sait formater sans connaître le DocType

    async def test_a_decimal_sum_stays_exact_beyond_what_a_double_holds(self, svc, db):
        if db.backend == "sqlite":
            pytest.skip("SQLite n'a pas de décimal : il additionne en double, exact jusqu'à ~15 chiffres (documenté dans doc/phase-ij.md)")
        # 4503599627370497.01 a 18 chiffres : un double (2^52 près de 4,5e15, pas de 0,5) perdrait les centimes.
        await sale(svc, amount="4503599627370497.01")
        await sale(svc, amount="0.01")
        assert (await agg(svc, metrics=["sum:amount"]))["rows"][0]["values"] == ["4503599627370497.02"]

    async def test_all_functions_over_decimal_and_number(self, svc):
        await seed(svc)
        res = await agg(svc, metrics=["sum:amount", "avg:amount", "min:amount", "max:amount", "sum:qty", "avg:qty", "min:qty", "max:qty"])
        # Montants présents : 10.10, 0.20, 5.00, 7.75 (le cinquième n'en a pas : il ne compte ni dans la somme ni dans la moyenne).
        assert res["rows"][0]["values"] == ["23.05", "5.76", "0.20", "10.10", 20, 4, 1, 10]

    async def test_the_tenant_never_leaks_into_a_total(self, svc):
        await seed(svc)
        mine = await agg(svc, metrics=["count", "sum:qty"])
        theirs = await agg(svc, GLOBEX, metrics=["count", "sum:qty"])
        assert mine["rows"][0]["values"] == [5, 20] and theirs["rows"][0]["values"] == [1, 99]

    async def test_filters_are_the_lists_filters(self, svc):
        await seed(svc)
        assert (await agg(svc, filters={"status": "Payée"}))["rows"][0]["values"] == [2]
        assert (await agg(svc, filters={"amount": [">", "5.00"]}))["rows"][0]["values"] == [2]
        assert (await agg(svc, filters={"status": ["in", ["Ouverte", "Perdue"]], "paid": False}))["rows"][0]["values"] == [2]
        assert (await agg(svc, filters={"status": ["!=", "Payée"]}))["rows"][0]["values"] == [3]


class TestGroups:
    async def test_by_select_biggest_group_first_then_key(self, svc):
        await seed(svc)
        res = await agg(svc, group_by="status")
        assert [(r["key"], r["values"]) for r in res["rows"]] == [("Ouverte", [2]), ("Payée", [2]), ("Perdue", [1])]
        assert res["group"] == {"field": "status", "bucket": None, "fieldtype": "Select", "options": ["Ouverte", "Payée", "Perdue"], "label": "status"}

    async def test_by_select_ordered_by_the_first_metric(self, svc):
        await seed(svc)
        res = await agg(svc, group_by="status", metrics=["sum:qty", "count"])
        assert [(r["key"], r["values"]) for r in res["rows"]] == [("Ouverte", [11, 2]), ("Payée", [5, 2]), ("Perdue", [4, 1])]

    async def test_by_link_exposes_the_target_and_keeps_a_group_for_the_missing_value(self, svc):
        clients = await seed(svc)
        res = await agg(svc, group_by="client", metrics=["sum:amount"])
        assert res["group"]["options"] == "Client" and res["group"]["fieldtype"] == "Link"
        got = table(res)
        assert got[clients["c1"]] == ["10.30"] and got[clients["c3"]] == ["7.75"] and got[None] == ["0.00"]

    async def test_by_boolean(self, svc):
        await seed(svc)
        assert table(await agg(svc, group_by="paid")) == {True: [2], False: [2], None: [1]}

    async def test_by_a_system_column(self, svc):
        await seed(svc)
        assert table(await agg(svc, group_by="docstatus")) == {0: [5]}
        assert table(await agg(svc, group_by="owner")) == {"alice": [5]}

    async def test_a_group_limit_truncates_and_says_so(self, svc):
        await seed(svc)
        res = await agg(svc, group_by="status", limit=2)
        assert len(res["rows"]) == 2 and res["truncated"] is True
        assert (await agg(svc, group_by="status", limit=3))["truncated"] is False

    async def test_free_text_cannot_be_grouped(self, svc):
        with pytest.raises(ValidationFailed, match="regroupement impossible"):
            await agg(svc, group_by="note")
        with pytest.raises(ValidationFailed, match="regroupement impossible"):
            await agg(svc, group_by="amount")


class TestTime:
    async def test_by_month_oldest_first(self, svc):
        await seed(svc)
        res = await agg(svc, group_by="due")  # `month` par défaut
        assert [r["key"] for r in res["rows"]] == ["2025-12", "2026-08", "2026-10"]
        assert [r["values"] for r in res["rows"]] == [[1], [1], [2]]
        assert res["group"]["bucket"] == "month"

    async def test_by_year_and_by_day(self, svc):
        await seed(svc)
        assert table(await agg(svc, group_by="due", bucket="year")) == {"2025": [1], "2026": [3]}
        assert table(await agg(svc, group_by="due", bucket="day"))["2026-10-03"] == [1]

    async def test_a_document_without_a_date_has_no_place_on_the_timeline(self, svc):
        await seed(svc)
        res = await agg(svc, group_by="due", bucket="year")
        assert None not in table(res) and sum(v[0] for v in table(res).values()) == 4  # 5 ventes, 1 sans échéance

    async def test_periods_fill_the_empty_months_with_zero(self, svc):
        await seed(svc)
        res = await agg(svc, group_by="due", periods=4, metrics=["count", "sum:amount", "avg:amount"])
        assert [r["key"] for r in res["rows"]] == ["2026-07", "2026-08", "2026-09", "2026-10"]
        assert [r["values"] for r in res["rows"]] == [
            [0, "0.00", None],  # un mois vide : zéro, et pas de moyenne
            [1, "5.00", "5.00"],
            [0, "0.00", None],
            [2, "10.30", "5.15"],
        ]

    async def test_periods_exclude_what_lies_outside_the_window(self, svc):
        await seed(svc)
        res = await agg(svc, group_by="due", periods=2)
        assert [r["key"] for r in res["rows"]] == ["2026-09", "2026-10"] and [r["values"] for r in res["rows"]] == [[0], [2]]

    async def test_periods_by_day_and_by_year(self, svc):
        await seed(svc)
        days = await agg(svc, group_by="due", bucket="day", periods=3)
        assert [r["key"] for r in days["rows"]] == ["2026-10-15", "2026-10-16", "2026-10-17"]
        years = await agg(svc, group_by="due", bucket="year", periods=2)
        assert [(r["key"], r["values"]) for r in years["rows"]] == [("2025", [1]), ("2026", [3])]

    async def test_a_window_ignores_the_periods_after_it_before_the_limit_applies(self, svc):
        # Cinq mois à venir et le mois en cours : sans borne en SQL, la limite (2) garderait les deux mois les plus lointains.
        for due in ("2026-10-02", "2027-01-10", "2027-02-10", "2027-03-10", "2027-04-10", "2027-05-10"):
            await sale(svc, due=due)
        res = await agg(svc, group_by="due", periods=2, limit=2)
        assert [(r["key"], r["values"]) for r in res["rows"]] == [("2026-09", [0]), ("2026-10", [1])] and res["truncated"] is False

    async def test_without_a_window_a_cut_keeps_the_most_recent_periods(self, svc):
        await seed(svc)  # trois mois : 2025-12, 2026-08, 2026-10
        res = await agg(svc, group_by="due", limit=2)
        assert [r["key"] for r in res["rows"]] == ["2026-08", "2026-10"] and res["truncated"] is True

    async def test_a_window_longer_than_the_default_limit_is_not_cut(self, svc):
        from datetime import date, timedelta

        # 60 jours consécutifs, une vente chacun : plus de groupes que la limite par défaut (50), tous dans la fenêtre.
        for n in range(60):
            await sale(svc, due=(date(2026, 10, 17) - timedelta(days=n)).isoformat(), qty=1)
        res = await agg(svc, group_by="due", bucket="day", periods=60, metrics=["count", "sum:qty"])
        assert len(res["rows"]) == 60 and res["truncated"] is False
        assert all(r["values"] == [1, 1] for r in res["rows"])

    async def test_today_is_the_organisations_not_the_servers(self, db, tables):
        from zoneinfo import ZoneInfo

        reg = DocTypeRegistry()
        for raw in (CLIENT, SALE):
            reg.register(parse_definition(raw))
        late = datetime(2026, 10, 31, 23, 30, tzinfo=UTC)  # déjà le 1er novembre à Auckland
        auckland = DocumentService(reg, db, tables, _Log(), timezone=ZoneInfo("Pacific/Auckland"), clock=lambda: late)
        utc = DocumentService(reg, db, tables, _Log(), clock=lambda: late)
        assert [r["key"] for r in (await auckland.aggregate(ALICE, "Sale", group_by="due", periods=2))["rows"]] == ["2026-10", "2026-11"]
        assert [r["key"] for r in (await utc.aggregate(ALICE, "Sale", group_by="due", periods=2))["rows"]] == ["2026-09", "2026-10"]

    async def test_creation_and_modified_group_by_utc_day(self, db, tables):
        # La création est datée par la base (maintenant, UTC) : on lit le jour sur l'horloge réelle.
        reg = DocTypeRegistry()
        for raw in (CLIENT, SALE):
            reg.register(parse_definition(raw))
        svc = DocumentService(reg, db, tables, _Log())
        await sale(svc, qty=1)
        await sale(svc, qty=2)
        today = datetime.now(UTC).date().isoformat()
        for column in ("creation", "modified"):
            res = await svc.aggregate(ALICE, "Sale", group_by=column, bucket="day", metrics=["count", "sum:qty"])
            assert [(r["key"], r["values"]) for r in res["rows"]] == [(today, [2, 3])]
        res = await svc.aggregate(ALICE, "Sale", group_by="creation", bucket="year", periods=1)
        assert [(r["key"], r["values"]) for r in res["rows"]] == [(today[:4], [2])]
        assert (await svc.aggregate(ALICE, "Sale", group_by="creation"))["group"]["bucket"] == "month"


class TestPermissions:
    async def test_no_read_grant_is_refused(self, svc):
        with pytest.raises(PermissionDenied):
            await svc.aggregate(make_ctx("acme", "eve", permissions=[]), "Sale")

    async def test_an_owner_only_reader_counts_only_their_documents(self, svc):
        await sale(svc, status="Ouverte", qty=5)
        bob = make_ctx("acme", "bob", permissions=ALL)
        await sale(svc, bob, status="Payée", qty=7)
        only_bob = make_ctx("acme", "bob", permissions=OWN)
        res = await svc.aggregate(only_bob, "Sale", group_by="status", metrics=["count", "sum:qty"])
        assert table(res) == {"Payée": [1, 7]}  # le chiffre de la liste de Bob, pas celui du tenant
        assert (await svc.list(only_bob, "Sale"))["total"] == 1
        # Sans regroupement aussi : un total qui compterait les documents des autres révélerait leur nombre.
        assert (await svc.aggregate(only_bob, "Sale", metrics=["count", "sum:qty"]))["rows"][0]["values"] == [1, 7]
        assert (await svc.aggregate(ALICE, "Sale"))["rows"][0]["values"] == [2]

    async def test_an_unknown_doctype_is_not_found_like_everywhere(self, svc):
        with pytest.raises(NotFound):
            await svc.aggregate(ALICE, "Nope")

    async def test_the_caller_must_be_identified(self, svc):
        from xcore_doctype.context import RequestContext
        from xcore_doctype.errors import ContextError

        with pytest.raises(ContextError):
            await svc.aggregate(RequestContext("acme", None, "http"), "Sale")


class TestValidation:
    @pytest.mark.parametrize(
        "kw, fragment",
        [
            (dict(metrics=["sum:note"]), "seul un Number, un Decimal"),
            (dict(metrics=["sum:ghost"]), "champ inconnu"),
            (dict(metrics=["total:qty"]), "mesure invalide"),
            (dict(metrics=["sum"]), "mesure invalide"),
            (dict(metrics=["count:qty"]), "mesure invalide"),
            (dict(metrics=["sum:"]), "mesure invalide"),
            (dict(metrics=["count", "count"]), "deux fois"),
            (dict(metrics=[]), "count"),  # une liste vide retombe sur `count` (voir resolve) : ce cas ne lève pas
            (dict(group_by="ghost"), "champ inconnu"),
            (dict(group_by="status", bucket="month"), "n'a de sens que pour une date"),
            (dict(group_by="due", bucket="week"), "bucket inconnu"),
            (dict(bucket="month"), "exigent un regroupement"),
            (dict(periods=3), "exigent un regroupement"),
            (dict(group_by="status", periods=3), "par date"),
            (dict(group_by="due", bucket="day", periods=367), "entre 1 et 366"),
            (dict(group_by="due", bucket="month", periods=121), "entre 1 et 120"),
            (dict(group_by="due", periods=0), "entre 1 et"),
            (dict(group_by="due", periods=True), "entre 1 et"),
            (dict(group_by="status", limit=0), "limit"),
            (dict(group_by="status", limit=401), "limit"),
            (dict(filters=["status"]), "objet"),
            (dict(filters={"ghost": 1}), "champs inconnus"),
            (dict(filters={"qty": "x"}), "filtre invalide"),
        ],
    )
    async def test_a_bad_request_is_refused_with_a_reason(self, svc, kw, fragment):
        if kw == dict(metrics=[]):
            assert (await agg(svc, **kw))["rows"][0]["values"] == [0]
            return
        with pytest.raises(ValidationFailed, match=fragment):
            await agg(svc, **kw)

    async def test_a_table_cannot_be_filtered(self, db, tables):
        reg = DocTypeRegistry()
        reg.register(parse_definition({"schema_version": 1, "name": "Line", "istable": True, "fields": [{"fieldname": "x", "fieldtype": "Text"}]}))
        reg.register(parse_definition({"schema_version": 1, "name": "Doc", "module": "shop", "fields": [{"fieldname": "lines", "fieldtype": "Table", "options": "Line"}]}))
        svc = DocumentService(reg, db, tables, _Log())
        with pytest.raises(ValidationFailed, match="Table"):
            await svc.aggregate(make_ctx("acme", "alice", permissions=["shop:doc:read"]), "Doc", filters={"lines": 1})


class TestMetricParsing:
    def test_parse_and_render(self):
        assert Metric.parse("count") == Metric("count") and str(Metric.parse("sum:qty")) == "sum:qty"

    def test_not_a_string(self):
        with pytest.raises(ValidationFailed):
            Metric.parse(["count"])


class TestWindowMath:
    @pytest.mark.parametrize(
        "bucket, today, periods, expected",
        [
            ("month", (2026, 1, 15), 3, ["2025-11", "2025-12", "2026-01"]),  # franchit l'année
            ("month", (2026, 3, 31), 1, ["2026-03"]),
            ("day", (2026, 3, 1), 3, ["2026-02-27", "2026-02-28", "2026-03-01"]),
            ("day", (2024, 3, 1), 2, ["2024-02-29", "2024-03-01"]),  # année bissextile
            ("year", (2026, 6, 1), 3, ["2024", "2025", "2026"]),
        ],
    )
    def test_bucket_keys(self, bucket, today, periods, expected):
        from datetime import date

        assert aggregation.bucket_keys(bucket, date(*today), periods) == expected

    def test_decimal_results_are_quantized_to_the_fields_decimals(self):
        m = Metric("avg", "amount")
        assert aggregation._finish(Decimal("5.7625"), m, 2) == "5.76"
        assert aggregation._finish(5.7625000000001, m, 2) == "5.76"
        assert aggregation._finish(Decimal("-0.001"), m, 2) == "0.00"  # pas de « -0.00 »
