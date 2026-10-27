"""Modifier QUELQUES champs (`DocumentService.patch`, action `doc.patch`, `PATCH /doc/{dt}/{name}`).

`update` remplace `data` entier : un automate (xflow), un import ou un appel d'API qui ne connaît que les champs qu'il change effacerait
les autres. `patch` lit le document avec le périmètre de l'appelant, y pose ce qui est envoyé, et repasse par `update` : aucune règle
nouvelle, aucune porte dérobée. Chaque test est défendu par une mutation qui le casse.
"""
from __future__ import annotations

import pytest

from tests.conftest import make_ctx
from xcore_doctype.document import DocumentService
from xcore_doctype.errors import NotFound, PermissionDenied, ValidationFailed, VersionConflict
from xcore_doctype.permissions import Scope
from xcore_doctype.registry import DocTypeRegistry
from xcore_doctype.schema import parse_definition
from xcore_doctype.sealing import Sealer

GRANTS = ["crm:ticket:read", "crm:ticket:write"]
ALICE = make_ctx(user="alice", permissions=GRANTS)
BOB = make_ctx(user="bob", permissions=GRANTS)
OWNER_ONLY = [{"grant": "crm:ticket:write", "if_owner": True}]
SEALER = Sealer("k" * 32)


class _Log:
    def info(self, *a, **k): ...
    def error(self, *a, **k): ...
    def warning(self, *a, **k): ...


def _service(db, tables, write=None) -> DocumentService:
    reg = DocTypeRegistry()
    reg.register(parse_definition({"schema_version": 1, "name": "Etape", "istable": True, "fields": [{"fieldname": "quoi", "fieldtype": "Text"}]}))
    ticket = {
        "schema_version": 1, "name": "Ticket", "module": "crm",
        "fields": [
            {"fieldname": "titre", "fieldtype": "Text", "required": True},
            {"fieldname": "statut", "fieldtype": "Select", "options": ["Ouvert", "Fermé"], "default": "Ouvert"},
            {"fieldname": "priorite", "fieldtype": "Int"},
            {"fieldname": "cle", "fieldtype": "Password"},
            {"fieldname": "etapes", "fieldtype": "Table", "options": "Etape"},
        ],
    }
    if write is not None:
        ticket["permissions"] = {"read": ["crm:ticket:read"], "write": write}
    reg.register(parse_definition(ticket))
    reg.validate_links()
    return DocumentService(reg, db, tables, _Log(), sealer=SEALER)


@pytest.fixture
def svc(db, tables):
    return _service(db, tables)


async def _ticket(svc) -> dict:
    return await svc.create(ALICE, "Ticket", {"titre": "Imprimante", "priorite": 2, "cle": "s3cret", "etapes": [{"quoi": "redémarrer"}]})


async def test_only_the_named_fields_change_the_others_stay(svc):
    doc = await _ticket(svc)
    out = await svc.patch(ALICE, "Ticket", doc["name"], {"statut": "Fermé"})
    assert out["data"] == {**doc["data"], "statut": "Fermé"}  # titre, priorité, étapes intacts ; le secret se lit masqué
    assert out["version"] == doc["version"] + 1


async def test_a_secret_read_back_masked_is_kept_not_overwritten_by_the_mask(svc, db, tables):
    doc = await _ticket(svc)
    await svc.patch(ALICE, "Ticket", doc["name"], {"priorite": 5})
    async with svc._query(ALICE) as q:
        stored = await q.get("Ticket", doc["name"], scope=Scope.everything("read"))
    assert SEALER.unseal(stored.data["cle"]) == "s3cret"  # jamais « ******** » écrit à sa place


async def test_a_version_read_earlier_detects_a_change_in_between(svc):
    doc = await _ticket(svc)
    await svc.patch(ALICE, "Ticket", doc["name"], {"priorite": 3})
    with pytest.raises(VersionConflict):
        await svc.patch(ALICE, "Ticket", doc["name"], {"priorite": 4}, doc["version"])
    assert (await svc.get(ALICE, "Ticket", doc["name"]))["data"]["priorite"] == 3


async def test_the_ordinary_rules_apply_schema_and_write_scope(db, tables):
    svc = _service(db, tables, write=OWNER_ONLY)
    doc = await _ticket(svc)
    with pytest.raises(ValidationFailed):
        await svc.patch(ALICE, "Ticket", doc["name"], {"priorite": "haute"})  # écriture stricte, comme update
    with pytest.raises((PermissionDenied, NotFound)):
        await svc.patch(BOB, "Ticket", doc["name"], {"statut": "Fermé"})  # bob lit, mais n'écrit que ses propres tickets
    with pytest.raises(NotFound):
        await svc.patch(make_ctx(tenant="globex", user="alice", permissions=GRANTS), "Ticket", doc["name"], {"statut": "Fermé"})
    assert (await svc.get(ALICE, "Ticket", doc["name"]))["data"]["statut"] == "Ouvert"


async def test_data_must_be_an_object_and_an_empty_one_changes_nothing(svc):
    doc = await _ticket(svc)
    with pytest.raises(ValidationFailed) as refused:
        await svc.patch(ALICE, "Ticket", doc["name"], ["statut", "Fermé"])
    assert refused.value.code == "patch_data"
    same = await svc.patch(ALICE, "Ticket", doc["name"], {})
    assert same["data"] == doc["data"]
    assert (await svc.history(ALICE, "Ticket", doc["name"]))["total"] == 1  # rien n'a changé : seule la création est à l'historique
