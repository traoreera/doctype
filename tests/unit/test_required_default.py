"""Un champ OBLIGATOIRE qui a un défaut le reçoit du serveur à la création (traoreera/doctype#69).

Avant : `required` + `default` exigeait quand même que le client envoie la valeur, et l'ERP relâchait `required` (règles R3/R3b de son
`scripts/adapt.py`) sur les Select obligatoires. Ce que ces tests défendent :
- une création par l'API sans le champ prend le défaut, sous sa forme stockée (un Decimal canonique) ;
- une valeur envoyée gagne ; `null` reste refusé (obligatoire) ;
- une MISE À JOUR qui oublie le champ est toujours refusée : elle remplace les données, l'oublier ne doit pas le remettre à son défaut ;
- une cellule obligatoire absente d'une LIGNE prend son défaut (une ligne n'a ni création ni mise à jour) ;
- un hook qui crée par `tx.create` en profite aussi ;
- sans défaut, rien ne change : le champ doit être envoyé.
"""

from __future__ import annotations

import pytest

from tests.conftest import make_ctx
from xcore_doctype.document import DocumentService
from xcore_doctype.errors import ValidationFailed
from xcore_doctype.hooks import HookRegistry
from xcore_doctype.registry import DocTypeRegistry
from xcore_doctype.schema import parse_definition

LINE = {
    "schema_version": 1, "name": "Task Line", "istable": True,
    "fields": [
        {"fieldname": "label", "fieldtype": "Text", "required": True},
        {"fieldname": "unit", "fieldtype": "Select", "options": ["h", "j"], "required": True, "default": "h"},
    ],
}
TASK = {
    "schema_version": 1, "name": "Task", "module": "erp", "submittable": True,
    "fields": [
        {"fieldname": "title", "fieldtype": "Text", "required": True},
        {"fieldname": "status", "fieldtype": "Select", "options": ["Ouverte", "Close"], "required": True, "default": "Ouverte"},
        {"fieldname": "budget", "fieldtype": "Decimal", "decimals": 2, "required": True, "default": "0"},
        {"fieldname": "lines", "fieldtype": "Table", "options": "Task Line"},
    ],
    "hooks": {"on_submit": ["erp.spawn"]},
}
ALICE = make_ctx("acme", "alice", permissions=["erp:task:read", "erp:task:write", "erp:task:submit"])


class Log:
    def info(self, *a, **k): ...
    def warning(self, *a, **k): ...
    def error(self, *a, **k): ...


@pytest.fixture
def svc(db, tables):
    reg = DocTypeRegistry()
    reg.register(parse_definition(LINE))
    reg.register(parse_definition(TASK))
    reg.validate_links()
    hooks = HookRegistry()

    async def spawn(event):
        if event.data["title"] == "mère":
            await event.tx.create("Task", {"title": "fille"})

    hooks.register("erp.spawn", spawn, owner="erp")
    return DocumentService(reg, db, tables, Log(), hooks=hooks)


async def test_a_creation_without_the_field_takes_its_default(svc):
    doc = await svc.create(ALICE, "Task", {"title": "A"})
    assert doc["data"]["status"] == "Ouverte"
    assert doc["data"]["budget"] == "0.00"  # la forme STOCKÉE d'un Decimal, pas celle écrite dans le YAML


async def test_a_sent_value_wins_and_null_is_still_refused(svc):
    doc = await svc.create(ALICE, "Task", {"title": "A", "status": "Close", "budget": "12.50"})
    assert (doc["data"]["status"], doc["data"]["budget"]) == ("Close", "12.50")
    with pytest.raises(ValidationFailed) as e:
        await svc.create(ALICE, "Task", {"title": "A", "status": None})
    assert e.value.details[0]["field"] == "status"


async def test_an_update_that_forgets_the_field_is_still_refused(svc):
    doc = await svc.create(ALICE, "Task", {"title": "A", "status": "Close"})
    with pytest.raises(ValidationFailed) as e:
        await svc.update(ALICE, "Task", doc["name"], {"title": "B", "budget": "0.00"}, version=doc["version"])
    assert [d["field"] for d in e.value.details] == ["status"]
    assert (await svc.get(ALICE, "Task", doc["name"]))["data"]["status"] == "Close"  # jamais remis à « Ouverte » en silence


async def test_a_row_cell_takes_its_default(svc):
    doc = await svc.create(ALICE, "Task", {"title": "A", "lines": [{"label": "x"}, {"label": "y", "unit": "j"}]})
    assert [r["unit"] for r in doc["data"]["lines"]] == ["h", "j"]
    with pytest.raises(ValidationFailed) as e:
        await svc.create(ALICE, "Task", {"title": "A", "lines": [{"unit": "h"}]})
    assert e.value.details[0]["field"] == "lines.0.label"


async def test_a_hook_that_creates_gets_the_default_too(svc):
    doc = await svc.create(ALICE, "Task", {"title": "mère"})
    await svc.submit(ALICE, "Task", doc["name"], version=doc["version"])
    page = await svc.list(ALICE, "Task", filters={"title": "fille"})
    assert [d["data"]["status"] for d in page["items"]] == ["Ouverte"]


async def test_without_a_default_the_field_must_be_sent(svc):
    with pytest.raises(ValidationFailed) as e:
        await svc.create(ALICE, "Task", {})
    assert [d["field"] for d in e.value.details] == ["title"]
