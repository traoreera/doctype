"""Une case à cocher ABSENTE vaut `false` : « absent », `null` et `false` sont le MÊME état (traoreera/doctype#60).

Le Desk renvoie `false` pour une case non cochée. Un document créé SANS cette case (un champ ajouté après coup, un import, une pièce
créée par un script) n'avait donc pas la clé, et la comparaison « absent ≠ false » faisait voir un CHANGEMENT au moteur : il refusait
l'enregistrement sur un champ `read_only`, et le refusait aussi sur un document soumis au titre du gel — même en contexte système. Le
document devenait impossible à enregistrer, sans qu'aucune valeur ne change.

`builder.same_value` canonise la comparaison par type, aux deux seuls endroits qui décident qu'un client a changé un champ
(`_condition_errors` pour `read_only` / `set_only_once`, `_check_frozen` pour un document soumis). Un VRAI changement de case reste
refusé : c'est l'autre moitié de chaque test.
"""

from __future__ import annotations

import pytest

from tests.conftest import make_ctx
from xcore_doctype.document import DocumentService
from xcore_doctype.errors import ValidationFailed
from xcore_doctype.registry import DocTypeRegistry
from xcore_doctype.schema import parse_definition

GRANTS = ["lab:ticket:read", "lab:ticket:write", "lab:ticket:submit", "lab:ticket:cancel"]
CTX = make_ctx("acme", "alice", permissions=GRANTS)

TICKET = {
    "schema_version": 1, "name": "Ticket", "module": "lab", "submittable": True,
    "fields": [
        {"fieldname": "title", "fieldtype": "Text", "required": True},
        # Une case que le SERVEUR écrit (un hook) : l'utilisateur la voit sans pouvoir la changer. C'est la forme que l'ERP donne à
        # tout état tenu par le code (`is_cancelled`, `is_return`…).
        {"fieldname": "closed", "fieldtype": "Boolean", "read_only": True},
        # Une case ordinaire, que l'utilisateur coche : elle est libre au brouillon, et FIGÉE dès la soumission.
        {"fieldname": "urgent", "fieldtype": "Boolean"},
        {"fieldname": "note", "fieldtype": "Text", "allow_on_submit": True},
    ],
}


class _Log:
    def __init__(self) -> None:
        self.lines: list[tuple[str, dict]] = []

    def info(self, msg: str, **k) -> None:
        self.lines.append((msg, k))

    error = info


@pytest.fixture
def svc(db, tables) -> DocumentService:
    reg = DocTypeRegistry()
    reg.register(parse_definition(TICKET))
    return DocumentService(reg, db, tables, _Log())


async def test_une_case_absente_renvoyee_a_false_n_est_pas_un_changement(svc) -> None:
    """Le cas qui bloquait : le document n'a pas la clé, le Desk renvoie `false`, le champ est en lecture seule."""
    doc = await svc.create(CTX, "Ticket", {"title": "Fuite"})
    assert "closed" not in doc["data"]
    saved = await svc.update(CTX, "Ticket", doc["name"], {"title": "Fuite d'eau", "closed": False}, doc["version"])
    assert saved["data"]["title"] == "Fuite d'eau"


async def test_une_case_a_null_est_le_meme_etat_que_false(svc) -> None:
    doc = await svc.create(CTX, "Ticket", {"title": "Fuite", "closed": None})
    await svc.update(CTX, "Ticket", doc["name"], {"title": "Fuite", "closed": False}, doc["version"])


async def test_cocher_vraiment_une_case_en_lecture_seule_reste_refuse(svc) -> None:
    doc = await svc.create(CTX, "Ticket", {"title": "Fuite"})
    with pytest.raises(ValidationFailed) as e:
        await svc.update(CTX, "Ticket", doc["name"], {"title": "Fuite", "closed": True}, doc["version"])
    assert [(d["field"], d["type"]) for d in e.value.details] == [("closed", "read_only")]


async def test_decocher_vraiment_une_case_en_lecture_seule_reste_refuse(svc) -> None:
    """L'autre sens : la case est cochée en base (un hook l'a posée), le client l'efface en l'omettant."""
    doc = await svc.create(CTX, "Ticket", {"title": "Fuite", "closed": True})
    with pytest.raises(ValidationFailed) as e:
        await svc.update(CTX, "Ticket", doc["name"], {"title": "Fuite"}, doc["version"])
    assert [(d["field"], d["type"]) for d in e.value.details] == [("closed", "read_only")]


async def test_un_document_soumis_se_modifie_encore_quand_une_case_absente_revient_a_false(svc) -> None:
    """Le second endroit qui comparait : le gel d'un document soumis. Un paiement qui met à jour le reste dû d'une facture renvoie
    tout le document, cases non cochées comprises — et se faisait refuser « Champ figé »."""
    doc = await svc.create(CTX, "Ticket", {"title": "Fuite"})
    doc = await svc.submit(CTX, "Ticket", doc["name"], doc["version"])
    saved = await svc.update(CTX, "Ticket", doc["name"],
                             {"title": "Fuite", "closed": False, "urgent": False, "note": "relancé"}, doc["version"])
    assert saved["data"]["note"] == "relancé"


async def test_un_document_soumis_refuse_toujours_un_vrai_changement_de_case(svc) -> None:
    doc = await svc.create(CTX, "Ticket", {"title": "Fuite"})
    doc = await svc.submit(CTX, "Ticket", doc["name"], doc["version"])
    with pytest.raises(ValidationFailed) as e:
        await svc.update(CTX, "Ticket", doc["name"], {"title": "Fuite", "urgent": True}, doc["version"])
    assert [(d["field"], d["type"]) for d in e.value.details] == [("urgent", "locked_after_submit")]
