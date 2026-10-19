"""Habillages (overrides) : ce qu'un opérateur peut ADAPTER dans le Desk sans toucher au YAML d'un plugin.

Le YAML reste la source de vérité : un habillage s'y ajoute, par tenant, et se range dans `xdt_layout_override` (une ligne par
`(tenant_id, kind, target)`). Trois genres (`kind`) :

- `workspace` : le corps d'un espace de travail (`workspaces.WorkspaceDef` moins `schema_version`/`name`, qui viennent de la cible) ;
- `form` : la disposition d'un formulaire (`layout`) et des retouches de champ (`fields` : `label`, `hidden`, `description`) ;
- `workspace_user`, `dashboard_user` : la disposition PERSONNELLE d'un utilisateur (même corps), qui passe avant celle de l'organisation ;
- `dashboard` : le tableau de bord d'un DocType (`blocks`, comme `views.dashboard` de sa définition ; `doctype` y est implicite).

Un habillage se VALIDE à l'écriture avec les mêmes règles que le chargement d'un fichier (cibles connues, champs placés une fois,
etc.). À la LECTURE on reste tolérant : un habillage devenu invalide (un DocType a disparu) est ignoré au profit de la définition
d'origine, jamais une page cassée.
"""

from __future__ import annotations

import hashlib
from typing import Any, Mapping, Sequence

from pydantic import TypeAdapter, ValidationError

from .dashboards import MAX_BLOCKS, DashboardBlock, check_measured
from .errors import ValidationFailed
from .workspaces import WorkspaceDef, parse_workspace

__all__ = [
    "KIND_WORKSPACE",
    "KIND_FORM",
    "KIND_DASHBOARD",
    "KIND_WORKSPACE_USER",
    "KIND_DASHBOARD_USER",
    "user_target",
    "keep_hidden",
    "validate_dashboard_override",
    "apply_dashboard_override",
    "validate_workspace_override",
    "workspace_from_override",
    "validate_form_override",
    "apply_form_override",
]

KIND_WORKSPACE = "workspace"
KIND_FORM = "form"
KIND_DASHBOARD = "dashboard"
# Les mêmes, PERSONNELS : la disposition d'UN utilisateur, par-dessus celle de l'organisation (la sienne seulement ; voir `user_target`).
KIND_WORKSPACE_USER = "workspace_user"
KIND_DASHBOARD_USER = "dashboard_user"

_FIELD_KEYS = ("label", "hidden", "description")


def user_target(name: str, owner: str) -> str:
    """La clé d'un habillage PERSONNEL : `<espace ou type>@<empreinte de l'utilisateur>`. L'empreinte (et non l'identifiant) garde la clé d'une
    longueur fixe, quel que soit l'identifiant que fournit l'authentification ; `@` ne figure ni dans un nom de module ni de DocType."""
    return f"{name}@{hashlib.blake2b(owner.encode(), digest_size=16).hexdigest()}"


def validate_workspace_override(registry: Any, name: str, config: object) -> dict[str, Any]:
    """Valide le corps d'un habillage d'espace (`config`) et rend sa forme canonique (sans `schema_version`/`name`).

    Mêmes contrôles que `DocTypeRegistry.load_workspaces` : chaque bloc vise un DocType chargé qui n'est pas une table enfant, et
    chaque carte/graphique se mesure contre le DocType visé.
    """
    if not isinstance(config, dict):
        raise ValidationFailed("habillage d'espace : le corps doit être un objet")
    if "name" in config or "schema_version" in config:
        raise ValidationFailed("habillage d'espace : `name` et `schema_version` viennent de la cible, pas du corps")
    raw = {"schema_version": 1, "name": name, **config}
    try:
        ws = parse_workspace(raw, source=f"habillage de {name!r}")
    except Exception as e:  # InvalidDefinition porte déjà un message lisible
        raise ValidationFailed(str(e)) from e
    problems: list[str] = []
    for target in ws.doctypes():
        item = registry.get(target) if target in registry.names() else None
        if item is None:
            problems.append(f"bloc qui vise un DocType inconnu : {target!r}")
        elif item.definition.istable:
            problems.append(f"bloc qui vise {target!r} : c'est un DocType enfant")
    for i, block in enumerate(ws.blocks):
        if block.type in ("number_card", "chart") or (block.type == "quick_list" and block.doctype in registry.names()):
            resolve = lambda t: registry.get(t) if t in registry.names() else None  # noqa: E731
            problems += [f"blocs.{i} ({getattr(block, 'label', '')}): {p}" for p in check_measured(block, resolve, None)]
    if problems:
        raise ValidationFailed("habillage d'espace invalide : " + "; ".join(problems))
    body = ws.model_dump(mode="json", exclude_none=True)
    body.pop("schema_version", None)
    body.pop("name", None)
    return body


def workspace_from_override(name: str, config: Any) -> WorkspaceDef | None:
    """Rend l'espace décrit par un habillage stocké, ou `None` s'il n'est plus valide (le YAML d'origine reprend la main)."""
    if not isinstance(config, dict):
        return None
    try:
        return parse_workspace({"schema_version": 1, "name": name, **config}, source=f"habillage de {name!r}")
    except Exception:
        return None


def validate_form_override(compiled: Any, config: object) -> dict[str, Any]:
    """Valide un habillage de formulaire et rend sa forme canonique (`{"layout": [...], "fields": {...}}`).

    `layout` : mêmes règles que la clé `layout` d'une définition (champ connu, non caché, placé une seule fois, ≤ 4 colonnes).
    `fields` : des retouches (`label`, `hidden`, `description`) sur des champs connus.
    """
    if not isinstance(config, dict):
        raise ValidationFailed("habillage de formulaire : le corps doit être un objet")
    unknown = set(config) - {"layout", "fields"}
    if unknown:
        raise ValidationFailed(f"habillage de formulaire : clés inconnues {sorted(unknown)}")
    definition = compiled.definition
    fields = {f.fieldname: f for f in definition.fields}
    hidden = {f.fieldname for f in definition.fields if f.hidden}
    out: dict[str, Any] = {}

    layout = config.get("layout")
    if layout is not None:
        if not isinstance(layout, list):
            raise ValidationFailed("habillage de formulaire : `layout` doit être une liste")
        sections: list[dict[str, Any]] = []
        for i, s in enumerate(layout):
            if not isinstance(s, dict):
                raise ValidationFailed(f"habillage de formulaire : layout.{i} doit être un objet")
            bad = set(s) - {"section", "collapsible", "collapsed", "columns"}
            if bad:
                raise ValidationFailed(f"habillage de formulaire : layout.{i} clés inconnues {sorted(bad)}")
            columns = s.get("columns")
            if not isinstance(columns, list):
                raise ValidationFailed(f"habillage de formulaire : layout.{i}.columns doit être une liste")
            sections.append({"section": s.get("section"), "collapsible": bool(s.get("collapsible", False)),
                             "collapsed": bool(s.get("collapsed", False)), "columns": columns})
        placed: set[str] = set()
        problems = []
        for i, section in enumerate(sections):
            if not 1 <= len(section["columns"]) <= 4:
                problems.append(f"layout.{i}: entre 1 et 4 colonnes")
            if section["collapsed"] and not section["collapsible"]:
                problems.append(f"layout.{i}: `collapsed` exige `collapsible: true`")
            for ci, col in enumerate(section["columns"]):
                if not isinstance(col, list) or not col:
                    problems.append(f"layout.{i}.columns.{ci}: une liste de champs non vide")
                    continue
                for name in col:
                    if not isinstance(name, str) or name not in fields:
                        problems.append(f"layout.{i}: champ inconnu {name!r}")
                    elif fields[name].fieldtype == "Table":
                        problems.append(f"layout.{i}: {name!r} est une table (elle a sa propre disposition)")
                    elif name in hidden:
                        problems.append(f"layout.{i}: le champ caché {name!r} ne peut pas être placé")
                    elif name in placed:
                        problems.append(f"layout.{i}: champ placé deux fois {name!r}")
                    else:
                        placed.add(name)
        if problems:
            raise ValidationFailed("habillage de formulaire invalide : " + "; ".join(problems))
        out["layout"] = sections

    tweaks = config.get("fields")
    if tweaks is not None:
        if not isinstance(tweaks, dict):
            raise ValidationFailed("habillage de formulaire : `fields` doit être un objet")
        clean: dict[str, Any] = {}
        problems = []
        for name, spec in tweaks.items():
            if name not in fields:
                problems.append(f"fields.{name}: champ inconnu")
                continue
            if not isinstance(spec, dict):
                problems.append(f"fields.{name}: doit être un objet")
                continue
            bad = set(spec) - set(_FIELD_KEYS)
            if bad:
                problems.append(f"fields.{name}: clés inconnues {sorted(bad)}")
                continue
            entry: dict[str, Any] = {}
            for key in _FIELD_KEYS:
                if key not in spec:
                    continue
                value = spec[key]
                if key == "hidden":
                    if not isinstance(value, bool):
                        problems.append(f"fields.{name}.hidden: doit être un booléen")
                        continue
                elif not isinstance(value, str) or not value.strip():
                    problems.append(f"fields.{name}.{key}: du texte non vide")
                    continue
                entry[key] = value
            if entry:
                clean[name] = entry
        if problems:
            raise ValidationFailed("habillage de formulaire invalide : " + "; ".join(problems))
        out["fields"] = clean
    return out


def apply_form_override(meta: dict[str, Any], config: Any) -> None:
    """Applique un habillage de formulaire au méta rendu, en place. Silencieux si l'habillage ne concerne plus de champ connu."""
    if not isinstance(config, dict):
        return
    layout = config.get("layout")
    if isinstance(layout, list) and layout:
        meta["layout"] = layout
    tweaks = config.get("fields")
    if isinstance(tweaks, dict):
        for field in meta.get("fields", []):
            spec = tweaks.get(field.get("fieldname"))
            if not isinstance(spec, dict):
                continue
            if isinstance(spec.get("label"), str):
                field["label"] = spec["label"]
            if isinstance(spec.get("description"), str):
                field["description"] = spec["description"]
            if spec.get("hidden") is True:
                field["hidden"] = True


_DASHBOARD = TypeAdapter(list[DashboardBlock])


def validate_dashboard_override(compiled: Any, config: object) -> dict[str, Any]:
    """Valide l'habillage du tableau de bord d'un DocType et rend sa forme canonique (`{"blocks": [...]}`).

    Mêmes règles que `views.dashboard` d'une définition : en-têtes, cartes de chiffres et graphiques ; `doctype` n'y figure pas (c'est ce
    type-ci) ; chaque carte se mesure contre le type (champ connu, calcul permis, filtres valides). Un tableau vide n'est pas un habillage :
    pour retrouver le tableau généré, on retire l'habillage.
    """
    if not isinstance(config, dict):
        raise ValidationFailed("habillage de tableau de bord : le corps doit être un objet")
    unknown = set(config) - {"blocks"}
    if unknown:
        raise ValidationFailed(f"habillage de tableau de bord : clés inconnues {sorted(unknown)}")
    raw = config.get("blocks")
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_BLOCKS:
        raise ValidationFailed(f"habillage de tableau de bord : `blocks` est une liste de 1 à {MAX_BLOCKS} blocs (pour retrouver le tableau généré, retirez l'habillage)")
    try:
        blocks = _DASHBOARD.validate_python(raw)
    except ValidationError as e:
        shown = "; ".join(f"blocs.{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in e.errors()[:5])
        raise ValidationFailed(f"habillage de tableau de bord invalide : {shown}") from e
    name = compiled.definition.name
    problems: list[str] = []
    for i, block in enumerate(blocks):
        if block.type in ("number_card", "chart"):
            problems += [f"blocs.{i} ({block.label}): {p}" for p in check_measured(block, lambda n: compiled if n == name else None, name)]
    if problems:
        raise ValidationFailed("habillage de tableau de bord invalide : " + "; ".join(problems))
    return {"blocks": [b.model_dump(mode="json", exclude_none=True) for b in blocks]}


def apply_dashboard_override(meta: dict[str, Any], config: Any, doctype: str) -> bool:
    """Remplace `views.dashboard` du méta rendu, en place. Un habillage devenu illisible est ignoré : le tableau d'origine reste (retourne `False`)."""
    blocks = config.get("blocks") if isinstance(config, dict) else None
    if not isinstance(blocks, list) or not blocks:
        return False
    try:
        checked = _DASHBOARD.validate_python(blocks)
    except ValidationError:
        return False
    views = meta.setdefault("views", {})
    views["dashboard"] = [
        {**b, "doctype": doctype} if b["type"] in ("number_card", "chart") else b for b in (c.model_dump(mode="json", exclude_none=True) for c in checked)
    ]
    return True


def _loose_key(block: Mapping[str, Any]) -> tuple[Any, ...]:
    """Ce qui identifie un bloc malgré les retouches de mise en page (largeur, couleur) ou de libellé résolu : pour retrouver sa place."""
    kind = block.get("type")
    if kind == "header" or kind == "paragraph":
        return (kind, block.get("text"))
    if kind == "card":
        return (kind, block.get("title"))
    if kind == "shortcut":
        return (kind, block.get("doctype"))
    return (kind, block.get("doctype"), block.get("label"))


def keep_hidden(base: Sequence[Mapping[str, Any]], shown: set[int], submitted: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Les blocs `submitted` (ce que l'utilisateur a vu et arrangé), PLUS ceux qu'il ne voit pas et que `base` contient.

    Il n'enregistre que ce qu'il voit : sans cela, enregistrer sa disposition effacerait, pour tout le monde, les blocs que ses droits lui
    cachent. Un bloc caché (`base[i]`, `i` hors de `shown`) retrouve sa place à côté de son voisin visible : après le bloc visible qui le
    précédait s'il est encore là, sinon avant celui qui le suivait, sinon à la fin. Il n'est jamais modifié : l'utilisateur ne le contrôle pas.
    """
    out = [dict(b) for b in submitted]
    keys = [_loose_key(b) for b in out]
    for i, hidden in enumerate(base):
        if i in shown:
            continue
        spot = None
        for j in range(i - 1, -1, -1):  # le voisin visible qui précédait
            if j in shown and _loose_key(base[j]) in keys:
                spot = keys.index(_loose_key(base[j])) + 1
                break
        if spot is None:
            for j in range(i + 1, len(base)):  # sinon celui qui suivait
                if j in shown and _loose_key(base[j]) in keys:
                    spot = keys.index(_loose_key(base[j]))
                    break
        if spot is None:
            spot = len(out)
        out.insert(spot, dict(hidden))
        keys.insert(spot, _loose_key(hidden))
    return out
