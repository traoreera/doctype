"""Définition -> modèle Pydantic de validation d'écriture (doc/doctype.md §7.1).

Écriture stricte : `extra="forbid"`, types non coercés. La lecture ne repasse jamais par ici
(un ancien document qui ne respecte plus la définition ne doit pas faire échouer une liste).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import date
from typing import Annotated, Any, Literal, Mapping

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    PlainValidator,
    StrictBool,
    StringConstraints,
    TypeAdapter,
    ValidationError,
    create_model,
)

from . import fieldtypes
from .decimals import canonical
from .errors import ValidationFailed
from .expr import Node, compile_expr, evaluate
from .schema import DECIMAL_TYPES, MAX_TABLE_ROWS, PARENT_PREFIX, TODAY, DocTypeDef, FieldDef, NamingField
from .sealing import kept


def _iso_date(value: str) -> str:
    # fromisoformat accepte aussi "20261017". Écriture stricte : seule la forme canonique est
    # acceptée, ce qui garantit aussi que le tri textuel d'une colonne Date est chronologique.
    try:
        canonical = date.fromisoformat(value).isoformat() == value
    except ValueError:
        canonical = False
    if not canonical:
        raise ValueError("date ISO attendue (AAAA-MM-JJ)")
    return value


def _number(value: object) -> int | float:
    # Pas de coercion (« 12 » n'est pas 12), pas de booléen, et surtout pas de NaN/Infinity : le JSON
    # brut les accepte côté Python, SQLite les stocke, PostgreSQL (JSONB) les refuse, et une fois
    # stockés ils font échouer la sérialisation de toute réponse qui contient le document.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("nombre attendu")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("nombre fini attendu (NaN et Infinity sont refusés)")
    return value


_IsoDate = Annotated[str, AfterValidator(_iso_date)]
_Number = Annotated[int | float, PlainValidator(_number)]
_LinkTarget = Annotated[str, StringConstraints(min_length=1, max_length=140)]


def _decimal_validator(decimals: int):
    def check(value: object) -> str:  # un seul paramètre : pydantic passerait sinon son `info` comme second argument
        return canonical(value, decimals)

    return check


def python_type(field: FieldDef) -> Any:
    """Type d'un champ SCALAIRE. Un champ Table n'en a pas : ses lignes sont validées par le modèle de leur DocType enfant."""
    match field.fieldtype:
        case "Text":
            if field.format:
                kind = field.format
                return Annotated[str, AfterValidator(lambda v: fieldtypes.text_format(kind, v))]
            return str
        case "LongText":
            return Annotated[str, AfterValidator(fieldtypes.long_text)]
        case "Datetime":
            return Annotated[str, AfterValidator(fieldtypes.iso_datetime)]
        case "Color":
            return Annotated[str, AfterValidator(fieldtypes.color)]
        case "Image" | "Attach":
            return Annotated[str, AfterValidator(fieldtypes.file_id)]  # l'identifiant d'un fichier téléversé ; son existence se vérifie dans le service
        case "Rating":
            stars = field.stars or fieldtypes.DEFAULT_STARS
            return Annotated[int, PlainValidator(lambda v: fieldtypes.rating(v, stars))]
        case "Int":
            return Annotated[int, PlainValidator(fieldtypes.integer)]
        case "Percent":
            return Annotated[int | float, PlainValidator(fieldtypes.percent)]
        case "Time":
            return Annotated[str, AfterValidator(fieldtypes.time_of_day)]
        case "Duration":
            return Annotated[int, PlainValidator(fieldtypes.duration)]
        case "Code":
            return Annotated[str, AfterValidator(fieldtypes.code)]
        case "JSON":
            return Annotated[dict | list, PlainValidator(fieldtypes.json_value)]
        case "TextEditor":
            return Annotated[str, AfterValidator(fieldtypes.rich_text)]
        case "Password":
            return Annotated[str, AfterValidator(fieldtypes.password)]
        case "Number":
            return _Number
        case "Decimal" | "Currency":
            # Chaîne canonique : voir decimals.py. Le nombre de décimales est celui du champ.
            return Annotated[str, PlainValidator(_decimal_validator(field.decimals or 0))]
        case "Boolean":
            return StrictBool
        case "Date":
            return _IsoDate
        case "Select":
            return Literal[tuple(field.options or [])]
        case "Link" | "DynamicLink":
            return _LinkTarget  # un DynamicLink est aussi un nom de document ; le DocType visé se lit dans son champ de type (document.py, `_check_links`)
    raise AssertionError(f"fieldtype non géré : {field.fieldtype}")  # pragma: no cover


def build_model(defn: DocTypeDef, row_models: Mapping[str, type[BaseModel]] | None = None) -> type[BaseModel]:
    spec: dict[str, Any] = {}
    for f in defn.fields:
        if f.fieldtype == "Table":
            # Chaque ligne est un petit document : mêmes types stricts, `extra="forbid"`, champs obligatoires du DocType enfant.
            tp = Annotated[list[(row_models or {})[str(f.options)]], Field(max_length=MAX_TABLE_ROWS)]
        else:
            tp = python_type(f)
        # Un champ que `depends_on` peut masquer n'est obligatoire QUE s'il est visible : voir CompiledDocType._conditions.
        default = canonical(f.default, f.decimals) if f.fieldtype in DECIMAL_TYPES and f.default is not None else f.default
        if default in (TODAY, fieldtypes.NOW):
            default = None  # « aujourd'hui » / « maintenant » ne se figent pas dans le modèle : le service les résout à chaque création (document.py)
        spec[f.fieldname] = (tp, ...) if f.required and f.depends_on is None else (tp | None, default)
    safe_name = "DocType_" + re.sub(r"\W", "_", defn.name)
    return create_model(safe_name, __config__=ConfigDict(extra="forbid"), **spec)


# Opérateurs de filtre autorisés par type de champ. Pas d'ordre sur le texte : il suivrait la collation de chaque base
# (SQLite binaire, PostgreSQL localisée) ; les dates ISO canoniques, elles, se trient identiquement partout.
FILTER_OPS: dict[str, frozenset[str]] = {
    "Text": frozenset({"=", "!=", "like", "in"}),
    "Select": frozenset({"=", "!=", "in"}),
    "Link": frozenset({"=", "!=", "like", "in"}),
    "DynamicLink": frozenset({"=", "!=", "like", "in"}),
    "Number": frozenset({"=", "!=", ">", ">=", "<", "<=", "in"}),
    "Decimal": frozenset({"=", "!=", ">", ">=", "<", "<=", "in"}),
    "Currency": frozenset({"=", "!=", ">", ">=", "<", "<=", "in"}),
    "Rating": frozenset({"=", "!=", ">", ">=", "<", "<=", "in"}),
    "Datetime": frozenset({"=", "!=", ">", ">=", "<", "<=", "in"}),
    "LongText": frozenset({"like"}),
    "Int": frozenset({"=", "!=", ">", ">=", "<", "<=", "in"}),
    "Percent": frozenset({"=", "!=", ">", ">=", "<", "<=", "in"}),
    "Duration": frozenset({"=", "!=", ">", ">=", "<", "<=", "in"}),
    "Time": frozenset({"=", "!=", ">", ">=", "<", "<=", "in"}),
    "Code": frozenset({"like"}),
    "TextEditor": frozenset(),  # du HTML ne se filtre pas : on chercherait dans les balises
    "JSON": frozenset(),
    "Password": frozenset(),  # un secret ne se compare jamais
    "Color": frozenset({"=", "!=", "in"}),
    "Image": frozenset(),  # un identifiant de fichier ne se filtre pas
    "Attach": frozenset(),
    "Date": frozenset({"=", "!=", ">", ">=", "<", "<=", "in"}),
    "Boolean": frozenset({"=", "!="}),
}
# Colonnes système filtrables, avec le type dont elles empruntent les opérateurs. `docstatus` : 0 brouillon, 1 soumis, 2 annulé.
SYSTEM_FILTERS: dict[str, str] = {"name": "Text", "docstatus": "Number"}
MAX_IN = 100
MAX_LIKE = 100
_SYSTEM_ADAPTERS: dict[str, TypeAdapter] = {
    "name": TypeAdapter(str),
    "docstatus": TypeAdapter(Annotated[int, Field(ge=0, le=2, strict=True)]),
}


@dataclass(frozen=True, slots=True)
class Filter:
    """Un filtre autre que l'égalité simple. `op` : `!=`, `like` (contient, sans casse), `>`, `>=`, `<`, `<=`, `in`."""

    op: str
    value: Any


@dataclass(frozen=True, slots=True)
class CompiledDocType:
    definition: DocTypeDef
    model: type[BaseModel]
    adapters: Mapping[str, TypeAdapter]
    # fieldname -> {"depends_on" | "mandatory_depends_on" | "read_only_depends_on" -> arbre}
    conditions: Mapping[str, Mapping[str, Node]]
    # DocTypes enfants des champs Table de celui-ci, par nom (vide sans champ Table).
    children: Mapping[str, DocTypeDef] = dataclass_field(default_factory=dict)
    # Conditions des champs de LIGNE : DocType enfant -> fieldname -> {"depends_on" | "mandatory_depends_on" -> arbre}. Elles lisent la
    # ligne et le parent (`parent_<champ>`), d'où leur compilation ICI, avec les champs de CE parent : le même enfant peut servir ailleurs.
    row_conditions: Mapping[str, Mapping[str, Mapping[str, Node]]] = dataclass_field(default_factory=dict)

    @classmethod
    def compile(cls, defn: DocTypeDef, children: Mapping[str, DocTypeDef] | None = None) -> "CompiledDocType":
        """`children` : les définitions des DocTypes de lignes que `defn` cite (voir `DocTypeRegistry.register`)."""
        children = dict(children or {})
        names = [f.fieldname for f in defn.fields]
        conditions = {}
        # Les conditions d'un champ de LIGNE se compilent avec leur parent (`row_conditions`, plus bas) : seule la table sait ce que `parent_x` désigne.
        for f in () if defn.istable else defn.fields:
            mine = {
                kind: compile_expr(getattr(f, kind), names)
                for kind in ("depends_on", "mandatory_depends_on", "read_only_depends_on")
                if getattr(f, kind) is not None
            }
            if mine:
                conditions[f.fieldname] = mine
        row_models = {name: build_model(child) for name, child in children.items()}
        parent_names = [PARENT_PREFIX + p.fieldname for p in defn.fields if p.fieldtype != "Table" and p.fieldtype not in DECIMAL_TYPES]
        row_conditions: dict[str, dict[str, dict[str, Node]]] = {}
        for cname, child in children.items():
            row_names = [f.fieldname for f in child.fields] + parent_names
            per_field = {}
            for cf in child.fields:
                mine = {
                    kind: compile_expr(getattr(cf, kind), row_names)
                    for kind in ("depends_on", "mandatory_depends_on")
                    if getattr(cf, kind) is not None
                }
                if mine:
                    per_field[cf.fieldname] = mine
            if per_field:
                row_conditions[cname] = per_field
        return cls(
            defn,
            build_model(defn, row_models),
            {f.fieldname: TypeAdapter(python_type(f)) for f in defn.fields if f.fieldtype != "Table"},
            conditions,
            children,
            row_conditions,
        )

    def validate_data(self, payload: Any, *, old: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Valide un payload d'écriture et retourne le JSON à stocker (les `None` sont omis).

        `old` : le document avant modification. Fourni pour l'entrée d'un CLIENT, il active `read_only_depends_on` ; il ne
        l'est pas pour le résultat d'un hook, qui a le droit de poser un champ que l'interface verrouille.
        """
        if not isinstance(payload, dict):
            raise ValidationFailed("`data` doit être un objet")
        try:
            clean = self.model.model_validate(payload).model_dump(mode="json", exclude_none=True)
        except ValidationError as e:
            raise ValidationFailed(details=_errors(e)) from e
        for f in self.definition.fields:
            if f.fieldtype == "Table" and clean.get(f.fieldname) == []:
                del clean[f.fieldname]  # « aucune ligne » et « champ absent » sont la même chose : un seul état stocké
        errors = self.rule_errors(clean, old)
        if errors:
            raise ValidationFailed(details=errors)
        return clean

    def rule_errors(self, clean: Mapping[str, Any], old: Mapping[str, Any] | None) -> list[dict[str, str]]:
        """Ce que `validate_data` refuse APRÈS le schéma : conditions, obligations, champs figés, conditions des lignes. Aussi ce que l'écran « Fichiers »
        demande avant de proposer de vider un champ (`FileLibrary._may_empty`) : une seule logique, pas une copie qui divergerait."""
        return self._condition_errors(clean, old) + self._row_errors(clean)

    def _condition_errors(self, clean: Mapping[str, Any], old: Mapping[str, Any] | None) -> list[dict[str, str]]:
        """Revérifie côté serveur ce que l'interface applique (D22). Un client ne peut pas contourner l'obligation."""
        errors = []
        secrets = self.definition.secret_fields
        for f in self.definition.fields:
            cond = self.conditions.get(f.fieldname, {})
            visible = evaluate(cond["depends_on"], clean) if "depends_on" in cond else True
            required = (f.required and visible) or ("mandatory_depends_on" in cond and evaluate(cond["mandatory_depends_on"], clean))
            if required and f.fieldname not in clean:
                errors.append({"field": f.fieldname, "msg": "Champ obligatoire", "type": "missing"})
            if old is None:
                continue
            # Sur un secret, le masque renvoyé tel qu'on l'a lu est « inchangé » (`sealing.kept`), jamais comparé au secret chiffré.
            new, before = clean.get(f.fieldname), old.get(f.fieldname)
            changed = new != before and not (f.fieldname in secrets and kept(new, before))
            # `read_only` (toujours) ou `read_only_depends_on` (vrai sur l'état AVANT) : un client ne change pas le champ ; un hook, qui n'a pas `old`, le peut.
            locked = f.read_only or ("read_only_depends_on" in cond and evaluate(cond["read_only_depends_on"], old))
            if locked and changed:
                errors.append({"field": f.fieldname, "msg": "Champ en lecture seule", "type": "read_only"})
            # `set_only_once` : libre tant qu'il est vide, figé dès qu'il a une valeur. Le champ qui NOMME le document l'est d'office (le nom ne bouge pas).
            elif before is not None and changed and self._set_once(f):
                errors.append({"field": f.fieldname, "msg": "Champ non modifiable une fois renseigné", "type": "set_only_once"})
        return errors

    def _set_once(self, f: FieldDef) -> bool:
        naming = self.definition.naming
        return f.set_only_once or (isinstance(naming, NamingField) and naming.field == f.fieldname)

    def _row_errors(self, clean: Mapping[str, Any]) -> list[dict[str, str]]:
        """Les conditions des champs de ligne, réévaluées côté serveur : une cellule rendue obligatoire par la nature du document
        (`mandatory_depends_on: "parent_purpose == 'Sortie'"`) ne peut pas manquer, quoi que le client ait envoyé."""
        errors: list[dict[str, str]] = []
        parent = {PARENT_PREFIX + k: v for k, v in clean.items() if not isinstance(v, list)}
        for f in self.definition.fields:
            child = self.children.get(str(f.options)) if f.fieldtype == "Table" else None
            conds = self.row_conditions.get(child.name) if child else None
            if not child or not conds:
                continue
            for i, row in enumerate(clean.get(f.fieldname) or []):
                ctx = {**row, **parent}
                for cf in child.fields:
                    c = conds.get(cf.fieldname, {})
                    visible = evaluate(c["depends_on"], ctx) if "depends_on" in c else True
                    required = (cf.required and visible) or ("mandatory_depends_on" in c and evaluate(c["mandatory_depends_on"], ctx))
                    if required and cf.fieldname not in row:
                        errors.append({"field": f"{f.fieldname}.{i}.{cf.fieldname}", "msg": "Champ obligatoire", "type": "missing"})
        return errors

    def coerce_filter(self, fieldname: str, value: Any) -> Any:
        """Valide la valeur d'un filtre avec le type du champ (jamais de SQL construit sur une chaîne brute).

        `value` est une valeur (égalité) ou `[opérateur, valeur]`. Retourne la valeur validée, ou un `Filter`.
        """
        # `name` (l'identifiant du document) se filtre comme un texte : le Desk s'en sert pour retrouver le titre d'un lien.
        adapter = _SYSTEM_ADAPTERS[fieldname] if fieldname in SYSTEM_FILTERS else self.adapters[fieldname]
        ftype = SYSTEM_FILTERS[fieldname] if fieldname in SYSTEM_FILTERS else self.definition.field_map[fieldname].fieldtype
        try:
            if isinstance(value, list):
                if len(value) != 2 or not isinstance(value[0], str):
                    raise ValueError("un filtre à opérateur s'écrit [opérateur, valeur]")
                op, operand = value
                if op not in FILTER_OPS[ftype]:
                    raise ValueError(f"opérateur {op!r} impossible sur {ftype} (permis : {', '.join(sorted(FILTER_OPS[ftype]))})")
                if op == "=":
                    return adapter.validate_python(operand)
                if op == "like":
                    if not isinstance(operand, str) or not operand or len(operand) > MAX_LIKE:
                        raise ValueError(f"`like` exige un texte de 1 à {MAX_LIKE} caractères")
                    return Filter("like", operand)
                if op == "in":
                    if not isinstance(operand, list) or len(operand) > MAX_IN:
                        raise ValueError(f"`in` exige une liste d'au plus {MAX_IN} valeurs")
                    return Filter("in", [adapter.validate_python(v) for v in operand])
                return Filter(op, adapter.validate_python(operand))  # != > >= < <=
            if "=" not in FILTER_OPS[ftype]:
                raise ValueError(f"l'égalité est impossible sur {ftype} (permis : {', '.join(sorted(FILTER_OPS[ftype]))})")
            return adapter.validate_python(value)
        except ValidationError as e:
            raise ValidationFailed(f"filtre invalide sur {fieldname!r}", details=_errors(e)) from e
        except ValueError as e:
            raise ValidationFailed(f"filtre invalide sur {fieldname!r} : {e}") from None


def _errors(e: ValidationError) -> list[dict[str, str]]:
    # Ni `input` ni `ctx` : une valeur refusée peut être sensible.
    return [
        {"field": ".".join(str(p) for p in err["loc"]), "msg": err["msg"], "type": err["type"]}
        for err in e.errors(include_url=False, include_input=False, include_context=False)
    ]
