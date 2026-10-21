"""Espaces de travail (palier 70 %, phase G, `doc/palier70.md`) : la page d'accueil d'un MODULE dans le Desk.

Un plugin métier décrit son espace en YAML, comme il décrit ses DocTypes (`<plugin>/workspaces/*.yaml`, chargé par
`doctype_registry.load_workspaces`) :

    schema_version: 1
    name: stock                     # le module : doit être le nom du plugin qui l'apporte
    label: Stock
    icon: warehouse                 # une icône PrimeIcons (`pi pi-warehouse`)
    description: Articles, entrepôts et mouvements
    blocks:
      - { type: header, text: Opérations }
      - { type: number_card, label: Brouillons, doctype: Stock Entry, filters: { docstatus: 0 }, color: orange }
      - { type: chart, label: Mouvements par mois, doctype: Stock Entry, kind: bar, group_by: posting_date, periods: 6 }
      - { type: shortcut, doctype: Stock Entry, new: true }    # un raccourci vers la liste ; `new` ajoute « + »
      - { type: shortcut, doctype: Bin }

Les blocs sont une liste fermée (voir `dashboards.py`) : `header`, `paragraph`, `spacer`, `shortcut`, `number_card`, `chart`, `quick_list`,
`card`. Rien ici n'est du code : un fichier d'espace ne peut rien exécuter, il DÉCRIT ce que le Desk calcule avec les droits de celui qui
regarde.

Ce qu'un utilisateur voit est FILTRÉ par ses droits (`DocumentService.list_workspaces`) : un bloc qui parle d'un type qu'il ne peut pas
lire disparaît (raccourci, carte, graphique, liste), une section sans bloc visible aussi, un espace vide aussi. Un module qui n'a pas de
fichier d'espace en reçoit un fabriqué (un raccourci par type lisible) : aucun DocType n'est jamais inaccessible faute de page d'accueil.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from .dashboards import (
    MAX_BLOCKS,
    ChartBlock,
    ExtensionBlock,
    HeaderBlock,
    LinksCardBlock,
    NumberCardBlock,
    ParagraphBlock,
    QuickListBlock,
    ShortcutBlock,
    SpacerBlock,
    WorkspaceBlock,
)
from .errors import InvalidDefinition

__all__ = [
    "ChartBlock", "ExtensionBlock", "HeaderBlock", "LinksCardBlock", "NumberCardBlock", "ParagraphBlock", "QuickListBlock", "ShortcutBlock", "SpacerBlock",
    "WorkspaceContribution", "WorkspaceDef", "parse_contribution", "parse_workspace", "module_label",
]

_Module = Annotated[str, StringConstraints(pattern=r"^[a-z0-9_]+$", max_length=63)]
# Une icône PrimeIcons, sans le préfixe (`warehouse` pour `pi pi-warehouse`) : une valeur libre ne doit pas pouvoir porter de classe CSS.
Icon = Annotated[str, StringConstraints(pattern=r"^[a-z0-9-]{1,40}$")]


class WorkspaceDef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    name: _Module
    label: Annotated[str, StringConstraints(min_length=1, max_length=80)]
    icon: Icon | None = None
    description: Annotated[str, StringConstraints(min_length=1, max_length=300)] | None = None
    order: Annotated[int, Field(ge=0, le=1000, strict=True)] = 100  # rang dans la barre latérale (puis l'ordre alphabétique)
    blocks: Annotated[list[WorkspaceBlock], Field(min_length=1, max_length=MAX_BLOCKS)]

    def doctypes(self) -> list[str]:
        """Les DocTypes que les raccourcis, listes rapides et cartes de liens visent (leur existence se contrôle au chargement)."""
        out: list[str] = []
        for b in self.blocks:
            if isinstance(b, (ShortcutBlock, QuickListBlock)):
                out.append(b.doctype)
            elif isinstance(b, LinksCardBlock):
                out.extend(link.doctype for link in b.links)
        return out

    def measured(self) -> list[NumberCardBlock | ChartBlock]:
        """Les cartes et graphiques : ceux dont les champs et filtres se contrôlent contre le DocType visé."""
        return [b for b in self.blocks if isinstance(b, (NumberCardBlock, ChartBlock))]


class WorkspaceContribution(BaseModel):
    """Des blocs qu'un plugin ajoute à l'espace d'un AUTRE module (D27, `doc/extensions.md`) : un fichier de ses `workspaces/` qui porte
    `contributes_to` au lieu de `name`. Les blocs suivent ceux de l'espace visé (déclaré ou fabriqué), filtrés par les mêmes droits."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    contributes_to: _Module
    blocks: Annotated[list[WorkspaceBlock], Field(min_length=1, max_length=MAX_BLOCKS)]

    def doctypes(self) -> list[str]:
        return WorkspaceDef.doctypes(self)  # type: ignore[arg-type]  # mêmes blocs, même lecture

    def measured(self) -> list[NumberCardBlock | ChartBlock]:
        return WorkspaceDef.measured(self)  # type: ignore[arg-type]


def _validated(model: type[BaseModel], raw: object, source: str, what: str) -> Any:
    if not isinstance(raw, dict):
        raise InvalidDefinition(f"{source}: {what} doit être un objet")
    try:
        return model.model_validate(raw)
    except ValidationError as e:
        lines = []
        for err in e.errors(include_url=False, include_input=False):
            where = ".".join(str(p) for p in err["loc"]) or "(racine)"
            lines.append(f"  - {where}: {err['msg']}")
        raise InvalidDefinition(f"{source}: {what} invalide\n" + "\n".join(lines)) from e


def parse_contribution(raw: object, *, source: str = "<contribution>") -> WorkspaceContribution:
    """Valide une contribution d'espace brute ou lève `InvalidDefinition`."""
    return _validated(WorkspaceContribution, raw, source, "la contribution d'espace")


def with_contributions(ws: WorkspaceDef, contributions: list[WorkspaceContribution]) -> WorkspaceDef:
    """L'espace suivi des blocs que d'autres plugins y ajoutent. Sans revalidation : chaque partie l'a été à son chargement
    (et la somme peut dépasser `MAX_BLOCKS`, qui borne un FICHIER, pas ce que plusieurs plugins composent ensemble)."""
    if not contributions:
        return ws
    return ws.model_copy(update={"blocks": [*ws.blocks, *(b for c in contributions for b in c.blocks)]})


def parse_workspace(raw: object, *, source: str = "<espace>") -> WorkspaceDef:
    """Valide un espace brut (dict issu de YAML/JSON) ou lève `InvalidDefinition`, comme `schema.parse_definition`."""
    if not isinstance(raw, dict):
        raise InvalidDefinition(f"{source}: l'espace de travail doit être un objet")
    try:
        return WorkspaceDef.model_validate(raw)
    except ValidationError as e:
        lines = []
        for err in e.errors(include_url=False, include_input=False):
            where = ".".join(str(p) for p in err["loc"]) or "(racine)"
            lines.append(f"  - {where}: {err['msg']}")
        raise InvalidDefinition(f"{source}: espace de travail invalide\n" + "\n".join(lines)) from e


def module_label(module: str | None) -> str:
    """« sales » -> « Sales », « stock_control » -> « Stock control » : le nom d'un module est un identifiant, pas encore un libellé."""
    spaced = (module or "").replace("_", " ").strip()
    return spaced[:1].upper() + spaced[1:] if spaced else "Autres"
