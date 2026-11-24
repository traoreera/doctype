"""Imprimer des données qui ne sont PAS un document stocké : états, registres (traoreera/doctype#61, process/impression.md §15).

Une balance, un bilan, un grand livre ou un livre de paie ne sont pas des documents : un plugin les CALCULE. Avant ce
module ils ne pouvaient pas passer par l'impression du moteur (`print_service._printable` refuse ce qu'il ne stocke pas), et
l'ERP fabriquait à la volée une définition de type déduite des données, réimplémentait les contrôles de gabarit et écrivait
le livre de paie en HTML à la main.

Ici, PUR comme `printing.py` (rien n'est lu ni écrit) :

- un **schéma léger** (`DataSchema`) : des champs, leurs types, et des tables de colonnes. Ce n'est pas un DocType : ni
  stockage, ni droits, ni hooks, ni route. Le vocabulaire des types est FERMÉ (`DATA_TYPES`) — ce qui s'imprime, et rien
  qui exigerait une lecture (un `Link` demanderait un titre, donc un droit : un état imprime des valeurs déjà résolues) ;
- une **source d'impression** (`PrintSource`) : ce schéma, compilé UNE fois en définitions que `printing.render_html`,
  `template_fields_problems` et `generated_template` savent déjà lire. Aucun second moteur de rendu, aucun second contrôle
  de gabarit : un état et une facture sortent de la même chaîne, éditeur de gabarits du Desk compris ;
- le **contrôle des données** (`data_problems`) : une clé que le schéma ne connaît pas est une faute (elle s'imprimerait
  comme une case vide, sans un mot), une table qui n'est pas une liste de lignes aussi. Les VALEURS, elles, ne sont pas
  converties : imprimer c'est lire, et les lectures sont tolérantes.

Le nom d'une source est `<plugin>.<état>` (`accounts.balance`). Le point n'existe dans aucun nom de DocType
(`schema._DocTypeName`) : une source ne peut donc ni masquer un type ni être masquée par lui, et la cible de son gabarit
d'organisation (`<source>:<gabarit>`, `print_service.target_of`) ne se confond avec celle d'aucun type.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Annotated, Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, StringConstraints, ValidationError
from pydantic import Field as PydanticField

from .errors import InvalidDefinition
from .schema import DocTypeDef, parse_definition

# Ce qu'un état peut porter. Fermé : chaque type ajouté ici doit avoir un rendu dans `printing._cell`.
DATA_TYPES = ("Text", "LongText", "Int", "Number", "Percent", "Decimal", "Currency", "Date", "Datetime", "Boolean", "Select")
TABLE = "Table"
MAX_DATA_FIELDS = 200
MAX_DATA_COLUMNS = 40
MAX_DATA_ROWS = 5000  # un grand livre annuel tient ; au-delà c'est un export, pas une impression
SOURCE_NAME = re.compile(r"^([a-z][a-z0-9_]{0,39})\.([a-z][a-z0-9_]{0,59})$")

_FieldName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,62}$")]
_Label = Annotated[str, StringConstraints(min_length=1, max_length=140)]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DataColumn(_Model):
    """Une valeur d'un état : un champ de tête, ou une colonne d'une de ses tables."""

    fieldname: _FieldName
    fieldtype: Literal["Text", "LongText", "Int", "Number", "Percent", "Decimal", "Currency", "Date", "Datetime", "Boolean", "Select"]
    label: _Label | None = None
    decimals: int | None = PydanticField(default=None, ge=0, le=9)  # obligatoire pour Decimal et Currency, comme sur un DocType
    options: str | None = None  # Currency : le code de la devise ; Select : les choix (un par ligne)
    currency_from: str | None = None  # Currency : le champ qui porte le code (même état, ou `parent_<champ>` depuis une ligne)


class DataField(DataColumn):
    """Un champ de tête. `Table` : une liste de lignes, décrite par ses `columns`."""

    fieldtype: Literal["Text", "LongText", "Int", "Number", "Percent", "Decimal", "Currency", "Date", "Datetime", "Boolean", "Select", "Table"]  # type: ignore[assignment]
    columns: Annotated[list[DataColumn], PydanticField(min_length=1, max_length=MAX_DATA_COLUMNS)] | None = None


class DataSchema(_Model):
    """Le schéma léger d'un état : ce qu'un plugin déclare pour que le moteur sache l'imprimer."""

    schema_version: Literal[1]
    name: str
    label: _Label | None = None
    # Qui ouvre l'éditeur de gabarits de cet état au Desk, et qui enregistre le gabarit de son organisation : les porteurs de
    # CE grant (un grant que le plugin déclare déjà à auth). Absent : l'état n'a pas de gabarit d'organisation, seul le
    # plugin le met en page. Ce n'est PAS le droit de voir l'état : celui-là est jugé par la route du plugin, qui calcule.
    grant: Annotated[str, StringConstraints(pattern=r"^[a-z0-9_]+(:[a-z0-9_ -]+){1,3}$", max_length=140)] | None = None
    fields: Annotated[list[DataField], PydanticField(min_length=1, max_length=MAX_DATA_FIELDS)]
    # Des données d'EXEMPLE, pour l'aperçu de l'éditeur : un état n'a pas de « dernier document » à montrer.
    example: dict[str, Any] | None = None


@dataclass(frozen=True)
class PrintSource:
    """Un état prêt à imprimer. Porte ce que `PrintService` lit d'un type compilé (`definition`, `children`,
    `children_by_field`) : le service n'a qu'UN chemin de rendu, pour un document comme pour un état."""

    name: str
    owner: str
    schema: DataSchema
    definition: DocTypeDef
    children: Mapping[str, DocTypeDef]  # par nom du type de lignes (ce que cite `options` d'un champ Table)
    children_by_field: Mapping[str, DocTypeDef]  # par champ Table

    @property
    def grant(self) -> str | None:
        return self.schema.grant

    @property
    def example(self) -> dict[str, Any]:
        return dict(self.schema.example or {})


def _column(raw: DataColumn) -> dict[str, Any]:
    return {k: v for k, v in raw.model_dump(exclude={"columns"}).items() if v is not None}


def compile_source(raw: object, *, owner: str, source: str = "<état>") -> PrintSource:
    """Valide le schéma d'un état et le compile, ou lève `InvalidDefinition` — au CHARGEMENT, jamais à la première impression."""
    if not isinstance(raw, Mapping):
        raise InvalidDefinition(f"{source}: le schéma d'un état doit être un objet")
    try:
        schema = DataSchema.model_validate(dict(raw))
    except ValidationError as e:
        lines = [f"  - {'.'.join(str(p) for p in err['loc']) or '(racine)'}: {err['msg']}" for err in e.errors(include_url=False, include_input=False)]
        raise InvalidDefinition(f"{source}: schéma d'état invalide\n" + "\n".join(lines)) from e
    found = SOURCE_NAME.match(schema.name)
    if found is None:
        raise InvalidDefinition(f"{source}: le nom d'un état est `<plugin>.<état>` en minuscules (ex. accounts.balance), pas {schema.name!r}")
    if found.group(1) != owner:
        raise InvalidDefinition(f"{source}: l'état {schema.name!r} doit commencer par le nom du plugin qui l'apporte ({owner!r})")

    seen: set[str] = set()
    children: dict[str, DocTypeDef] = {}
    by_field: dict[str, DocTypeDef] = {}
    fields: list[dict[str, Any]] = []
    for f in schema.fields:
        if f.fieldname in seen:
            raise InvalidDefinition(f"{source}: le champ {f.fieldname!r} est déclaré deux fois")
        seen.add(f.fieldname)
        if f.fieldtype != TABLE:
            if f.columns is not None:
                raise InvalidDefinition(f"{source}: {f.fieldname!r} n'est pas une table : `columns` n'a pas de sens")
            fields.append(_column(f))
            continue
        if not f.columns:
            raise InvalidDefinition(f"{source}: la table {f.fieldname!r} doit déclarer ses `columns`")
        names = [c.fieldname for c in f.columns]
        if len(set(names)) != len(names):
            raise InvalidDefinition(f"{source}: la table {f.fieldname!r} déclare deux fois la même colonne")
        # Le nom du type de lignes ne sort jamais du moteur : il n'est ni enregistré ni adressable. Il doit seulement
        # satisfaire `_DocTypeName` et être unique dans l'état.
        child_name = f"Etat {f.fieldname}"[:63]
        child = _definition({"schema_version": 1, "name": child_name, "istable": True, "fields": [_column(c) for c in f.columns]}, source)
        children[child_name] = child
        by_field[f.fieldname] = child
        fields.append({"fieldname": f.fieldname, "fieldtype": TABLE, "options": child_name, **({"label": f.label} if f.label else {})})
    parent = _definition({"schema_version": 1, "name": "Etat", "fields": fields}, source)
    # `DocTypeDef.name` refuse le point : la définition est validée sous un nom neutre, puis NOMMÉE comme la source —
    # c'est sous ce nom que le rendu, les gabarits (`template.doctype`) et l'habillage de l'organisation la désignent.
    definition = parent.model_copy(update={"name": schema.name, "label": schema.label or schema.name})
    made = PrintSource(schema.name, owner, schema, definition, children, by_field)
    if schema.example is not None and (problems := data_problems(made, schema.example)):
        raise InvalidDefinition(f"{source}: `example` ne suit pas le schéma :\n" + "\n".join(f"  - {p}" for p in problems[:8]))
    return made


def _definition(raw: dict[str, Any], source: str) -> DocTypeDef:
    """Une définition TRANSITOIRE, par le même analyseur qu'un DocType : mêmes refus (`decimals` manquant, devise…)."""
    return parse_definition(raw, source=source)


def data_problems(source: PrintSource, data: object) -> list[str]:
    """Ce qui empêche d'imprimer ces données avec ce schéma. Vide : elles s'impriment."""
    if not isinstance(data, Mapping):
        return ["les données d'un état sont un objet {champ: valeur}"]
    problems: list[str] = []
    fields = source.definition.field_map
    for key, value in data.items():
        f = fields.get(key) if isinstance(key, str) else None
        if f is None:
            problems.append(f"{key!r} n'est pas un champ de {source.name}")
        elif f.fieldtype == TABLE:
            problems += _rows_problems(key, value, source.children_by_field[key])
        elif isinstance(value, (Mapping, list, tuple, set)):
            problems.append(f"{key}: une valeur simple est attendue, pas {type(value).__name__}")
    return problems


def _rows_problems(key: str, rows: object, child: DocTypeDef) -> list[str]:
    if rows is None:
        return []
    if not isinstance(rows, (list, tuple)):
        return [f"{key}: une table est une liste de lignes"]
    if len(rows) > MAX_DATA_ROWS:
        return [f"{key}: {len(rows)} lignes, {MAX_DATA_ROWS} au plus (au-delà c'est un export, pas une impression)"]
    columns = child.field_map
    problems: list[str] = []
    for i, row in enumerate(rows):
        if not isinstance(row, Mapping):
            problems.append(f"{key}.{i}: une ligne est un objet {{colonne: valeur}}")
        else:
            problems += [f"{key}.{i}.{c}: colonne inconnue" for c in row if c not in columns]
        if len(problems) >= 20:
            break  # un état de 5000 lignes mal formé ne rend pas 5000 messages
    return problems
