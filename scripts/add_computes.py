"""Migration T15 : les totaux des pièces se CALCULENT (`compute:`, décision 2 de `programme.md` §3.7).

Deuxième marche du chantier des hooks. Mesuré avant d'écrire : 129 champs de montant clés du jeu sont
`read_only` et **rien ne les remplissait** — une facture ne pouvait donc pas avoir de total, et un hook de
comptabilisation aurait posté des montants vides.

Ce script ne devine rien : il applique une TABLE écrite à la main, pièce par pièce, où chaque ligne a été
vérifiée contre la définition (genre numérique, décimales, présence du champ). Une règle automatique sur les
noms serait plus courte et fausse — `base_*` est en devise de la société (produit par un taux de change qui est
un flottant, donc hors du vocabulaire fermé), et `rounded_total` demande un arrondi au franc que `compute:` ne
fait pas.

Il retire aussi `required` des champs devenus calculés : le schéma le refuse, et à raison — un champ que le
serveur écrit ne peut pas être exigé du client.

    poetry run python scripts/add_computes.py            # rapport seul
    poetry run python scripts/add_computes.py --write     # applique
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent / "doctypes"

# Fichier -> {champ: expression}. Chaque expression a été vérifiée : tous ses opérandes existent, sont du même
# genre numérique, et le champ qui reçoit a au moins autant de décimales (sauf produit, qui arrondit).
PLAN: dict[str, dict[str, str]] = {
    # ── La facture de vente, et sa ligne : le cas canonique de l'ERP ──
    "accounts/sales_invoice_item.yaml": {
        "amount": "qty * rate",  # 3 décimales × 2 -> arrondi à 2, l'échelle déclarée du montant
        "net_amount": "amount - discount_amount",
    },
    "accounts/sales_invoice.yaml": {
        "total_qty": "sum(items.qty)",
        "total": "sum(items.amount)",
        "net_total": "sum(items.net_amount)",
        "grand_total": "total + total_taxes_and_charges",
        # Ce qui reste dû : ERPNext retire aussi les acomptes et le montant passé en perte.
        "outstanding_amount": "grand_total - total_advance - paid_amount - write_off_amount",
    },
    # ── La facture d'achat : la même forme, en miroir ──
    "accounts/purchase_invoice_item.yaml": {
        "amount": "qty * rate",
        "net_amount": "amount - discount_amount",
    },
    "accounts/purchase_invoice.yaml": {
        "total_qty": "sum(items.qty)",
        "total": "sum(items.amount)",
        "net_total": "sum(items.net_amount)",
        "grand_total": "total + total_taxes_and_charges",
        "outstanding_amount": "grand_total - total_advance - paid_amount - write_off_amount",
    },
    # ── Les pièces de vente et d'achat en amont : mêmes lignes, mêmes totaux ──
    "selling/sales_order_item.yaml": {"amount": "qty * rate", "net_amount": "amount - discount_amount"},
    "selling/sales_order.yaml": {
        "total_qty": "sum(items.qty)",
        "total": "sum(items.amount)",
        "net_total": "sum(items.net_amount)",
        "grand_total": "total + total_taxes_and_charges",
    },
    "selling/quotation_item.yaml": {"amount": "qty * rate", "net_amount": "amount - discount_amount"},
    "selling/quotation.yaml": {
        "total_qty": "sum(items.qty)",
        "total": "sum(items.amount)",
        "net_total": "sum(items.net_amount)",
        "grand_total": "total + total_taxes_and_charges",
    },
    "buying/purchase_order_item.yaml": {"amount": "qty * rate", "net_amount": "amount - discount_amount"},
    "buying/purchase_order.yaml": {
        "total_qty": "sum(items.qty)",
        "total": "sum(items.amount)",
        "net_total": "sum(items.net_amount)",
        "grand_total": "total + total_taxes_and_charges",
    },
    "stock/delivery_note_item.yaml": {"amount": "qty * rate", "net_amount": "amount - discount_amount"},
    "stock/delivery_note.yaml": {
        "total_qty": "sum(items.qty)",
        "total": "sum(items.amount)",
        "net_total": "sum(items.net_amount)",
        "grand_total": "total + total_taxes_and_charges",
    },
    "stock/purchase_receipt_item.yaml": {"amount": "qty * rate", "net_amount": "amount - discount_amount"},
    "stock/purchase_receipt.yaml": {
        "total_qty": "sum(items.qty)",
        "total": "sum(items.amount)",
        "net_total": "sum(items.net_amount)",
        "grand_total": "total + total_taxes_and_charges",
    },
}


def apply(path: Path, wanted: dict[str, str], write: bool) -> list[str]:
    """Pose `compute:` sur les champs nommés et retire leur `required`. Rend ce qui a changé, ou pourquoi non."""
    text = path.read_text(encoding="utf-8")
    parsed = yaml.safe_load(text) or {}
    by_name = {f["fieldname"]: f for f in parsed.get("fields") or []}
    notes: list[str] = []
    lines = text.splitlines(keepends=True)

    for field, expression in wanted.items():
        found = by_name.get(field)
        if found is None:
            notes.append(f"ABSENT {field}")
            continue
        if found.get("compute") == expression:
            continue  # idempotent
        where = _field_block(lines, field)
        if where is None:
            notes.append(f"ILLISIBLE {field} (écriture inattendue)")
            continue
        start, end, indent, flow = where
        if flow:
            notes.append(f"FLOT {field} (à écrire à la main)")
            continue
        drop_required = found.get("required") is True
        block = [ln for ln in lines[start:end] if not (drop_required and ln.strip() == "required: true")]
        block.append(f"{indent}compute: {expression}\n")
        lines[start:end] = block
        notes.append(f"{field} = {expression}" + (" (required retiré)" if drop_required else ""))

    if write and notes:
        path.write_text("".join(lines), encoding="utf-8")
    return notes


def _field_block(lines: list[str], field: str) -> tuple[int, int, str, bool] | None:
    """`(début, fin, indentation des clés, écriture en flot)` du bloc du champ `field`."""
    for i, line in enumerate(lines):
        bare = line.rstrip("\n")
        if bare.strip().startswith("{") or (bare.lstrip().startswith("- {") and f"fieldname: {field}" in bare):
            if f"fieldname: {field}" in bare:
                return (i, i + 1, "", True)
            continue
        if bare.strip() in (f"- fieldname: {field}", f"fieldname: {field}") or bare.strip() == f"- fieldname: {field}":
            indent = " " * (len(bare) - len(bare.lstrip()) + (2 if bare.lstrip().startswith("- ") else 0))
            j = i + 1
            while j < len(lines):
                nxt = lines[j].rstrip("\n")
                if not nxt.strip() or nxt.strip().startswith("- ") or (len(nxt) - len(nxt.lstrip())) < len(indent):
                    break
                j += 1
            return (i, j, indent, False)
    return None


def main() -> int:
    write = "--write" in sys.argv
    total = 0
    for relative, wanted in PLAN.items():
        path = ROOT / relative
        if not path.is_file():
            print(f"  {relative}: FICHIER ABSENT")
            continue
        notes = apply(path, wanted, write)
        if notes:
            total += len([n for n in notes if "=" in n])
            print(f"  {relative}:")
            for n in notes:
                print(f"      {n}")
    print(f"\n{total} champ(s) {'calculé(s)' if write else 'à calculer'}")
    if not write and total:
        print("Rien n'a été écrit : relancez avec --write")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
