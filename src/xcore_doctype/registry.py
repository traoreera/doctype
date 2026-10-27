"""Registre des DocTypes. Porté par l'instance du plugin, créé au `on_load` (D8) : aucun état de module."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Sequence

import yaml

from .builder import SYSTEM_FILTERS, CompiledDocType
from .dashboards import MAX_BLOCKS, MAX_COMPOSED_BLOCKS, check_measured
from .desk_extensions import DeskExtensions
from .errors import InvalidDefinition, NotFound, ValidationFailed
from .expr import ExprError
from .extensions import Extension, merge, parse_extension, target_problem
from .mapping import mappable_types, same_number_kind
from .schema import LINK_FILTER_REF, DocTypeDef, parse_definition
from .workspaces import WorkspaceContribution, WorkspaceDef, parse_contribution, parse_workspace, with_contributions

_SUFFIXES = {".yaml", ".yml", ".json"}


@dataclass(frozen=True, slots=True)
class Referrer:
    """Un champ Link qui pointe vers un DocType : où chercher les documents qui empêchent une suppression.

    `table` : le champ Table de `doctype` dont les LIGNES portent le Link (`fieldname` est alors un champ du DocType enfant) ;
    `None` pour un Link du document lui-même.
    """

    doctype: str
    fieldname: str
    table: str | None = None
    # Un champ DynamicLink : le champ (du document ou de la ligne) qui dit DANS quel DocType chercher. `None` pour un Link ordinaire.
    type_field: str | None = None

    def key(self) -> tuple[Any, ...]:
        """La forme que `Query.references_to` / `referrers_of` attendent : 3 éléments, 4 avec un champ de type."""
        base = (self.doctype, self.fieldname, self.table)
        return base if self.type_field is None else (*base, self.type_field)


# Les types dont un champ `fetch_from` peut recevoir la valeur : des valeurs simples que la cible a déjà validées (jamais un fichier, un secret, un lien ni un tableau).
FETCHABLE_TYPES = (
    "Text", "LongText", "Select", "Number", "Int", "Percent", "Rating", "Decimal", "Currency", "Date", "Datetime", "Time", "Duration",
    "Boolean", "Color", "Code", "TextEditor",
)


def _filter_incompatible(filtered: Any, source: Any, key: str) -> str | None:
    """Pourquoi la valeur du champ `source` ne peut pas filtrer le champ `filtered` de la cible, ou `None`.

    `filtered` vaut `None` pour un filtre système (`name`, `docstatus`, `owner`…), qui attend alors un texte, un entier ou un booléen.
    """
    if filtered is None:
        expected = SYSTEM_FILTERS.get(key)  # `name` (Text), `docstatus` (Number) : les seuls champs filtrables qui ne sont pas déclarés
        return None if expected is None or source.fieldtype == expected else f"{key} attend un {expected}, pas un {source.fieldtype}"
    if source.fieldtype != filtered.fieldtype:
        return f"{key} est un {filtered.fieldtype} chez la cible, le champ qui le filtre est un {source.fieldtype}"
    if filtered.fieldtype == "Link" and source.options != filtered.options:
        return f"{key} vise {filtered.options!r}, le champ qui le filtre vise {source.options!r}"
    if filtered.fieldtype == "Select" and not set(source.options or []) <= set(filtered.options or []):
        return f"{key} ne propose pas toutes les valeurs du champ qui le filtre"
    if filtered.fieldtype in ("Decimal", "Currency") and filtered.decimals != source.decimals:
        return f"{key} a {filtered.decimals} décimales, le champ qui le filtre en a {source.decimals}"
    return None


def fetch_incompatible(dest: Any, source: Any) -> str | None:
    """Pourquoi `dest` ne peut pas recevoir la valeur de `source` (copiée telle quelle), ou `None`. Une copie refusée à l'écriture serait découverte à la première saisie."""
    if dest.fieldtype not in FETCHABLE_TYPES:
        return f"un champ {dest.fieldtype} ne peut pas être rempli par fetch_from"
    if source.fieldtype != dest.fieldtype and not (dest.fieldtype == "Text" and source.fieldtype == "Select"):
        return f"{dest.fieldtype} ne reçoit pas un {source.fieldtype}"
    if dest.fieldtype in ("Decimal", "Currency") and dest.decimals != source.decimals:
        return f"décimales différentes ({dest.decimals} contre {source.decimals} dans la source)"
    if dest.fieldtype == "Select" and not set(source.options or []) <= set(dest.options or []):
        return "le Select ne propose pas toutes les valeurs de la source"
    if dest.fieldtype == "Rating" and dest.stars != source.stars:
        return f"nombre d'étoiles différent ({dest.stars} contre {source.stars})"
    if dest.fieldtype == "Text" and dest.format is not None and dest.format != getattr(source, "format", None):
        return f"le format {dest.format!r} n'est pas celui de la source"
    return None


# Un plugin propriétaire donne son nom au `module` de ses DocTypes, donc au premier segment de leurs grants (`sales:customer:read`) :
# même forme que pour `rbac.declare` (voir schema._Module). Un plugin au nom tiré (`my-sales`) ne peut donc pas en posséder.
_OWNER_RE = re.compile(r"^[a-z0-9_]+$")


class DocTypeRegistry:
    def __init__(self) -> None:
        self._items: dict[str, CompiledDocType] = {}
        self._owners: dict[str, str] = {}  # DocType -> plugin propriétaire (absent : DocType de l'opérateur, `doctypes_dirs`)
        self._workspaces: dict[str, WorkspaceDef] = {}  # espace de travail -> définition (nom == module == plugin propriétaire)
        self._workspace_owners: dict[str, str] = {}
        # Extensions de DocType (D26) : la définition d'ORIGINE de chaque DocType, et ce que d'autres plugins y ajoutent (par DocType visé,
        # dans l'ordre de chargement). `_items` tient la FUSION compilée. Les extensions survivent au retrait du DocType visé : elles se
        # réappliquent quand son propriétaire le recharge.
        self._origins: dict[str, tuple[DocTypeDef, str]] = {}
        self._extensions: dict[str, list[Extension]] = {}
        # Contributions d'espace (D27) : module visé -> [(plugin, contribution)], dans l'ordre de chargement. Gardées si le module visé part.
        self._contributions: dict[str, list[tuple[str, WorkspaceContribution]]] = {}
        # Modules FRÈRES (`load_dir(…, peers=True)`) : des plugins qui se citent en cycle (accounts ↔ stock ↔ selling…) ne peuvent pas se
        # charger l'un après l'autre. Un parent dont la table enfant vient d'un frère pas encore chargé ATTEND ici (invisible, aucune écriture),
        # et s'enregistre à l'arrivée de cet enfant ; une définition dont un contrôle échoue à l'arrivée d'un frère est BRISÉE (écritures refusées).
        self._waiting: dict[str, tuple[DocTypeDef, str, str | None]] = {}  # DocType -> (définition d'origine, source, propriétaire)
        self._broken: dict[str, list[str]] = {}
        self._peered: set[str] = set()  # chargés avec `peers=True` : eux seuls peuvent être incomplets (une cible absente ailleurs est une erreur de chargement)

    def register(self, defn: DocTypeDef, *, source: str = "<code>", owner: str | None = None) -> CompiledDocType:
        """Enregistre un DocType. Les DocTypes enfants qu'il cite (champs Table) doivent l'être AVANT lui.

        `owner` : le plugin qui l'apporte. Son `module` DOIT alors être le nom de ce plugin : c'est lui qui déclare les grants
        du module à auth (`doctype_grants.for_plugin`), un plugin ne peut donc pas écrire des DocTypes dans le module d'un autre.
        """
        if defn.name in self._items or (defn.name in self._waiting and self._waiting[defn.name][1] != source):
            raise InvalidDefinition(f"{source}: DocType {defn.name!r} déjà défini")
        # Un DocType enfant n'a pas de `module` (il hérite de son parent, voir schema._child_rules) : il appartient au plugin qui l'apporte.
        if owner is not None and not defn.istable and defn.module != owner:
            raise InvalidDefinition(
                f"{source}: DocType {defn.name!r} : `module` doit être {owner!r} (le plugin qui l'apporte), pas {defn.module!r}"
            )
        # Ce que d'autres plugins ont ajouté à ce DocType avant qu'il (re)vienne : s'il ne s'applique plus, le chargement échoue (D26).
        for ext in self._extensions.get(defn.name, []):
            if (why := target_problem(defn, owner, ext.owner)) is not None:
                raise InvalidDefinition(f"{source}: {defn.name!r} est étendu par {ext.owner} ({ext.source}), ce qui n'est plus possible : {why}")
        compiled = self._compile(merge(defn, self._extensions.get(defn.name, [])), source)
        self._items[defn.name] = compiled
        self._origins[defn.name] = (defn, source)
        if owner is not None:
            self._owners[defn.name] = owner
        return compiled

    def origin(self, name: str) -> DocTypeDef:
        """La définition D'ORIGINE d'un type (son YAML), sans les extensions d'autres plugins (D26) : le constructeur de DocTypes part de là."""
        self.get(name)  # inconnu : la même erreur que partout
        return self._origins[name][0]

    def origins(self) -> list[tuple[DocTypeDef, str]]:
        """Toutes les définitions d'origine chargées, avec leur source (le registre d'essai du mode développeur les recharge)."""
        return list(self._origins.values())

    def with_custom_fields(self, name: str, added: Sequence[Mapping[str, Any]]) -> CompiledDocType:
        """Le DocType vu par UNE organisation : sa définition, plus les champs qu'elle a ajoutés (process/programme.md §3.5).

        La fusion repasse par `parse_definition` puis par la compilation, comme une extension de plugin (D26) : toutes les règles
        du schéma s'appliquent au résultat, et le reste du moteur n'a pas à savoir qu'un champ vient d'une organisation. Une
        faute de définition sort donc ici, en `InvalidDefinition`, et l'appelant la rend à celui qui a enregistré l'habillage.
        """
        compiled = self.get(name)
        if not added:
            return compiled
        raw = compiled.definition.model_dump(mode="json", exclude_none=True)
        raw["fields"] = [*raw["fields"], *[dict(f) for f in added]]
        return self._compile(parse_definition(raw, source=f"habillage de {name}"), f"habillage de {name}")

    def _compile(self, defn: DocTypeDef, source: str) -> CompiledDocType:
        """Compile une définition (l'origine, ou sa fusion avec des extensions) : lignes de tables, conditions, tableau de bord."""
        children: dict[str, DocTypeDef] = {}
        for f in defn.fields:
            if f.fieldtype != "Table":
                continue
            child = self._items.get(str(f.options))
            if child is None or not child.definition.istable:
                why = "inconnu (enregistrez-le avant celui-ci)" if child is None else "n'est pas un DocType enfant (`istable: true`)"
                raise InvalidDefinition(f"{source}: {defn.name}.{f.fieldname}: le DocType de lignes {f.options!r} {why}")
            if f.multiselect:
                links = [cf for cf in child.definition.fields if cf.fieldtype == "Link"]
                if len(child.definition.fields) != 1 or len(links) != 1:
                    raise InvalidDefinition(
                        f"{source}: {defn.name}.{f.fieldname}: une sélection multiple (`multiselect`) exige un DocType de lignes à UN seul champ, un Link ; "
                        f"{f.options!r} en a {len(child.definition.fields)}"
                    )
            children[child.definition.name] = child.definition
        try:
            compiled = CompiledDocType.compile(defn, children)
        except ExprError as e:  # une condition de ligne qui cite un champ du parent qui n'existe pas
            raise InvalidDefinition(f"{source}: {defn.name}: condition invalide dans une ligne : {e}") from None
        if defn.views is not None and defn.views.dashboard:
            # Les blocs d'un tableau de bord de DocType parlent de CE DocType : ses champs, ses filtres (le reste est dans `schema`).
            problems = []
            for i, block in enumerate(defn.views.dashboard):
                if block.type != "header":
                    problems += [f"views.dashboard.{i} ({block.label}): {p}" for p in check_measured(block, lambda n: compiled if n == defn.name else None, defn.name)]
            if problems:
                raise InvalidDefinition(f"{source}: {defn.name}:\n" + "\n".join(f"  - {p}" for p in problems))
        return compiled

    def load_dir(self, directory: Path, *, owner: str | None = None, peers: bool = False) -> list[str]:
        """Charge `*.yaml|*.yml|*.json`, SOUS-DOSSIERS COMPRIS. Une définition invalide fait échouer tout le chargement, ET n'en laisse aucune.

        Les DocTypes enfants passent d'abord : un parent compile ses lignes avec la définition de leur DocType. Ce tri
        porte sur TOUT l'arbre, et il suffit toujours : un enfant ne peut pas avoir de champ Table (tables imbriquées
        refusées, voir `schema`), c'est donc une feuille du graphe parent→enfant, qui ne peut pas avoir de cycle. Un jeu de
        DocTypes rangé en sous-dossiers (un par module, pour la lecture) se charge ainsi d'un seul appel, et un parent peut
        citer l'enfant d'un autre dossier sans que l'ordre des dossiers compte.

        Tout fichier de l'arbre est lu comme un DocType : les espaces de travail d'un plugin vivent à côté de son dossier
        de DocTypes (`load_workspaces`), jamais dedans.
        `owner` : voir `register`. `peers` : un parent dont la table enfant appartient à un module frère PAS ENCORE chargé attend
        (`_waiting`) au lieu d'échouer, et s'enregistre à l'arrivée de l'enfant (`settle`). Rend les noms ENREGISTRÉS.
        """
        if owner is not None and not _OWNER_RE.match(owner):
            raise InvalidDefinition(
                f"plugin {owner!r}: son nom ne peut pas servir de `module` (attendu : minuscules, chiffres et `_`, "
                "comme le premier segment d'un grant d'auth)"
            )
        directory = Path(directory)
        if not directory.is_dir():
            raise InvalidDefinition(f"dossier de DocTypes introuvable : {directory}")
        parsed = []
        for path in sorted(p for p in directory.rglob("*") if p.suffix in _SUFFIXES and p.is_file()):
            try:
                text = path.read_text(encoding="utf-8")
                raw = json.loads(text) if path.suffix == ".json" else yaml.safe_load(text)
            except (OSError, ValueError, yaml.YAMLError) as e:
                raise InvalidDefinition(f"{path}: illisible ({e})") from e
            parsed.append((parse_definition(raw, source=str(path)), str(path)))
        loaded: list[str] = []
        parked: list[str] = []
        try:
            for defn, source in sorted(parsed, key=lambda item: not item[0].istable):  # tri stable : l'ordre des fichiers est gardé
                if peers and self._missing_children(defn):
                    self._park(defn, source, owner)
                    parked.append(defn.name)
                    continue
                loaded.append(self.register(defn, source=source, owner=owner).definition.name)
        except Exception:
            for name in loaded:  # tout ou rien : un dossier à moitié chargé laisserait des DocTypes sans leurs voisins
                self._remove(name)
            for name in parked:
                self._waiting.pop(name, None)
            raise
        if peers:
            self._peered.update(loaded + parked)
        return loaded

    # ── Modules frères (un plugin par module, liens en cycle) ─────────────
    def _missing_children(self, defn: DocTypeDef) -> list[str]:
        return [str(f.options) for f in defn.fields if f.fieldtype == "Table" and str(f.options) not in self._items]

    def _park(self, defn: DocTypeDef, source: str, owner: str | None) -> None:
        if defn.name in self._items or defn.name in self._waiting:
            raise InvalidDefinition(f"{source}: DocType {defn.name!r} déjà défini")
        if owner is not None and not defn.istable and defn.module != owner:
            raise InvalidDefinition(f"{source}: DocType {defn.name!r} : `module` doit être {owner!r} (le plugin qui l'apporte), pas {defn.module!r}")
        self._waiting[defn.name] = (defn, source, owner)

    def waiting(self) -> dict[str, list[str]]:
        """Les DocTypes qui attendent la table enfant d'un module frère : DocType -> tables manquantes."""
        return {name: self._missing_children(defn) for name, (defn, _, _) in sorted(self._waiting.items())}

    def owner_waiting(self, name: str) -> str | None:
        entry = self._waiting.get(name)
        return entry[2] if entry else None

    def waiting_definitions(self) -> list[DocTypeDef]:
        """Leurs définitions : un plugin déclare les grants de TOUS ses DocTypes à son chargement, y compris ceux qui attendent encore."""
        return [defn for defn, _, _ in self._waiting.values()]

    def settle(self) -> tuple[list[str], dict[str, list[str]]]:
        """Enregistre les parents dont toutes les tables sont maintenant chargées (jusqu'à ce que plus rien ne bouge). Un parent qui échoue
        alors (sa définition ne compile pas avec l'enfant arrivé) est BRISÉ, pas levé : le frère qui vient d'arriver n'en répond pas.
        Rend (enregistrés, brisés -> raisons)."""
        done: list[str] = []
        failed: dict[str, list[str]] = {}
        progress = True
        while progress:
            progress = False
            for name, (defn, source, owner) in list(self._waiting.items()):
                if self._missing_children(defn):
                    continue
                del self._waiting[name]
                progress = True
                try:
                    self.register(defn, source=source, owner=owner)
                except InvalidDefinition as e:
                    failed[name] = [str(e)]
                    continue
                done.append(name)
        return done, failed

    def citing(self, arrived: Sequence[str]) -> list[str]:
        """Les DocTypes enregistrés qui citent l'un de ces DocTypes (Link, ligne, DynamicLink par Select, `created_from`) : leurs contrôles
        dépendant de la cible (fetch_from, link_filters, unit_from, target_status, mappings) se refont à son arrivée."""
        wanted = set(arrived)
        out = []
        for name, compiled in self._items.items():
            if name in wanted:
                continue
            if self._cited_targets(compiled) & wanted:
                out.append(name)
        return sorted(out)

    def _cited_targets(self, compiled: CompiledDocType) -> set[str]:
        d = compiled.definition
        targets: set[str] = {m.source for m in d.created_from or []}
        for f in d.fields:
            if f.fieldtype == "Link":
                targets.add(str(f.options))
            elif f.fieldtype == "DynamicLink":
                holder = d.field_map.get(str(f.options))
                targets.update((holder.options if holder is not None and holder.fieldtype == "Select" else None) or [])
        return targets

    def mark_broken(self, name: str, problems: list[str]) -> None:
        """Pose (ou, vide, retire) la raison pour laquelle un DocType n'est plus écrivable depuis l'arrivée d'un frère."""
        if problems:
            self._broken[name] = problems
        else:
            self._broken.pop(name, None)

    def incomplete(self, name: str) -> list[str]:
        """Pourquoi on n'écrit pas encore ce DocType : les DocTypes qu'il cite et qui ne sont pas chargés (un module frère attendu), ou le
        contrôle qui a échoué à l'arrivée d'un frère. Vide : il est complet. Toute écriture d'un type incomplet est refusée (fermé, 503)."""
        if name in self._broken:
            return list(self._broken[name])
        compiled = self._items.get(name)
        if compiled is None or name not in self._peered:
            return []
        missing = {t for t in self._cited_targets(compiled) if t not in self._items}
        for child in compiled.children.values():
            for cf in child.fields:
                if cf.fieldtype == "Link" and str(cf.options) not in self._items:
                    missing.add(str(cf.options))
        return [f"DocType {t!r} pas encore chargé" for t in sorted(missing)]

    def unregister_owner(self, owner: str) -> list[str]:
        """Retire les DocTypes et les espaces de travail qu'un plugin a apportés (à son `on_unload`). Les documents, eux, restent en base."""
        mine = [name for name, who in self._owners.items() if who == owner]
        for name in mine:
            self._remove(name)
        for name in [n for n, (_, _, who) in self._waiting.items() if who == owner]:
            del self._waiting[name]
            self._peered.discard(name)
        # Un parent d'un AUTRE plugin qui avait une table de celui-ci retourne en attente : il revient quand le plugin revient.
        for name, compiled in list(self._items.items()):
            if any(str(f.options) not in self._items for f in compiled.definition.fields if f.fieldtype == "Table"):
                defn, source = self._origins[name]
                who, peered = self._owners.get(name), name in self._peered
                self._remove(name)
                self._waiting[name] = (defn, source, who)
                if peered:
                    self._peered.add(name)
        self._broken = {n: why for n, why in self._broken.items() if n in self._items}
        self._drop_extensions(owner)
        for name in [n for n, who in self._workspace_owners.items() if who == owner]:
            self._workspaces.pop(name, None)
            self._workspace_owners.pop(name, None)
        for target in list(self._contributions):
            kept = [(o, c) for o, c in self._contributions[target] if o != owner]
            if kept:
                self._contributions[target] = kept
            else:
                del self._contributions[target]
        return sorted(mine)

    # ── Espaces de travail ────────────────────────────────────
    def load_workspaces(self, directory: Path, *, owner: str) -> list[str]:
        """Charge les espaces de travail d'un plugin (`*.yaml|yml|json`). Tout ou rien, comme `load_dir`.

        Règles : le NOM de l'espace est le module, donc le nom du plugin (il ne peut pas prendre la page d'un autre module) ; chaque
        raccourci vise un DocType déjà chargé (le sien, ou celui d'un plugin dont il dépend), jamais une table de lignes.
        Un fichier qui porte `contributes_to` au lieu de `name` ajoute ses blocs à l'espace d'un AUTRE module (D27) ; il est rendu
        sous la forme `+<module>` dans la liste retournée.
        """
        if not _OWNER_RE.match(owner):
            raise InvalidDefinition(f"plugin {owner!r}: son nom ne peut pas servir de module d'espace de travail")
        directory = Path(directory)
        if not directory.is_dir():
            raise InvalidDefinition(f"dossier d'espaces de travail introuvable : {directory}")
        parsed: list[WorkspaceDef] = []
        contributed: list[WorkspaceContribution] = []
        for path in sorted(p for p in directory.iterdir() if p.suffix in _SUFFIXES):
            try:
                text = path.read_text(encoding="utf-8")
                raw = json.loads(text) if path.suffix == ".json" else yaml.safe_load(text)
            except (OSError, ValueError, yaml.YAMLError) as e:
                raise InvalidDefinition(f"{path}: illisible ({e})") from e
            if isinstance(raw, dict) and "contributes_to" in raw:
                # D27 : des blocs pour l'espace d'un AUTRE module.
                contribution = parse_contribution(raw, source=str(path))
                target = contribution.contributes_to
                if target == owner:
                    raise InvalidDefinition(f"{path}: `contributes_to` vise le module du plugin lui-même : mettez ces blocs dans son espace")
                if not self._module_exists(target):
                    raise InvalidDefinition(f"{path}: `contributes_to` vise un module inconnu {target!r} (son plugin doit être chargé avant : `requires`)")
                if any(o == owner for o, _ in self._contributions.get(target, [])) or any(c.contributes_to == target for c in contributed):
                    raise InvalidDefinition(f"{path}: ce plugin contribue déjà à l'espace {target!r} (une contribution par module)")
                if problems := self._block_problems(contribution):
                    raise InvalidDefinition(f"{path}: " + "; ".join(problems))
                base = len(self._workspaces[target].blocks) if target in self._workspaces else 0
                total = base + sum(len(c.blocks) for c in self.contributions_to(target)) + len(contribution.blocks)
                if total > MAX_COMPOSED_BLOCKS:
                    raise InvalidDefinition(f"{path}: l'espace {target!r} dépasserait {MAX_COMPOSED_BLOCKS} blocs avec cette contribution")
                contributed.append(contribution)
                continue
            ws = parse_workspace(raw, source=str(path))
            if len(ws.blocks) > MAX_BLOCKS:
                raise InvalidDefinition(f"{path}: un fichier d'espace a au plus {MAX_BLOCKS} blocs (il en a {len(ws.blocks)})")
            if ws.name != owner:
                raise InvalidDefinition(f"{path}: `name` doit être {owner!r} (le plugin qui l'apporte), pas {ws.name!r}")
            if ws.name in self._workspaces or any(p.name == ws.name for p in parsed):
                raise InvalidDefinition(f"{path}: espace de travail {ws.name!r} déjà défini")
            if problems := self._block_problems(ws):
                raise InvalidDefinition(f"{path}: " + "; ".join(problems))
            parsed.append(ws)
        for ws in parsed:
            self._workspaces[ws.name] = ws
            self._workspace_owners[ws.name] = owner
        for contribution in contributed:
            self._contributions.setdefault(contribution.contributes_to, []).append((owner, contribution))
        return [ws.name for ws in parsed] + [f"+{c.contributes_to}" for c in contributed]

    def _module_exists(self, module: str) -> bool:
        """Un module « existe » s'il a un espace déclaré ou au moins un DocType qui lui fabrique son espace."""
        # (Un réglage, `single`, ne fait pas d'espace : un module qui n'a que des réglages n'en montre aucun, une contribution y serait perdue.)
        return module in self._workspaces or any(
            (c.definition.module or "other") == module and not c.definition.istable and not c.definition.single for c in self._items.values()
        )

    def _block_problems(self, ws: WorkspaceDef | WorkspaceContribution) -> list[str]:
        """Ce que les blocs d'un espace (ou d'une contribution) visent doit exister : DocTypes chargés, champs et filtres des mesures."""
        problems = []
        for target in ws.doctypes():
            item = self._items.get(target)
            if item is None and target in self._waiting:
                continue  # attend la table d'un module frère : invisible jusqu'à son arrivée (comme un DocType non chargé)
            if item is None:
                problems.append(f"bloc qui vise un DocType inconnu : {target!r}")
            elif item.definition.istable:
                problems.append(f"bloc qui vise {target!r} : c'est un DocType enfant (ses lignes n'ont pas de liste)")
        for i, block in enumerate(ws.blocks):
            if block.type in ("number_card", "chart"):  # (les autres sont contrôlés plus haut, par `doctypes()`)
                problems += [f"blocs.{i} ({block.label}): {p}" for p in check_measured(block, self._items.get, None)]
            elif block.type == "quick_list" and block.doctype in self._items:
                problems += [f"blocs.{i} ({block.label}): {p}" for p in check_measured(block, self._items.get, None)]
        return problems

    def contributions_to(self, module: str) -> list[WorkspaceContribution]:
        """Les blocs que d'autres plugins ajoutent à l'espace de `module`, dans l'ordre de chargement."""
        return [c for _, c in self._contributions.get(module, [])]

    def workspaces(self) -> list[WorkspaceDef]:
        """Dans l'ordre de la barre latérale : `order`, puis le libellé. Chacun suivi des blocs que d'autres plugins y ajoutent (D27)."""
        return [
            with_contributions(w, self.contributions_to(w.name))
            for w in sorted(self._workspaces.values(), key=lambda w: (w.order, w.label.lower()))
        ]

    def owner_of(self, name: str) -> str | None:
        return self._owners.get(name)

    def _remove(self, name: str) -> None:
        self._items.pop(name, None)
        self._owners.pop(name, None)
        self._origins.pop(name, None)
        self._broken.pop(name, None)
        self._peered.discard(name)

    # ── Extensions de DocType (D26) ───────────────────────────
    def load_extensions(self, directory: Path, *, owner: str) -> list[str]:
        """Charge les extensions d'un plugin (`*.yaml|yml|json`) : des champs et des hooks ajoutés aux DocTypes d'AUTRES plugins.

        Tout ou rien : si une seule ne s'applique pas, aucune ne reste. Retourne les DocTypes étendus. À appeler APRÈS `load_dir` (un champ
        ajouté peut viser un DocType du plugin) ; les DocTypes visés doivent être chargés (ceux d'un plugin dont il dépend, par `requires`).
        """
        if not _OWNER_RE.match(owner):
            raise InvalidDefinition(f"plugin {owner!r}: son nom ne peut pas préfixer des champs ajoutés (attendu : minuscules, chiffres et `_`)")
        directory = Path(directory)
        if not directory.is_dir():
            raise InvalidDefinition(f"dossier d'extensions introuvable : {directory}")
        parsed: list[Extension] = []
        for path in sorted(p for p in directory.iterdir() if p.suffix in _SUFFIXES):
            try:
                text = path.read_text(encoding="utf-8")
                raw = json.loads(text) if path.suffix == ".json" else yaml.safe_load(text)
            except (OSError, ValueError, yaml.YAMLError) as e:
                raise InvalidDefinition(f"{path}: illisible ({e})") from e
            ext = Extension(owner, parse_extension(raw, owner=owner, source=str(path)), str(path))
            target = ext.definition.doctype
            compiled = self._items.get(target)
            why = None
            if compiled is None:
                why = "DocType inconnu (son plugin doit être chargé avant : `requires`)"
            elif (problem := target_problem(compiled.definition, self._owners.get(target), owner)) is not None:
                why = problem
            elif any(x.owner == owner for x in self._extensions.get(target, [])) or any(x.definition.doctype == target for x in parsed):
                why = "ce plugin l'étend déjà (une extension par DocType)"
            if why is not None:
                raise InvalidDefinition(f"{path}: extension de {target!r} : {why}")
            parsed.append(ext)
        before = {x.definition.doctype: (self._items[x.definition.doctype], list(self._extensions.get(x.definition.doctype, []))) for x in parsed}
        try:
            for ext in parsed:
                target = ext.definition.doctype
                origin, source = self._origins[target]
                chain = [*self._extensions.get(target, []), ext]
                self._items[target] = self._compile(merge(origin, chain), source)
                self._extensions[target] = chain
            self.validate_links(list(before))  # un Link ajouté vise un DocType chargé, un `fetch_from` un champ qui existe…
        except Exception:
            for target, (compiled, chain) in before.items():
                self._items[target] = compiled
                if chain:
                    self._extensions[target] = chain
                else:
                    self._extensions.pop(target, None)
            raise
        return sorted(before)

    def _drop_extensions(self, owner: str) -> None:
        """Retire les extensions d'un plugin et recompile les DocTypes qu'elles visaient (s'ils sont là)."""
        for target in [t for t, chain in self._extensions.items() if any(x.owner == owner for x in chain)]:
            chain = [x for x in self._extensions[target] if x.owner != owner]
            if chain:
                self._extensions[target] = chain
            else:
                self._extensions.pop(target)
            if target in self._origins:
                origin, source = self._origins[target]
                try:
                    self._items[target] = self._compile(merge(origin, chain), source)
                except InvalidDefinition:
                    # Une autre extension citait un champ de celle-ci (une condition…) : on garde la fusion d'avant. Ses hooks, dont le
                    # plugin vient de partir, font REFUSER les sauvegardes (503) : jamais une règle silencieusement oubliée.
                    pass

    def extensions_of(self, doctype: str) -> list[dict[str, Any]]:
        """Qui étend ce DocType, et avec quoi : `[{plugin, label, fields, hooks}]` (pour `/meta` et les messages)."""
        return [
            {"plugin": x.owner, "label": x.definition.label, "fields": [f.fieldname for f in x.definition.fields],
             "hooks": x.definition.hooks.all_refs()}
            for x in self._extensions.get(doctype, [])
        ]

    def validate_links(self, only: list[str] | None = None, *, allow_missing: bool = False) -> None:
        """Toute cible de champ Link doit être un DocType chargé : sinon aucun document ne pourrait jamais la satisfaire.

        À appeler une fois TOUS les dossiers de l'opérateur chargés (une définition peut viser un DocType défini plus loin).
        `only` limite le contrôle à ces DocTypes : un plugin qui arrive ne répond pas des défauts des autres. `allow_missing` (modules frères) :
        une cible pas encore chargée n'est pas une faute — le type reste incomplet (`incomplete`) jusqu'à son arrivée, où tout se refait.
        """
        problems: list[str] = []
        self._link_problems(only, problems, allow_missing=allow_missing)
        if problems:
            raise InvalidDefinition("champs Link invalides :\n" + "\n".join(f"  - {p}" for p in problems))

    def link_problems(self, name: str) -> list[str]:
        """Les mêmes contrôles, pour UN DocType, rendus au lieu d'être levés (cibles absentes tolérées) : ce qu'on refait à l'arrivée d'un frère."""
        problems: list[str] = []
        self._link_problems([name], problems, allow_missing=True)
        return problems

    def _link_problems(self, only: list[str] | None, problems: list[str], *, allow_missing: bool) -> None:
        for name in (self.names() if only is None else sorted(only)):
            self._check_units(name, problems)
            self._check_fetch(name, problems)
            self._check_link_filters(name, problems)
            self._check_dynamic(name, problems, allow_missing=allow_missing)
            self._check_mappings(name, problems, allow_missing=allow_missing)
            for f in self._items[name].definition.fields:
                if f.fieldtype != "Link":
                    continue
                target = self._items.get(str(f.options))
                if target is None and allow_missing:
                    continue
                if target is None:
                    problems.append(f"{name}.{f.fieldname}: DocType cible inconnu {f.options!r}")
                elif target.definition.istable:
                    problems.append(f"{name}.{f.fieldname}: {f.options!r} est un DocType enfant, on ne peut pas le citer par un Link")
                elif (allowed := f.allowed_docstatus) is not None and 0 not in allowed and not target.definition.submittable:
                    problems.append(
                        f"{name}.{f.fieldname}: `target_status` vise {f.options!r}, qui n'est jamais soumettable : aucun document ne satisfera jamais cette contrainte"
                    )

    # Les deux prédicats de type vivent dans `mapping.py`, avec la logique qu'ils gouvernent (import local : `mapping`
    # importe `schema`, et le registre n'a besoin d'eux qu'ici).
    def _check_mappings(self, name: str, problems: list[str], *, allow_missing: bool = False) -> None:
        """`created_from` (process/mapper.md §6) : tout ce qui rendrait un préremplissage impossible échoue ICI, au chargement.

        Rien n'est vérifié à la première saisie : une source inconnue, un champ absent d'un côté, deux quantités de genres
        différents sont des fautes de DÉFINITION, et le moteur refuse de charger plutôt que de servir un brouillon faux.
        """
        d = self._items[name].definition
        for m in d.created_from or []:
            where = f"{name}.created_from[{m.source}]"
            src = self._items.get(m.source)
            if src is None and allow_missing:
                continue  # un module frère pas encore chargé : contrôlé à son arrivée
            if src is None:
                problems.append(f"{where}: source inconnue {m.source!r} (chargez-la avant, ou nommez-la dans `requires`)")
                continue
            s = src.definition
            if s.istable:
                problems.append(f"{where}: {m.source!r} est un DocType enfant, il n'a pas de documents à copier")
                continue
            if s.single or s.source is not None:
                problems.append(f"{where}: {m.source!r} est {'un document unique' if s.single else 'un DocType virtuel'}, il ne peut pas être une source")
                continue
            if m.source_status == "submitted" and not s.submittable:
                problems.append(
                    f"{where}: `source_status: submitted` mais {m.source!r} n'est jamais soumettable : aucun document ne serait jamais mappable"
                )
            self._check_mapped_fields(where, d, s, m.fields, m.exclude, problems)
            for target_table, lines in m.lines.items():
                self._check_mapped_lines(where, d, s, target_table, lines, problems)

    def _check_mapped_fields(self, where: str, d: Any, s: Any, mapped: Mapping[str, str], exclude: Sequence[str], problems: list[str]) -> None:
        """Les exceptions de recopie : les deux champs existent, et la valeur de l'un sera acceptée par l'autre."""
        for target_field, source_field in mapped.items():
            tf, sf = d.field_map.get(target_field), s.field_map.get(source_field)
            if tf is None:
                problems.append(f"{where}: fields: {target_field!r} n'est pas un champ de {d.name}")
            if sf is None:
                problems.append(f"{where}: fields: {source_field!r} n'est pas un champ de {s.name}")
            if tf is not None and sf is not None and not mappable_types(sf, tf):
                problems.append(
                    f"{where}: fields: {source_field!r} ({sf.fieldtype}) ne peut pas remplir {target_field!r} ({tf.fieldtype})"
                )
        for field in exclude:
            if field not in d.field_map:
                problems.append(f"{where}: exclude: {field!r} n'est pas un champ de {d.name}")

    def _check_mapped_lines(self, where: str, d: Any, s: Any, target_table: str, lines: Any, problems: list[str]) -> None:
        """Une table se remplit depuis une table, et les champs de trace sont du bon genre sur la LIGNE cible."""
        at = f"{where}.lines.{target_table}"
        tfield, sfield = d.field_map.get(target_table), s.field_map.get(lines.from_)
        if tfield is None or tfield.fieldtype != "Table":
            problems.append(f"{at}: {target_table!r} n'est pas un champ Table de {d.name}")
            return
        if sfield is None or sfield.fieldtype != "Table":
            problems.append(f"{at}: from: {lines.from_!r} n'est pas un champ Table de {s.name}")
            return
        target_child, source_child = self._items.get(str(tfield.options)), self._items.get(str(sfield.options))
        if target_child is None or source_child is None:
            return  # le DocType de lignes manquant est déjà signalé par la compilation
        tc, sc = target_child.definition, source_child.definition
        self._check_mapped_fields(at, tc, sc, lines.fields, lines.exclude, problems)
        if lines.reference is not None:
            ref = tc.field_map.get(lines.reference)
            if ref is None or ref.fieldtype != "Text":
                problems.append(f"{at}: reference: {lines.reference!r} doit être un champ Text de {tc.name} (il porte l'indice de la ligne source)")
        if lines.document is not None:
            doc = tc.field_map.get(lines.document)
            if doc is None or doc.fieldtype != "Link":
                problems.append(f"{at}: document: {lines.document!r} doit être un champ Link de {tc.name}")
            elif str(doc.options) != s.name:
                problems.append(f"{at}: document: {lines.document!r} vise {doc.options!r}, pas la source {s.name!r}")
        if lines.quantity is not None:
            sq, tq = sc.field_map.get(lines.quantity.source), tc.field_map.get(lines.quantity.target)
            if sq is None:
                problems.append(f"{at}: quantity.source: {lines.quantity.source!r} n'est pas un champ de {sc.name}")
            if tq is None:
                problems.append(f"{at}: quantity.target: {lines.quantity.target!r} n'est pas un champ de {tc.name}")
            if sq is not None and tq is not None and not same_number_kind(sq, tq):
                problems.append(
                    f"{at}: quantity: {lines.quantity.source!r} ({sq.fieldtype}) et {lines.quantity.target!r} ({tq.fieldtype}) ne sont pas "
                    "des nombres du même genre : la soustraction du reste à faire doit rester exacte"
                )

    def _check_fetch(self, name: str, problems: list[str]) -> None:
        """`fetch_from: lien.champ` : le Link vise un DocType chargé qui a ce champ, et la valeur copiée sera acceptée par le champ qui la reçoit."""
        d = self._items[name].definition
        for f in d.fields:
            if f.fetch_from is None:
                continue
            link_name, _, source_name = f.fetch_from.partition(".")
            link = d.field_map[link_name]  # la présence et le type du Link sont vérifiés par le schéma
            target = self._items.get(str(link.options))
            where = f"{name}.{f.fieldname}: fetch_from {f.fetch_from!r}"
            if target is None:
                continue  # la cible inconnue est signalée plus bas, avec les autres Link
            source = target.definition.field_map.get(source_name)
            if source is None:
                problems.append(f"{where} : le DocType {link.options!r} n'a pas de champ {source_name!r}")
                continue
            if (why := fetch_incompatible(f, source)) is not None:
                problems.append(f"{where} : {why}")

    def _check_link_filters(self, name: str, problems: list[str]) -> None:
        """`link_filters` (D29) : les champs filtrés existent chez la CIBLE, s'y filtrent, et la valeur donnée leur convient.

        Une valeur littérale passe par le `coerce_filter` de la cible : exactement le contrôle qu'un filtre de liste subirait, donc
        une faute de définition échoue au chargement et jamais à la première saisie. Une valeur `$champ` est comparée en TYPE au
        champ filtré (un Link se filtre par un Link vers le même DocType, un Select par les mêmes valeurs).
        """
        d = self._items[name].definition
        for f in d.fields:
            if not f.link_filters:
                continue
            target = self._items.get(str(f.options))
            if target is None:
                continue  # cible inconnue : signalée avec les autres Link
            for key, value in f.link_filters.items():
                where = f"{name}.{f.fieldname}: link_filters.{key}"
                filtered = target.definition.field_map.get(key)
                if filtered is None and key not in SYSTEM_FILTERS:
                    problems.append(f"{where} : le DocType {f.options!r} n'a pas de champ {key!r}")
                    continue
                if isinstance(value, str) and value.startswith(LINK_FILTER_REF):
                    source = d.field_map[value[len(LINK_FILTER_REF):]]  # présence vérifiée par le schéma
                    if (why := _filter_incompatible(filtered, source, key)) is not None:
                        problems.append(f"{where} : {why}")
                    continue
                try:
                    target.coerce_filter(key, value)
                except ValidationFailed as e:
                    problems.append(f"{where} : {e.message or 'valeur refusée par le champ filtré'}")

    def _check_dynamic(self, name: str, problems: list[str], *, allow_missing: bool = False) -> None:
        """Un DynamicLink dont le champ de type est un Select ne peut désigner que des DocTypes chargés (les options le disent d'avance)."""
        d = self._items[name].definition
        for f in d.fields:
            if f.fieldtype != "DynamicLink":
                continue
            holder = d.field_map[str(f.options)]
            for option in (holder.options if holder.fieldtype == "Select" else None) or []:
                target = self._items.get(option)
                if target is None and allow_missing:
                    continue
                if target is None:
                    problems.append(f"{name}.{f.fieldname}: DynamicLink : l'option {option!r} de {holder.fieldname!r} n'est pas un DocType chargé")
                elif target.definition.istable:
                    problems.append(f"{name}.{f.fieldname}: DynamicLink : {option!r} est un DocType enfant, on ne peut pas le citer")
                elif (allowed := f.allowed_docstatus) is not None and 0 not in allowed and not target.definition.submittable:
                    problems.append(
                        f"{name}.{f.fieldname}: `target_status` peut viser {option!r}, qui n'est jamais soumettable : aucun document ne satisfera jamais cette contrainte"
                    )

    def _check_units(self, name: str, problems: list[str]) -> None:
        """`unit_from` doit désigner un champ qui existe : `uom` (même document ou même ligne) ou `item.uom` (champ Link, puis champ de la cible)."""
        d = self._items[name].definition
        for f in d.fields:
            if f.unit_from is None:
                continue
            first, _, rest = f.unit_from.partition(".")
            holder = d.field_map.get(first)
            where = f"{name}.{f.fieldname}: unit_from {f.unit_from!r}"
            if holder is None or holder.fieldtype == "Table":
                problems.append(f"{where} : champ inconnu {first!r}")
            elif not rest:
                if holder.fieldtype not in ("Text", "Select"):
                    problems.append(f"{where} : {first!r} doit être un texte (Text ou Select), pas un {holder.fieldtype}")
            elif holder.fieldtype != "Link":
                problems.append(f"{where} : {first!r} n'est pas un champ Link")
            else:
                target = self._items.get(str(holder.options))
                unit = target.definition.field_map.get(rest) if target is not None else None
                if target is not None and unit is None:
                    problems.append(f"{where} : le DocType {holder.options!r} n'a pas de champ {rest!r}")
                elif unit is not None and unit.fieldtype not in ("Text", "Select"):
                    problems.append(f"{where} : {holder.options}.{rest} doit être un texte (Text ou Select), pas un {unit.fieldtype}")

    def referrers(self, target: str) -> list[Referrer]:
        """Les champs Link qui visent `target`, dans les documents ET dans les lignes de leurs tables (le sien compris).

        Les champs DynamicLink y sont TOUS : ils peuvent viser n'importe quel DocType, `type_field` dit au moment de chercher lequel.
        """
        out = []
        for name in self.names():
            compiled = self._items[name]
            if compiled.definition.istable:
                continue  # les lignes sont atteintes par le champ Table du parent, jamais seules
            for f in compiled.definition.fields:
                if f.fieldtype == "Link" and f.options == target:
                    out.append(Referrer(name, f.fieldname))
                elif f.fieldtype == "DynamicLink":
                    out.append(Referrer(name, f.fieldname, None, str(f.options)))
                elif f.fieldtype == "Table":
                    child = compiled.children[str(f.options)]
                    for cf in child.fields:
                        if cf.fieldtype == "Link" and cf.options == target:
                            out.append(Referrer(name, cf.fieldname, f.fieldname))
                        elif cf.fieldtype == "DynamicLink":
                            out.append(Referrer(name, cf.fieldname, f.fieldname, str(cf.options)))
        return out

    def get(self, name: str) -> CompiledDocType:
        try:
            return self._items[name]
        except KeyError:
            raise NotFound("doctype_not_found", f"DocType inconnu : {name!r}") from None

    def has(self, name: str) -> bool:
        """Le DocType est-il chargé ? (Un habillage enregistré peut citer le type d'un plugin parti depuis.)"""
        return name in self._items

    def names(self) -> list[str]:
        return sorted(self._items)

    def clear(self) -> None:
        self._peered.clear()
        self._waiting.clear()
        self._broken.clear()
        self._items.clear()
        self._owners.clear()
        self._workspaces.clear()
        self._workspace_owners.clear()
        self._origins.clear()
        self._extensions.clear()
        self._contributions.clear()


class DocTypeChannel:
    """Ce que voient les AUTRES plugins du registre : le service exporté `doctype_registry`.

    Un plugin métier qui déclare `requires: [xcore-doctype]` y apporte son dossier de DocTypes, puis le retire à son départ :

        # dans on_load (AVANT de demander ses grants à `doctype_grants`, pour qu'ils les incluent)
        self.get_service("doctype_registry").load_dir(self.ctx.name, Path(__file__).parent.parent / "doctypes")
        # dans on_unload
        self.get_service("doctype_registry").unregister_owner(self.ctx.name)

    Le plugin passe SON nom (comme pour `doctype_hooks.register(owner=…)`) : tout DocType du dossier doit avoir `module: <ce nom>`.
    Les cibles de ses champs Link doivent déjà être chargées (les siennes, ou celles d'un plugin dont il dépend par `requires`) :
    xcore charge un plugin après ceux qu'il requiert, c'est ce qui donne l'ordre.
    Le registre n'est pas exposé : ni lecture des définitions compilées, ni retrait d'un DocType qu'on n'a pas apporté.
    """

    def __init__(
        self, registry: DocTypeRegistry, logger: Any = None, *, unique_check: Callable[[CompiledDocType], Awaitable[None]] | None = None,
        desk: DeskExtensions | None = None,
    ) -> None:
        self._registry, self._log, self._unique_check = registry, logger, unique_check
        self._desk = desk if desk is not None else DeskExtensions()
        self._checks: set[asyncio.Task[None]] = set()  # les contrôles `unique` rétroactifs en cours (gardés : une tâche orpheline serait ramassée)

    def load_dir(self, owner: str, directory: str | Path, *, peers: bool = False) -> list[str]:
        """`peers=True` : les DocTypes de ce plugin citent ceux de modules FRÈRES qui peuvent se charger APRÈS lui (les modules d'un ERP se
        citent en cycle : accounts ↔ stock ↔ selling…, aucun ordre de `requires` n'existe). Une cible pas encore chargée n'est alors pas une
        faute : le type reste INCOMPLET — lisible, mais toute écriture refusée (503 `doctype_incomplete`) — jusqu'à l'arrivée du frère, où les
        contrôles qui dépendent de la cible se refont ; un parent dont la table enfant vient d'un frère attend, invisible, de pouvoir compiler.
        Sans `peers`, une cible inconnue échoue ici comme avant (les fautes de frappe se voient au chargement)."""
        names = self._registry.load_dir(Path(directory), owner=owner, peers=peers)
        # Tout ce que le dossier apporte doit pouvoir être lié : une cible de Link inconnue échoue ici, pas à la première saisie.
        try:
            self._registry.validate_links(names, allow_missing=peers)
        except InvalidDefinition:
            self._registry.unregister_owner(owner)
            raise
        names += self._welcome(names)
        if peers and (waiting := {n: m for n, m in self._registry.waiting().items() if self._registry.owner_waiting(n) == owner}):
            if self._log is not None:
                self._log.warning("doctypes waiting for a peer module", plugin=owner, waiting=waiting)
        if peers and (incomplete := {n: why for n in names if (why := self._registry.incomplete(n))}):
            if self._log is not None:
                self._log.warning("doctypes incomplete until their peer modules load", plugin=owner, incomplete=incomplete)
        if self._unique_check is not None:
            # Un champ rendu `unique` après coup ne s'active pas sur des documents déjà en double (D25). Le contrôle lit la base (asynchrone)
            # alors que `load_dir` est appelé sans `await` par les plugins : il part en tâche, et un double RETIRE les DocTypes du plugin
            # (fermé en cas de doute), avec une erreur au journal. Hors boucle (outil, test synchrone), il s'exécute sur place.
            pending = [c for c in (self._registry.get(n) for n in names) if c.definition.unique_fields and not c.definition.istable]
            if pending:
                check = self._check_unique(owner, pending)
                try:
                    task = asyncio.get_running_loop().create_task(check)
                except RuntimeError:
                    asyncio.run(check)
                else:
                    self._checks.add(task)
                    task.add_done_callback(self._checks.discard)
        if self._log is not None:
            self._log.info("doctypes registered by plugin", plugin=owner, doctypes=names)
        return names

    def _welcome(self, arrived: list[str]) -> list[str]:
        """Ce que l'arrivée de ces DocTypes débloque chez les autres : les parents qui attendaient l'une de ces tables s'enregistrent, et les
        types qui les citent refont leurs contrôles dépendant de la cible. Une faute révélée ainsi BRISE le type qui cite (écritures refusées,
        journalisé) au lieu de faire échouer le plugin qui arrive : il n'en répond pas. Rend les noms des parents enregistrés."""
        registered, failed = self._registry.settle()
        for name, why in failed.items():
            if self._log is not None:
                self._log.error("waiting doctype failed to compile when its peer arrived", doctype=name, reason=why)
        for name in sorted(set(registered) | set(self._registry.citing(arrived + registered))):
            problems = self._registry.link_problems(name)
            self._registry.mark_broken(name, problems)
            if problems and self._log is not None:
                self._log.error("doctype broken by a peer module's definition", doctype=name, problems=problems)
        return registered

    async def _check_unique(self, owner: str, compiled: list[CompiledDocType]) -> None:
        try:
            for c in compiled:
                await self._unique_check(c)  # type: ignore[misc]  # présent : seul appelant, sous `if self._unique_check`
        except InvalidDefinition as e:
            self._registry.unregister_owner(owner)
            if self._log is not None:
                self._log.error("doctypes withdrawn: retroactive unique check failed", plugin=owner, reason=str(e))

    async def settled(self) -> None:
        """Attendre la fin des contrôles `unique` rétroactifs lancés par `load_dir` (tests, outils)."""
        while self._checks:
            await asyncio.gather(*list(self._checks), return_exceptions=True)

    def load_workspaces(self, owner: str, directory: str | Path) -> list[str]:
        """À appeler APRÈS `load_dir` (les raccourcis visent des DocTypes chargés) : `…load_workspaces(self.ctx.name, dossier / "workspaces")`."""
        names = self._registry.load_workspaces(Path(directory), owner=owner)
        if self._log is not None:
            self._log.info("workspaces registered by plugin", plugin=owner, workspaces=names)
        return names

    def load_extensions(self, owner: str, directory: str | Path) -> list[str]:
        """Des champs et des hooks ajoutés aux DocTypes d'AUTRES plugins (D26) : `…load_extensions(self.ctx.name, dossier / "extends")`, APRÈS
        `load_dir`. Les hooks qu'elles citent doivent être enregistrés (`doctype_hooks`) : sinon les sauvegardes du DocType étendu sont refusées."""
        names = self._registry.load_extensions(Path(directory), owner=owner)
        if self._log is not None:
            self._log.info("doctype extensions registered by plugin", plugin=owner, doctypes=names)
        return names

    def load_desk(self, owner: str, directory: str | Path) -> bool:
        """L'extension JavaScript du Desk que ce plugin livre (D28) : un dossier avec `index.js`. Vrai si l'opérateur la laisse servir
        (`doctype.desk.extensions`) ; sinon elle est journalisée et le plugin reste utilisable sans elle."""
        return self._desk.register(owner, directory)

    def unregister_owner(self, owner: str) -> list[str]:
        self._desk.unregister(owner)
        return self._registry.unregister_owner(owner)
