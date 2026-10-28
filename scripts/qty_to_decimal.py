"""Migration T14 : les quantités du jeu ERP passent de `Number` (flottant) à `Decimal` exact.

Décision 1 de `process/programme.md` §3.7, et la PREMIÈRE marche du chantier des hooks : le cas canonique est
`amount = qty × rate`, or multiplier un flottant par un décimal exact pour produire de l'argent exigerait une
règle d'arrondi — et arrondir de l'argent est une question métier, pas technique. Le moteur refusant déjà un
flottant pour un montant, on ne fait pas entrer le flottant par la porte du calcul.

Pourquoi 3 décimales partout : `mapping.same_number_kind` exige, pour deux `Decimal`, le MÊME nombre de
décimales de part et d'autre d'un mapping — et dix mappings de `doctypes/` recopient `qty` vers `qty`. Une
échelle uniforme satisfait la règle sans table d'exceptions, et c'est déjà celle du jeu d'essai `stock`
(`decimals: 3`, comme la précision par défaut d'ERPNext pour les quantités).

Le script réécrit le TEXTE ligne à ligne plutôt que de réémettre le YAML : réémettre reformaterait 704 fichiers
(ordre des clés, guillemets, commentaires) et rendrait la revue impossible. Il est idempotent.

    poetry run python scripts/qty_to_decimal.py            # rapport seul
    poetry run python scripts/qty_to_decimal.py --write    # applique
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "doctypes"
DECIMALS = 3
# Un champ de QUANTITÉ : `qty`, `quantity`, ou un mot composé (`stock_qty`, `qty_to_order`). Jamais un simple
# `quality` ni un `qty` au milieu d'un autre mot : la limite de mot est explicite.
QTY = re.compile(r"(^|_)(qty|quantity)($|_)")
# Le jeu emploie DEUX écritures, et les deux doivent être traitées : une clé par ligne (« bloc ») et le champ
# entier sur une ligne entre accolades (« flot »). Ne traiter que la première en convertissait 242 sur 273.
FIELDNAME = re.compile(r"^(\s*)-?\s*fieldname:\s*(\S+)\s*$")
FIELDTYPE = re.compile(r"^(\s*)fieldtype:\s*Number\s*$")
DEFAULT = re.compile(r"^(\s*)default:\s*(-?\d+(?:\.\d+)?)\s*$")
# Flot : `- { fieldname: qty, …, fieldtype: Number, …, default: 1.0, … }`
FLOW = re.compile(r"^\s*-?\s*\{.*\bfieldname:\s*([A-Za-z0-9_\"']+).*\}\s*$")
FLOW_TYPE = re.compile(r"\bfieldtype:\s*Number\b")
FLOW_DEFAULT = re.compile(r"\bdefault:\s*(-?\d+(?:\.\d+)?)(?=\s*[,}])")


def migrate(text: str) -> tuple[str, int, int]:
    """Rend `(texte, champs convertis, défauts canonisés)`. Ne touche que les champs de quantité en `Number`."""
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    converted = defaults = 0
    # Le champ courant : son nom, et s'il est une quantité qu'on vient de convertir.
    current = ""
    inside_converted = False
    for line in lines:
        bare = line.rstrip("\n")
        # ── Écriture « flot » : tout le champ sur une ligne ──
        if (m := FLOW.match(bare)) and FLOW_TYPE.search(bare) and QTY.search(m.group(1).strip("\"'")):
            fixed = FLOW_TYPE.sub(f"fieldtype: Decimal, decimals: {DECIMALS}", bare, count=1)
            if d := FLOW_DEFAULT.search(fixed):
                fixed = fixed[: d.start(1)] + f'"{float(d.group(1)):.{DECIMALS}f}"' + fixed[d.end(1) :]
                defaults += 1
            out.append(fixed + "\n")
            converted += 1
            current = ""
            inside_converted = False
            continue
        if m := FIELDNAME.match(bare):
            current = m.group(2)
            inside_converted = False
            out.append(line)
            continue
        if inside_converted and (m := DEFAULT.match(line.rstrip("\n"))):
            # Un `Decimal` n'accepte pas un nombre JSON : sa valeur est une chaîne canonique à l'échelle du champ.
            out.append(f'{m.group(1)}default: "{float(m.group(2)):.{DECIMALS}f}"\n')
            defaults += 1
            continue
        if QTY.search(current) and (m := FIELDTYPE.match(line.rstrip("\n"))):
            indent = m.group(1)
            out.append(f"{indent}fieldtype: Decimal\n{indent}decimals: {DECIMALS}\n")
            converted += 1
            inside_converted = True
            continue
        out.append(line)
    return "".join(out), converted, defaults


def main() -> int:
    write = "--write" in sys.argv
    total_fields = total_defaults = total_files = 0
    for path in sorted(ROOT.rglob("*.yaml")):
        text = path.read_text(encoding="utf-8")
        new, fields, defaults = migrate(text)
        if new == text:
            continue
        total_files += 1
        total_fields += fields
        total_defaults += defaults
        print(f"  {path.relative_to(ROOT.parent)}: {fields} quantité(s), {defaults} défaut(s)")
        if write:
            path.write_text(new, encoding="utf-8")
    verb = "converties" if write else "à convertir"
    print(f"\n{total_fields} quantité(s) {verb} dans {total_files} fichier(s), {total_defaults} défaut(s) canonisé(s)")
    if not write and total_fields:
        print("Rien n'a été écrit : relancez avec --write")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
