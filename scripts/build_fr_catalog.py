"""Assemble `translations/fr.yaml` (le catalogue français des types de `doctypes/`) à partir des lots de traduction.

Un lot est un fichier `index<TAB>traduction` : `index` désigne un texte de la liste des textes à traduire (`--todo`, un JSON produit à
partir des définitions des cinq modules de la première version : setup, accounts, stock, selling, buying, et des espaces de travail).
Travailler par index évite d'avoir à réécrire chaque texte source et garantit un YAML correctement échappé (yaml.safe_dump).

Un texte que AUCUN lot ne traduit est consigné dans `translations/fr-unchanged.txt` : un numéro, un modèle de numérotation
(`ACC-SINV-.YYYY.-`), un sigle, ou un texte déjà en français. `tests/unit/test_erp_doctypes.py` exige que chaque texte des cinq modules
soit traduit OU consigné là : un libellé ajouté sans traduction se voit.

Il a servi à l'assemblage INITIAL (2 nov. 2026, 3770 textes en 11 lots) ; les lots ne sont pas versionnés. Désormais on modifie
`translations/fr.yaml` et `fr-unchanged.txt` directement : relancer ce script avec d'autres lots écraserait le catalogue.

Usage : python scripts/build_fr_catalog.py --todo todo.json lot1.tsv lot2.tsv …
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1] / "translations"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--todo", required=True)
    parser.add_argument("batches", nargs="+")
    args = parser.parse_args()
    todo: list[str] = json.loads(Path(args.todo).read_text(encoding="utf-8"))
    done: dict[str, str] = {}
    for batch in args.batches:
        # Une ligne qui ne commence pas par `index<TAB>` CONTINUE la traduction précédente (un texte source sur plusieurs lignes).
        entries: list[tuple[int, int, list[str]]] = []
        for n, line in enumerate(Path(batch).read_text(encoding="utf-8").split("\n"), 1):
            head, tab, rest = line.partition("\t")
            if tab and head.isdigit():
                entries.append((n, int(head), [rest]))
            elif entries:
                entries[-1][2].append(line)
            elif line.strip():
                raise SystemExit(f"{batch}:{n} : une ligne doit commencer par « index<TAB> »")
        for n, index, lines in entries:
            source = todo[index]
            text = "\n".join(lines).strip() if "\n" in source else " ".join(lines).strip()
            if not text:
                raise SystemExit(f"{batch}:{n} : traduction vide pour {source!r}")
            if source in done and done[source] != text:
                raise SystemExit(f"{batch}:{n} : {source!r} traduit deux fois différemment ({done[source]!r} / {text!r})")
            done[source] = text
    messages = {k: v for k, v in sorted(done.items(), key=lambda kv: kv[0].lower()) if k != v}
    unchanged = sorted({s for s in todo if s not in done} | {k for k, v in done.items() if k == v}, key=str.lower)
    ROOT.mkdir(exist_ok=True)
    header = (
        "# Le catalogue français des types de doctypes/ (translations.py) : clé = le libellé source (anglais), valeur = le français.\n"
        "# Assemblé par scripts/build_fr_catalog.py ; les textes laissés tels quels sont dans fr-unchanged.txt.\n"
    )
    body = yaml.safe_dump({"messages": messages}, allow_unicode=True, sort_keys=False, width=1000)
    (ROOT / "fr.yaml").write_text(header + body, encoding="utf-8")
    (ROOT / "fr-unchanged.txt").write_text("\n".join(unchanged) + "\n", encoding="utf-8")
    print(f"{len(messages)} traductions, {len(unchanged)} textes laissés tels quels (fr-unchanged.txt), sur {len(todo)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
