"""`doctype_writer` : la porte d'écriture des plugins (M4 de `process/plan-erp.md`). Le bouton (`as_request`) a sa preuve sur un vrai
hôte (`tests/integration/test_stock_plugin.py::test_a_server_button_writes_with_the_user_s_rights`) ; ici, la porte SYSTÈME et `ensure`.

Ce que ces tests défendent, chacun par une mutation qui le casse :
- une tâche écrit les types de SON module, et pas ceux d'un autre (403 `writer_foreign_module`, rien d'écrit) ;
- ce qu'elle écrit est signé `system:<raison>` dans l'historique (D20) ;
- en soumettant sa pièce, ses hooks écrivent AILLEURS par `tx` : la limite est la porte, pas les conséquences ;
- `ensure` rend une tâche rejouable : relancée, ou lancée par plusieurs processus EN MÊME TEMPS, elle n'écrit qu'une fois ;
- `ensure` refuse une clé qui n'est pas garantie par la base, et ne montre pas un document hors du périmètre de l'appelant.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

from tests.conftest import make_ctx
from xcore_doctype.document import DocumentService
from xcore_doctype.errors import DoctypeError, NotFound, ValidationFailed
from xcore_doctype.hooks import HookRegistry
from xcore_doctype.registry import DocTypeRegistry
from xcore_doctype.schema import parse_definition
from xcore_doctype.writer import DocumentWriter, DocumentWriters

# Le plugin `assets` : une dotation aux amortissements par actif et par mois, numérotée par série (nom imprévisible).
DEPRECIATION = {
    "schema_version": 1, "name": "Depreciation", "module": "assets", "submittable": True,
    "naming": {"series": "AM-{#####}"},
    "fields": [
        {"fieldname": "key", "fieldtype": "Text", "required": True, "unique": True},  # « <actif>/<mois> » : la clé de la tâche
        {"fieldname": "amount", "fieldtype": "Decimal", "decimals": 2, "required": True},
    ],
    "hooks": {"on_submit": ["assets.post"]},
}
TAG = {  # même module, nommé par l'appelant
    "schema_version": 1, "name": "Asset Tag", "module": "assets", "naming": "prompt",
    "fields": [{"fieldname": "label", "fieldtype": "Text"}],
}
NOTE = {  # même module, sans champ unique
    "schema_version": 1, "name": "Asset Note", "module": "assets",
    "fields": [{"fieldname": "text", "fieldtype": "Text", "required": True},
               {"fieldname": "ref", "fieldtype": "Text", "unique": True}],
    "permissions": {"read": [{"grant": "assets:asset_note:read", "if_owner": True}], "write": ["assets:asset_note:write"]},
}
# Le plugin `accounts` : son grand livre, qu'une tâche d'`assets` n'écrit pas elle-même.
ENTRY = {
    "schema_version": 1, "name": "Ledger Entry", "module": "accounts",
    "fields": [{"fieldname": "voucher", "fieldtype": "Text", "required": True}, {"fieldname": "amount", "fieldtype": "Decimal", "decimals": 2}],
}
TENANT = "acme"
REASON = "assets.monthly_depreciation"


class Log:
    def info(self, *a, **k): ...
    def warning(self, *a, **k): ...
    def error(self, *a, **k): ...


@pytest.fixture
def writers(db, tables):
    reg = DocTypeRegistry()
    for d in (DEPRECIATION, TAG, NOTE, ENTRY):
        reg.register(parse_definition(d))
    reg.validate_links()
    hooks = HookRegistry()

    async def post(event):
        await event.tx.create("Ledger Entry", {"voucher": event.name, "amount": event.data["amount"]})

    hooks.register("assets.post", post)
    svc = DocumentService(reg, db, tables, Log(), hooks=hooks)
    return DocumentWriters(svc, settings=None), svc  # `settings` ne sert qu'à `as_request`


def job(writers) -> DocumentWriter:
    return writers[0].system("assets", TENANT, REASON)


async def test_a_job_writes_its_own_module_signed_with_its_reason(writers):
    doc = await job(writers).create("Depreciation", {"key": "A1/2026-10", "amount": "125.00"})
    assert doc["owner"] == "system"
    history = await writers[1].history(make_ctx(TENANT, "audit", permissions=["assets:depreciation:read"]), "Depreciation", doc["name"])
    assert history["items"][0]["user"] == f"system:{REASON}"


async def test_a_job_does_not_write_another_module(writers):
    with pytest.raises(DoctypeError) as e:
        await job(writers).create("Ledger Entry", {"voucher": "x", "amount": "1.00"})
    assert e.value.code == "writer_foreign_module" and e.value.status == 403
    assert (await job(writers).list("Ledger Entry"))["total"] == 0  # lire reste permis ; rien n'a été écrit


@pytest.mark.parametrize("op", ["update", "submit", "cancel", "delete", "amend", "transition"])
async def test_every_write_obeys_the_module_boundary(writers, op):
    w = job(writers)
    calls = {
        "update": lambda: w.update("Ledger Entry", "x", {}, 1), "submit": lambda: w.submit("Ledger Entry", "x", 1),
        "cancel": lambda: w.cancel("Ledger Entry", "x", 1), "delete": lambda: w.delete("Ledger Entry", "x"),
        "amend": lambda: w.amend("Ledger Entry", "x"), "transition": lambda: w.transition("Ledger Entry", "x", "Approuver", 1),
    }
    with pytest.raises(DoctypeError) as e:
        await calls[op]()
    assert e.value.code == "writer_foreign_module"


async def test_submitting_its_voucher_lets_the_hooks_write_elsewhere(writers):
    w = job(writers)
    doc = await w.create("Depreciation", {"key": "A1/2026-10", "amount": "125.00"})
    await w.submit("Depreciation", doc["name"], doc["version"])
    entries = (await w.list("Ledger Entry"))["items"]
    assert [(e["data"]["voucher"], Decimal(e["data"]["amount"])) for e in entries] == [(doc["name"], Decimal("125.00"))]


async def test_ensure_writes_once_when_the_job_runs_again(writers):
    w = job(writers)
    first, created = await w.ensure("Depreciation", {"key": "A1/2026-10", "amount": "125.00"}, unique="key")
    again, created_again = await w.ensure("Depreciation", {"key": "A1/2026-10", "amount": "125.00"}, unique="key")
    assert (created, created_again) == (True, False) and again["name"] == first["name"]
    assert (await w.list("Depreciation"))["total"] == 1


async def test_ensure_writes_once_when_several_processes_run_together(writers):
    """Le backend `memory` de l'ordonnanceur n'a pas de verrou : chaque processus lance la tâche. La base tranche."""
    w = job(writers)
    results = await asyncio.gather(*(w.ensure("Depreciation", {"key": "A2/2026-10", "amount": "80.00"}, unique="key") for _ in range(5)))
    assert sum(created for _, created in results) == 1
    assert len({doc["name"] for doc, _ in results}) == 1
    assert (await w.list("Depreciation"))["total"] == 1


async def test_ensure_by_a_chosen_name(writers):
    w = job(writers)
    _, created = await w.ensure("Asset Tag", {"label": "Bureau"}, name="BUREAU")
    doc, created_again = await w.ensure("Asset Tag", {"label": "autre"}, name="BUREAU")
    assert (created, created_again) == (True, False) and doc["data"]["label"] == "Bureau"


@pytest.mark.parametrize("kw, data, code", [
    ({"unique": "amount"}, {"key": "k", "amount": "1.00"}, "ensure_key_not_unique"),  # rien en base ne l'empêcherait
    ({"unique": "key"}, {"amount": "1.00"}, "ensure_key_required"),
    ({}, {"key": "k", "amount": "1.00"}, "ensure_key_required"),
    ({"unique": "key", "name": "x"}, {"key": "k", "amount": "1.00"}, "ensure_key_required"),
])
async def test_ensure_refuses_a_key_the_database_does_not_guarantee(writers, kw, data, code):
    with pytest.raises(ValidationFailed) as e:
        await job(writers).ensure("Depreciation", data, **kw)
    assert e.value.code == code
    assert (await job(writers).list("Depreciation"))["total"] == 0


async def test_ensure_does_not_reveal_what_the_caller_cannot_read(writers):
    _, svc = writers
    grants = ["assets:asset_note:read", "assets:asset_note:write"]
    bob = DocumentWriter(svc, make_ctx(TENANT, "bob", permissions=grants))
    alice = DocumentWriter(svc, make_ctx(TENANT, "alice", permissions=grants))
    await bob.create("Asset Note", {"text": "privé", "ref": "R1"})
    with pytest.raises(NotFound):
        await alice.ensure("Asset Note", {"text": "autre", "ref": "R1"}, unique="ref")


async def test_a_user_writer_keeps_the_user_s_rights(writers):
    _, svc = writers
    reader = DocumentWriter(svc, make_ctx(TENANT, "sam", permissions=["assets:depreciation:read"]))
    with pytest.raises(DoctypeError) as e:
        await reader.create("Depreciation", {"key": "k", "amount": "1.00"})
    assert e.value.code == "permission_denied"


def test_a_job_names_its_plugin(writers):
    with pytest.raises(DoctypeError) as e:
        writers[0].system("", TENANT, REASON)
    assert e.value.code == "writer_plugin_required"
