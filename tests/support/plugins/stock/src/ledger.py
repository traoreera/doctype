"""Règles du stock, en QUANTITÉS seulement (palier 40 % phase A, process/palier40.md §4).

Le stock d'un article dans un entrepôt n'est jamais un champ qu'on édite : c'est la somme des écritures immuables du journal
(`Stock Ledger Entry`). Le solde (`Bin`) est MAINTENU à chaque écriture, dans la même transaction que la soumission du mouvement,
jamais recalculé à la lecture ; `verify_balances` le recalcule pour le vérifier.

    soumettre un mouvement  ->  `post_entry`    : une écriture par entrée / sortie, solde mis à jour, stock négatif REFUSÉ
    annuler un mouvement    ->  `reverse_entry` : les écritures inverses (le journal ne perd rien), stock négatif refusé aussi

Tout se passe dans `event.tx`, la transaction du mouvement : un refus (stock insuffisant, antidatage…) défait l'état du mouvement,
les écritures et les soldes ensemble. Aucun SQL ici : le moteur fournit le verrou de ligne (`tx.get(..., lock=True)`), la création
atomique (`tx.ensure`) et le contrôle de version (`tx.update`), sur PostgreSQL comme sur SQLite (process/phase-a.md).

DÉCISIONS (process/palier40.md §8) :
- pas de valorisation : des quantités, rien d'autre ;
- PAS D'ANTIDATAGE : un mouvement ne peut être daté ni dans le futur, ni avant le dernier mouvement déjà passé sur un de ses
  couples (article, entrepôt). Les soldes à une date passée restent ainsi la somme du journal jusqu'à cette date. L'annulation est
  une écriture du jour (le journal ne se réécrit pas), elle n'est jamais refusée pour sa date.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Awaitable, Callable, Iterable

from xcore_doctype import DocumentValidationError, HookEvent
from xcore_doctype.decimals import canonical

RECEIPT, ISSUE, TRANSFER = "Réception", "Sortie", "Transfert"
DECIMALS = 3  # = `decimals` de tous les champs de quantité de doctypes/*.yaml
LEDGER, BIN, ITEM, WAREHOUSE = "Stock Ledger Entry", "Bin", "Item", "Warehouse"
PAGE = 200  # taille de page de la vérification (le maximum de l'API de liste)


@dataclass(frozen=True, slots=True)
class Move:
    """Une entrée (`qty` > 0) ou une sortie (`qty` < 0) d'un article dans un entrepôt, issue de la ligne `line` du mouvement."""

    line: int
    item: str
    warehouse: str
    qty: Decimal


def bin_name(item: str, warehouse: str) -> str:
    return f"{item}@{warehouse}"


def qty_text(value: Decimal) -> str:
    return canonical(value, DECIMALS)


def shown(value: Decimal) -> str:
    """Une quantité dans un MESSAGE destiné à l'utilisateur (français : « 12,500 »). Ce qu'on stocke reste « 12.500 »."""
    return qty_text(value).replace(".", ",")


def moves_of(data: Any) -> list[Move]:
    """Les mouvements d'articles qu'un document « Stock Entry » demande, dans l'ordre de ses lignes (une sortie avant son entrée)."""
    purpose, out = data.get("purpose"), []
    for i, row in enumerate(data.get("items") or []):
        qty = Decimal(row["qty"])
        if purpose in (ISSUE, TRANSFER):
            out.append(Move(i, row["item"], row["source_warehouse"], -qty))
        if purpose in (RECEIPT, TRANSFER):
            out.append(Move(i, row["item"], row["target_warehouse"], qty))
    return out


def _refuse(message: str, *errors: tuple[str, str, str]) -> DocumentValidationError:
    return DocumentValidationError(
        message, details=[{"field": field, "msg": msg, "type": kind} for field, msg, kind in errors] or None
    )


# ── validate : la cohérence des lignes avec la nature du mouvement ───────────────────────────────


async def check_entry(event: HookEvent) -> None:
    """`validate` de Stock Entry : chaque ligne ne porte que les entrepôts que la nature du mouvement admet.

    Que les entrepôts EXIGÉS soient présents, c'est le moteur qui le vérifie (conditions de ligne de `stock_entry_line.yaml`) : ce hook
    ne reçoit un mouvement qu'une fois cela acquis. Il garde ce que le YAML ne dit pas.
    """
    data = event.data
    purpose, errors = data.get("purpose"), []
    for i, row in enumerate(data.get("items") or []):
        source, target = row.get("source_warehouse"), row.get("target_warehouse")
        if Decimal(row["qty"]) <= 0:
            errors.append((f"items.{i}.qty", "La quantité doit être strictement positive", "not_positive"))
        if purpose == RECEIPT and source:
            errors.append((f"items.{i}.source_warehouse", "Une réception n'a pas d'entrepôt source", "not_allowed"))
        if purpose == ISSUE and target:
            errors.append((f"items.{i}.target_warehouse", "Une sortie n'a pas d'entrepôt cible", "not_allowed"))
        if purpose == TRANSFER and source and source == target:
            errors.append((f"items.{i}.target_warehouse", "Un transfert va d'un entrepôt à un AUTRE", "same_warehouse"))
    if errors:
        raise _refuse("Mouvement de stock invalide", *errors)


# ── on_submit / on_cancel : écrire le journal et maintenir les soldes ────────────────────────────


async def post_entry(event: HookEvent) -> None:
    """`on_submit` : le mouvement entre dans le journal."""
    await _apply(event, reverse=False)


async def reverse_entry(event: HookEvent) -> None:
    """`on_cancel` : le mouvement sort du journal par des écritures inverses, jamais par une suppression."""
    await _apply(event, reverse=True)


async def _apply(event: HookEvent, *, reverse: bool) -> None:
    tx, data, voucher = event.tx, event.data, event.name
    assert tx is not None and voucher is not None  # garanti : hooks de transition
    moves = moves_of(data)
    if reverse:
        moves = [Move(m.line, m.item, m.warehouse, -m.qty) for m in reversed(moves)]
    posting = tx.today if reverse else date.fromisoformat(data["posting_date"])

    # 1. Tous les soldes concernés, créés s'ils manquent puis VERROUILLÉS, toujours dans le même ordre (article, entrepôt) : deux
    #    mouvements qui se croisent (A -> B et B -> A) prennent leurs verrous dans le même ordre et ne se bloquent jamais l'un l'autre.
    bins: dict[tuple[str, str], dict[str, Any]] = {}
    for item, warehouse in sorted({(m.item, m.warehouse) for m in moves}):
        name = bin_name(item, warehouse)
        await tx.ensure(BIN, name, {"item": item, "warehouse": warehouse, "qty": qty_text(Decimal(0))})
        found = await tx.get(BIN, name, lock=True)
        assert found is not None  # créé ci-dessus, dans cette transaction
        bins[(item, warehouse)] = found

    # 2. Le calendrier (pas d'antidatage) : seulement pour un mouvement qu'on passe, pas pour son annulation.
    if not reverse:
        if posting > tx.today:
            raise _refuse("La date du mouvement est dans le futur", ("posting_date", "Date dans le futur", "future_date"))
        for (item, warehouse), b in bins.items():
            last = b["data"].get("last_posting_date")
            if last and posting < date.fromisoformat(last):
                raise _refuse(
                    f"Antidatage refusé : le dernier mouvement de « {await _title(tx, ITEM, item)} » dans "
                    f"« {await _title(tx, WAREHOUSE, warehouse)} » date du {last}",
                    ("posting_date", f"Antérieure au dernier mouvement ({last})", "backdated"),
                )

    # 3. Les écritures, une à une : le stock ne descend jamais sous zéro, et chaque écriture garde le solde qui en résulte.
    balance = {pair: Decimal(b["data"]["qty"]) for pair, b in bins.items()}
    rows = []
    for m in moves:
        after = balance[(m.item, m.warehouse)] + m.qty
        if after < 0:
            raise await _insufficient(tx, m, balance[(m.item, m.warehouse)], reverse)
        balance[(m.item, m.warehouse)] = after
        rows.append((m, after))

    # 4. Écrire : le journal (nom déterministe, donc rejouer est impossible) puis les soldes (contrôle de version).
    for m, after in rows:
        side = "in" if m.qty > 0 else "out"
        await tx.create(
            LEDGER,
            {
                "posting_date": posting.isoformat(),
                "item": m.item,
                "warehouse": m.warehouse,
                "qty_change": qty_text(m.qty),
                "balance_qty": qty_text(after),
                "voucher": voucher,
                "voucher_line": m.line,
                "is_cancellation": reverse,
            },
            name=f"{voucher}.{m.line}.{side}" + (".rev" if reverse else ""),
        )
    for (item, warehouse), b in bins.items():
        last = b["data"].get("last_posting_date")
        newest = max(posting, date.fromisoformat(last)) if last else posting
        await tx.update(
            BIN,
            b["name"],
            {"item": item, "warehouse": warehouse, "qty": qty_text(balance[(item, warehouse)]), "last_posting_date": newest.isoformat()},
            b["version"],
        )


async def _title(tx: Any, doctype: str, name: str) -> str:
    doc = await tx.get(doctype, name)
    return (doc or {}).get("data", {}).get("item_name" if doctype == ITEM else "warehouse_name", name)


async def _insufficient(tx: Any, m: Move, available: Decimal, reverse: bool) -> DocumentValidationError:
    item, warehouse = await _title(tx, ITEM, m.item), await _title(tx, WAREHOUSE, m.warehouse)
    wanted = shown(-m.qty)
    if reverse:
        return _refuse(
            f"Annulation impossible : le stock de « {item} » dans « {warehouse} » deviendrait négatif "
            f"({shown(available)} disponible, {wanted} à reprendre)"
        )
    return _refuse(
        f"Stock insuffisant pour « {item} » dans « {warehouse} » : {shown(available)} disponible, {wanted} demandé",
        (f"items.{m.line}.qty", f"Stock disponible : {shown(available)}", "insufficient_stock"),
    )


HOOKS: dict[str, Callable[[HookEvent], Awaitable[None]]] = {
    "stock.check_entry": check_entry,
    "stock.post_entry": post_entry,
    "stock.reverse_entry": reverse_entry,
}


# ── Vérification : le solde maintenu doit être la somme du journal ───────────────────────────────

Lister = Callable[[str, int, int], Awaitable[dict[str, Any]]]  # (doctype, offset, limit) -> {"items": [...], "total": n}


async def _all(list_page: Lister, doctype: str) -> Iterable[dict[str, Any]]:
    out, offset = [], 0
    while True:
        page = await list_page(doctype, offset, PAGE)
        out.extend(page["items"])
        offset += PAGE
        if offset >= page["total"] or not page["items"]:
            return out


async def verify_balances(list_page: Lister) -> list[dict[str, str]]:
    """Recalcule chaque solde depuis le journal et rend les écarts (liste vide : tout concorde).

    `list_page(doctype, offset, limit)` lit un type de document SANS filtre de permission (contexte système) ; la fonction ne lit rien
    d'autre. Un écart n'est jamais corrigé ici : savoir pourquoi un solde dérive est la question, pas l'effacer.
    Phase A : la lecture est page à page sur la table générique ; les agrégats SQL viennent avec la table dédiée (phase B).
    """
    expected: dict[tuple[str, str], Decimal] = {}
    for entry in await _all(list_page, LEDGER):
        d = entry["data"]
        pair = (d["item"], d["warehouse"])
        expected[pair] = expected.get(pair, Decimal(0)) + Decimal(d["qty_change"])
    actual = {(b["data"]["item"], b["data"]["warehouse"]): Decimal(b["data"]["qty"]) for b in await _all(list_page, BIN)}
    problems = []
    for pair in sorted(set(expected) | set(actual)):
        want, have = expected.get(pair, Decimal(0)), actual.get(pair)
        if have is None:
            problems.append({"item": pair[0], "warehouse": pair[1], "ledger": qty_text(want), "bin": "(absent)"})
        elif have != want:
            problems.append({"item": pair[0], "warehouse": pair[1], "ledger": qty_text(want), "bin": qty_text(have)})
        if have is not None and have < 0:
            problems.append({"item": pair[0], "warehouse": pair[1], "ledger": qty_text(want), "bin": qty_text(have), "negative": "true"})
    return problems
