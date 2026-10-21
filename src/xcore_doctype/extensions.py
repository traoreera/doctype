"""Extensions de DocType (D26, `doc/extensions.md`) : un plugin ajoute des hooks et des champs au DocType d'un AUTRE plugin.

Un plugin les décrit dans son dossier (`<plugin>/extends/*.yaml`), chargé par `doctype_registry.load_extensions` :

    schema_version: 1
    doctype: Stock Entry            # le DocType étendu (celui d'un plugin dont on dépend)
    label: Ventes                   # titre de la section ajoutée à la fiche (défaut : le nom du plugin)
    fields:
      - { fieldname: sales_customer, fieldtype: Link, options: Customer }
    hooks:
      on_submit: [sales.stock_entry_submitted]

Le registre garde la définition d'ORIGINE et sert la FUSION (`merge`) : elle repasse par `parse_definition`, donc par tous les contrôles
du schéma, puis par la compilation. Le reste du moteur ne sait pas qu'un champ vient d'une extension.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from .errors import InvalidDefinition
from .schema import HOOK_EVENTS, DocTypeDef, FieldDef, HooksDef, parse_definition
from .workspaces import module_label

# Ce qu'une extension ne peut pas poser sur un champ : les documents existants et le code du propriétaire (ses `tx.create`) l'ignorent.
_REFUSED_FIELD_FLAGS = ("required", "unique", "set_only_once")


class ExtensionDef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    doctype: Annotated[str, StringConstraints(min_length=1, max_length=63)]
    label: Annotated[str, StringConstraints(min_length=1, max_length=140)] | None = None
    fields: Annotated[list[FieldDef], Field(max_length=100)] = Field(default_factory=list)
    hooks: HooksDef = Field(default_factory=HooksDef)


@dataclass(frozen=True, slots=True)
class Extension:
    """Une extension chargée : ce qu'elle ajoute, qui l'apporte, d'où elle vient (pour les messages d'erreur)."""

    owner: str
    definition: ExtensionDef
    source: str


def parse_extension(raw: object, *, owner: str, source: str = "<extension>") -> ExtensionDef:
    """Valide une extension brute ou lève `InvalidDefinition`. Les règles qui dépendent du DocType visé sont dans `merge`."""
    if not isinstance(raw, dict):
        raise InvalidDefinition(f"{source}: l'extension doit être un objet")
    try:
        ext = ExtensionDef.model_validate(raw)
    except ValidationError as e:
        lines = []
        for err in e.errors(include_url=False, include_input=False):
            where = ".".join(str(p) for p in err["loc"]) or "(racine)"
            lines.append(f"  - {where}: {err['msg']}")
        raise InvalidDefinition(f"{source}: extension invalide\n" + "\n".join(lines)) from e
    problems = []
    if not ext.fields and not ext.hooks.all_refs():
        problems.append("une extension doit ajouter au moins un champ ou un hook")
    prefix = f"{owner}_"
    for f in ext.fields:
        if not f.fieldname.startswith(prefix):
            problems.append(f"fields.{f.fieldname}: le nom d'un champ ajouté commence par {prefix!r} (le plugin qui l'apporte)")
        for flag in _REFUSED_FIELD_FLAGS:
            if getattr(f, flag):
                problems.append(f"fields.{f.fieldname}: `{flag}` n'est pas permis dans une extension (les documents existants ne le respectent pas)")
        if f.fieldtype == "Table":
            problems.append(f"fields.{f.fieldname}: un champ Table ne s'ajoute pas par une extension")
    if problems:
        raise InvalidDefinition(f"{source}: " + "; ".join(problems))
    return ext


def merge(origin: DocTypeDef, extensions: list[Extension]) -> DocTypeDef:
    """La définition servie : l'origine, puis chaque extension dans l'ordre (champs à la suite, une section de fiche chacune, hooks après les siens).

    La fusion repasse par `parse_definition` : un nom de champ en double, une condition qui cite un champ inconnu, un hook dupliqué…
    échouent ici, avec le nom de l'extension en cause.
    """
    if not extensions:
        return origin
    raw: dict[str, Any] = origin.model_dump(by_alias=True, exclude_unset=True, mode="python")
    fields = list(raw.get("fields", []))
    layout = list(raw["layout"]) if raw.get("layout") is not None else None
    hooks: dict[str, list[str]] = {e: list(origin.hooks.for_event(e)) for e in HOOK_EVENTS}
    for ext in extensions:
        d = ext.definition
        added = [f.model_dump(by_alias=True, exclude_unset=True, mode="python") for f in d.fields]
        fields += added
        if layout is not None and added:
            layout.append({"section": d.label or module_label(ext.owner), "columns": [[f["fieldname"] for f in added]]})
        for e in HOOK_EVENTS:
            hooks[e] += d.hooks.for_event(e)
    raw["fields"] = fields
    if layout is not None:
        raw["layout"] = layout
    raw["hooks"] = {e: refs for e, refs in hooks.items() if refs}
    names = ", ".join(f"{x.owner} ({x.source})" for x in extensions)
    try:
        return parse_definition(raw, source=f"{origin.name} étendu par {names}")
    except InvalidDefinition as e:
        raise InvalidDefinition(f"{origin.name}: les extensions ne s'appliquent pas : {e}") from None
