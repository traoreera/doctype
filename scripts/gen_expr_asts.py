"""Génère `tests/vectors/expr_asts.json` : l'arbre que le serveur envoie au navigateur pour chaque condition des vecteurs.

    python scripts/gen_expr_asts.py          # écrit le fichier
Les résultats attendus (`expr_cases.json`) sont écrits à la main ; seuls les arbres sont générés.
"""

from __future__ import annotations

import json
from pathlib import Path

from xcore_doctype.expr import compile_expr

VECTORS = Path(__file__).resolve().parents[1] / "tests" / "vectors"


def build() -> dict:
    doc = json.loads((VECTORS / "expr_cases.json").read_text())
    return {c["expr"]: compile_expr(c["expr"], doc["fields"]) for c in doc["cases"]}


if __name__ == "__main__":
    (VECTORS / "expr_asts.json").write_text(json.dumps(build(), ensure_ascii=False, indent=1) + "\n")
    print("écrit", VECTORS / "expr_asts.json")
