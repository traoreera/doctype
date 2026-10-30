"""Chercher et sommer dans la transaction d'un hook (`tx.find`, `tx.sum`) — M1 de `process/plan-erp.md`.

Le cas d'école d'un ERP : une PIÈCE soumise écrit ses écritures au grand livre ; annulée, elle doit retrouver CES écritures pour les
extourner, et un contrôle de crédit lit le SOLDE d'un compte. Avant M1, un hook ne savait lire un document que par son nom.

Ce que ces tests défendent, chacun par une mutation qui le casse :
- `find` retrouve des documents par leurs champs, avec les filtres typés de la liste, et voit ce que la transaction vient d'écrire ;
- l'annulation retrouve les écritures d'une pièce écrites dans une transaction PRÉCÉDENTE, et le solde revient à zéro ;
- `sum` est EXACT : en `Decimal`, jamais par le SUM du SQL — sous SQLite un double, qui perd des chiffres sur un grand montant ;
- rien d'un autre tenant n'est trouvé ni compté ;
- les erreurs d'un hook sont dites (champ inconnu, champ non sommable, bornes).
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from tests.conftest import make_ctx
from xcore_doctype.document import MAX_FIND, DocumentService
from xcore_doctype.errors import ValidationFailed
from xcore_doctype.hooks import HookRegistry
from xcore_doctype.registry import DocTypeRegistry
from xcore_doctype.schema import parse_definition

VOUCHER = {
    "schema_version": 1, "name": "Voucher", "module": "erp", "submittable": True,
    "fields": [
        {"fieldname": "account", "fieldtype": "Text", "required": True},
        {"fieldname": "amount", "fieldtype": "Decimal", "decimals": 2, "required": True},
    ],
    "hooks": {"on_submit": ["erp.post"], "on_cancel": ["erp.unpost"]},
}
# Le grand livre : écrit par les hooks seulement, jamais par un humain.
ENTRY = {
    "schema_version": 1, "name": "Entry", "module": "erp", "track_changes": False,
    "fields": [
        {"fieldname": "voucher", "fieldtype": "Text", "required": True},
        {"fieldname": "account", "fieldtype": "Text", "required": True},
        {"fieldname": "amount", "fieldtype": "Decimal", "decimals": 2, "required": True},
        {"fieldname": "count", "fieldtype": "Int"},
    ],
    "permissions": {"write": [], "create": [], "delete": []},
}
GRANTS = [f"erp:{t}:{a}" for t in ("voucher", "entry") for a in ("read", "write", "submit", "cancel")]
ALICE = make_ctx("acme", "alice", permissions=GRANTS)
OTHER = make_ctx("globex", "zoe", permissions=GRANTS)


class Log:
    def info(self, *a, **k): ...
    def warning(self, *a, **k): ...
    def error(self, *a, **k): ...


class Env:
    def __init__(self, db, tables):
        reg = DocTypeRegistry()
        for d in (ENTRY, VOUCHER):
            reg.register(parse_definition(d))
        reg.validate_links()
        self.hooks = HookRegistry()
        self.hooks.register("erp.post", self.post)
        self.hooks.register("erp.unpost", self.unpost)
        self.svc = DocumentService(reg, db, tables, Log(), hooks=self.hooks)
        self.seen_in_tx: list[str] = []
        self.probe = None  # une coroutine `(tx) -> valeur`, appelée par le hook de soumission
        self.probed: object = None

    async def post(self, event):
        """Partie double : le compte de la pièce au débit, la banque au crédit. Noms tirés par le moteur, pas prévisibles."""
        amount = Decimal(event.data["amount"])
        await event.tx.create("Entry", {"voucher": event.name, "account": event.data["account"], "amount": str(amount)})
        await event.tx.create("Entry", {"voucher": event.name, "account": "bank", "amount": str(-amount)})
        # Ce que la transaction vient d'écrire est déjà visible.
        self.seen_in_tx = [e["data"]["account"] for e in await event.tx.find("Entry", {"voucher": event.name})]
        if self.probe is not None:
            try:
                self.probed = await self.probe(event.tx)
            except Exception as e:  # noqa: BLE001 — le test lit l'erreur elle-même
                self.probed = e

    async def unpost(self, event):
        """L'extourne : retrouver les écritures de CETTE pièce (écrites à sa soumission) et les inverser."""
        for e in await event.tx.find("Entry", {"voucher": event.name}, lock=True):
            await event.tx.create("Entry", {"voucher": event.name, "account": e["data"]["account"], "amount": str(-Decimal(e["data"]["amount"])), "count": 1})


@pytest.fixture
def env(db, tables):
    return Env(db, tables)


async def submitted(env: Env, ctx=ALICE, **data) -> str:
    doc = await env.svc.create(ctx, "Voucher", {"account": "sales", "amount": "100.00", **data})
    await env.svc.submit(ctx, "Voucher", doc["name"], version=doc["version"])
    return doc["name"]


async def balance(env: Env, account: str, ctx=ALICE) -> Decimal:
    async def read(tx):
        return await tx.sum("Entry", "amount", {"account": account})
    env.probe = read
    await submitted(env, ctx, account="probe", amount="0.00")
    env.probe = None
    return env.probed  # type: ignore[return-value]


async def test_find_sees_what_the_transaction_just_wrote(env):
    await submitted(env)
    assert sorted(env.seen_in_tx) == ["bank", "sales"]


async def test_a_cancellation_finds_its_own_entries_and_reverses_them(env):
    name = await submitted(env, amount="40.00")
    await submitted(env, amount="25.00")  # une AUTRE pièce : ses écritures ne doivent pas être extournées
    assert await balance(env, "sales") == Decimal("65.00")
    doc = await env.svc.get(ALICE, "Voucher", name)
    await env.svc.cancel(ALICE, "Voucher", name, version=doc["version"])
    assert await balance(env, "sales") == Decimal("25.00")
    assert await balance(env, "bank") == Decimal("-25.00")
    reversal = [e for e in (await env.svc.list(make_ctx("acme", "audit", permissions=GRANTS), "Entry", limit=200))["items"] if e["data"].get("count")]
    assert sorted(e["data"]["amount"] for e in reversal) == ["-40.00", "40.00"]


async def test_the_sum_is_exact_even_where_a_double_loses_digits(env):
    """Trois montants de 18 chiffres : un double n'en garde que 15 à 17. La somme exacte est vérifiable à la main."""
    for amount in ("9007199254740993.01", "1.01", "-0.01"):
        await submitted(env, account="big", amount=amount)
    total = await balance(env, "big")
    assert total == Decimal("9007199254740994.01") and str(total) == "9007199254740994.01"


async def test_the_sum_is_at_the_field_scale_and_zero_when_nothing_matches(env):
    assert str(await balance(env, "nobody")) == "0.00"


async def test_another_tenant_is_never_found_nor_counted(env):
    await submitted(env, OTHER, amount="999.00")
    assert await balance(env, "sales") == Decimal("0.00")

    async def entries(tx):
        return await tx.find("Entry", {"account": "sales"})
    env.probe = entries
    await submitted(env, account="probe", amount="0.00")
    assert env.probed == []


async def test_find_takes_the_typed_filters_of_the_list(env):
    await submitted(env, amount="10.00")
    await submitted(env, amount="30.00")

    async def big(tx):
        return [e["data"]["amount"] for e in await tx.find("Entry", {"account": "sales", "amount": [">", "20.00"]})]
    env.probe = big
    await submitted(env, account="probe", amount="0.00")
    assert env.probed == ["30.00"]


@pytest.mark.parametrize("call, code", [
    (lambda tx: tx.find("Entry", {"inconnu": "x"}), None),
    (lambda tx: tx.find("Entry", order_by="inconnu"), None),
    (lambda tx: tx.find("Entry", limit=MAX_FIND + 1), None),
    (lambda tx: tx.sum("Entry", "account"), "not_summable"),
    (lambda tx: tx.sum("Entry", "count"), "not_summable"),
    (lambda tx: tx.sum("Entry", "inconnu"), "not_summable"),
])
async def test_a_hook_mistake_is_said(env, call, code):
    env.probe = call
    await submitted(env, account="probe", amount="0.00")
    assert isinstance(env.probed, ValidationFailed), env.probed
    if code is not None:
        assert env.probed.code == code
