"""Impression : le vocabulaire des gabarits et le rendu HTML (process/impression.md).

Ce module est PUR : il reçoit un DocType compilé, les données d'un document et un gabarit, il rend une chaîne
HTML. Il ne touche ni la base, ni les droits, ni le moteur PDF. C'est le découpage de `mapping.py` contre
`document.py`, et il se justifie pareil — l'HTML se teste sur des dictionnaires, à la ligne près, sans binaire.

Trois règles gouvernent tout :

- **Aucun code.** Sept types de blocs, fermés. La substitution est celle des règles de notification
  (`notification_rules.render`) : `{{ doc.<champ> }}`, motif linéaire, valeur bornée, **échappée**. On n'ouvre
  pas Jinja — c'est la décision prise pour les notifications, pour `compute:` (programme §3.7) et pour les
  extensions du Desk.
- **Pas de CSS grid**, parce que le moteur de rendu ne le sait pas (PlutoPrint, mesuré) : le gabarit est une
  PILE de blocs et la mise en page passe par le flux et les tableaux. La contrainte est tenue par construction,
  pas par surveillance.
- **Les sous-totaux sont exacts.** Une somme de colonne se fait en `decimal.Decimal` et sort à l'échelle du
  champ : on ne fait pas entrer un flottant dans un montant.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from html import escape
from typing import Annotated, Any, Literal, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field as PydanticField, StringConstraints, ValidationError, model_validator

from .decimals import canonical, to_decimal
from .errors import InvalidDefinition
from .notification_rules import SPECIALS, render as render_text, template_problems
from .schema import DECIMAL_TYPES, FILE_TYPES, DocTypeDef

# Un gabarit reste lisible d'un coup d'œil : au-delà, ce n'est plus une facture.
MAX_PRINT_BLOCKS = 100
MAX_PRINT_COLUMNS = 12
MAX_PRINT_FIELDS = 60  # champs d'un bloc `fields`
MAX_TEXT = 2000

# Ce qu'un bloc ne peut pas imprimer : un secret, un fichier, une table (elle a son bloc), une structure.
UNPRINTABLE = ("Password", "JSON", "TextEditor", *FILE_TYPES)
# Ce qu'une colonne de tableau peut sommer, et ce qu'un `group_by` peut regrouper.
NUMERIC = ("Number", "Int", "Percent", *DECIMAL_TYPES)
GROUPABLE = ("Text", "Select", "Link", "DynamicLink", "Date", "Boolean", "Int")

_Name = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,62}$")]
_Fieldname = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,62}$")]
_Text = Annotated[str, StringConstraints(min_length=1, max_length=MAX_TEXT)]

# Les variables que l'impression connaît. `notification_rules.SPECIALS` en contient d'autres (`event`, `actor`,
# `link`) qui n'ont aucun sens sur un document imprimé : on les REFUSE au chargement plutôt que de les laisser
# s'imprimer littéralement, ce que ferait la substitution pour un jeton qu'elle ne sait pas remplacer.
PRINT_SPECIALS = ("doctype", "name")
_OUT_OF_PLACE = tuple(s for s in SPECIALS if s not in PRINT_SPECIALS)
_TOKEN = re.compile(r"\{\{([^{}]*)\}\}")


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PageSetup(_Model):
    size: Literal["A3", "A4", "A5", "B4", "B5", "Letter", "Legal", "Ledger"] = "A4"
    orientation: Literal["portrait", "landscape"] = "portrait"
    # En millimètres. Jamais négatives : une marge négative sort de la page.
    top: Annotated[int, PydanticField(ge=0, le=100)] = 20
    bottom: Annotated[int, PydanticField(ge=0, le=100)] = 20
    left: Annotated[int, PydanticField(ge=0, le=100)] = 15
    right: Annotated[int, PydanticField(ge=0, le=100)] = 15


class TitleBlock(_Model):
    type: Literal["title"]
    text: _Text


class FieldsBlock(_Model):
    """Des champs en colonnes. Un TABLEAU sans bordure, pas une grille CSS."""

    type: Literal["fields"]
    columns: Annotated[int, PydanticField(ge=1, le=4)] = 2
    fields: Annotated[list[_Fieldname], PydanticField(min_length=1, max_length=MAX_PRINT_FIELDS)]
    label: str | None = None


class TableColumn(_Model):
    field: _Fieldname
    label: str | None = None
    align: Literal["left", "right", "center"] | None = None
    # Largeur en POURCENT de la table : leur somme ne dépasse pas 100 (vérifié au chargement).
    width: Annotated[int, PydanticField(ge=1, le=100)] | None = None


class TableBlock(_Model):
    type: Literal["table"]
    field: _Fieldname  # le champ Table du document
    columns: Annotated[list[TableColumn], PydanticField(min_length=1, max_length=MAX_PRINT_COLUMNS)]
    style: Literal["bordered", "ruled", "plain"] = "bordered"
    density: Literal["compact", "normal"] = "normal"
    # L'en-tête se répète à chaque page : c'est du CSS paginé (`table-header-group`), vérifié contre le moteur.
    repeat_header: bool = True
    group_by: _Fieldname | None = None
    subtotal: list[_Fieldname] = PydanticField(default_factory=list)
    label: str | None = None

    @model_validator(mode="after")
    def _check(self) -> "TableBlock":
        widths = [c.width for c in self.columns if c.width is not None]
        if sum(widths) > 100:
            raise ValueError("columns: la somme des largeurs dépasse 100 %")
        if self.subtotal and self.group_by is None:
            raise ValueError("subtotal: exige `group_by` (un sous-total se calcule par groupe)")
        named = {c.field for c in self.columns}
        if missing := [f for f in self.subtotal if f not in named]:
            raise ValueError(f"subtotal: {', '.join(missing)} ne figure pas dans `columns`")
        return self


class TotalsBlock(_Model):
    """Les montants, alignés à droite. Le dernier est mis en évidence."""

    type: Literal["totals"]
    fields: Annotated[list[_Fieldname], PydanticField(min_length=1, max_length=MAX_PRINT_FIELDS)]
    label: str | None = None


class TextBlock(_Model):
    type: Literal["text"]
    text: _Text


class SpacerBlock(_Model):
    type: Literal["spacer"]
    size: Annotated[int, PydanticField(ge=1, le=100)] = 5  # millimètres


class PageBreakBlock(_Model):
    type: Literal["page_break"]


PrintBlock = TitleBlock | FieldsBlock | TableBlock | TotalsBlock | TextBlock | SpacerBlock | PageBreakBlock


class LetterheadRef(_Model):
    """Le papier à en-tête : un `Letter Head` par son nom, ou un PDF de FOND sur lequel on imprime."""

    name: str | None = None
    # L'identifiant d'un fichier `xdt_file` du tenant. JAMAIS une URL ni un chemin (process/impression.md §6).
    pdf: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{32}$")] | None = None
    pages: Literal["first", "all"] = "all"
    # Les zones (mm) que le fond occupe : le contenu s'y interdit, en s'ajoutant aux marges de la page.
    offset_top: Annotated[int, PydanticField(ge=0, le=150)] = 0
    offset_bottom: Annotated[int, PydanticField(ge=0, le=150)] = 0

    @model_validator(mode="after")
    def _check(self) -> "LetterheadRef":
        if self.name is not None and self.pdf is not None:
            raise ValueError("un papier à en-tête est un `Letter Head` OU un PDF de fond, pas les deux")
        return self


class PrintTemplate(_Model):
    schema_version: Literal[1]
    doctype: str
    name: _Name = "standard"  # plusieurs gabarits par type : « standard », « duplicata »…
    label: str | None = None
    page: PageSetup = PydanticField(default_factory=PageSetup)
    letterhead: LetterheadRef | None = None
    blocks: Annotated[list[Annotated[PrintBlock, PydanticField(discriminator="type")]],
                      PydanticField(min_length=1, max_length=MAX_PRINT_BLOCKS)]


def parse_template(raw: object, *, source: str = "<gabarit>") -> PrintTemplate:
    """Valide un gabarit brut (YAML/JSON) ou lève `InvalidDefinition`, comme `schema.parse_definition`."""
    if not isinstance(raw, Mapping):
        raise InvalidDefinition(f"{source}: un gabarit d'impression doit être un objet")
    try:
        return PrintTemplate.model_validate(dict(raw))
    except ValidationError as e:
        lines = [f"  - {'.'.join(str(p) for p in err['loc']) or '(racine)'}: {err['msg']}" for err in e.errors()]
        raise InvalidDefinition(f"{source}: gabarit d'impression invalide\n" + "\n".join(lines)) from e


# ── Refus au CHARGEMENT ───────────────────────────────────────────────────────


def template_fields_problems(template: PrintTemplate, definition: DocTypeDef, child_of: Any) -> list[str]:
    """Tout ce qui rendrait une impression impossible, dit AU CHARGEMENT (process/impression.md §8).

    `child_of(table_fieldname)` rend la définition du DocType de lignes, ou `None`. Rien n'est vérifié à la
    première impression : un gabarit qui cite un champ absent est une faute de définition.
    """
    problems: list[str] = []
    fields = definition.field_map
    specials = {s: "" for s in SPECIALS}

    def check_field(where: str, name: str, *, allow: Sequence[str] | None = None) -> None:
        f = fields.get(name)
        if f is None:
            problems.append(f"{where}: {name!r} n'est pas un champ de {definition.name}")
        elif f.fieldtype in UNPRINTABLE:
            problems.append(f"{where}: un champ {f.fieldtype} ne s'imprime pas ({name!r})")
        elif f.fieldtype == "Table":
            problems.append(f"{where}: {name!r} est une table, elle a son propre bloc")
        elif allow is not None and f.fieldtype not in allow:
            problems.append(f"{where}: {name!r} est un {f.fieldtype}, attendu : {', '.join(allow)}")

    for i, block in enumerate(template.blocks):
        at = f"blocks.{i} ({block.type})"
        if isinstance(block, (TitleBlock, TextBlock)):
            problems += [f"{at}: {p}" for p in template_problems(block.text, fields)]
            for m in _TOKEN.finditer(block.text):
                if m.group(1).strip() in _OUT_OF_PLACE:
                    problems.append(f"{at}: {{{{ {m.group(1).strip()} }}}} n'existe pas sur un document imprimé "
                                    f"(disponibles : {', '.join(PRINT_SPECIALS)} ou doc.<champ>)")
        elif isinstance(block, FieldsBlock):
            for name in block.fields:
                check_field(at, name)
        elif isinstance(block, TotalsBlock):
            for name in block.fields:
                check_field(at, name, allow=NUMERIC)
        elif isinstance(block, TableBlock):
            problems += _table_problems(at, block, definition, child_of)
    return problems


def _table_problems(at: str, block: TableBlock, definition: DocTypeDef, child_of: Any) -> list[str]:
    problems: list[str] = []
    parent_field = definition.field_map.get(block.field)
    if parent_field is None or parent_field.fieldtype != "Table":
        problems.append(f"{at}: {block.field!r} n'est pas un champ Table de {definition.name}")
        return problems
    child = child_of(block.field)
    if child is None:
        return problems  # le DocType de lignes manquant est déjà signalé par la compilation
    rows = child.field_map
    for col in block.columns:
        f = rows.get(col.field)
        if f is None:
            problems.append(f"{at}: la colonne {col.field!r} n'est pas un champ de {child.name}")
        elif f.fieldtype in UNPRINTABLE or f.fieldtype == "Table":
            problems.append(f"{at}: une colonne {f.fieldtype} ne s'imprime pas ({col.field!r})")
    if block.group_by is not None:
        g = rows.get(block.group_by)
        if g is None:
            problems.append(f"{at}: group_by: {block.group_by!r} n'est pas un champ de {child.name}")
        elif g.fieldtype not in GROUPABLE:
            problems.append(f"{at}: group_by: un champ {g.fieldtype} ne regroupe pas ({block.group_by!r})")
    for name in block.subtotal:
        f = rows.get(name)
        if f is not None and f.fieldtype not in NUMERIC:
            problems.append(f"{at}: subtotal: {name!r} est un {f.fieldtype}, pas un nombre")
    return problems


# ── Rendu ─────────────────────────────────────────────────────────────────────


def _label(field: Any, fallback: str) -> str:
    return escape(getattr(field, "label", None) or fallback)


def _cell(value: Any, field: Any) -> str:
    """La valeur d'un champ, en texte ÉCHAPPÉ. Un Decimal garde sa chaîne canonique : il ne passe pas par un float."""
    if value is None or isinstance(value, (list, dict)):
        return ""
    if isinstance(value, bool):
        return "Oui" if value else "Non"
    return escape(str(value))


def _sum(rows: Sequence[Mapping[str, Any]], name: str, field: Any) -> str:
    """La somme d'une colonne, EXACTE (`Decimal`), rendue à l'échelle du champ."""
    total = Decimal(0)
    for row in rows:
        raw = row.get(name)
        if raw is None:
            continue
        try:
            total += to_decimal(raw)
        except (ArithmeticError, InvalidOperation, ValueError, TypeError):
            continue  # une valeur illisible dans une ligne déjà écrite ne casse pas l'impression
    if field is not None and field.fieldtype in DECIMAL_TYPES:
        return escape(canonical(total, int(field.decimals or 0)))
    if field is not None and field.fieldtype == "Int":
        return escape(str(int(total)))
    return escape(f"{float(total):g}")


def render_html(
    template: PrintTemplate,
    definition: DocTypeDef,
    data: Mapping[str, Any],
    *,
    name: str,
    children: Mapping[str, Any] | None = None,
    letterhead_html: str = "",
    css_extra: str = "",
) -> str:
    """Le document, en HTML prêt pour le moteur PDF. Pur : rien n'est lu ni écrit.

    `children` : par champ Table, la définition du DocType de ses lignes (pour les libellés et les échelles).
    `letterhead_html` : l'en-tête déjà rendu par l'appelant (lui seul sait lire un fichier du tenant).
    """
    fields = definition.field_map
    values = {"doctype": definition.label or definition.name, "name": name}
    body: list[str] = []
    for block in template.blocks:
        body.append(_render_block(block, definition, data, fields, children or {}, values))
    return (
        "<!doctype html>\n<html><head><meta charset=\"utf-8\"><title>"
        + escape(f"{definition.label or definition.name} {name}")
        + "</title><style>"
        + _stylesheet(template.page)
        + escape(css_extra, quote=False)
        + "</style></head><body>"
        + letterhead_html
        + "".join(body)
        + "</body></html>"
    )


def _render_block(block: PrintBlock, definition: DocTypeDef, data: Mapping[str, Any],
                  fields: Mapping[str, Any], children: Mapping[str, Any], values: Mapping[str, str]) -> str:
    if isinstance(block, TitleBlock):
        return f"<h1>{escape(render_text(block.text, values, data))}</h1>"
    if isinstance(block, TextBlock):
        return f"<p class=t>{escape(render_text(block.text, values, data))}</p>"
    if isinstance(block, SpacerBlock):
        return f'<div style="height:{block.size}mm"></div>'
    if isinstance(block, PageBreakBlock):
        return '<div class="pb"></div>'
    if isinstance(block, FieldsBlock):
        return _render_fields(block, data, fields)
    if isinstance(block, TotalsBlock):
        return _render_totals(block, data, fields)
    if isinstance(block, TableBlock):
        return _render_table(block, data, children.get(block.field))
    return ""


def _render_fields(block: FieldsBlock, data: Mapping[str, Any], fields: Mapping[str, Any]) -> str:
    cells = [
        f"<td><span class=k>{_label(fields.get(n), n)}</span><br>{_cell(data.get(n), fields.get(n))}</td>"
        for n in block.fields
    ]
    rows = [cells[i : i + block.columns] for i in range(0, len(cells), block.columns)]
    pad = lambda r: r + ["<td></td>"] * (block.columns - len(r))  # noqa: E731 — une ligne incomplète garde ses colonnes
    head = f"<h2>{escape(block.label)}</h2>" if block.label else ""
    return head + '<table class="fields">' + "".join(f"<tr>{''.join(pad(r))}</tr>" for r in rows) + "</table>"


def _render_totals(block: TotalsBlock, data: Mapping[str, Any], fields: Mapping[str, Any]) -> str:
    last = len(block.fields) - 1
    rows = []
    for i, n in enumerate(block.fields):
        strong = ' class="grand"' if i == last else ""
        rows.append(f"<tr{strong}><td>{_label(fields.get(n), n)}</td><td class=r>{_cell(data.get(n), fields.get(n))}</td></tr>")
    return '<table class="totaux">' + "".join(rows) + "</table>"


def _render_table(block: TableBlock, data: Mapping[str, Any], child: Any) -> str:
    rows = data.get(block.field)
    rows = [r for r in rows if isinstance(r, Mapping)] if isinstance(rows, list) else []
    child_fields = child.field_map if child is not None else {}
    head = "".join(
        f'<th class={c.align or "left"}'
        + (f' style="width:{c.width}%"' if c.width else "")
        + f">{escape(c.label) if c.label else _label(child_fields.get(c.field), c.field)}</th>"
        for c in block.columns
    )
    thead = f"<thead><tr>{head}</tr></thead>" if block.repeat_header else ""
    groups: list[tuple[Any, list[Mapping[str, Any]]]] = []
    if block.group_by is None:
        groups = [(None, rows)]
    else:
        seen: dict[Any, list[Mapping[str, Any]]] = {}
        for row in rows:  # l'ordre de PREMIÈRE apparition : celui que l'utilisateur a saisi
            seen.setdefault(row.get(block.group_by), []).append(row)
        groups = list(seen.items())
    out: list[str] = []
    for key, members in groups:
        if block.group_by is not None:
            out.append(f'<tr class="grp"><td colspan={len(block.columns)}>{_cell(key, child_fields.get(block.group_by))}</td></tr>')
        for row in members:
            out.append("<tr>" + "".join(
                f"<td class={c.align or 'left'}>{_cell(row.get(c.field), child_fields.get(c.field))}</td>"
                for c in block.columns) + "</tr>")
        if block.subtotal:
            cells = []
            for c in block.columns:
                if c.field in block.subtotal:
                    cells.append(f"<td class={c.align or 'right'}>{_sum(members, c.field, child_fields.get(c.field))}</td>")
                else:
                    cells.append(f"<td class={c.align or 'left'}></td>")
            out.append('<tr class="sub">' + "".join(cells) + "</tr>")
    label = f"<h2>{escape(block.label)}</h2>" if block.label else ""
    return f'{label}<table class="lines {block.style} {block.density}">{thead}<tbody>{"".join(out)}</tbody></table>'


def _stylesheet(page: PageSetup) -> str:
    """La feuille de style. Flux et tableaux SEULEMENT : le moteur ne connaît pas CSS grid."""
    size = f"{page.size} {page.orientation}" if page.orientation == "landscape" else page.size
    return (
        f"@page{{size:{size};margin:{page.top}mm {page.right}mm {page.bottom}mm {page.left}mm}}"
        "body{font-family:sans-serif;font-size:10pt;color:#000;margin:0}"
        "h1{font-size:16pt;margin:0 0 5mm}h2{font-size:11pt;margin:5mm 0 2mm}"
        "p.t{margin:2mm 0;white-space:pre-wrap}"
        "span.k{font-size:8pt;color:#555}"
        "table.fields{width:100%;border-collapse:collapse;margin-bottom:4mm}"
        "table.fields td{padding:1mm 2mm 1mm 0;vertical-align:top}"
        "table.lines{width:100%;border-collapse:collapse}"
        "table.lines thead{display:table-header-group}"  # l'en-tête se répète : mesuré sur 11 pages
        "table.lines tbody tr{page-break-inside:avoid}"
        "table.lines.bordered th,table.lines.bordered td{border:0.2mm solid #888}"
        "table.lines.ruled th,table.lines.ruled td{border-bottom:0.2mm solid #bbb}"
        "table.lines.bordered th,table.lines.ruled th{background:#eee}"
        "table.lines.normal th,table.lines.normal td{padding:1.2mm 2mm}"
        "table.lines.compact th,table.lines.compact td{padding:0.6mm 1.5mm;font-size:9pt}"
        "tr.grp td{font-weight:bold;background:#f4f4f4}"
        "tr.sub td{font-weight:bold;border-top:0.3mm solid #888}"
        "table.totaux{margin:5mm 0 0 auto;width:70mm;border-collapse:collapse}"
        "table.totaux td{padding:1mm 0}table.totaux tr.grand td{font-weight:bold;border-top:0.3mm solid #000}"
        ".left{text-align:left}.right{text-align:right}.center{text-align:center}td.r,th.r{text-align:right}"
        ".pb{page-break-before:always}"
    )


# ── Le gabarit FABRIQUÉ ───────────────────────────────────────────────────────


def generated_template(definition: DocTypeDef, children: Mapping[str, Any] | None = None) -> PrintTemplate:
    """Le gabarit d'un DocType qui n'en déclare aucun : aucun document n'est jamais inimprimable.

    Il suit le `layout` quand il y en a un (c'est l'ordre que l'utilisateur connaît), sinon l'ordre des champs,
    et il CHOISIT par type — jamais par une propriété nouvelle du schéma (process/impression.md §4) : il saute
    les secrets, les fichiers, les structures et les champs cachés. Les tables deviennent des blocs `table`,
    les numériques de la fin un bloc `totals`.
    """
    placed: list[str] = []
    for section in definition.layout or []:
        for column in section.columns:
            placed.extend(column)
    order = placed or [f.fieldname for f in definition.fields]
    seen: set[str] = set()
    simple: list[str] = []
    tables: list[str] = []
    for name in order:
        field = definition.field_map.get(name)
        if field is None or name in seen:
            continue
        seen.add(name)
        if field.hidden or field.fieldtype in UNPRINTABLE:
            continue
        (tables if field.fieldtype == "Table" else simple).append(name)
    # Les montants de la fin font les totaux : c'est la forme d'une facture, et ils sont alignés à droite.
    totals = [n for n in simple if definition.field_map[n].fieldtype in DECIMAL_TYPES]
    head = [n for n in simple if n not in totals]

    blocks: list[dict[str, Any]] = [{"type": "title", "text": f"{definition.label or definition.name} {{{{ name }}}}"}]
    if head:
        blocks.append({"type": "fields", "columns": 2, "fields": head[:MAX_PRINT_FIELDS]})
    for table in tables:
        child = (children or {}).get(table)
        columns = [
            {"field": c.fieldname, **({"align": "right"} if c.fieldtype in NUMERIC else {})}
            for c in (child.fields if child is not None else [])
            if not c.hidden and c.fieldtype not in UNPRINTABLE and c.fieldtype != "Table"
        ][:MAX_PRINT_COLUMNS]
        if columns:
            blocks.append({"type": "table", "field": table, "columns": columns,
                           "label": definition.field_map[table].label or table})
    if totals:
        blocks.append({"type": "totals", "fields": totals[:MAX_PRINT_FIELDS]})
    return parse_template({"schema_version": 1, "doctype": definition.name, "name": "standard",
                           "label": definition.label or definition.name, "blocks": blocks},
                          source=f"gabarit fabriqué de {definition.name}")
