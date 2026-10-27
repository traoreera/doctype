"""Ce qu'un hook transactionnel écrit garde les règles du type qu'il écrit (`_Transaction`, préalable aux plugins d'ERP).

Le cas d'école d'un ERP : un PAIEMENT soumis met à jour la FACTURE déjà soumise (son reste dû), et la facture, à son tour, écrit au journal.
Ce que ces tests défendent, chacun par une mutation qui le casse :
- un type qui a des hooks de confiance s'écrit depuis un hook : `validate` / `before_save` / `before_insert` tournent dans la transaction (le
  total se calcule, un refus défait tout), `after_insert` / `after_save` après le commit — jamais si la transaction échoue ;
- un document SOUMIS se modifie depuis un hook, mais seulement ses champs `allow_on_submit` (un champ figé changé est un défaut du hook, 500),
  et son `on_update_after_submit` tourne dans la même transaction ; annulé, il refuse (409) ;
- des hooks qui s'appellent en boucle s'arrêtent (`hook_recursion`) au lieu d'épuiser la pile ;
- un type à hooks `action:` (sandboxés, hors transaction) reste refusé, comme un hook indisponible (503).
"""

from __future__ import annotations

import pytest

from tests.conftest import make_ctx
from xcore_doctype import DocumentValidationError
from xcore_doctype.document import MAX_TX_DEPTH, DocumentService
from xcore_doctype.errors import DoctypeError, DocumentLocked
from xcore_doctype.hooks import HookRegistry
from xcore_doctype.registry import DocTypeRegistry
from xcore_doctype.schema import parse_definition

INVOICE = {
    "schema_version": 1, "name": "Invoice", "module": "erp", "submittable": True,
    "fields": [
        {"fieldname": "customer", "fieldtype": "Text", "required": True},
        {"fieldname": "qty", "fieldtype": "Int"},
        {"fieldname": "rate", "fieldtype": "Int"},
        {"fieldname": "total", "fieldtype": "Int", "read_only": True},
        {"fieldname": "paid", "fieldtype": "Int", "allow_on_submit": True},
        {"fieldname": "outstanding", "fieldtype": "Int", "read_only": True, "allow_on_submit": True},
    ],
    "hooks": {
        "validate": ["erp.invoice_check"],
        "before_save": ["erp.invoice_totals"],
        "after_save": ["erp.invoice_seen"],
        "on_update_after_submit": ["erp.invoice_paid"],
    },
}
PAYMENT = {
    "schema_version": 1, "name": "Payment", "module": "erp", "submittable": True,
    "fields": [
        {"fieldname": "invoice", "fieldtype": "Link", "options": "Invoice"},
        {"fieldname": "amount", "fieldtype": "Int"},
    ],
    "hooks": {"on_submit": ["erp.pay"]},
}
JOURNAL = {
    "schema_version": 1, "name": "Journal", "module": "erp", "track_changes": False,
    "fields": [{"fieldname": "ref", "fieldtype": "Text"}, {"fieldname": "amount", "fieldtype": "Int"}],
    "permissions": {"write": [], "create": [], "delete": []},
}
# Deux documents qui se mettent à jour l'un l'autre après soumission : la boucle que la profondeur arrête.
PING = {
    "schema_version": 1, "name": "Ping", "module": "erp", "submittable": True,
    "fields": [{"fieldname": "other", "fieldtype": "Text"}, {"fieldname": "n", "fieldtype": "Int", "allow_on_submit": True}],
    "hooks": {"on_update_after_submit": ["erp.ping"]},
}
SANDBOXED = {
    "schema_version": 1, "name": "Sandboxed", "module": "erp",
    "fields": [{"fieldname": "x", "fieldtype": "Int"}], "hooks": {"validate": ["action:outside.check"]},
}
GRANTS = [f"erp:{t}:{a}" for t in ("invoice", "payment", "journal", "ping", "sandboxed") for a in ("read", "write", "submit", "cancel")]
ALICE = make_ctx("acme", "alice", permissions=GRANTS)


class Log:
    def __init__(self):
        self.errors = []

    def info(self, *a, **k): ...
    def warning(self, *a, **k): ...
    def error(self, msg, **k):
        self.errors.append((msg, k))


class Env:
    def __init__(self, db, tables):
        reg = DocTypeRegistry()
        for d in (INVOICE, PAYMENT, JOURNAL, PING, SANDBOXED):
            reg.register(parse_definition(d))
        reg.validate_links()
        self.hooks, self.log = HookRegistry(), Log()
        self.seen: list[tuple[str, str]] = []
        self.frozen_change = False
        self.totals_touch_customer = False
        for name, fn in {
            "erp.invoice_check": self.invoice_check, "erp.invoice_totals": self.invoice_totals, "erp.invoice_seen": self.invoice_seen,
            "erp.invoice_paid": self.invoice_paid, "erp.pay": self.pay, "erp.ping": self.ping,
        }.items():
            self.hooks.register(name, fn)
        self.svc = DocumentService(reg, db, tables, self.log, hooks=self.hooks)

    # Facture : refuse un paiement au-delà du total, calcule total et reste dû, et note au journal chaque paiement reçu.
    async def invoice_check(self, event):
        if (event.data.get("paid") or 0) > (event.data.get("qty") or 0) * (event.data.get("rate") or 0):
            raise DocumentValidationError("Paiement supérieur au total")

    async def invoice_totals(self, event):
        total = (event.data.get("qty") or 0) * (event.data.get("rate") or 0)
        event.data["total"] = total
        event.data["outstanding"] = total - (event.data.get("paid") or 0)
        if self.totals_touch_customer:
            event.data["customer"] = "renommé par le hook"

    async def invoice_seen(self, event):
        self.seen.append((event.event, event.name))

    async def invoice_paid(self, event):
        await event.tx.create("Journal", {"ref": event.name, "amount": event.data["paid"] - (event.old.get("paid") or 0)})

    # Paiement : à sa soumission, il porte son montant sur la facture (soumise), sans en toucher le reste.
    async def pay(self, event):
        inv = await event.tx.get("Invoice", event.data["invoice"], lock=True)
        data = {**inv["data"], "paid": (inv["data"].get("paid") or 0) + event.data["amount"]}
        if self.frozen_change:
            data["customer"] = "quelqu'un d'autre"
        await event.tx.update("Invoice", inv["name"], data, inv["version"])

    async def ping(self, event):
        other = await event.tx.get("Ping", event.data["other"], lock=True)
        await event.tx.update("Ping", other["name"], {**other["data"], "n": (other["data"].get("n") or 0) + 1}, other["version"])

    async def invoice(self, **data):
        doc = await self.svc.create(ALICE, "Invoice", {"customer": "Ada", "qty": 2, "rate": 50, **data})
        return await self.svc.submit(ALICE, "Invoice", doc["name"], doc["version"])

    async def pay_invoice(self, invoice: str, amount: int):
        pay = await self.svc.create(ALICE, "Payment", {"invoice": invoice, "amount": amount})
        return await self.svc.submit(ALICE, "Payment", pay["name"], pay["version"])

    async def journal(self):
        return sorted((d["data"]["ref"], d["data"]["amount"]) for d in (await self.svc.list(ALICE, "Journal", limit=50))["items"])


@pytest.fixture
def env(db, tables):
    return Env(db, tables)


async def test_a_payment_updates_the_submitted_invoice_through_its_own_hooks(env):
    inv = await env.invoice()
    assert (inv["data"]["total"], inv["data"]["outstanding"]) == (100, 100)
    env.seen.clear()
    await env.pay_invoice(inv["name"], 30)
    after = await env.svc.get(ALICE, "Invoice", inv["name"])
    assert after["docstatus"] == 1  # toujours soumise
    assert (after["data"]["paid"], after["data"]["outstanding"]) == (30, 70)  # le reste dû recalculé par le before_save DE LA FACTURE
    assert await env.journal() == [(inv["name"], 30)]  # son on_update_after_submit a tourné, dans la même transaction
    assert env.seen == [("after_save", inv["name"])]  # son after_save, après le commit


async def test_a_refusal_by_the_written_documents_hook_undoes_the_whole_transaction(env):
    inv = await env.invoice()
    env.seen.clear()
    with pytest.raises(DocumentValidationError):
        await env.pay_invoice(inv["name"], 500)  # la facture refuse un paiement au-delà de son total
    after = await env.svc.get(ALICE, "Invoice", inv["name"])
    assert (after["data"].get("paid"), after["version"]) == (None, inv["version"])
    assert await env.journal() == []
    payments = (await env.svc.list(ALICE, "Payment", limit=10))["items"]
    assert [p["docstatus"] for p in payments] == [0]  # le paiement n'est pas soumis non plus
    assert env.seen == []  # aucun after_save : la transaction a échoué


async def test_a_hook_cannot_unfreeze_a_submitted_field(env):
    inv = await env.invoice()
    env.frozen_change = True
    with pytest.raises(DoctypeError) as e:
        await env.pay_invoice(inv["name"], 10)
    assert e.value.code == "hook_invalid_write" and e.value.status == 500
    assert (await env.svc.get(ALICE, "Invoice", inv["name"]))["data"]["customer"] == "Ada"


async def test_the_written_documents_own_hook_cannot_unfreeze_a_field_either(env):
    inv = await env.invoice()
    env.totals_touch_customer = True  # le before_save de la FACTURE (pas l'appelant) change un champ figé
    with pytest.raises(DoctypeError) as e:
        await env.pay_invoice(inv["name"], 10)
    assert e.value.code == "hook_invalid_write"
    assert (await env.svc.get(ALICE, "Invoice", inv["name"]))["data"]["customer"] == "Ada"


async def test_a_cancelled_document_is_not_written_from_a_hook(env):
    inv = await env.invoice()
    await env.svc.cancel(ALICE, "Invoice", inv["name"], inv["version"])
    with pytest.raises(DocumentLocked):
        await env.pay_invoice(inv["name"], 10)
    assert await env.journal() == []


async def test_hooks_calling_each_other_in_a_loop_stop(env):
    a = await env.svc.create(ALICE, "Ping", {"other": "pong"}, )
    b = await env.svc.create(ALICE, "Ping", {"other": a["name"]})
    await env.svc.update(ALICE, "Ping", a["name"], {"other": b["name"]}, a["version"])
    a = await env.svc.submit(ALICE, "Ping", a["name"], a["version"] + 1)
    b = await env.svc.submit(ALICE, "Ping", b["name"], b["version"])
    with pytest.raises(DoctypeError) as e:
        await env.svc.update(ALICE, "Ping", a["name"], {**a["data"], "n": 1}, a["version"])  # a -> b -> a -> b …
    assert e.value.code == "hook_recursion"
    assert (await env.svc.get(ALICE, "Ping", b["name"]))["data"].get("n") is None  # tout défait
    assert MAX_TX_DEPTH >= 2


async def test_a_doctype_with_sandboxed_hooks_or_a_missing_hook_stays_refused(env):
    async def write_sandboxed(event):
        await event.tx.create("Sandboxed", {"x": 1})

    env.hooks.unregister("erp.pay")
    env.hooks.register("erp.pay", write_sandboxed)
    inv = await env.invoice()
    with pytest.raises(DoctypeError) as e:
        await env.pay_invoice(inv["name"], 10)
    assert e.value.code == "hook_not_supported"

    env.hooks.unregister("erp.pay")
    env.hooks.register("erp.pay", Env.pay.__get__(env))
    env.hooks.unregister("erp.invoice_seen")  # même un hook d'APRÈS le commit qui manque : on n'écrit pas la facture sans lui
    with pytest.raises(DoctypeError) as e:
        await env.pay_invoice(inv["name"], 10)
    assert (e.value.code, e.value.status) == ("hook_unavailable", 503)
    assert (await env.svc.get(ALICE, "Invoice", inv["name"]))["data"].get("paid") is None
