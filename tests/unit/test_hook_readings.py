"""`event.find` / `event.sum` : un hook CHERCHE des documents dans la transaction, y compris en `before_save` (traoreera/doctype#57).

Avant, seuls les hooks transactionnels (`on_submit`, `on_cancel`…) savaient chercher, par leur `tx`. Un `validate` ou un
`before_save` n'avait que `event.get` (un document par son nom) : il ne pouvait pas répondre à « les feuilles d'heures soumises de
cet employé pour ce mois ». Les plugins s'en sortaient en tenant leur PROPRE registre dans un champ caché du document voisin (dans
`traoreera/erp`, `Employee.erp_salary` : cinq rubriques, une par type de pièce, inscrites à la soumission, retirées à l'annulation,
plus des contrôles de péremption sur chaque brouillon) — beaucoup de code, et un registre de plus à chaque type ajouté.

`HookEvent.find` / `.sum` passent par la même implémentation que `tx.find` / `tx.sum` (`_Readings`, dont `_Transaction` hérite) :
mêmes filtres typés que la liste, même plafond, même périmètre système. Après le commit (`after_save`, `after_insert`,
`after_delete`), il n'y a plus de transaction : la lecture est refusée en disant d'utiliser `doctype_writer`.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from tests.conftest import make_ctx
from xcore_doctype import HookEvent, HookRegistry
from xcore_doctype.document import DocumentService
from xcore_doctype.errors import DoctypeError
from xcore_doctype.registry import DocTypeRegistry
from xcore_doctype.schema import parse_definition

GRANTS = ["lab:sheet:read", "lab:sheet:write", "lab:sheet:submit", "lab:slip:read", "lab:slip:write"]
CTX = make_ctx("acme", "alice", permissions=GRANTS)

SHEET = {
    "schema_version": 1, "name": "Sheet", "module": "lab", "submittable": True,
    "fields": [
        {"fieldname": "worker", "fieldtype": "Text", "required": True},
        {"fieldname": "hours", "fieldtype": "Decimal", "decimals": 2, "required": True},
    ],
}
SLIP = {
    "schema_version": 1, "name": "Slip", "module": "lab",
    "fields": [
        {"fieldname": "worker", "fieldtype": "Text", "required": True},
        {"fieldname": "hours", "fieldtype": "Decimal", "decimals": 2},
        {"fieldname": "sheets", "fieldtype": "Int"},
    ],
    "hooks": {"before_save": ["lab.slip"]},
}


class _Log:
    def __init__(self) -> None:
        self.lines: list[tuple[str, dict]] = []

    def info(self, msg: str, **k) -> None:
        self.lines.append((msg, k))

    error = info


def build(db, tables, hooks: dict[str, object], slip=SLIP) -> DocumentService:
    reg = DocTypeRegistry()
    for raw in (SHEET, slip):
        reg.register(parse_definition(raw))
    registry = HookRegistry()
    for name, fn in hooks.items():
        registry.register(name, fn, owner="test")  # type: ignore[arg-type]
    return DocumentService(reg, db, tables, _Log(), hooks=registry)


async def test_un_before_save_additionne_les_pieces_soumises_du_salarie(db, tables) -> None:
    """Le cas réel : un bulletin qui lit, au moment où il se calcule, les feuilles d'heures SOUMISES de son salarié."""

    async def slip(event: HookEvent) -> None:
        worker = event.data["worker"]
        event.data["hours"] = str(await event.sum("Sheet", "hours", {"worker": worker, "docstatus": 1}))
        event.data["sheets"] = len(await event.find("Sheet", {"worker": worker, "docstatus": 1}))

    svc = build(db, tables, {"lab.slip": slip})
    for hours in ("7.50", "2.25"):
        sheet = await svc.create(CTX, "Sheet", {"worker": "aminata", "hours": hours})
        await svc.submit(CTX, "Sheet", sheet["name"], sheet["version"])
    await svc.create(CTX, "Sheet", {"worker": "aminata", "hours": "100.00"})  # un brouillon ne compte pas
    await svc.create(CTX, "Sheet", {"worker": "ousmane", "hours": "5.00"})    # un autre salarié non plus

    made = await svc.create(CTX, "Slip", {"worker": "aminata"})
    assert (made["data"]["hours"], made["data"]["sheets"]) == ("9.75", 2)


async def test_un_before_save_voit_les_filtres_typés_de_la_liste(db, tables) -> None:
    """Les mêmes filtres que la liste, donc un opérateur aussi — et un filtre sur un champ inconnu est refusé, pas ignoré."""
    seen: list[list[str]] = []

    async def slip(event: HookEvent) -> None:
        found = await event.find("Sheet", {"hours": [">=", "7.00"]}, order_by="hours", descending=True)
        seen.append([d["data"]["hours"] for d in found])

    svc = build(db, tables, {"lab.slip": slip})
    for hours in ("7.50", "2.25", "9.00"):
        await svc.create(CTX, "Sheet", {"worker": "aminata", "hours": hours})
    await svc.create(CTX, "Slip", {"worker": "aminata"})
    assert seen == [["9.00", "7.50"]]


async def test_un_hook_ne_cherche_pas_sur_un_champ_qui_n_existe_pas(db, tables) -> None:
    async def slip(event: HookEvent) -> None:
        await event.find("Sheet", {"inconnu": "x"})

    svc = build(db, tables, {"lab.slip": slip})
    with pytest.raises(DoctypeError):
        await svc.create(CTX, "Slip", {"worker": "aminata"})


async def test_un_hook_transactionnel_cherche_par_la_meme_implementation(db, tables) -> None:
    """`tx.find` et `event.find` sont le même code : dans un `on_submit`, `event.find` passe par la transaction du hook."""
    counted: list[int] = []

    async def on_submit(event: HookEvent) -> None:
        assert event.reads is event.tx
        counted.append(len(await event.find("Sheet", {"worker": event.data["worker"]})))

    reg = DocTypeRegistry()
    reg.register(parse_definition({**SHEET, "hooks": {"on_submit": ["lab.sheet"]}}))
    registry = HookRegistry()
    registry.register("lab.sheet", on_submit, owner="test")
    svc = DocumentService(reg, db, tables, _Log(), hooks=registry)
    sheet = await svc.create(CTX, "Sheet", {"worker": "aminata", "hours": "1.00"})
    await svc.submit(CTX, "Sheet", sheet["name"], sheet["version"])
    assert counted == [1]


async def test_apres_le_commit_la_recherche_est_refusee_en_disant_quoi_faire(db, tables) -> None:
    """`after_save` tourne hors transaction : chercher là reviendrait à lire une autre connexion en se croyant dans la sienne."""
    failed: list[str] = []

    async def after(event: HookEvent) -> None:
        try:
            await event.find("Sheet", {})
        except DoctypeError as e:
            failed.append(e.code)
            raise

    svc = build(db, tables, {"lab.slip": after}, slip={**SLIP, "hooks": {"after_save": ["lab.slip"]}})
    await svc.create(CTX, "Slip", {"worker": "aminata"})  # une erreur d'`after_save` est journalisée, jamais levée
    assert failed == ["not_in_transaction"]


async def test_la_somme_refuse_un_champ_qui_n_est_pas_un_montant(db, tables) -> None:
    async def slip(event: HookEvent) -> None:
        await event.sum("Sheet", "worker")

    svc = build(db, tables, {"lab.slip": slip})
    with pytest.raises(DoctypeError):
        await svc.create(CTX, "Slip", {"worker": "aminata"})


async def test_la_somme_vaut_zero_a_l_echelle_du_champ_sans_aucun_document(db, tables) -> None:
    got: list[Decimal] = []

    async def slip(event: HookEvent) -> None:
        got.append(await event.sum("Sheet", "hours", {"worker": "personne"}))

    svc = build(db, tables, {"lab.slip": slip})
    await svc.create(CTX, "Slip", {"worker": "aminata"})
    assert got == [Decimal("0.00")]
