"""Module Stock, quantités seulement (palier 40 %, phase A) : le critère de sortie, sur les deux bases.

« Réception, sortie et transfert soumis, annulés par mouvement inverse ; stock négatif refusé en concurrence sur les deux bases. »

Les règles sont dans `tests/support/plugins/stock/src/ledger.py` (code de confiance, branché sur `on_submit` / `on_cancel`). Ces tests le font tourner
sur le VRAI moteur (DocumentService, SQLite et PostgreSQL), avec les DocTypes réels du plugin : seul l'hôte xcore est absent
(il est couvert par tests/integration/test_stock_plugin.py).
"""

from __future__ import annotations

import asyncio
import importlib
import sys
import types
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from tests.conftest import make_ctx
from tests.support import FIXTURES
from xcore_doctype import DocumentValidationError
from xcore_doctype.document import DocumentService
from xcore_doctype.errors import DoctypeError, InvalidDocStatus, PermissionDenied, ValidationFailed
from xcore_doctype.hooks import HookRegistry
from xcore_doctype.registry import DocTypeRegistry

STOCK, GEO = FIXTURES / "stock", FIXTURES / "geo"


def _load_ledger():
    """Charge `ledger.py` comme le fait xcore (un package virtuel dont le chemin est `src/`), sous un autre nom."""
    pkg = types.ModuleType("stock_under_test")
    pkg.__path__ = [str(STOCK / "src")]
    sys.modules["stock_under_test"] = pkg
    return importlib.import_module("stock_under_test.ledger")


ledger = _load_ledger()

GRANTS = [f"stock:{t}:{a}" for t in ("item", "warehouse") for a in ("read", "write", "delete")] + [
    "stock:stock_entry:read", "stock:stock_entry:write", "stock:stock_entry:delete", "stock:stock_entry:submit", "stock:stock_entry:cancel",
    "stock:stock_ledger_entry:read", "stock:bin:read",
]
KEEPER = make_ctx("acme", "karim", permissions=GRANTS)
TODAY = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)


class Log:
    def __init__(self):
        self.errors = []

    def info(self, *a, **k): ...
    def error(self, msg, **k):
        self.errors.append((msg, k))
        print("LOGGED ERROR:", msg, k)  # visible avec `pytest -s` : un hook qui échoue ne dit rien d'autre à l'appelant


class Env:
    def __init__(self, db, tables):
        reg = DocTypeRegistry()
        reg.load_dir(GEO / "doctypes", owner="geo")  # le référentiel d'abord : `Warehouse.pays` le cite (plugin que `stock` requires)
        reg.load_dir(STOCK / "doctypes", owner="stock")
        reg.validate_links()
        hooks = HookRegistry()
        for name, fn in ledger.HOOKS.items():
            hooks.register(name, fn, owner="stock")
        self.log = Log()
        self.svc = DocumentService(reg, db, tables, self.log, hooks=hooks, clock=lambda: TODAY)
        self.now = TODAY

    async def make(self, doctype, ctx=KEEPER, **data):
        return (await self.svc.create(ctx, doctype, data))["name"]

    async def item(self, name="Vis", ctx=KEEPER):
        return await self.make("Item", ctx, item_name=name, uom="pcs")

    async def warehouse(self, name, ctx=KEEPER):
        return await self.make("Warehouse", ctx, warehouse_name=name)

    async def draft(self, purpose, lines, *, day="2026-06-15", ctx=KEEPER):
        rows = [{"item": i, "qty": q, **({"source_warehouse": s} if s else {}), **({"target_warehouse": t} if t else {})} for i, q, s, t in lines]
        return await self.svc.create(ctx, "Stock Entry", {"purpose": purpose, "posting_date": day, "items": rows})

    async def post(self, purpose, lines, *, day="2026-06-15", ctx=KEEPER):
        doc = await self.draft(purpose, lines, day=day, ctx=ctx)
        return await self.svc.submit(ctx, "Stock Entry", doc["name"], doc["version"])

    async def receive(self, item, warehouse, qty, **kw):
        return await self.post(ledger.RECEIPT, [(item, qty, None, warehouse)], **kw)

    async def issue(self, item, warehouse, qty, **kw):
        return await self.post(ledger.ISSUE, [(item, qty, warehouse, None)], **kw)

    async def qty(self, item, warehouse, tenant="acme") -> str | None:
        got = await self.svc.list(make_ctx(tenant, "k", permissions=["stock:bin:read"]), "Bin", filters={"name": ledger.bin_name(item, warehouse)})
        return got["items"][0]["data"]["qty"] if got["items"] else None

    async def entries(self, tenant="acme"):
        page = await self.svc.list(make_ctx(tenant, "k", permissions=["stock:stock_ledger_entry:read"]), "Stock Ledger Entry", limit=200, order_by="name", descending=False)
        return [(e["name"], e["data"]["qty_change"], e["data"]["balance_qty"]) for e in page["items"]]

    async def audit(self, tenant="acme"):
        """Le recalcul du solde depuis le journal, lu sans filtre de permission (contexte système)."""
        from xcore_doctype.context import RequestContext

        system = RequestContext.system(tenant, "stock.verify")

        async def page(doctype, offset, limit):
            return await self.svc.list(system, doctype, offset=offset, limit=limit, order_by="name", descending=False)

        return await ledger.verify_balances(page)


@pytest.fixture
async def env(db, tables):
    return Env(db, tables)


@pytest.fixture
async def world(env):
    """Un article, deux entrepôts."""
    return env, await env.item("Vis"), await env.warehouse("Paris"), await env.warehouse("Lyon")


class TestReceiptIssueTransfer:
    async def test_a_receipt_enters_the_ledger_and_the_bin(self, world):
        env, vis, paris, _ = world
        done = await env.receive(vis, paris, "10")
        assert done["docstatus"] == 1 and done["name"].startswith("MS-2026-")
        assert await env.qty(vis, paris) == "10.000"
        assert await env.entries() == [(f"{done['name']}.0.in", "10.000", "10.000")]
        assert await env.audit() == []

    async def test_an_issue_leaves_the_balance_and_the_ledger_remembers_each_step(self, world):
        env, vis, paris, _ = world
        await env.receive(vis, paris, "10")
        out = await env.issue(vis, paris, "4")
        assert await env.qty(vis, paris) == "6.000"
        assert [(q, b) for _, q, b in await env.entries()] == [("10.000", "10.000"), ("-4.000", "6.000")]
        assert (await env.entries())[1][0] == f"{out['name']}.0.out"

    async def test_a_transfer_is_two_entries_in_one_transaction(self, world):
        env, vis, paris, lyon = world
        await env.receive(vis, paris, "10")
        t = await env.post(ledger.TRANSFER, [(vis, "3", paris, lyon)])
        assert (await env.qty(vis, paris), await env.qty(vis, lyon)) == ("7.000", "3.000")
        assert sorted(n for n, _, _ in await env.entries() if n.startswith(t["name"])) == [f"{t['name']}.0.in", f"{t['name']}.0.out"]
        assert await env.audit() == []

    async def test_a_failed_transfer_leaves_neither_half(self, world):
        """La sortie du premier entrepôt réussirait, l'entrée dans le second aussi : c'est la seconde LIGNE qui échoue."""
        env, vis, paris, lyon = world
        ecrou = await env.item("Écrou")
        await env.receive(vis, paris, "10")
        draft = await env.draft(ledger.TRANSFER, [(vis, "3", paris, lyon), (ecrou, "1", paris, lyon)])  # pas d'écrou à Paris
        with pytest.raises(DocumentValidationError) as e:
            await env.svc.submit(KEEPER, "Stock Entry", draft["name"], 1)
        assert "Stock insuffisant" in e.value.message and "Écrou" in e.value.message and "Paris" in e.value.message
        assert e.value.details[0]["field"] == "items.1.qty"
        assert (await env.qty(vis, paris), await env.qty(vis, lyon), await env.qty(ecrou, paris)) == ("10.000", None, None)
        assert len(await env.entries()) == 1
        assert (await env.svc.get(KEEPER, "Stock Entry", draft["name"]))["docstatus"] == 0

    async def test_several_lines_of_one_entry_accumulate(self, world):
        env, vis, paris, _ = world
        ecrou = await env.item("Écrou")
        await env.post(ledger.RECEIPT, [(vis, "2", None, paris), (ecrou, "5", None, paris), (vis, "3", None, paris)])
        assert (await env.qty(vis, paris), await env.qty(ecrou, paris)) == ("5.000", "5.000")
        by_line = {n.rsplit(".", 2)[1]: b for n, _, b in await env.entries()}
        assert by_line == {"0": "2.000", "1": "5.000", "2": "5.000"}  # chaque écriture garde le solde qui en résulte (vis : 2 puis 5)

    async def test_quantities_are_exact_decimals(self, world):
        """Avec des flottants, 0.1 + 0.1 + 0.1 - 0.3 ne vaut pas 0 : un stock qui dérive d'un cheveu refuse ensuite une sortie légitime."""
        env, vis, paris, _ = world
        for _ in range(3):
            await env.receive(vis, paris, "0.1")
        await env.issue(vis, paris, "0.3")
        assert await env.qty(vis, paris) == "0.000"
        with pytest.raises(DocumentValidationError):
            await env.issue(vis, paris, "0.001")
        assert await env.audit() == []


class TestNegativeStockIsRefused:
    async def test_an_issue_beyond_the_balance(self, world):
        env, vis, paris, _ = world
        await env.receive(vis, paris, "5")
        draft = await env.draft(ledger.ISSUE, [(vis, "5.001", paris, None)])
        with pytest.raises(DocumentValidationError) as e:
            await env.svc.submit(KEEPER, "Stock Entry", draft["name"], 1)
        assert (e.value.status, e.value.code) == (422, "hook_rejected")
        assert "5,000 disponible, 5,001 demandé" in e.value.message
        assert await env.qty(vis, paris) == "5.000"

    async def test_an_issue_from_a_warehouse_that_never_held_the_item(self, world):
        env, vis, paris, lyon = world
        await env.receive(vis, paris, "5")
        with pytest.raises(DocumentValidationError):
            await env.issue(vis, lyon, "1")
        assert await env.qty(vis, lyon) is None  # ni solde créé à moitié : la transaction est défaite en entier

    async def test_the_exact_balance_can_be_issued(self, world):
        env, vis, paris, _ = world
        await env.receive(vis, paris, "5")
        await env.issue(vis, paris, "5")
        assert await env.qty(vis, paris) == "0.000"


class TestCancellation:
    async def test_cancelling_an_issue_puts_the_stock_back_by_inverse_entries(self, world):
        env, vis, paris, _ = world
        await env.receive(vis, paris, "10")
        out = await env.issue(vis, paris, "4")
        await env.svc.cancel(KEEPER, "Stock Entry", out["name"], out["version"])
        assert await env.qty(vis, paris) == "10.000"
        got = {n: (q, b) for n, q, b in await env.entries()}
        assert got[f"{out['name']}.0.out"] == ("-4.000", "6.000")  # l'écriture d'origine n'a pas bougé
        assert got[f"{out['name']}.0.in.rev"] == ("4.000", "10.000")  # et son inverse s'ajoute
        assert await env.audit() == []

    async def test_cancelling_a_transfer_reverses_both_halves(self, world):
        env, vis, paris, lyon = world
        await env.receive(vis, paris, "10")
        t = await env.post(ledger.TRANSFER, [(vis, "3", paris, lyon)])
        await env.svc.cancel(KEEPER, "Stock Entry", t["name"], t["version"])
        assert (await env.qty(vis, paris), await env.qty(vis, lyon)) == ("10.000", "0.000")
        assert len([n for n, _, _ in await env.entries() if n.startswith(t["name"])]) == 4
        assert await env.audit() == []

    async def test_a_receipt_cannot_be_cancelled_once_its_stock_is_gone(self, world):
        env, vis, paris, _ = world
        rec = await env.receive(vis, paris, "10")
        await env.issue(vis, paris, "7")
        with pytest.raises(DocumentValidationError) as e:
            await env.svc.cancel(KEEPER, "Stock Entry", rec["name"], rec["version"])
        assert "Annulation impossible" in e.value.message and "3,000 disponible, 10,000 à reprendre" in e.value.message
        assert (await env.svc.get(KEEPER, "Stock Entry", rec["name"]))["docstatus"] == 1  # toujours soumis
        assert await env.qty(vis, paris) == "3.000"

    async def test_a_cancellation_is_not_refused_for_its_date(self, world):
        env, vis, paris, _ = world
        rec = await env.receive(vis, paris, "10", day="2026-06-01")
        await env.receive(vis, paris, "1", day="2026-06-10")  # un mouvement plus récent sur le même solde
        done = await env.svc.cancel(KEEPER, "Stock Entry", rec["name"], rec["version"])
        assert done["docstatus"] == 2
        rev = [e for e in (await env.svc.list(KEEPER, "Stock Ledger Entry", limit=50))["items"] if e["name"].endswith(".rev")]
        assert [e["data"]["posting_date"] for e in rev] == ["2026-06-15"]  # l'inverse est une écriture d'AUJOURD'HUI

    async def test_amending_corrects_a_movement_through_the_ledger(self, world):
        env, vis, paris, _ = world
        wrong = await env.receive(vis, paris, "10")
        await env.svc.cancel(KEEPER, "Stock Entry", wrong["name"], wrong["version"])
        fix = await env.svc.amend(KEEPER, "Stock Entry", wrong["name"])
        rows = [{**fix["data"]["items"][0], "qty": "12"}]
        fixed = await env.svc.update(KEEPER, "Stock Entry", fix["name"], {**fix["data"], "items": rows}, fix["version"])
        await env.svc.submit(KEEPER, "Stock Entry", fixed["name"], fixed["version"])
        assert await env.qty(vis, paris) == "12.000"
        assert sorted(q for _, q, _ in await env.entries()) == ["-10.000", "10.000", "12.000"]
        assert await env.audit() == []


class TestSubmissionIsIdempotent:
    async def test_replaying_a_submission_posts_nothing_more(self, world):
        env, vis, paris, _ = world
        done = await env.receive(vis, paris, "10")
        for version in (1, done["version"]):
            with pytest.raises(InvalidDocStatus):
                await env.svc.submit(KEEPER, "Stock Entry", done["name"], version)
        assert await env.qty(vis, paris) == "10.000" and len(await env.entries()) == 1

    async def test_the_ledger_name_itself_refuses_a_double_posting(self, db, tables):
        """Même si le moteur laissait passer une seconde écriture du même mouvement, son nom est déterministe : l'unicité la refuse
        et la transaction entière est défaite (état, premier solde, première écriture)."""
        env = Env(db, tables)
        twice_calls = []

        async def twice(event):
            twice_calls.append(1)
            await ledger.post_entry(event)
            await ledger.post_entry(event)  # le même mouvement, posté deux fois

        env.svc._hooks.unregister("stock.post_entry")
        env.svc._hooks.register("stock.post_entry", twice)
        vis, paris = await env.item(), await env.warehouse("Paris")
        with pytest.raises(DoctypeError) as e:
            await env.receive(vis, paris, "10")
        assert (e.value.code, e.value.status) == ("already_exists", 409)
        assert await env.qty(vis, paris) is None and await env.entries() == []


class TestNoBackdating:
    async def test_a_movement_cannot_be_dated_before_the_last_one_on_its_balance(self, world):
        env, vis, paris, lyon = world
        await env.receive(vis, paris, "10", day="2026-06-10")
        with pytest.raises(DocumentValidationError) as e:
            await env.receive(vis, paris, "1", day="2026-06-09")
        assert "Antidatage refusé" in e.value.message and "2026-06-10" in e.value.message
        assert e.value.details[0]["field"] == "posting_date"
        await env.receive(vis, paris, "1", day="2026-06-10")  # le même jour : permis
        await env.receive(vis, paris, "1", day="2026-06-11")
        await env.receive(vis, lyon, "1", day="2026-06-01")  # un autre couple (article, entrepôt) a son propre calendrier
        assert await env.qty(vis, paris) == "12.000"

    async def test_a_transfer_is_dated_against_both_of_its_balances(self, world):
        env, vis, paris, lyon = world
        await env.receive(vis, paris, "10", day="2026-06-01")
        await env.receive(vis, lyon, "1", day="2026-06-12")
        with pytest.raises(DocumentValidationError):
            await env.post(ledger.TRANSFER, [(vis, "1", paris, lyon)], day="2026-06-05")  # Lyon a déjà bougé le 12

    async def test_a_movement_in_the_future_is_refused(self, world):
        env, vis, paris, _ = world
        with pytest.raises(DocumentValidationError) as e:
            await env.receive(vis, paris, "1", day="2026-06-16")
        assert e.value.details[0]["type"] == "future_date"

    async def test_a_refused_backdating_changes_nothing(self, world):
        env, vis, paris, _ = world
        await env.receive(vis, paris, "10", day="2026-06-10")
        before = await env.entries()
        with pytest.raises(DocumentValidationError):
            await env.receive(vis, paris, "1", day="2026-06-01")
        assert await env.entries() == before and await env.qty(vis, paris) == "10.000"


class TestLinesAreCoherent:
    @pytest.mark.parametrize(
        "purpose, source, target, field",
        [
            (ledger.RECEIPT, True, True, "items.0.source_warehouse"),  # une réception n'a pas de source
            (ledger.RECEIPT, False, False, "items.0.target_warehouse"),
            (ledger.ISSUE, True, True, "items.0.target_warehouse"),
            (ledger.ISSUE, False, False, "items.0.source_warehouse"),
            (ledger.TRANSFER, True, False, "items.0.target_warehouse"),
            (ledger.TRANSFER, False, True, "items.0.source_warehouse"),
        ],
    )
    async def test_the_warehouses_follow_the_purpose(self, world, purpose, source, target, field):
        env, vis, paris, lyon = world
        # Un entrepôt MANQUANT est refusé par le moteur (condition de ligne), un entrepôt SUPERFLU par le hook : deux 422 de même forme.
        with pytest.raises(ValidationFailed) as e:
            await env.draft(purpose, [(vis, "1", paris if source else None, lyon if target else None)])
        assert field in [d["field"] for d in e.value.details]

    async def test_a_transfer_to_the_same_warehouse_and_a_zero_quantity(self, world):
        env, vis, paris, _ = world
        with pytest.raises(DocumentValidationError) as e:
            await env.draft(ledger.TRANSFER, [(vis, "1", paris, paris)])
        assert e.value.details[0]["type"] == "same_warehouse"
        with pytest.raises(DocumentValidationError) as e:
            await env.draft(ledger.RECEIPT, [(vis, "0", None, paris)])
        assert e.value.details[0]["type"] == "not_positive"
        with pytest.raises(DocumentValidationError):
            await env.draft(ledger.RECEIPT, [(vis, "-1", None, paris)])


class TestClosedToHumans:
    async def test_nobody_writes_the_ledger_or_the_bins_by_hand(self, world):
        env, vis, paris, _ = world
        await env.receive(vis, paris, "10")
        everything = make_ctx("acme", "root", permissions=[*GRANTS, "stock:bin:write", "stock:bin:delete", "stock:stock_ledger_entry:write", "stock:stock_ledger_entry:delete"])
        for doctype, data in (("Bin", {"item": vis, "warehouse": paris, "qty": "99"}), ("Stock Ledger Entry", {"item": vis})):
            with pytest.raises(PermissionDenied):
                await env.svc.create(everything, doctype, data)
        page = await env.svc.list(everything, "Bin")
        with pytest.raises(PermissionDenied):
            await env.svc.update(everything, "Bin", page["items"][0]["name"], {"item": vis, "warehouse": paris, "qty": "99"}, 1)
        with pytest.raises(PermissionDenied):
            await env.svc.delete(everything, "Bin", page["items"][0]["name"])
        assert await env.qty(vis, paris) == "10.000"

    async def test_a_storekeeper_may_submit_without_being_able_to_read_anything_else(self, world):
        """Qui valide n'a pas besoin de lire les articles, les entrepôts, le journal ou les soldes : le code de confiance écrit pour lui."""
        env, vis, paris, _ = world
        draft = await env.draft(ledger.RECEIPT, [(vis, "3", None, paris)])
        clerk = make_ctx("acme", "cora", permissions=["stock:stock_entry:read", "stock:stock_entry:submit"])
        done = await env.svc.submit(clerk, "Stock Entry", draft["name"], draft["version"])
        assert done["docstatus"] == 1 and await env.qty(vis, paris) == "3.000"
        with pytest.raises(PermissionDenied):
            await env.svc.list(clerk, "Bin")  # et elle ne les lit toujours pas

    async def test_items_and_warehouses_that_were_moved_cannot_be_deleted(self, world):
        env, vis, paris, _ = world
        await env.receive(vis, paris, "1")
        for doctype, name in (("Item", vis), ("Warehouse", paris)):
            with pytest.raises(DoctypeError) as e:
                await env.svc.delete(KEEPER, doctype, name)
            assert e.value.code == "document_in_use"


class TestVerification:
    async def test_the_balance_matches_the_ledger_after_a_busy_day(self, world):
        env, vis, paris, lyon = world
        ecrou = await env.item("Écrou")
        await env.receive(vis, paris, "100")
        await env.receive(ecrou, lyon, "7.5")
        t = await env.post(ledger.TRANSFER, [(vis, "40", paris, lyon), (ecrou, "2.5", lyon, paris)])
        o = await env.issue(vis, lyon, "15")
        await env.svc.cancel(KEEPER, "Stock Entry", o["name"], o["version"])
        assert await env.audit() == []

    async def test_a_drifting_bin_is_reported_never_silently_fixed(self, world):
        env, vis, paris, _ = world
        await env.receive(vis, paris, "10")
        from xcore_doctype.context import RequestContext

        system = RequestContext.system("acme", "corruption-test")
        name = ledger.bin_name(vis, paris)
        doc = await env.svc.get(system, "Bin", name)
        # Seul le système (donc un opérateur ou un bug) peut écrire un solde : on simule la dérive.
        await env.svc.update(system, "Bin", name, {**doc["data"], "qty": "9.000"}, doc["version"])
        assert await env.audit() == [{"item": vis, "warehouse": paris, "ledger": "10.000", "bin": "9.000"}]
        assert await env.qty(vis, paris) == "9.000"  # constaté, pas corrigé

    async def test_a_missing_bin_and_a_negative_bin_are_reported(self, world):
        env, vis, paris, lyon = world
        await env.receive(vis, paris, "10")
        from xcore_doctype.context import RequestContext

        system = RequestContext.system("acme", "corruption-test")
        await env.svc.delete(system, "Bin", ledger.bin_name(vis, paris))
        assert await env.audit() == [{"item": vis, "warehouse": paris, "ledger": "10.000", "bin": "(absent)"}]


class TestConcurrency:
    """Le critère : le stock ne devient jamais négatif quand plusieurs mouvements se soumettent EN MÊME TEMPS, sur les deux bases."""

    async def test_simultaneous_issues_never_overdraw(self, world):
        env, vis, paris, _ = world
        await env.receive(vis, paris, "10")
        drafts = [await env.draft(ledger.ISSUE, [(vis, "3", paris, None)]) for _ in range(8)]
        results = await asyncio.gather(
            *(env.svc.submit(KEEPER, "Stock Entry", d["name"], d["version"]) for d in drafts), return_exceptions=True
        )
        won = [r for r in results if isinstance(r, dict)]
        refused = [r for r in results if not isinstance(r, dict)]
        assert len(won) == 3, [repr(r) for r in refused]  # 10 // 3 : trois sorties, pas une de plus
        assert all(isinstance(r, DocumentValidationError) and "Stock insuffisant" in r.message for r in refused), [repr(r) for r in refused]
        assert await env.qty(vis, paris) == "1.000"
        assert await env.audit() == []
        assert all(Decimal(b) >= 0 for _, _, b in await env.entries())

    async def test_first_receipts_of_a_new_balance_do_not_collide(self, world):
        """Cinq premières réceptions du même (article, entrepôt) neuf : un seul solde est créé, aucune n'échoue sur son unicité."""
        env, vis, paris, _ = world
        drafts = [await env.draft(ledger.RECEIPT, [(vis, "2", None, paris)]) for _ in range(5)]
        results = await asyncio.gather(
            *(env.svc.submit(KEEPER, "Stock Entry", d["name"], d["version"]) for d in drafts), return_exceptions=True
        )
        assert all(isinstance(r, dict) for r in results), [repr(r) for r in results]
        assert await env.qty(vis, paris) == "10.000" and len(await env.entries()) == 5
        assert await env.audit() == []

    async def test_crossing_transfers_do_not_deadlock(self, world):
        env, vis, paris, lyon = world
        await env.receive(vis, paris, "10")
        await env.receive(vis, lyon, "10")
        a = [await env.draft(ledger.TRANSFER, [(vis, "1", paris, lyon)]) for _ in range(3)]
        b = [await env.draft(ledger.TRANSFER, [(vis, "2", lyon, paris)]) for _ in range(3)]
        results = await asyncio.wait_for(
            asyncio.gather(*(env.svc.submit(KEEPER, "Stock Entry", d["name"], d["version"]) for d in a + b), return_exceptions=True), 60
        )
        assert all(isinstance(r, dict) for r in results), [repr(r) for r in results]
        assert (await env.qty(vis, paris), await env.qty(vis, lyon)) == ("13.000", "7.000")
        assert await env.audit() == []

    async def test_a_cancellation_racing_an_issue_never_overdraws(self, world):
        env, vis, paris, _ = world
        rec = await env.receive(vis, paris, "10")
        out = await env.draft(ledger.ISSUE, [(vis, "8", paris, None)])
        results = await asyncio.gather(
            env.svc.cancel(KEEPER, "Stock Entry", rec["name"], rec["version"]),
            env.svc.submit(KEEPER, "Stock Entry", out["name"], out["version"]),
            return_exceptions=True,
        )
        assert sum(isinstance(r, dict) for r in results) == 1, [repr(r) for r in results]  # l'une ou l'autre, jamais les deux
        assert Decimal(await env.qty(vis, paris)) >= 0 and await env.audit() == []


class TestTenants:
    async def test_two_tenants_keep_separate_stock_for_the_same_names(self, env):
        a = make_ctx("acme", "karim", permissions=GRANTS)
        g = make_ctx("globex", "gina", permissions=GRANTS)
        for ctx, qty in ((a, "5"), (g, "9")):
            item, wh = await env.item("Vis", ctx), await env.warehouse("Paris", ctx)
            await env.post(ledger.RECEIPT, [(item, qty, None, wh)], ctx=ctx)
            assert await env.qty(item, wh, ctx.tenant_id) == f"{qty}.000"
        assert await env.audit("acme") == [] and await env.audit("globex") == []
        assert len(await env.entries("acme")) == 1 and len(await env.entries("globex")) == 1
