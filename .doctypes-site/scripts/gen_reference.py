"""Génère la référence complète des champs depuis les YAML du jeu.

Usage (depuis la racine du dépôt ou de n'importe où) :

    python docs/scripts/gen_reference.py

Écrit `docs/reference/<module>.md` pour chaque dossier de module : un
DocType par section, chaque champ dans un tableau. Ces pages sont générées :
ne pas les éditer à la main, relancer le script après toute évolution des
définitions.
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import yaml

HERE = Path(__file__).resolve()
DOCTYPES = HERE.parents[2]          # doctypes/
OUT = HERE.parents[1] / "reference"  # doctypes/docs/reference/
EXCLUDE = {".git", "docs", "workspaces"}

MODULE_FR = {
    "accounts": "Comptabilité",
    "assets": "Immobilisations",
    "bulk_transaction": "Traitement en masse",
    "buying": "Achats",
    "communication": "Communication",
    "crm": "CRM",
    "edi": "EDI",
    "erpnext_integrations": "Intégrations ERPNext",
    "hr": "Ressources humaines",
    "maintenance": "Maintenance",
    "manufacturing": "Fabrication",
    "payroll": "Paie",
    "projects": "Projets",
    "quality_management": "Qualité",
    "regional": "Régionalisation",
    "selling": "Ventes",
    "setup": "Configuration",
    "stock": "Stock",
    "subcontracting": "Sous-traitance",
    "support": "Support",
    "telephony": "Téléphonie",
    "utilities": "Utilitaires",
}


def cell(value) -> str:
    """Une cellule de tableau markdown, échappée."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "oui" if value else "non"
    text = str(value).replace("|", "\\|").replace("\n", " ")
    return text


def load_modules():
    modules = {}
    for mod in sorted(p.name for p in DOCTYPES.iterdir()
                      if p.is_dir() and p.name not in EXCLUDE):
        docs = []
        for path in sorted((DOCTYPES / mod).rglob("*.yaml")):
            with open(path, encoding="utf-8") as fh:
                doc = yaml.safe_load(fh)
            if isinstance(doc, dict) and doc.get("name"):
                docs.append((path, doc))
        modules[mod] = docs
    return modules


def badges(doc: dict) -> list[str]:
    out = []
    if doc.get("istable"):
        out.append("table enfant")
    if doc.get("single"):
        out.append("document unique")
    if doc.get("source"):
        out.append(f"virtuel (`source: {doc['source']}`)")
    if doc.get("submittable"):
        out.append("soumis")
    return out


def naming_of(doc: dict) -> str:
    n = doc.get("naming")
    if not isinstance(n, dict):
        return ""
    if "series" in n:
        return f"série `{n['series']}`"
    if "field" in n:
        return f"nommé par le champ `{n['field']}`"
    if "prompt" in n:
        return "nom saisi à la création"
    return ", ".join(n)


def views_of(doc: dict) -> str:
    views = doc.get("views") or {}
    if not views:
        return ""
    parts = []
    for kind, conf in views.items():
        if isinstance(conf, dict) and conf.get("parent"):
            parts.append(f"{kind} (parent `{conf['parent']}`)")
        else:
            parts.append(kind)
    return ", ".join(parts)


def workflow_of(doc: dict) -> list[str]:
    wf = doc.get("workflow") or {}
    states = [s.get("name") for s in wf.get("states") or []]
    actions = sorted({t.get("action") for t in wf.get("transitions") or []})
    out = []
    if states:
        out.append("États : " + " → ".join(cell(s) for s in states if s))
    if actions:
        out.append("Actions : " + ", ".join(f"`{a}`" for a in actions if a))
    return out


def field_row(f: dict) -> tuple[str, str, str, str]:
    name = f"`{f.get('fieldname')}`"
    label = cell(f.get("label"))
    ft = f.get("fieldtype") or ""
    options = f.get("options")
    if isinstance(options, list):
        ft += " — " + " · ".join(cell(o) for o in options)
    elif isinstance(options, str) and ft in ("Link", "DynamicLink", "Table"):
        ft = f"{ft} → `{options}`"
    attrs = []
    if f.get("required"):
        attrs.append("**requis**")
    if f.get("in_list_view"):
        attrs.append("colonne de liste")
    if f.get("read_only"):
        attrs.append("lecture seule")
    if f.get("hidden"):
        attrs.append("masqué")
    if f.get("unique"):
        attrs.append("unique")
    if f.get("set_only_once"):
        attrs.append("posé une seule fois")
    if f.get("allow_on_submit"):
        attrs.append("modifiable après soumission")
    if f.get("default") is not None:
        attrs.append(f"défaut `{cell(f['default'])}`")
    if f.get("fetch_from"):
        attrs.append(f"recopié de `{f['fetch_from']}`")
    if f.get("compute"):
        attrs.append(f"calculé : `{f['compute']}`")
    if f.get("currency_from"):
        attrs.append(f"devise de `{f['currency_from']}`")
    if f.get("unit_from"):
        attrs.append(f"unité de `{f['unit_from']}`")
    if f.get("depends_on"):
        attrs.append(f"visible si `{f['depends_on']}`")
    if f.get("mandatory_depends_on"):
        attrs.append(f"obligatoire si `{f['mandatory_depends_on']}`")
    if f.get("read_only_depends_on"):
        attrs.append(f"verrouillé si `{f['read_only_depends_on']}`")
    if f.get("target_status"):
        attrs.append(f"cible {cell(f['target_status'])}")
    if f.get("link_filters"):
        attrs.append("filtres de lien")
    desc = f.get("description")
    if desc:
        attrs.append("— " + cell(desc)[:110])
    return name, label, ft, "; ".join(attrs)


def render_doctype(mod: str, path: Path, doc: dict) -> list[str]:
    lines = []
    name = doc["name"]
    title = doc.get("label") or name
    lines.append(f"## {name}")
    lines.append("")
    meta = [f"`{path.relative_to(DOCTYPES)}`"]
    b = badges(doc)
    if b:
        meta.append(" · ".join(b))
    if doc.get("submittable"):
        lines.append(f"*{title}* — {' · '.join(meta)}")
    else:
        lines.append(f"*{title}* — {' · '.join(meta)}")
    lines.append("")
    n = naming_of(doc)
    v = views_of(doc)
    facts = []
    if n:
        facts.append(f"**Numérotation** : {n}")
    if v:
        facts.append(f"**Vues** : {v}")
    if doc.get("title_field"):
        facts.append(f"**Titre** : `{doc['title_field']}`")
    if doc.get("icon"):
        facts.append(f"**Icône** : `{doc['icon']}`")
    if doc.get("track_changes") is False:
        facts.append("**Historique désactivé** (`track_changes: false`)")
    if doc.get("source"):
        facts.append(f"**Source** : `{doc['source']}`")
    for w in workflow_of(doc):
        facts.append(f"**Workflow** : {w}")
    if doc.get("permissions") is not None:
        perms = doc["permissions"] or {}
        acts = ", ".join(
            f"`{a}` : {len(v) if isinstance(v, list) else v}"
            for a, v in perms.items()
        )
        facts.append(f"**Permissions explicites** — {acts or 'fermées'}")
    if doc.get("cancel_ignores"):
        facts.append("**`cancel_ignores`** : " + ", ".join(f"`{c}`" for c in doc["cancel_ignores"]))
    if facts:
        lines.extend(f"- {fact}" for fact in facts)
        lines.append("")
    fields = doc.get("fields") or []
    if fields:
        lines.append("| Champ | Libellé | Type | Attributs |")
        lines.append("|---|---|---|---|")
        for f in fields:
            if not isinstance(f, dict) or not f.get("fieldname"):
                continue
            c1, c2, c3, c4 = field_row(f)
            lines.append(f"| {c1} | {c2} | {c3} | {c4} |")
        lines.append("")
    return lines


def main() -> int:
    modules = load_modules()
    OUT.mkdir(parents=True, exist_ok=True)
    total = 0
    for mod, docs in modules.items():
        if not docs:
            continue
        label = MODULE_FR.get(mod, mod)
        lines = [
            f"# Référence des champs — {label} (`{mod}`)",
            "",
            f"> Page **générée** par `docs/scripts/gen_reference.py` depuis les YAML de "
            f"`{mod}/` ({len(docs)} DocTypes). Ne pas éditer à la main : relancer le script.",
            "",
        ]
        ftypes = Counter()
        for _, doc in docs:
            for f in doc.get("fields") or []:
                ftypes[f.get("fieldtype")] += 1
        lines.append(
            f"{sum(len(doc.get('fields') or []) for _, doc in docs)} champs au total ; "
            f"types les plus fréquents : "
            + ", ".join(f"`{k}` ({v})" for k, v in ftypes.most_common(5))
            + "."
        )
        lines.append("")
        for path, doc in sorted(docs, key=lambda d: d[1]["name"].lower()):
            lines.extend(render_doctype(mod, path, doc))
            total += 1
        out = OUT / f"{mod}.md"
        out.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        print(f"{mod}: {len(docs)} DocTypes -> {out.relative_to(DOCTYPES)}")
    print(f"TOTAL {total} DocTypes documentés")
    return 0


if __name__ == "__main__":
    sys.exit(main())
