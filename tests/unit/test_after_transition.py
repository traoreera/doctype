"""`after_submit` / `after_cancel` : un effet EXTÉRIEUR a lieu APRÈS le commit (traoreera/doctype#59).

Tous les hooks d'un changement d'état tournaient DANS la transaction. Un envoi de courriel, un appel d'API, un webhook fait là ne
peut plus être défait si une écriture suivante échoue : le message est parti, et le document reste brouillon. C'est la limite que
`traoreera/erp` documentait pour son module `mail` — « re-submitting sends a second copy ».

Ces deux hooks sont au changement d'état ce qu'`after_save` est à l'écriture : après le commit, avec le document final, sans `tx`,
et sans droit de refuser — lever là ne défairait rien et mentirait à l'appelant, dont le document EST soumis.

Ce que ces tests défendent :
- ils ne partent QUE si la transaction a abouti : un `on_submit` qui refuse les laisse muets ;
- ils voient le document dans son état final (`docstatus` 1 ou 2), pas le brouillon d'avant ;
- une erreur du hook est journalisée et ne défait RIEN (le document reste soumis) ;
- une transition de WORKFLOW les déclenche aussi : un circuit d'approbation ne doit pas faire disparaître l'envoi ;
- ils n'ont pas de transaction : `event.tx` est absent et `event.find` refuse en le disant ;
- un type non `submittable` qui les déclare est refusé au CHARGEMENT.
"""

from __future__ import annotations

import pytest

from tests.conftest import make_ctx
from xcore_doctype import DocumentValidationError, HookRegistry
from xcore_doctype.document import DocumentService
from xcore_doctype.errors import DoctypeError, InvalidDefinition
from xcore_doctype.registry import DocTypeRegistry
from xcore_doctype.schema import parse_definition

GRANTS = ["lab:order:read", "lab:order:write", "lab:order:submit", "lab:order:cancel"]
CTX = make_ctx("acme", "alice", permissions=GRANTS)

ORDER = {
    "schema_version": 1, "name": "Order", "module": "lab", "submittable": True, "track_changes": True,
    "fields": [{"fieldname": "title", "fieldtype": "Text", "required": True}],
}
# Le même type, mais soumis par un CIRCUIT d'approbation : c'est l'autre chemin vers `docstatus = 1`.
FLOWED = {
    **ORDER, "name": "Flowed",
    "workflow": {
        "states": [{"name": "Brouillon"}, {"name": "Validée", "docstatus": "submitted"}, {"name": "Annulée", "docstatus": "cancelled"}],
        "transitions": [{"action": "Valider", "from": "Brouillon", "to": "Validée"},
                        {"action": "Annuler", "from": "Validée", "to": "Annulée"}],
    },
}
# Une transition de workflow s'ouvre par son propre grant : `<module>:<type>:workflow_<action>`.
FLOW_GRANTS = ["lab:flowed:read", "lab:flowed:write", "lab:flowed:workflow_valider", "lab:flowed:workflow_annuler"]


class _Log:
    def __init__(self) -> None:
        self.errors: list[tuple[str, dict]] = []

    def info(self, msg: str, **k) -> None:
        pass

    def error(self, msg: str, **k) -> None:
        self.errors.append((msg, k))


def build(db, tables, hooks: dict, definitions=(ORDER,)) -> tuple[DocumentService, _Log]:
    reg = DocTypeRegistry()
    for raw in definitions:
        reg.register(parse_definition(raw))
    registry = HookRegistry()
    for name, fn in hooks.items():
        registry.register(name, fn, owner="test")
    log = _Log()
    return DocumentService(reg, db, tables, log, hooks=registry), log


async def test_il_part_apres_le_commit_avec_le_document_final(db, tables) -> None:
    seen: list[tuple[str, int | None, bool]] = []

    async def sent(event) -> None:
        seen.append((event.name, event.data.get("docstatus"), event.tx is None))

    svc, _ = build(db, tables, {"lab.sent": sent}, ({**ORDER, "hooks": {"after_submit": ["lab.sent"]}},))
    doc = await svc.create(CTX, "Order", {"title": "Café"})
    assert seen == []  # une création ne soumet rien
    doc = await svc.submit(CTX, "Order", doc["name"], doc["version"])
    # Le document est vu SOUMIS, et sans transaction : c'est tout le contrat.
    assert seen == [(doc["name"], None, True)]
    assert (await svc.get(CTX, "Order", doc["name"]))["docstatus"] == 1


async def test_il_ne_part_pas_si_la_transaction_a_echoue(db, tables) -> None:
    """Le cœur du ticket : refuser dans `on_submit` doit laisser le courriel non envoyé."""
    sent: list[str] = []

    async def refuse_it(event) -> None:
        raise DocumentValidationError("pas aujourd'hui")

    async def sender(event) -> None:
        sent.append(event.name)

    svc, _ = build(db, tables, {"lab.refuse": refuse_it, "lab.sent": sender},
                   ({**ORDER, "hooks": {"on_submit": ["lab.refuse"], "after_submit": ["lab.sent"]}},))
    doc = await svc.create(CTX, "Order", {"title": "Café"})
    with pytest.raises(DocumentValidationError):
        await svc.submit(CTX, "Order", doc["name"], doc["version"])
    assert sent == []
    assert (await svc.get(CTX, "Order", doc["name"]))["docstatus"] == 0  # resté brouillon


async def test_une_erreur_du_hook_est_journalisee_et_ne_defait_rien(db, tables) -> None:
    """L'état est déjà durable : lever ici ne défait rien et mentirait à l'appelant."""

    async def boom(event) -> None:
        raise RuntimeError("le serveur SMTP ne répond pas")

    svc, log = build(db, tables, {"lab.boom": boom}, ({**ORDER, "hooks": {"after_submit": ["lab.boom"]}},))
    doc = await svc.create(CTX, "Order", {"title": "Café"})
    doc = await svc.submit(CTX, "Order", doc["name"], doc["version"])  # ne lève pas
    assert doc["docstatus"] == 1
    assert [m for m, _ in log.errors] == ["after_submit hook failed"]
    assert "SMTP" in log.errors[0][1]["error"]


async def test_l_annulation_a_le_sien(db, tables) -> None:
    seen: list[int | None] = []

    async def gone(event) -> None:
        seen.append(event.data.get("docstatus"))

    svc, _ = build(db, tables, {"lab.gone": gone}, ({**ORDER, "hooks": {"after_cancel": ["lab.gone"]}},))
    doc = await svc.create(CTX, "Order", {"title": "Café"})
    doc = await svc.submit(CTX, "Order", doc["name"], doc["version"])
    assert seen == []
    await svc.cancel(CTX, "Order", doc["name"], doc["version"])
    assert len(seen) == 1


async def test_une_transition_de_workflow_les_declenche_aussi(db, tables) -> None:
    """Un circuit d'approbation ne doit pas faire disparaître l'envoi : c'est l'autre chemin vers `docstatus = 1`."""
    sent: list[str] = []

    async def sender(event) -> None:
        sent.append(event.event)

    svc, _ = build(db, tables, {"lab.sent": sender},
                   ({**FLOWED, "hooks": {"after_submit": ["lab.sent"], "after_cancel": ["lab.sent"]}},))
    ctx = make_ctx("acme", "alice", permissions=FLOW_GRANTS)
    doc = await svc.create(ctx, "Flowed", {"title": "Café"})
    doc = await svc.workflow_transition(ctx, "Flowed", doc["name"], "Valider", doc["version"])
    assert sent == ["after_submit"]
    await svc.workflow_transition(ctx, "Flowed", doc["name"], "Annuler", doc["version"])
    assert sent == ["after_submit", "after_cancel"]


async def test_il_n_a_pas_de_transaction_et_le_dit(db, tables) -> None:
    """`event.find` après le commit : refusé en nommant `doctype_writer`, comme pour `after_save`."""
    codes: list[str] = []

    async def search(event) -> None:
        try:
            await event.find("Order", {})
        except DoctypeError as e:
            codes.append(e.code)

    svc, _ = build(db, tables, {"lab.search": search}, ({**ORDER, "hooks": {"after_submit": ["lab.search"]}},))
    doc = await svc.create(CTX, "Order", {"title": "Café"})
    await svc.submit(CTX, "Order", doc["name"], doc["version"])
    assert codes == ["not_in_transaction"]


def test_un_type_non_soumettable_qui_en_declare_est_refuse_au_chargement() -> None:
    with pytest.raises(InvalidDefinition, match="submittable"):
        parse_definition({**ORDER, "submittable": False, "hooks": {"after_submit": ["lab.sent"]}})


async def test_il_enregistre_ce_qu_il_a_fait_sur_son_document(db, tables) -> None:
    """Sans ça le hook est muet : rien ne distingue « envoyé » de « jamais tenté ». `doctype_writer` ne peut pas l'écrire — il a
    les droits d'un client, donc un champ `read_only` lui est refusé."""

    async def sender(event) -> None:
        await event.record(state="Envoyé")

    marked = {**ORDER, "fields": [*ORDER["fields"],
                                  {"fieldname": "state", "fieldtype": "Text", "read_only": True, "allow_on_submit": True}],
              "hooks": {"after_submit": ["lab.sent"]}}
    svc, log = build(db, tables, {"lab.sent": sender}, (marked,))
    doc = await svc.create(CTX, "Order", {"title": "Café"})
    doc = await svc.submit(CTX, "Order", doc["name"], doc["version"])
    assert log.errors == []
    fresh = await svc.get(CTX, "Order", doc["name"])
    assert fresh["data"]["state"] == "Envoyé" and fresh["docstatus"] == 1


async def test_il_ne_peut_pas_degeler_un_champ_que_la_soumission_a_figé(db, tables) -> None:
    """La porte reste bornée aux champs `allow_on_submit` : le reste est figé par la soumission, ici comme ailleurs."""

    async def sender(event) -> None:
        await event.record(title="réécrit après coup")

    svc, log = build(db, tables, {"lab.sent": sender}, ({**ORDER, "hooks": {"after_submit": ["lab.sent"]}},))
    doc = await svc.create(CTX, "Order", {"title": "Café"})
    doc = await svc.submit(CTX, "Order", doc["name"], doc["version"])
    assert [m for m, _ in log.errors] == ["after_submit hook failed"]
    assert (await svc.get(CTX, "Order", doc["name"]))["data"]["title"] == "Café"


async def test_record_n_existe_pas_dans_un_hook_qui_a_une_transaction(db, tables) -> None:
    codes: list[str] = []

    async def inside(event) -> None:
        try:
            await event.record(title="x")
        except DoctypeError as e:
            codes.append(e.code)

    svc, _ = build(db, tables, {"lab.inside": inside}, ({**ORDER, "hooks": {"on_submit": ["lab.inside"]}},))
    doc = await svc.create(CTX, "Order", {"title": "Café"})
    await svc.submit(CTX, "Order", doc["name"], doc["version"])
    assert codes == ["not_after_transition"]
