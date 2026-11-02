"""La devise d'un montant vient d'un champ (`currency_from`, M3 de `process/plan-erp.md`).

Une facture en dollars n'imprime pas « € » : la devise est celle de la PIÈCE (`currency`), d'un document lié (`company.default_currency`)
ou, pour une LIGNE, celle de son parent (`parent_currency`). Affichage seulement, sur le modèle de `unit_from`.

Ce que ces tests défendent, chacun par une mutation qui le casse :
- les fautes sont des erreurs de CHARGEMENT : champ inconnu, mauvais type, saut par autre chose qu'un Link, un parent qui n'a pas le champ ;
- `options` (devise fixe) et `currency_from` ensemble sont refusés, et `currency_from` hors d'une Currency aussi ;
- `/meta` envoie `currency_from` au Desk ;
- l'impression montre la devise résolue — sur le document, à travers un lien lu avec les droits de l'APPELANT, et sur chaque ligne
  depuis le parent — et une devise illisible laisse le montant seul, jamais une erreur.
"""

from __future__ import annotations

import pytest

from tests.conftest import make_ctx
from xcore_doctype import printing
from xcore_doctype.document import DocumentService
from xcore_doctype.errors import InvalidDefinition
from xcore_doctype.print_service import PrintService
from xcore_doctype.registry import DocTypeRegistry
from xcore_doctype.schema import parse_definition

CURRENCY = {
    "schema_version": 1, "name": "Currency", "module": "erp", "naming": {"field": "code"},
    "fields": [{"fieldname": "code", "fieldtype": "Text", "required": True}],
}
COMPANY = {
    "schema_version": 1, "name": "Company", "module": "erp",
    "fields": [{"fieldname": "default_currency", "fieldtype": "Link", "options": "Currency"}],
}
LINE = {
    "schema_version": 1, "name": "Invoice Line", "istable": True,
    "fields": [
        {"fieldname": "item", "fieldtype": "Text"},
        {"fieldname": "rate", "fieldtype": "Currency", "decimals": 2, "currency_from": "parent_currency"},
        {"fieldname": "base_rate", "fieldtype": "Currency", "decimals": 2, "currency_from": "parent_company.default_currency"},
    ],
}
INVOICE = {
    "schema_version": 1, "name": "Invoice", "module": "erp",
    "fields": [
        {"fieldname": "company", "fieldtype": "Link", "options": "Company"},
        {"fieldname": "currency", "fieldtype": "Link", "options": "Currency"},
        {"fieldname": "items", "fieldtype": "Table", "options": "Invoice Line"},
        {"fieldname": "total", "fieldtype": "Currency", "decimals": 2, "currency_from": "currency"},
        {"fieldname": "base_total", "fieldtype": "Currency", "decimals": 2, "currency_from": "company.default_currency"},
        {"fieldname": "fee", "fieldtype": "Currency", "decimals": 2, "options": "EUR"},
    ],
}
TYPES = (CURRENCY, COMPANY, LINE, INVOICE)
GRANTS = [f"erp:{t}:{a}" for t in ("currency", "company", "invoice") for a in ("read", "write")]
ALICE = make_ctx("acme", "alice", permissions=GRANTS)
# Lit les factures, pas les sociétés : la devise d'une société lui reste inconnue.
NO_COMPANY = make_ctx("acme", "nina", permissions=["erp:invoice:read", "erp:currency:read"])


def registry(*defs) -> DocTypeRegistry:
    reg = DocTypeRegistry()
    for d in defs:
        reg.register(parse_definition(d))
    reg.validate_links()
    return reg


def with_field(doc: dict, **changes) -> dict:
    """`doc` dont le champ nommé par `changes['fieldname']` reçoit les autres clés (None retire la clé)."""
    name = changes.pop("fieldname")
    fields = []
    for f in doc["fields"]:
        if f["fieldname"] == name:
            f = {k: v for k, v in {**f, **changes}.items() if v is not None}
        fields.append(f)
    return {**doc, "fields": fields}


def test_the_reference_set_loads():
    registry(*TYPES)


@pytest.mark.parametrize("doc, change, why", [
    (INVOICE, {"fieldname": "total", "currency_from": "devise"}, "champ inconnu 'devise'"),
    (INVOICE, {"fieldname": "total", "currency_from": "items"}, "champ inconnu 'items'"),  # une table ne porte pas de code
    (INVOICE, {"fieldname": "total", "currency_from": "fee"}, "code de devise"),  # un montant n'est pas un code
    (INVOICE, {"fieldname": "base_total", "currency_from": "currency.code.x"}, None),  # deux sauts : refusés par la forme
    (INVOICE, {"fieldname": "base_total", "currency_from": "total.code"}, "n'est pas un champ Link"),
    (INVOICE, {"fieldname": "base_total", "currency_from": "company.nothing"}, "pas de champ 'nothing'"),
    (LINE, {"fieldname": "rate", "currency_from": "parent_devise"}, "sur le parent 'Invoice'"),
    (LINE, {"fieldname": "base_rate", "currency_from": "parent_company.nothing"}, "sur le parent 'Invoice'"),
    (LINE, {"fieldname": "rate", "currency_from": "item.code"}, "n'est pas un champ Link"),  # sans `parent_`, c'est la ligne
])
def test_mistakes_are_load_errors(doc, change, why):
    defs = [with_field(d, **dict(change)) if d is doc else d for d in TYPES]
    with pytest.raises((InvalidDefinition, ValueError)) as e:
        registry(*defs)
    if why:
        assert why in str(e.value)


def test_a_fixed_currency_and_a_read_one_exclude_each_other():
    with pytest.raises(InvalidDefinition, match="s.excluent"):
        parse_definition(with_field(INVOICE, fieldname="total", options="EUR"))


def test_only_an_amount_has_a_currency():
    with pytest.raises(InvalidDefinition, match="Currency"):
        parse_definition(with_field(LINE, fieldname="item", currency_from="parent_currency"))


@pytest.fixture
def svc(db, tables):
    class Log:
        def info(self, *a, **k): ...
        def warning(self, *a, **k): ...
        def error(self, *a, **k): ...
    return DocumentService(registry(*TYPES), db, tables, Log())


async def invoice(svc):
    for code in ("USD", "XOF"):
        await svc.create(ALICE, "Currency", {"code": code})
    company = await svc.create(ALICE, "Company", {"default_currency": "XOF"})
    return await svc.create(ALICE, "Invoice", {
        "company": company["name"], "currency": "USD", "total": "12.50", "base_total": "7500.00", "fee": "1.00",
        "items": [{"item": "Vis", "rate": "12.50", "base_rate": "7500.00"}],
    })


async def test_meta_sends_where_the_currency_comes_from(svc):
    meta = await svc.fetch_meta(ALICE, "Invoice")
    fields = {f["fieldname"]: f for f in meta["fields"]}
    assert fields["total"]["currency_from"] == "currency" and fields["base_total"]["currency_from"] == "company.default_currency"
    rows = {f["fieldname"]: f for f in meta["children"]["Invoice Line"]["fields"]}
    assert rows["rate"]["currency_from"] == "parent_currency"


TEMPLATE = {
    "schema_version": 1, "doctype": "Invoice", "name": "t",
    "blocks": [
        {"type": "fields", "fields": ["total", "base_total", "fee"]},
        {"type": "table", "field": "items", "columns": [{"field": "item"}, {"field": "rate"}, {"field": "base_rate"}], "group_by": "item", "subtotal": ["rate"]},
    ],
}


async def printed(svc, ctx, doc) -> str:
    out = await PrintService(svc, None).preview(ctx, "Invoice", doc["name"], TEMPLATE)
    return out["html"]


async def test_print_shows_each_amount_in_its_currency(svc):
    doc = await invoice(svc)
    html = await printed(svc, ALICE, doc)
    assert "12.50 USD" in html  # la devise de la pièce
    assert "7500.00 XOF" in html  # celle de la société, à travers le lien
    assert "1.00 EUR" in html  # une devise fixe
    assert html.count("12.50 USD") == 3  # le total, la ligne (depuis le parent) et le sous-total
    assert html.count("7500.00 XOF") == 2  # le total de base et la ligne (parent_company.default_currency)


async def test_a_currency_the_caller_cannot_read_is_left_out(svc):
    doc = await invoice(svc)
    html = await printed(svc, NO_COMPANY, doc)
    assert "12.50 USD" in html and "7500.00 XOF" not in html and "7500.00" in html


def test_the_links_to_read_are_only_the_hops():
    reg = registry(*TYPES)
    compiled = reg.get("Invoice")
    data = {"company": "c1", "currency": "USD", "items": [{"rate": "1.00"}, {"rate": "2.00"}]}
    assert printing.currency_links(compiled.definition, data, compiled.children_by_field) == {"Company": {"c1"}}
