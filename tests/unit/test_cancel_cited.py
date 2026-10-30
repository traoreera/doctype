"""Une pièce encore citée par une pièce SOUMISE ne s'annule pas (M2 de `process/plan-erp.md`).

Le cas d'école : une facture payée. L'annuler pendant que son paiement tient laisserait un paiement d'une facture qui n'existe plus —
on annule d'abord le paiement. Ce que ces tests défendent, chacun par une mutation qui le casse :
- une citation par un Link, par une LIGNE de table et par un DynamicLink bloque l'annulation (409 `document_cited`), l'état reste soumis ;
- un brouillon ou un document annulé qui cite ne bloque pas ;
- `cancel_ignores` laisse passer les propres écritures du document, et rien d'autre ;
- un circuit (workflow) qui annule passe par la même règle ;
- les fautes de `cancel_ignores` sont des erreurs de CHARGEMENT ;
- sous PostgreSQL, une annulation et la soumission d'un paiement simultanées ne passent pas toutes les deux.
"""

from __future__ import annotations

import asyncio

import pytest

from tests.conftest import make_ctx
from xcore_doctype.document import DocumentService
from xcore_doctype.errors import DoctypeError, InvalidDefinition
from xcore_doctype.hooks import HookRegistry
from xcore_doctype.registry import DocTypeRegistry
from xcore_doctype.schema import parse_definition

INVOICE = {
    "schema_version": 1, "name": "Invoice", "module": "erp", "submittable": True,
    "fields": [{"fieldname": "customer", "fieldtype": "Text", "required": True}],
    "cancel_ignores": ["Ledger"],
}
PAYMENT = {
    "schema_version": 1, "name": "Payment", "module": "erp", "submittable": True,
    "fields": [{"fieldname": "invoice", "fieldtype": "Link", "options": "Invoice", "target_status": "submitted"}],
}
LINE = {
    "schema_version": 1, "name": "Batch Line", "istable": True,
    "fields": [{"fieldname": "invoice", "fieldtype": "Link", "options": "Invoice"}],
}
BATCH = {  # un lot de paiements : la facture est citée dans une LIGNE
    "schema_version": 1, "name": "Batch", "module": "erp", "submittable": True,
    "fields": [{"fieldname": "lines", "fieldtype": "Table", "options": "Batch Line"}],
}
LEDGER = {  # une écriture qui cite sa pièce par un DynamicLink, et que la pièce déclare ignorer
    "schema_version": 1, "name": "Ledger", "module": "erp", "submittable": True,
    "fields": [
        {"fieldname": "voucher_type", "fieldtype": "Text"},
        {"fieldname": "voucher_no", "fieldtype": "DynamicLink", "options": "voucher_type"},
    ],
}
NOTE = {  # comme Ledger, mais NON ignorée par la facture : elle bloque
    "schema_version": 1, "name": "Note", "module": "erp", "submittable": True,
    "fields": [
        {"fieldname": "against_type", "fieldtype": "Text"},
        {"fieldname": "against", "fieldtype": "DynamicLink", "options": "against_type"},
    ],
}
ORDER = {  # une commande à circuit : son annulation passe par une transition
    "schema_version": 1, "name": "Order", "module": "erp", "submittable": True,
    "fields": [{"fieldname": "ref", "fieldtype": "Text"}],
    "workflow": {
        "states": [{"name": "Brouillon"}, {"name": "Approuvée", "docstatus": "submitted"}, {"name": "Annulée", "docstatus": "cancelled"}],
        "transitions": [{"action": "Approuver", "from": "Brouillon", "to": "Approuvée"}, {"action": "Annuler", "from": "Approuvée", "to": "Annulée"}],
    },
}
ORDER_REF = {
    "schema_version": 1, "name": "Order Ref", "module": "erp", "submittable": True,
    "fields": [{"fieldname": "order", "fieldtype": "Link", "options": "Order"}],
}
TYPES = (LINE, INVOICE, PAYMENT, BATCH, LEDGER, NOTE, ORDER, ORDER_REF)
SLUGS = ("invoice", "payment", "batch", "ledger", "note", "order", "order_ref")
GRANTS = [f"erp:{t}:{a}" for t in SLUGS for a in ("read", "write", "submit", "cancel", "workflow_approuver", "workflow_annuler")]
ALICE = make_ctx("acme", "alice", permissions=GRANTS)


class Log:
    def info(self, *a, **k): ...
    def warning(self, *a, **k): ...
    def error(self, *a, **k): ...


def registry(*defs) -> DocTypeRegistry:
    reg = DocTypeRegistry()
    for d in defs:
        reg.register(parse_definition(d))
    reg.validate_links()
    return reg


@pytest.fixture
def svc(db, tables):
    return DocumentService(registry(*TYPES), db, tables, Log(), hooks=HookRegistry())


async def submitted(svc, doctype, data):
    doc = await svc.create(ALICE, doctype, data)
    return await svc.submit(ALICE, doctype, doc["name"], version=doc["version"])


async def cancel(svc, doctype, name):
    doc = await svc.get(ALICE, doctype, name)
    return await svc.cancel(ALICE, doctype, name, version=doc["version"])


async def test_a_paid_invoice_does_not_cancel(svc):
    invoice = await submitted(svc, "Invoice", {"customer": "Durand"})
    await submitted(svc, "Payment", {"invoice": invoice["name"]})
    with pytest.raises(DoctypeError) as e:
        await cancel(svc, "Invoice", invoice["name"])
    assert e.value.code == "document_cited" and e.value.status == 409
    assert e.value.details == [{"doctype": "Payment", "field": "invoice"}]
    assert (await svc.get(ALICE, "Invoice", invoice["name"]))["docstatus"] == 1  # l'état n'a pas bougé : tout est défait


async def test_cancelling_the_payment_first_frees_the_invoice(svc):
    invoice = await submitted(svc, "Invoice", {"customer": "Durand"})
    payment = await submitted(svc, "Payment", {"invoice": invoice["name"]})
    await cancel(svc, "Payment", payment["name"])
    assert (await cancel(svc, "Invoice", invoice["name"]))["docstatus"] == 2


async def test_a_draft_that_cites_does_not_block(svc):
    invoice = await submitted(svc, "Invoice", {"customer": "Durand"})
    await svc.create(ALICE, "Payment", {"invoice": invoice["name"]})
    assert (await cancel(svc, "Invoice", invoice["name"]))["docstatus"] == 2


async def test_a_row_of_a_submitted_document_blocks(svc):
    invoice = await submitted(svc, "Invoice", {"customer": "Durand"})
    await submitted(svc, "Batch", {"lines": [{"invoice": invoice["name"]}]})
    with pytest.raises(DoctypeError) as e:
        await cancel(svc, "Invoice", invoice["name"])
    assert e.value.details == [{"doctype": "Batch", "field": "invoice", "table": "lines"}]


async def test_a_dynamic_link_blocks_unless_the_type_is_ignored(svc):
    invoice = await submitted(svc, "Invoice", {"customer": "Durand"})
    await submitted(svc, "Ledger", {"voucher_type": "Invoice", "voucher_no": invoice["name"]})  # ses propres écritures : ignorées
    other = await submitted(svc, "Invoice", {"customer": "Martin"})
    await submitted(svc, "Note", {"against_type": "Invoice", "against": other["name"]})  # une note : pas ignorée
    assert (await cancel(svc, "Invoice", invoice["name"]))["docstatus"] == 2
    with pytest.raises(DoctypeError) as e:
        await cancel(svc, "Invoice", other["name"])
    assert e.value.code == "document_cited" and e.value.details == [{"doctype": "Note", "field": "against"}]


async def test_a_workflow_that_cancels_obeys_the_same_rule(svc):
    order = await svc.create(ALICE, "Order", {"ref": "C1"})
    order = await svc.workflow_transition(ALICE, "Order", order["name"], "Approuver", order["version"])
    await submitted(svc, "Order Ref", {"order": order["name"]})
    with pytest.raises(DoctypeError) as e:
        await svc.workflow_transition(ALICE, "Order", order["name"], "Annuler", order["version"])
    assert e.value.code == "document_cited"
    assert (await svc.get(ALICE, "Order", order["name"]))["docstatus"] == 1


@pytest.mark.parametrize("ignores, why", [
    (["Nothing"], "inconnu"),
    (["Batch Line"], "soumettable"),  # un enfant n'est pas soumettable
    (["Order"], "ne cite pas"),
])
def test_cancel_ignores_mistakes_are_load_errors(ignores, why):
    with pytest.raises(InvalidDefinition, match=why):
        registry(*[{**d, "cancel_ignores": ignores} if d is INVOICE else d for d in TYPES])


def test_cancel_ignores_is_refused_on_a_type_that_never_cancels():
    with pytest.raises(InvalidDefinition, match="cancel_ignores"):
        parse_definition({"schema_version": 1, "name": "Plain", "module": "erp", "fields": [{"fieldname": "x", "fieldtype": "Text"}], "cancel_ignores": ["Ledger"]})


async def test_cancel_and_a_concurrent_payment_do_not_both_pass(svc, db):
    """PostgreSQL : l'UPDATE de l'état ne gêne pas le `FOR KEY SHARE` d'une soumission concurrente ; le verrou de l'annulation, si.
    Les deux partent ensemble : soit le paiement passe et l'annulation est refusée, soit l'inverse — jamais les deux."""
    if db.backend != "postgres":
        pytest.skip("SQLite n'a pas de verrou de ligne : la base entière est sérialisée par la première écriture")
    for _ in range(20):  # mesuré : sans le verrou pris AVANT l'état, les deux passaient dans la plupart des courses
        invoice = await submitted(svc, "Invoice", {"customer": "Durand"})
        draft = await svc.create(ALICE, "Payment", {"invoice": invoice["name"]})
        results = await asyncio.gather(
            cancel(svc, "Invoice", invoice["name"]),
            svc.submit(ALICE, "Payment", draft["name"], version=draft["version"]),
            return_exceptions=True,
        )
        cancelled = not isinstance(results[0], Exception)
        paid = not isinstance(results[1], Exception)
        assert cancelled != paid, results
