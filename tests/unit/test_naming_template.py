"""`naming: {template: "{account_number} - {company.abbr}"}` : un nom COMPOSÉ par le serveur (traoreera/doctype#69).

L'ERP gardait des noms en hachage pour ses comptes, entrepôts et centres de coût, et portait à côté une clé unique cachée
« <numéro> - <ABRÉV> » posée par un hook. Ce que ces tests défendent :
- le nom est composé des champs du document et, par un saut, de ceux du document qu'un Link désigne ;
- il est unique par construction : la seconde création du même nom est refusée (409) ;
- un jeton vide, ou une cible introuvable ou illisible, refuse la création en nommant le champ ;
- un hook peut remplir un champ du nom (il est composé APRÈS eux, et ils voient `name is None`) ;
- les champs du nom ne changent plus une fois le document créé ;
- `tx.create` compose le nom de la même façon ;
- les fautes de gabarit sont des erreurs de CHARGEMENT.
"""

from __future__ import annotations

import pytest

from tests.conftest import make_ctx
from xcore_doctype.document import DocumentService
from xcore_doctype.errors import AlreadyExists, InvalidDefinition, ValidationFailed
from xcore_doctype.hooks import HookRegistry
from xcore_doctype.registry import DocTypeRegistry
from xcore_doctype.schema import parse_definition


def f(fieldname, fieldtype="Text", **kw):
    return {"fieldname": fieldname, "fieldtype": fieldtype, **kw}


COMPANY = {
    "schema_version": 1, "name": "Company", "module": "erp", "naming": {"field": "code"},
    "fields": [f("code", required=True), f("abbr"), f("founded", "Date")],
    "permissions": {"read": [{"grant": "erp:company:read", "if_owner": True}, "erp:company:read_all"]},
}
ACCOUNT = {
    "schema_version": 1, "name": "Account", "module": "erp", "submittable": True,
    "naming": {"template": "{account_number} - {company.abbr}"},
    "fields": [f("account_number", "Int"), f("label"), f("company", "Link", options="Company")],
    "hooks": {"before_save": ["erp.number"], "on_submit": ["erp.mirror"]},
}
GRANTS = ["erp:company:read_all", "erp:company:write", "erp:account:read", "erp:account:write", "erp:account:submit"]
ALICE = make_ctx("acme", "alice", permissions=GRANTS)
BOB = make_ctx("acme", "bob", permissions=["erp:company:read", "erp:company:write", "erp:account:read", "erp:account:write"])  # ne lit que SES sociétés


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
    hooks = HookRegistry()
    seen: list = []

    async def number(event):
        seen.append(event.name)
        if event.data.get("label") == "Clients" and "account_number" not in event.data:
            event.data["account_number"] = 411  # le hook remplit ce que le nom reprend

    async def mirror(event):
        if event.data.get("label") == "Capital":
            made = await event.tx.create("Account", {"account_number": 109, "label": "Miroir", "company": event.data["company"]})
            seen.append(made["name"])

    hooks.register("erp.number", number, owner="erp")
    hooks.register("erp.mirror", mirror, owner="erp")
    service = DocumentService(registry(COMPANY, ACCOUNT), db, tables, Log(), hooks=hooks)
    service.seen = seen
    return service


async def company(svc, code="ACME", abbr="ACME", ctx=ALICE):
    return await svc.create(ctx, "Company", {"code": code, **({"abbr": abbr} if abbr is not None else {})})


async def test_the_name_is_composed_from_the_document_and_the_linked_one(svc):
    await company(svc)
    doc = await svc.create(ALICE, "Account", {"account_number": 401, "label": "Fournisseurs", "company": "ACME"})
    assert doc["name"] == "401 - ACME"
    assert (await svc.get(ALICE, "Account", "401 - ACME"))["data"]["label"] == "Fournisseurs"


async def test_the_same_name_twice_is_refused(svc):
    await company(svc)
    await svc.create(ALICE, "Account", {"account_number": 401, "company": "ACME"})
    with pytest.raises(AlreadyExists):
        await svc.create(ALICE, "Account", {"account_number": 401, "label": "autre", "company": "ACME"})
    await company(svc, "BETA", "B")
    assert (await svc.create(ALICE, "Account", {"account_number": 401, "company": "BETA"}))["name"] == "401 - B"  # une autre société : un autre nom


async def test_a_hook_fills_a_part_of_the_name_and_sees_no_name_yet(svc):
    await company(svc)
    doc = await svc.create(ALICE, "Account", {"label": "Clients", "company": "ACME"})
    assert doc["name"] == "411 - ACME" and svc.seen == [None]


@pytest.mark.parametrize("data, field, kind", [
    ({"company": "ACME"}, "account_number", "naming_incomplete"),  # un champ du nom est vide
    ({"account_number": 401}, "company", "naming_incomplete"),  # le lien du saut est vide
    ({"account_number": 401, "company": "NULLE-PART"}, "company", "link_not_found"),
    ({"account_number": 401, "company": "VIDE"}, "company", "naming_incomplete"),  # la cible existe, son champ est vide
])
async def test_an_incomplete_name_refuses_the_creation_and_names_the_field(svc, data, field, kind):
    await company(svc)
    await company(svc, "VIDE", None)
    with pytest.raises(ValidationFailed) as e:
        await svc.create(ALICE, "Account", data)
    assert (e.value.details[0]["field"], e.value.details[0]["type"]) == (field, kind)


async def test_a_target_the_caller_cannot_read_is_not_found_and_gives_nothing_away(svc):
    await company(svc)  # la société d'alice : bob ne la lit pas
    with pytest.raises(ValidationFailed) as e:
        await svc.create(BOB, "Account", {"account_number": 401, "company": "ACME"})
    assert e.value.details[0]["type"] == "link_not_found" and "ACME" not in str(e.value.details[0]["msg"]).replace("'ACME'", "")


async def test_the_fields_of_the_name_are_frozen_once_created(svc):
    await company(svc)
    await company(svc, "BETA", "B")
    doc = await svc.create(ALICE, "Account", {"account_number": 401, "label": "Fournisseurs", "company": "ACME"})
    for change in ({"account_number": 402}, {"company": "BETA"}):
        with pytest.raises(ValidationFailed) as e:
            await svc.update(ALICE, "Account", doc["name"], {**doc["data"], **change}, version=doc["version"])
        assert e.value.details[0]["type"] == "set_only_once"
    renamed = await svc.update(ALICE, "Account", doc["name"], {**doc["data"], "label": "Fournisseurs d'exploitation"}, version=doc["version"])
    assert renamed["name"] == "401 - ACME" and renamed["data"]["label"] == "Fournisseurs d'exploitation"


async def test_a_hook_that_creates_gets_a_composed_name_too(svc):
    await company(svc)
    doc = await svc.create(ALICE, "Account", {"account_number": 101, "label": "Capital", "company": "ACME"})
    await svc.submit(ALICE, "Account", doc["name"], version=doc["version"])
    assert svc.seen[-1] == "109 - ACME"
    assert (await svc.get(ALICE, "Account", "109 - ACME"))["data"]["label"] == "Miroir"


async def test_a_composed_name_obeys_the_rules_of_a_name(svc):
    await company(svc, "SLASH", "A/B")
    with pytest.raises(ValidationFailed) as e:
        await svc.create(ALICE, "Account", {"account_number": 401, "company": "SLASH"})
    assert e.value.details[0]["type"] == "invalid_name"


def account(template, **extra):
    return {**ACCOUNT, "hooks": {}, "naming": {"template": template}, **extra}


@pytest.mark.parametrize("definition, why", [
    (account("Compte fixe"), "au moins un jeton"),
    (account("{account_number} - {Company}"), "accolade hors d'un jeton"),
    (account("{inconnu}"), "champ inconnu 'inconnu'"),
    (account("{label.abbr}"), "soit un Link"),
    (account("{company}", fields=ACCOUNT["fields"] + [f("total", "Decimal", decimals=2)]) | {"naming": {"template": "{total}"}}, "doit être de type"),
    (account("{account_number}", allow_rename=True), "allow_rename"),
])
def test_a_wrong_template_is_refused_by_the_schema(definition, why):
    with pytest.raises(InvalidDefinition, match=why):
        parse_definition(definition)


@pytest.mark.parametrize("template, why", [
    ("{company.inconnu}", "n'a pas de champ 'inconnu'"),
    ("{company.founded}", "doit être de type"),
])
def test_a_wrong_hop_is_a_load_error(template, why):
    with pytest.raises(InvalidDefinition, match=why):
        registry(COMPANY, account(template))
