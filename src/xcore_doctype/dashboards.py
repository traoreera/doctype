"""Blocs de tableau de bord et vues d'un DocType (palier 70 %, phases I et J, `doc/palier70.md`).

Deux endroits en déclarent, avec le MÊME vocabulaire :

- l'espace de travail d'un plugin (`workspaces/*.yaml`, `workspaces.py`) : la page d'accueil d'un module ;
- un DocType (`views:`, `schema.py`) : son propre tableau de bord et ses vues Kanban, Calendrier, Gantt.

    blocks:
      - { type: number_card, label: Brouillons, doctype: Stock Entry, filters: { docstatus: 0 }, color: orange }
      - { type: chart, label: Mouvements par mois, doctype: Stock Entry, kind: bar, group_by: posting_date, bucket: month, periods: 6 }
      - { type: chart, label: Stock par article, doctype: Bin, kind: donut, group_by: item, function: sum, field: qty }
      - { type: quick_list, label: Derniers mouvements, doctype: Stock Entry, limit: 5 }
      - { type: card, title: Rapports, links: [{ doctype: Bin }, { doctype: Stock Ledger Entry, label: Journal }] }

Rien n'est du code : une carte ou un graphique se DÉCRIT (un type, des champs, des filtres) et c'est `aggregation.py` qui le calcule, avec les
droits de celui qui regarde. Ce module ne contient que les modèles (extra="forbid") et leur contrôle contre les définitions : une faute
(champ inconnu, somme d'un texte, filtre invalide) est une erreur de chargement, jamais une carte vide en production.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Annotated, Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StringConstraints, model_validator

from . import aggregation
from .errors import ValidationFailed

if TYPE_CHECKING:  # pragma: no cover
    from .builder import CompiledDocType
    from .schema import DocTypeDef

_Label = Annotated[str, StringConstraints(min_length=1, max_length=80)]
_DocTypeRef = Annotated[str, StringConstraints(min_length=1, max_length=63)]
_Fieldname = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,62}$")]
# Une teinte de la palette du Desk (nom), OU une couleur libre `#rrggbb` : seulement ce format, car elle finit dans un attribut de style ; le Desk
# l'éclaircit en thème sombre quand elle y serait illisible (`ui/src/lib/brand.ts`).
HexColor = Annotated[str, StringConstraints(pattern=r"^#[0-9a-fA-F]{6}$")]
Color = Literal["blue", "green", "orange", "red", "purple", "teal", "gray"] | HexColor
# Largeur dans une grille de 12 colonnes (3 = un quart, 4 = un tiers, 6 = la moitié, 8 = deux tiers, 12 = la ligne).
Cols = Literal[3, 4, 6, 8, 12]
ChartKind = Literal["bar", "line", "pie", "donut", "percentage"]
# Ces trois graphiques disent « la part de chacun dans un total » : une moyenne ou un extremum ne s'additionne pas.
PART_TO_WHOLE = ("pie", "donut", "percentage")
Function = Literal["count", "sum", "avg", "min", "max"]
MAX_BLOCKS = 100
MAX_LINKS = 30
MAX_EXTENSION_PROPS = 2000


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class HeaderBlock(_Model):
    type: Literal["header"]
    text: _Label


class ParagraphBlock(_Model):
    """Un texte simple (jamais du HTML : le Desk l'affiche comme du texte)."""

    type: Literal["paragraph"]
    text: Annotated[str, StringConstraints(min_length=1, max_length=500)]
    cols: Cols = 12


class SpacerBlock(_Model):
    type: Literal["spacer"]


class ShortcutBlock(_Model):
    """Un raccourci vers la liste d'un DocType. `new` : un « + » pour en créer un (offert seulement à qui peut créer)."""

    type: Literal["shortcut"]
    doctype: _DocTypeRef
    label: _Label | None = None  # défaut : le libellé du DocType
    new: StrictBool = False


class _Measured(_Model):
    """Ce qu'une carte ou un graphique calcule : une mesure sur les documents d'un type, filtrés comme une liste."""

    # Absent seulement dans le tableau de bord d'un DocType, où il s'agit de ce DocType.
    doctype: _DocTypeRef | None = None
    function: Function = "count"
    field: _Fieldname | None = None  # le champ Number ou Decimal que `sum`, `avg`, `min`, `max` portent
    filters: dict[str, Any] | None = None

    def metric(self) -> aggregation.Metric:
        return aggregation.Metric(self.function, self.field)


class NumberCardBlock(_Measured):
    """Un chiffre : combien de documents, ou la somme / moyenne / minimum / maximum d'un champ. Un clic ouvre la liste filtrée."""

    type: Literal["number_card"]
    label: _Label
    color: Color | None = None
    cols: Cols = 3


class ChartBlock(_Measured):
    """Un graphique : la mesure PAR valeur d'un champ (`group_by`), ou par période si ce champ est une date."""

    type: Literal["chart"]
    label: _Label
    kind: ChartKind = "bar"
    group_by: _Fieldname | Literal["docstatus", "owner", "creation", "modified"]
    bucket: Literal["day", "month", "year"] | None = None
    periods: Annotated[int, Field(strict=True, ge=1, le=366)] | None = None
    limit: Annotated[int, Field(strict=True, ge=1, le=50)] = 10  # les plus gros groupes ; un graphique de 50 barres ne se lit plus
    color: Color | None = None  # la teinte des barres ou de la courbe (une seule série) ; un camembert, lui, garde la palette validée
    cols: Cols = 6


class QuickListBlock(_Model):
    """Les derniers documents d'un type (les plus récemment modifiés), filtrés. Un clic ouvre le document."""

    type: Literal["quick_list"]
    label: _Label
    doctype: _DocTypeRef
    filters: dict[str, Any] | None = None
    limit: Annotated[int, Field(strict=True, ge=1, le=10)] = 5
    cols: Cols = 6


class LinkItem(_Model):
    doctype: _DocTypeRef
    label: _Label | None = None


class LinksCardBlock(_Model):
    """Une carte de liens vers des listes (« Rapports et référentiels » dans Frappe)."""

    type: Literal["card"]
    title: _Label
    links: Annotated[list[LinkItem], Field(min_length=1, max_length=MAX_LINKS)]
    cols: Cols = 4


class ExtensionBlock(_Model):
    """Un bloc dessiné par une extension JavaScript du Desk (D28) : `component` = `<plugin>.<nom>` que l'extension enregistre (`desk.blocks.add`).

    Le serveur ne peut pas savoir si ce composant existe (il vit dans le navigateur) : il le sert à qui voit l'espace, le Desk montre un encart
    discret quand aucune extension chargée ne le fournit. `props` : un petit objet JSON transmis tel quel au composant (jamais du code).
    """

    type: Literal["extension"]
    component: Annotated[str, StringConstraints(pattern=r"^[a-z0-9_]+\.[a-z0-9_-]{1,40}$")]
    label: _Label | None = None
    props: dict[str, Any] | None = None
    cols: Cols = 6

    @model_validator(mode="after")
    def _small_props(self) -> "ExtensionBlock":
        if self.props is not None and len(json.dumps(self.props, ensure_ascii=False)) > MAX_EXTENSION_PROPS:
            raise ValueError(f"props : au plus {MAX_EXTENSION_PROPS} caractères en JSON")
        return self


# Les blocs qu'un ESPACE accepte. Un DocType, lui, n'en accepte que ceux qui parlent de lui-même (voir `DOCTYPE_BLOCKS`).
WorkspaceBlock = Annotated[
    HeaderBlock | ParagraphBlock | SpacerBlock | ShortcutBlock | NumberCardBlock | ChartBlock | QuickListBlock | LinksCardBlock | ExtensionBlock,
    Field(discriminator="type"),
]
DashboardBlock = Annotated[HeaderBlock | NumberCardBlock | ChartBlock, Field(discriminator="type")]


# ── Vues d'un DocType ─────────────────────────────────────────────────────────────────────────

VIEW_NAMES = ("list", "report", "dashboard", "kanban", "calendar", "gantt", "tree")


class KanbanView(_Model):
    """Un tableau : une colonne par valeur d'un champ Select. Glisser une carte change la valeur."""

    field: _Fieldname  # un Select, obligatoire ou avec un défaut : chaque document a une colonne
    card_fields: Annotated[list[_Fieldname], Field(max_length=4)] | None = None  # défaut : les colonnes de la liste


class TreeView(_Model):
    """Une hiérarchie : un champ Link du type vers LUI-MÊME nomme le parent de chaque document (`null` : racine)."""

    parent: _Fieldname


class CalendarView(_Model):
    """Les documents sur un mois ou une semaine, placés par une date (ou une plage `start` -> `end`)."""

    start: _Fieldname
    end: _Fieldname | None = None
    title: _Fieldname | None = None  # défaut : le titre du document
    color: _Fieldname | None = None  # un Select : une teinte par valeur


class GanttView(_Model):
    """Des barres entre deux dates, sur une échelle de temps."""

    start: _Fieldname
    end: _Fieldname
    title: _Fieldname | None = None
    color: _Fieldname | None = None


class ViewsDef(_Model):
    """`views:` d'un DocType. La liste et le rapport existent toujours ; le tableau de bord est généré si on n'en écrit pas."""

    default: Literal["list", "report", "dashboard", "kanban", "calendar", "gantt", "tree"] = "list"
    kanban: KanbanView | None = None
    calendar: CalendarView | None = None
    gantt: GanttView | None = None
    tree: TreeView | None = None
    dashboard: Annotated[list[DashboardBlock], Field(min_length=1, max_length=MAX_BLOCKS)] | None = None


# ── Contrôle contre les définitions ───────────────────────────────────────────────────────────


def check_measured(
    block: _Measured | QuickListBlock,
    resolve: Callable[[str], "CompiledDocType | None"],
    own: str | None,
) -> list[str]:
    """Les fautes d'un bloc qui calcule : cible inconnue, champ absent ou de mauvais type, filtre invalide. Vide si tout va bien.

    `resolve` retrouve un DocType chargé par son nom ; `own` : le DocType implicite (tableau de bord d'un DocType), sinon `None`.
    """
    target = block.doctype or own
    if target is None:
        return ["`doctype` est obligatoire dans un espace de travail"]
    if own is not None and block.doctype is not None:
        return [f"`doctype` n'a pas de sens ici (ce tableau de bord parle de {own!r})"]
    compiled = resolve(target)
    if compiled is None:
        return [f"DocType inconnu : {target!r}"]
    if compiled.definition.istable:
        return [f"{target!r} est un DocType enfant (ses lignes n'ont ni liste ni agrégat)"]
    # Un clic sur une carte ouvre la liste avec ces filtres : il faut que la liste sache les montrer (sinon le chiffre et la liste divergent).
    shown = block.filters or {}
    if "name" in shown:
        return ["`filters.name` n'est pas permis dans un bloc (filtrer sur l'identifiant d'un document n'a pas de sens ici)"]
    if "docstatus" in shown and not compiled.definition.submittable:
        return [f"`filters.docstatus` : {target!r} n'est pas soumettable, son état est toujours « brouillon »"]
    if isinstance(block, QuickListBlock):
        try:
            aggregation.validate_filters(compiled, block.filters)
        except ValidationFailed as e:
            return [e.message]
        return []
    # La forme d'abord (un message qui dit quoi écrire), puis ce que le DocType permet.
    if isinstance(block, ChartBlock) and block.color is not None and block.kind not in ("bar", "line"):
        return [f"`color` n'a pas de sens pour `kind: {block.kind}` (plusieurs teintes : la palette du Desk) ; il colore les barres ou la courbe"]
    if isinstance(block, ChartBlock) and block.kind in PART_TO_WHOLE and block.function not in ("count", "sum"):
        return [f"`kind: {block.kind}` montre la part de chacun dans un total : `function` doit être count ou sum (pas {block.function}), ou choisir bar / line"]
    if block.function == "count" and block.field is not None:
        return ["`field` n'a pas de sens avec `function: count`"]
    if block.function != "count" and block.field is None:
        return [f"`function: {block.function}` exige `field` (un champ Number ou Decimal)"]
    try:
        aggregation.resolve(
            compiled.definition,
            group_by=getattr(block, "group_by", None),
            bucket=getattr(block, "bucket", None),
            metrics=[block.metric()],
            periods=getattr(block, "periods", None),
            limit=getattr(block, "limit", aggregation.DEFAULT_GROUPS),
        )
        aggregation.validate_filters(compiled, block.filters)
    except ValidationFailed as e:
        return [e.message]
    return []


def default_dashboard(defn: "DocTypeDef") -> list[dict[str, Any]]:
    """Le tableau de bord d'un DocType qui n'en déclare pas : un plugin n'a rien à écrire pour en avoir un.

    Le nombre de documents et la somme de ses deux premières quantités (les cartes se partagent la rangée), l'évolution par mois, puis la
    répartition par ses deux premiers champs Select (et par état pour un document soumettable). L'évolution suit la date que le type annonce
    déjà pour son Gantt ou son calendrier (la date de la pièce, pas celle de sa saisie), sinon la création. Les blocs ne citent que des
    champs qui existent : ils ne peuvent pas se tromper.
    """
    own = defn.name
    fields = {f.fieldname: f for f in defn.fields}
    quantities = [f for f in defn.fields if f.fieldtype in ("Number", "Decimal", "Currency", "Int") and not f.hidden][:2]
    span = 12 // (1 + len(quantities))
    blocks: list[dict[str, Any]] = [{"type": "number_card", "label": defn.label or own, "doctype": own, "function": "count", "cols": span, "color": "blue"}]
    for f in quantities:
        blocks.append(
            {"type": "number_card", "label": f"{f.label or f.fieldname} (somme)", "doctype": own, "function": "sum", "field": f.fieldname, "cols": span, "color": "teal"}
        )

    v = defn.views
    declared = next((n for n in (v.gantt.start if v and v.gantt else None, v.calendar.start if v and v.calendar else None) if n in fields and fields[n].fieldtype == "Date"), None)
    axis, title = ("creation", "Créés par mois") if declared is None else (declared, f"{fields[declared].label or declared} par mois")

    donuts: list[tuple[str, str]] = [("docstatus", "Par état")] if defn.submittable else []
    donuts += [(f.fieldname, f"Par {(f.label or f.fieldname).lower()}") for f in defn.fields if f.fieldtype == "Select" and not f.hidden][:2]
    first, rest = donuts[:1], donuts[1:]
    blocks.append(
        {"type": "chart", "label": title, "doctype": own, "kind": "bar", "group_by": axis, "bucket": "month", "periods": 12,
         "function": "count", "limit": 10, "cols": 8 if first else 12}
    )
    for i, (group, label) in enumerate(first + rest):
        cols = 4 if i == 0 else 6
        blocks.append({"type": "chart", "label": label, "doctype": own, "kind": "donut", "group_by": group, "function": "count", "limit": 10, "cols": cols})
    return blocks
