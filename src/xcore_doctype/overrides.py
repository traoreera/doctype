"""Habillages (overrides) : ce qu'un opérateur peut ADAPTER dans le Desk sans toucher au YAML d'un plugin.

Le YAML reste la source de vérité : un habillage s'y ajoute, par tenant, et se range dans `xdt_layout_override` (une ligne par
`(tenant_id, kind, target)`). Trois genres (`kind`) :

- `workspace` : le corps d'un espace de travail (`workspaces.WorkspaceDef` moins `schema_version`/`name`, qui viennent de la cible) ;
- `form` : la disposition d'un formulaire (`layout`) et des retouches de champ (`fields` : `label`, `hidden`, `description`, qui ne changent que
  l'affichage, et `read_only`, `mandatory`, `default`, que le SERVEUR applique à l'écriture pour l'organisation : `form_rules` ; seul qui écrit
  TOUS les documents du type les règle, `changed_rules`) ;
- `workspace_user`, `dashboard_user` : la disposition PERSONNELLE d'un utilisateur (même corps), qui passe avant celle de l'organisation ;
- `dashboard` : le tableau de bord d'un DocType (`blocks`, comme `views.dashboard` de sa définition ; `doctype` y est implicite) ;
- `workflow` : ce que l'organisation RESSERRE sur l'automate du YAML (`transitions` : fermer une transition, lui ajouter une condition,
  imposer `not_owner`) — jamais un état ni une transition de plus (process/programme.md §3.6).

Un habillage se VALIDE à l'écriture avec les mêmes règles que le chargement d'un fichier (cibles connues, champs placés une fois,
etc.). À la LECTURE on reste tolérant : un habillage devenu invalide (un DocType a disparu) est ignoré au profit de la définition
d'origine, jamais une page cassée.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, Sequence

from pydantic import TypeAdapter, ValidationError

from . import fieldtypes
from .builder import NO_RULES, NO_WORKFLOW_RULES, FormRules, WorkflowRules
from .dashboards import MAX_BLOCKS, DashboardBlock, check_measured
from .decimals import canonical
from .errors import InvalidDefinition, ValidationFailed
from .expr import ExprError, compile_expr, referenced_fields
from .schema import DECIMAL_TYPES, FILE_TYPES, NO_DEFAULT_TYPES, TODAY, FieldDef, NamingField, complete_layout, layout_tab_problems
from .workspaces import WorkspaceDef, parse_workspace

__all__ = [
    "KIND_WORKSPACE",
    "KIND_FORM",
    "KIND_WORKFLOW",
    "KIND_DASHBOARD",
    "KIND_PRINT",
    "validate_print_override",
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
    "form_rules",
    "form_base",
    "changed_rules",
    "validate_workflow_override",
    "workflow_rules",
    "apply_workflow_override",
    "check_size",
    "MAX_FORM_TWEAKS",
    "MAX_OVERRIDE_BYTES",
]

KIND_WORKSPACE = "workspace"
KIND_FORM = "form"
KIND_DASHBOARD = "dashboard"
# L'automate d'un type, resserré par l'organisation (jamais personnel : une approbation est partagée). Voir `validate_workflow_override`.
KIND_WORKFLOW = "workflow"
# Gabarit d'impression de l'ORGANISATION (process/impression.md §2) : cible `<DocType>` ou `<DocType>:<nom>`.
# Pas de couche PERSONNELLE : imprimer est institutionnel, pas personnel.
KIND_PRINT = "print"
# Les mêmes, PERSONNELS : la disposition d'UN utilisateur, par-dessus celle de l'organisation (la sienne seulement ; voir `user_target`).
KIND_WORKSPACE_USER = "workspace_user"
KIND_DASHBOARD_USER = "dashboard_user"

# Ce qu'un habillage retouche sur un champ : l'AFFICHAGE (`/meta` seulement)...
_DISPLAY_KEYS = ("label", "hidden", "description")
# ... et l'ÉCRITURE (le serveur l'applique pour l'organisation, voir `form_rules`) : il ne fait que RESSERRER le YAML.
_WRITE_KEYS = ("read_only", "mandatory", "default")
_FIELD_KEYS = _DISPLAY_KEYS + _WRITE_KEYS

# Bornes d'un habillage : il se relit à chaque `/meta` et à chaque écriture d'un client (`form_rules`), il ne grossit pas sans fin. Un formulaire
# retouche au plus MAX_FORM_TWEAKS champs ; tout habillage tient en MAX_OVERRIDE_BYTES de JSON (libellés, descriptions et défauts compris).
MAX_FORM_TWEAKS = 500
MAX_OVERRIDE_BYTES = 256 * 1024


def check_size(config: object, what: str) -> None:
    """Refuse (422 `override_too_large`) un habillage trop volumineux, AVANT de le valider : on ne parcourt pas un million de clés pour les refuser une à une."""
    size = len(json.dumps(config, ensure_ascii=False, separators=(",", ":"), default=str).encode())
    if size > MAX_OVERRIDE_BYTES:
        raise ValidationFailed(f"{what} trop volumineux : {size} octets, au plus {MAX_OVERRIDE_BYTES}", code="override_too_large")


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
    check_size(config, "habillage d'espace")
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
    `fields` : des retouches sur des champs connus, d'affichage (`label`, `hidden`, `description`) ou d'écriture (`read_only`, `mandatory`,
    `default` : voir `_tightening`, qui dit ce qu'un habillage ne peut pas faire).
    """
    if not isinstance(config, dict):
        raise ValidationFailed("habillage de formulaire : le corps doit être un objet")
    check_size(config, "habillage de formulaire")
    unknown = set(config) - {"layout", "fields", "added"}  # `added` : les champs que l'organisation ajoute au type (§3.5)
    if unknown:
        raise ValidationFailed(f"habillage de formulaire : clés inconnues {sorted(unknown)}")
    definition = compiled.definition
    fields = {f.fieldname: f for f in definition.fields}
    hidden = {f.fieldname for f in definition.fields if f.hidden}
    out: dict[str, Any] = {}
    # Les champs que l'organisation AJOUTE se placent dans SA disposition comme les autres (le Form Builder du Desk les range où on les
    # dépose) ; sans ça, ils finiraient toujours dans le dernier bloc. Leurs propriétés vivent dans leur propre définition (`added`), pas
    # dans `fields` : seuls les champs du type se retouchent.
    added = config.get("added")
    cleaned_added = _clean_added(added, fields) if added is not None else None
    placeable = set(fields) | {a["fieldname"] for a in cleaned_added or []}
    placeable_hidden = hidden | {a["fieldname"] for a in cleaned_added or [] if a.get("hidden") is True}

    layout = config.get("layout")
    if layout is not None:
        if not isinstance(layout, list):
            raise ValidationFailed("habillage de formulaire : `layout` doit être une liste")
        sections: list[dict[str, Any]] = []
        for i, s in enumerate(layout):
            if not isinstance(s, dict):
                raise ValidationFailed(f"habillage de formulaire : layout.{i} doit être un objet")
            bad = set(s) - {"tab", "section", "collapsible", "collapsed", "columns"}
            if bad:
                raise ValidationFailed(f"habillage de formulaire : layout.{i} clés inconnues {sorted(bad)}")
            columns = s.get("columns")
            if not isinstance(columns, list):
                raise ValidationFailed(f"habillage de formulaire : layout.{i}.columns doit être une liste")
            tab = s.get("tab")
            if tab is not None:
                tab = tab.strip() if isinstance(tab, str) else tab
                if not isinstance(tab, str) or not 1 <= len(tab) <= 60:  # comme le YAML : sans les blancs autour, de 1 à 60 caractères
                    raise ValidationFailed(f"habillage de formulaire : layout.{i}.tab : un nom d'onglet de 1 à 60 caractères")
            sections.append({**({"tab": tab} if tab is not None else {}), "section": s.get("section"),
                             "collapsible": bool(s.get("collapsible", False)), "collapsed": bool(s.get("collapsed", False)), "columns": columns})
        placed: set[str] = set()
        problems = [f"layout: {p}" for p in layout_tab_problems([sec.get("tab") for sec in sections])]  # les règles d'onglets du YAML
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
                    if not isinstance(name, str) or name not in placeable:
                        problems.append(f"layout.{i}: champ inconnu {name!r}")
                    elif name in placeable_hidden:
                        problems.append(f"layout.{i}: le champ caché {name!r} ne peut pas être placé")
                    elif name in placed:
                        problems.append(f"layout.{i}: champ placé deux fois {name!r}")
                    else:
                        placed.add(name)
        if problems:
            raise ValidationFailed("habillage de formulaire invalide : " + "; ".join(problems))
        # Une disposition IDENTIQUE à celle du YAML n'est pas un habillage : la garder la figerait (des onglets que le plugin ajoute ensuite
        # ne paraîtraient jamais pour cette organisation). Le Desk propose la disposition courante quand on n'en a pas encore enregistré.
        if sections != definition.effective_layout():
            out["layout"] = sections

    tweaks = config.get("fields")
    if tweaks is not None:
        if not isinstance(tweaks, dict):
            raise ValidationFailed("habillage de formulaire : `fields` doit être un objet")
        if len(tweaks) > MAX_FORM_TWEAKS:
            raise ValidationFailed(f"habillage de formulaire trop volumineux : au plus {MAX_FORM_TWEAKS} champs retouchés", code="override_too_large")
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
            for key in _DISPLAY_KEYS:
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
            tightened, refused = _tightening(compiled, fields[name], spec)
            entry.update(tightened)
            problems += refused
            if entry.get("hidden") is True and (why := _unhideable(fields[name], "default" in tightened)) is not None:
                problems.append(f"fields.{name}.hidden: {why}")
            if entry:
                clean[name] = entry
        if problems:
            raise ValidationFailed("habillage de formulaire invalide : " + "; ".join(problems))
        out["fields"] = clean

    if cleaned_added is not None:
        out["added"] = cleaned_added
    return out


# ── Champs ajoutés par l'organisation (process/programme.md §3.5) ────────────────────────────────────────────────
# Le préfixe réservé : un champ ajouté par une organisation ne peut jamais entrer en collision avec un champ que le plugin
# ajoutera plus tard à son YAML, et on voit d'où il vient en le lisant. Même raison que le `<plugin>_` des extensions (D26).
CUSTOM_PREFIX = "custom_"

# Ce qu'une organisation peut ajouter. Les types ABSENTS le sont pour une raison, pas par prudence :
#  - `Link` / `DynamicLink` : le registre ne connaît pas ces champs (ils sont par organisation), donc `references_to` ne les
#    verrait pas — on pourrait supprimer un document encore cité. L'intégrité des liens passe avant la souplesse.
#  - `Table` : il faudrait un DocType de lignes, qui n'existe pas par organisation.
#  - `Password` : le scellement se règle sur la définition compilée du type, pas sur celle d'une organisation.
#  - `Image` / `Attach` : un fichier se rattache à une place (type, champ) que l'écran « Fichiers » construit depuis le type.
ADDED_TYPES = (
    "Text", "LongText", "Select", "Number", "Int", "Percent", "Decimal", "Currency", "Rating",
    "Date", "Datetime", "Time", "Duration", "Boolean", "Color", "Code", "JSON", "TextEditor",
)
_ADDED_KEYS = ("fieldname", "fieldtype", "label", "description", "required", "default", "options", "decimals", "stars", "in_list_view", "hidden")


def _clean_added(added: Any, existing: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Les champs qu'une organisation ajoute à ce type : leur forme canonique, ou un refus qui dit pourquoi.

    On ne valide ici que ce qui est PROPRE à l'habillage (préfixe réservé, type permis, pas de collision) : le reste — un défaut
    compatible, des `options` pour un Select, des `decimals` pour un Decimal — est vérifié par `parse_definition` quand la
    définition fusionnée repasse par le schéma, exactement comme pour une extension de plugin (D26).
    """
    if not isinstance(added, list):
        raise ValidationFailed("habillage de formulaire : `added` doit être une liste")
    if len(added) > MAX_FORM_TWEAKS:
        raise ValidationFailed(f"habillage de formulaire trop volumineux : au plus {MAX_FORM_TWEAKS} champs ajoutés", code="override_too_large")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    problems: list[str] = []
    for i, spec in enumerate(added):
        if not isinstance(spec, dict):
            problems.append(f"added.{i}: doit être un objet")
            continue
        if bad := sorted(set(spec) - set(_ADDED_KEYS)):
            problems.append(f"added.{i}: clés inconnues {bad}")
            continue
        name, ftype = spec.get("fieldname"), spec.get("fieldtype")
        if not isinstance(name, str) or not name.startswith(CUSTOM_PREFIX) or len(name) <= len(CUSTOM_PREFIX):
            problems.append(f"added.{i}.fieldname: doit commencer par `{CUSTOM_PREFIX}` (un champ ajouté par une organisation ne masque jamais un champ du type)")
            continue
        if name in existing:
            problems.append(f"added.{i}: {name!r} existe déjà dans ce type")
            continue
        if name in seen:
            problems.append(f"added.{i}: {name!r} ajouté deux fois")
            continue
        if ftype not in ADDED_TYPES:
            problems.append(f"added.{i}.fieldtype: {ftype!r} ne peut pas être ajouté par une organisation (permis : {', '.join(ADDED_TYPES)})")
            continue
        seen.add(name)
        out.append({k: v for k, v in spec.items() if v is not None})
    if problems:
        raise ValidationFailed("habillage de formulaire invalide : " + "; ".join(problems))
    return out


def _opaque(f: FieldDef) -> str | None:
    """Pourquoi ce champ échappe aux retouches d'écriture d'un habillage, ou `None`. Le serveur l'écrit ou le garde à sa façon : une table se
    valide ligne à ligne, un secret se relit masqué (scellé), un fichier se dépose puis se cite, un `fetch_from` se recopie du document lié.
    Ni obligatoire, ni lecture seule, ni valeur par défaut : ce que le YAML en dit reste la seule règle (les mêmes que `/bulk` écarte)."""
    if f.fieldtype == "Table":
        return "une table se valide ligne à ligne"
    if f.fieldtype == "Password":
        return "un secret se relit masqué"
    if f.fieldtype in FILE_TYPES:
        return "un fichier se dépose, il ne se saisit pas"
    if f.fetch_from is not None:
        return "un champ `fetch_from` est recopié du document lié par le serveur"
    return None


def _no_default(compiled: Any, f: FieldDef) -> str | None:
    """Pourquoi ce champ ne peut pas recevoir de valeur par défaut d'un habillage (les règles du YAML : `schema.FieldDef._check`, et plus), ou `None`."""
    if (why := _opaque(f)) is not None:
        return why
    if f.fieldtype in NO_DEFAULT_TYPES:
        return f"un champ {f.fieldtype} n'a pas de valeur par défaut"
    naming = compiled.definition.naming
    if f.unique or (isinstance(naming, NamingField) and naming.field == f.fieldname):
        # Chaque création sans valeur recevrait la même : la seconde serait refusée (doublon, ou nom déjà pris).
        return "une valeur unique (ou qui nomme le document) ne se devine pas"
    return None


def _default_value(compiled: Any, f: FieldDef, value: Any) -> Any:
    """La valeur par défaut sous sa forme ÉCRITE : validée par le type du champ comme une écriture (stricte : `"12"` n'est pas 12, un flottant
    n'est pas un Decimal), canonique (`"12.5"` -> `"12.500"`). `today` (Date) et `now` (Datetime) se résolvent à chaque création, comme dans le YAML."""
    if (f.fieldtype == "Date" and value == TODAY) or (f.fieldtype == "Datetime" and value == fieldtypes.NOW):
        return value
    return compiled.adapters[f.fieldname].validate_python(value)


def _tightening(compiled: Any, f: FieldDef, spec: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Les retouches d'ÉCRITURE d'un champ (`read_only`, `mandatory`, `default`) : leur forme canonique, et ce qui est refusé.

    Un habillage ne fait que RESSERRER : il ne rend ni facultatif un champ obligatoire du YAML, ni modifiable un champ en lecture seule.
    Aucune retouche d'écriture sur une table, un secret, un fichier ni un `fetch_from` (`_opaque`). Un champ rendu obligatoire doit pouvoir
    être rempli : ni caché (YAML ou habillage), ni en lecture seule, ni verrouillé par sa condition (`read_only_depends_on`) ; un champ
    obligatoire du YAML ne se verrouille que s'il a une valeur par défaut (le Desk ne saisit pas un champ en lecture seule, même à la création),
    et jamais quand une condition le rend obligatoire (`mandatory_depends_on`). Une valeur par défaut est validée par le type du champ,
    comme une écriture, jamais sur une valeur unique ni sur le champ qui nomme le document. Ce qui est refusé manque à la forme canonique :
    `form_rules` s'en sert aussi pour relire, tolérante, un habillage devenu invalide.
    """
    name = f.fieldname
    opaque = _opaque(f)
    out: dict[str, Any] = {}
    problems: list[str] = []
    if spec.get("default") is not None:  # `default: null` : pas de défaut d'habillage (celui du YAML s'applique)
        reason = _no_default(compiled, f)
        if reason is not None:
            problems.append(f"fields.{name}.default: pas de valeur par défaut ici ({reason})")
        else:
            try:
                out["default"] = _default_value(compiled, f, spec["default"])
            except (ValidationError, ValueError, TypeError):
                problems.append(f"fields.{name}.default: ce n'est pas une valeur de {f.fieldtype} (écriture stricte)")
    if "read_only" in spec:
        value = spec["read_only"]
        if not isinstance(value, bool):
            problems.append(f"fields.{name}.read_only: doit être un booléen")
        elif not value and f.read_only:
            problems.append(f"fields.{name}.read_only: le champ est en lecture seule dans la définition (un habillage ne fait que resserrer)")
        elif value and not f.read_only and opaque is not None:
            problems.append(f"fields.{name}.read_only: un habillage ne le verrouille pas ({opaque})")
        elif value and not f.read_only and f.required and f.default is None and "default" not in out:
            problems.append(f"fields.{name}.read_only: un champ obligatoire sans valeur par défaut ne se verrouille pas (personne ne pourrait le remplir)")
        elif value and not f.read_only and f.mandatory_depends_on is not None:
            # Verrouillé, il ne se remplirait plus le jour où sa condition le rend obligatoire : aucun document n'atteindrait cet état.
            problems.append(f"fields.{name}.read_only: un champ rendu obligatoire par sa condition (`mandatory_depends_on`) ne se verrouille pas")
        else:
            out["read_only"] = value
    if "mandatory" in spec:
        value = spec["mandatory"]
        locked = f.read_only or out.get("read_only") is True
        if not isinstance(value, bool):
            problems.append(f"fields.{name}.mandatory: doit être un booléen")
        elif not value and f.required:
            problems.append(f"fields.{name}.mandatory: le champ est obligatoire dans la définition (un habillage ne fait que resserrer)")
        elif value and not f.required and opaque is not None:
            problems.append(f"fields.{name}.mandatory: un habillage ne le rend pas obligatoire ({opaque})")
        elif value and not f.required and (f.hidden or spec.get("hidden") is True):
            problems.append(f"fields.{name}.mandatory: un champ caché ne peut pas être obligatoire (personne ne pourrait le remplir)")
        elif value and not f.required and locked:
            problems.append(f"fields.{name}.mandatory: un champ en lecture seule ne peut pas être obligatoire (personne ne pourrait le remplir)")
        elif value and not f.required and f.read_only_depends_on is not None:
            # Exigé ET figé par sa condition : un document dans cet état sans valeur ne se modifierait plus du tout (ni pour le remplir, ni pour en sortir).
            problems.append(f"fields.{name}.mandatory: un champ que sa condition verrouille (`read_only_depends_on`) ne peut pas être obligatoire")
        else:
            out["mandatory"] = value
    return out, problems


def _unhideable(f: FieldDef, org_default: bool) -> str | None:
    """Pourquoi un habillage ne MASQUE pas ce champ, ou `None` : obligatoire dans le YAML (`required` ou `mandatory_depends_on`) et sans valeur
    par défaut (du YAML ou de cet habillage), personne ne le remplirait ; le Desk ne montre ni ne vérifie un champ masqué, le serveur l'exigerait.
    Avec un défaut, le Desk le pré-remplit et l'envoie. (Obligatoire par l'habillage lui-même : `_tightening` refuse déjà masqué.)"""
    if f.hidden or not (f.required or f.mandatory_depends_on is not None) or f.default is not None or org_default:
        return None
    return "un champ obligatoire sans valeur par défaut ne se masque pas (personne ne pourrait le remplir)"


def form_base(compiled: Any) -> dict[str, dict[str, Any]]:
    """Ce que la DÉFINITION impose à chaque champ, habillage à part (`required`, `read_only`, `hidden`, `default`), pour les seuls champs qui en
    ont. `/meta` ne le dit plus une fois l'habillage appliqué ; l'éditeur du Desk en a besoin pour griser ce qu'un habillage ne desserre pas."""
    out: dict[str, dict[str, Any]] = {}
    for f in compiled.definition.fields:
        entry: dict[str, Any] = {key: True for key in ("required", "read_only", "hidden") if getattr(f, key)}
        if f.default is not None:
            entry["default"] = canonical(f.default, f.decimals) if f.fieldtype in DECIMAL_TYPES else f.default
        if entry:
            out[f.fieldname] = entry
    return out


def form_rules(compiled: Any, config: Any) -> FormRules:
    """Ce que l'habillage de formulaire stocké (`config`) resserre à l'ÉCRITURE, contre la définition ACTUELLE (`builder.FormRules`).

    Tolérante comme toute lecture d'habillage : une retouche devenue invalide (champ disparu, devenu caché ou `fetch_from` dans le YAML) est
    ignorée, les autres s'appliquent. `/meta` montre exactement ces règles (`apply_form_override`) : le Desk et le serveur voient les mêmes.
    """
    tweaks = config.get("fields") if isinstance(config, dict) else None
    if not isinstance(tweaks, dict) or not tweaks:
        return NO_RULES
    fields = compiled.definition.field_map
    mandatory: set[str] = set()
    read_only: set[str] = set()
    defaults: dict[str, Any] = {}
    for name, spec in tweaks.items():
        f = fields.get(name)
        if f is None or not isinstance(spec, dict):
            continue
        kept, _ = _tightening(compiled, f, spec)
        if kept.get("mandatory") is True:
            mandatory.add(name)
        if kept.get("read_only") is True:
            read_only.add(name)
        if "default" in kept:
            defaults[name] = kept["default"]
    if not (mandatory or read_only or defaults):
        return NO_RULES
    return FormRules(frozenset(mandatory), frozenset(read_only), defaults)


def apply_form_override(meta: dict[str, Any], config: Any, rules: FormRules | None = None) -> None:
    """Applique un habillage de formulaire au méta rendu, en place. Silencieux si l'habillage ne concerne plus de champ connu.

    `rules` (`form_rules`) : ce que le serveur applique à l'écriture, montré tel quel (`required`, `read_only`, `default`) pour que le Desk
    demande ce que le serveur exigera.
    """
    if not isinstance(config, dict):
        return
    layout = config.get("layout")
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
            if spec.get("hidden") is True and not _hides_a_duty(field, rules):
                field["hidden"] = True
    if isinstance(layout, list) and layout:
        # La disposition de l'organisation, complétée de ce qu'elle ne place pas (les tables, les champs ajoutés au YAML depuis) : sinon ils
        # disparaîtraient de la fiche. Après les retouches : un champ que l'habillage masque n'est pas ajouté.
        meta["layout"] = complete_layout(layout, [f["fieldname"] for f in meta.get("fields", []) if not f.get("hidden")])
    for field in meta.get("fields", []) if rules is not None else ():
        name = field.get("fieldname")
        if name in rules.mandatory:
            field["required"] = True
        if name in rules.read_only:
            field["read_only"] = True
        if name in rules.defaults:
            field["default"] = rules.defaults[name]


def _hides_a_duty(field: Mapping[str, Any], rules: FormRules | None) -> bool:
    """Lecture tolérante de `_unhideable` sur le méta rendu (le YAML seul, avant les règles) : un masquage enregistré devenu invalide (le champ
    est devenu obligatoire, ou a perdu son défaut) ne cache plus le champ, sinon plus aucune création ne passerait dans le Desk."""
    duty = field.get("required") is True or "mandatory_depends_on" in field
    return duty and field.get("default") is None and field.get("fieldname") not in (rules.defaults if rules else {})


def changed_rules(compiled: Any, before: Any, after: Any) -> list[str]:
    """Les champs dont les règles d'ÉCRITURE effectives (`form_rules` : obligatoire, lecture seule, défaut) diffèrent entre l'habillage stocké
    (`before`) et celui qu'on enregistre (`after`, `{}` pour un retrait) : ce que l'enregistrement pose, change ou retire. Comparées telles que
    le serveur les applique : une retouche devenue sans effet (champ disparu) se retire sans rien changer."""
    a, b = form_rules(compiled, before), form_rules(compiled, after)
    names = set((a.mandatory ^ b.mandatory) | (a.read_only ^ b.read_only))
    for name in set(a.defaults) | set(b.defaults):
        x, y = a.defaults.get(name, _ABSENT), b.defaults.get(name, _ABSENT)
        if type(x) is not type(y) or x != y:  # `1` et `1.0`, `0` et `false` : des valeurs différentes pour le JSON stocké
            names.add(name)
    return sorted(names)


# Un habillage d'automate reste lisible : au plus une retouche par transition du YAML, et le YAML en borne déjà le nombre.
MAX_WORKFLOW_TWEAKS = 100
_TRANSITION_KEYS = ("closed", "condition", "not_owner")


def _condition_problem(compiled: Any, source: str) -> str | None:
    """Ce qui empêche cette condition de servir sur ce type : mêmes refus qu'une condition du YAML (`DocTypeDef._check_conditions_and_layout`)."""
    fields = compiled.definition.field_map
    try:
        used = referenced_fields(compile_expr(source, list(fields)))
    except ExprError as e:
        return str(e)
    opaque = sorted(n for n in used if fields[n].fieldtype in ("Table", "Password", "JSON", *DECIMAL_TYPES))
    if opaque:
        return f"{', '.join(opaque)} n'a pas de valeur comparable (table, secret, JSON ou décimal)"
    return None


def validate_workflow_override(compiled: Any, config: object) -> dict[str, Any]:
    """Valide un habillage d'automate et rend sa forme canonique (`{"transitions": {"<action>": {...}}}`).

    Une organisation ne fait que RESSERRER ce que le YAML déclare (process/programme.md §3.6) : fermer une transition (`closed: true`),
    lui ajouter une condition (`condition`, ET avec celle du YAML) ou imposer la séparation des pouvoirs (`not_owner: true`). Tout le
    reste est refusé : une action inconnue, un desserrage (`closed: false`, `not_owner: false` sur une transition qui le porte déjà,
    une condition vide), et surtout un CUL-DE-SAC — fermer la dernière sortie d'un état y échouerait des documents sans recours.
    """
    if not isinstance(config, dict):
        raise ValidationFailed("habillage d'automate : le corps doit être un objet")
    check_size(config, "habillage d'automate")
    wf = compiled.definition.workflow
    if wf is None:
        raise ValidationFailed(f"{compiled.definition.name} n'a pas de workflow", code="no_workflow")
    if unknown := sorted(set(config) - {"transitions"}):
        raise ValidationFailed(f"habillage d'automate : clé inconnue {', '.join(unknown)} (seul `transitions` se retouche)")
    tweaks = config.get("transitions") or {}
    if not isinstance(tweaks, dict):
        raise ValidationFailed("habillage d'automate : `transitions` doit être un objet {action: retouches}")
    if len(tweaks) > MAX_WORKFLOW_TWEAKS:
        raise ValidationFailed(f"habillage d'automate : {MAX_WORKFLOW_TWEAKS} transitions retouchées au plus", code="override_too_large")
    by_action = {t.action: t for t in wf.transitions}
    out: dict[str, dict[str, Any]] = {}
    problems: list[str] = []
    for action, spec in tweaks.items():
        t = by_action.get(action) if isinstance(action, str) else None
        if t is None:
            problems.append(f"{action!r} n'est pas une transition de ce type")
            continue
        if not isinstance(spec, dict):
            problems.append(f"{action}: les retouches doivent être un objet")
            continue
        if extra := sorted(set(spec) - set(_TRANSITION_KEYS)):
            problems.append(f"{action}: {', '.join(extra)} ne se retouche pas (seuls {', '.join(_TRANSITION_KEYS)})")
            continue
        kept: dict[str, Any] = {}
        for key in ("closed", "not_owner"):
            if key not in spec:
                continue
            if spec[key] is not True:
                # `false` n'est pas un retrait : pour rendre une transition, on retire la retouche. Et le YAML ne se desserre pas.
                problems.append(f"{action}.{key}: seul `true` a un sens (pour revenir en arrière, retirez la retouche)")
            elif key == "not_owner" and t.not_owner:
                problems.append(f"{action}.not_owner: le type l'impose déjà")
            else:
                kept[key] = True
        if "condition" in spec:
            source = spec["condition"]
            if not isinstance(source, str) or not source.strip():
                problems.append(f"{action}.condition: une expression est attendue (pour la retirer, retirez la clé)")
            elif (bad := _condition_problem(compiled, source)) is not None:
                problems.append(f"{action}.condition: {bad}")
            else:
                kept["condition"] = source.strip()
        if kept:
            out[action] = kept
    if problems:
        raise ValidationFailed("habillage d'automate invalide : " + "; ".join(problems))
    if dead := _dead_ends(wf, {a for a, k in out.items() if k.get("closed")}):
        raise ValidationFailed(
            f"Fermer ces transitions n'y laisserait aucune sortie : {', '.join(dead)} — un document s'y échouerait sans recours",
            code="workflow_dead_end",
        )
    return {"transitions": out}


def _dead_ends(wf: Any, closed: set[str]) -> list[str]:
    """Les états qui avaient une sortie et n'en auraient plus. Une CONDITION peut bloquer de la même façon, mais cela ne se vérifie
    pas sans évaluer des données : c'est une règle métier, pas une erreur de configuration (process/programme.md §3.6)."""
    out = []
    for state in {t.from_ for t in wf.transitions}:
        if all(t.action in closed for t in wf.transitions if t.from_ == state):
            out.append(state)
    return sorted(out)


def workflow_rules(compiled: Any, config: Any) -> WorkflowRules:
    """Ce que l'habillage stocké resserre, contre la définition ACTUELLE (`builder.WorkflowRules`).

    Tolérante comme toute lecture d'habillage : une retouche qui vise une transition disparue du YAML, ou dont la condition ne compile
    plus, est ignorée — le YAML reprend la main pour celle-là, jamais un type inutilisable.
    """
    tweaks = config.get("transitions") if isinstance(config, dict) else None
    wf = compiled.definition.workflow
    if not isinstance(tweaks, dict) or not tweaks or wf is None:
        return NO_WORKFLOW_RULES
    actions = {t.action for t in wf.transitions}
    closed: set[str] = set()
    not_owner: set[str] = set()
    conditions = {}
    for action, spec in tweaks.items():
        if action not in actions or not isinstance(spec, dict):
            continue
        if spec.get("closed") is True:
            closed.add(action)
        if spec.get("not_owner") is True:
            not_owner.add(action)
        source = spec.get("condition")
        if isinstance(source, str) and source.strip():
            try:
                conditions[action] = compile_expr(source, [f.fieldname for f in compiled.definition.fields])
            except ExprError:
                continue  # devenue invalide (un champ a disparu) : le YAML seul juge cette transition
    if not (closed or not_owner or conditions):
        return NO_WORKFLOW_RULES
    return WorkflowRules(frozenset(closed), conditions, frozenset(not_owner))


def apply_workflow_override(meta: dict[str, Any], rules: WorkflowRules) -> None:
    """Montre l'automate TEL QUE l'organisation l'a resserré : une transition fermée disparaît de `/meta`, un `not_owner` imposé s'y voit.

    Les ÉTATS restent ceux du YAML : un document peut se trouver dans chacun d'eux, et la liste doit pouvoir les filtrer tous.
    """
    wf = meta.get("workflow")
    if not isinstance(wf, dict) or rules is NO_WORKFLOW_RULES:
        return
    kept = []
    for t in wf.get("transitions", []):
        action = t.get("action")
        if action in rules.closed:
            continue
        if action in rules.not_owner:
            t["not_owner"] = True
        kept.append(t)
    wf["transitions"] = kept


_ABSENT = object()
_DASHBOARD = TypeAdapter(list[DashboardBlock])


def validate_dashboard_override(compiled: Any, config: object) -> dict[str, Any]:
    """Valide l'habillage du tableau de bord d'un DocType et rend sa forme canonique (`{"blocks": [...]}`).

    Mêmes règles que `views.dashboard` d'une définition : en-têtes, cartes de chiffres et graphiques ; `doctype` n'y figure pas (c'est ce
    type-ci) ; chaque carte se mesure contre le type (champ connu, calcul permis, filtres valides). Un tableau vide n'est pas un habillage :
    pour retrouver le tableau généré, on retire l'habillage.
    """
    if not isinstance(config, dict):
        raise ValidationFailed("habillage de tableau de bord : le corps doit être un objet")
    check_size(config, "habillage de tableau de bord")
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


def validate_print_override(compiled: Any, config: object) -> dict[str, Any]:
    """Valide le gabarit d'impression d'une ORGANISATION et rend sa forme canonique (process/impression.md §2).

    Les mêmes règles qu'un gabarit livré par un plugin, à la lettre : un seul contrôle, `printing`. C'est ce qui
    garantit qu'un gabarit édité à l'écran ne peut pas être moins sûr qu'un gabarit versionné — en particulier le
    refus des URL externes et des champs qui ne s'impriment pas (§6, §8).
    """
    from .printing import parse_template, template_fields_problems

    if not isinstance(config, dict):
        raise ValidationFailed("gabarit d'impression : le corps doit être un objet")
    check_size(config, "gabarit d'impression")
    definition = compiled.definition
    # Le `doctype` n'est pas celui que le client dit : c'est celui de la cible, sinon on imprimerait un autre type.
    raw = {**config, "schema_version": 1, "doctype": definition.name}
    try:
        template = parse_template(raw, source="gabarit d'impression")
    except InvalidDefinition as e:
        raise ValidationFailed(str(e)) from None
    problems = template_fields_problems(
        template, definition, lambda f: compiled.children.get(str(definition.field_map[f].options))
    )
    if problems:
        raise ValidationFailed("gabarit d'impression invalide : " + "; ".join(problems[:5]))
    return template.model_dump(mode="json", exclude_none=True)


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
