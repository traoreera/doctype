"""Service `Document` : la seule porte vers les données (doc/doctype.md §5, D20).

Les routes HTTP et les actions xcore ne sont que des enveloppes qui construisent un
`RequestContext`. Ce service ne lit jamais d'état ambiant.

Permissions (D6) : refus par défaut. Une action est refusée (403) si aucune règle ne l'accorde aux rôles de
l'utilisateur ; un document hors du périmètre de LECTURE est « introuvable » (404), pour ne pas révéler son
existence. Modifier ou supprimer exige donc de pouvoir lire le document, puis d'avoir l'action sur sa valeur
actuelle ET, pour une écriture, sur la valeur écrite (on ne crée ni ne déplace un document hors de son périmètre).
Le contexte système n'est pas soumis aux permissions (D20) mais reste journalisé.

Ordre d'une sauvegarde (doc/doctype.md §11, D2, D21) :
1. identité, permission de l'action, disponibilité des hooks (un hook référencé mais non enregistré REFUSE la
   sauvegarde : ne jamais l'appliquer sans validation) ;
2. validation stricte du schéma, puis contrôle de la valeur écrite par le périmètre de permission ;
3. hooks `action:` (plugins sandboxés), SANS connexion ni verrou tenus : la réponse est revalidée ;
4. transaction : hooks de confiance `validate` puis `before_save`, revalidation, écriture avec contrôle de version ;
5. commit, sortie du bloc de session, puis `after_save` et événement : des notifications qui ne défont rien.
Les hooks `action:` s'exécutent donc AVANT les hooks de confiance, quel que soit l'événement qui les déclare.

Documents soumettables (palier 40 %, phase A) : un brouillon (`docstatus` 0) se soumet (1), un document soumis s'annule (2) et un
document annulé s'amende (une copie en brouillon). Soumettre ou annuler est UNE transaction :
1. identité, droit `submit` / `cancel` sur la valeur actuelle du document, état de départ, version, hooks disponibles ;
2. transaction : le changement d'état D'ABORD (un `UPDATE` conditionnel sur l'état et la version : une soumission rejouée ou
   simultanée n'en passe qu'une, et sur SQLite c'est cette écriture qui prend le verrou de la base pour tout le reste) ;
3. l'entrée d'historique, puis les hooks `on_submit` / `on_cancel`, qui peuvent écrire d'autres documents (`HookTransaction`)
   et tout annuler en refusant ;
4. commit, puis l'événement `doctype.<nom>.after_submit` / `after_cancel` (sans contenu).
Un document soumis ou annulé est figé : ni modification ni suppression.
"""

from __future__ import annotations

import asyncio
import re
import secrets
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, tzinfo
from types import MappingProxyType
from typing import Any, AsyncIterator, Awaitable, Callable, Mapping, Protocol, Sequence

from . import aggregation, fieldtypes, integrity, memory
from .builder import CompiledDocType
from .decimals import canonical
from .context import RequestContext
from .db.rls import bind_tenant
from .db.tables import SystemTables
from .errors import (
    AlreadyExists,
    DoctypeError,
    DocumentInUse,
    DocumentLocked,
    InvalidDefinition,
    InvalidDocStatus,
    NotFound,
    PermissionDenied,
    ValidationFailed,
    VersionConflict,
)
from .hooks import ActionHooks, HookEvent, HookRegistry, HookSettings
from .files import ATTACHMENT_FIELD
from .permissions import ACTIONS, Action, Scope, require, scope_for
from .query import CANCELLED, DRAFT, STATUS_WORDS, SUBMITTED, SYSTEM_SORTABLE, Query, StoredDoc, UniqueViolation
from .registry import DocTypeRegistry
from .schema import FILE_TYPES, TODAY, DocTypeDef, FieldDef, NamingField
from .sealing import MASK, Sealer
from .sources import DocumentSource, DocumentSources
from .overrides import (
    KIND_DASHBOARD,
    KIND_DASHBOARD_USER,
    KIND_FORM,
    KIND_WORKSPACE,
    KIND_WORKSPACE_USER,
    apply_dashboard_override,
    apply_form_override,
    keep_hidden,
    validate_dashboard_override,
    validate_form_override,
    user_target,
    validate_workspace_override,
    workspace_from_override,
)
from .workspaces import (
    WorkspaceDef,
    module_label,
    parse_workspace,
    ChartBlock,
    HeaderBlock,
    LinksCardBlock,
    NumberCardBlock,
    ParagraphBlock,
    QuickListBlock,
    ShortcutBlock,
    SpacerBlock,
)
from .versions import diff, snapshot

MAX_PAGE = 200
DEFAULT_PAGE = 20
MAX_SEARCH = 50
MAX_HISTORY = 200
MAX_NAME = 140  # largeur de la colonne `name`
MAX_UNIQUE_VALUE = 500  # largeur de la colonne `value` de xdt_unique
MAX_BULK = 100  # documents par action en lot
BULK_ACTIONS = ("update", "delete", "submit", "cancel")
# Un nom se lit dans une URL (`/app/Client/<nom>`) et dans un chemin d'API : ni séparateur, ni caractère de contrôle, ni ce qui change le sens d'une URL.
_FORBIDDEN_IN_NAME = re.compile(r"[\x00-\x1f\x7f/\\?#%]")
RESERVED_NAMES = frozenset({"new"})  # `/app/<Type>/new` ouvre le formulaire de création : un document de ce nom serait inatteignable


def valid_name(value: Any, field: str = "name") -> str:
    """Un nom de document choisi (par l'utilisateur ou tiré d'un champ), ou une `ValidationFailed` adressée à `field`."""

    def refuse(msg: str) -> ValidationFailed:
        return ValidationFailed(details=[{"field": field, "msg": msg, "type": "invalid_name"}])

    if not isinstance(value, str) or not value:
        raise refuse("Un nom est un texte non vide")
    if value != value.strip():
        raise refuse("Le nom ne commence ni ne finit par une espace")
    if len(value) > MAX_NAME:
        raise refuse(f"Le nom dépasse {MAX_NAME} caractères")
    if _FORBIDDEN_IN_NAME.search(value):
        raise refuse("Le nom ne peut contenir ni / \\ ? # %, ni caractère de contrôle")
    if value.lower() in RESERVED_NAMES:
        raise refuse(f"« {value} » est un mot réservé")
    return value


# action -> (état de départ, état d'arrivée, hook de la transition)
_TRANSITIONS = {"submit": (DRAFT, SUBMITTED, "on_submit"), "cancel": (SUBMITTED, CANCELLED, "on_cancel")}
_AMENDMENT = re.compile(r"^(?P<root>.+)-(?P<n>\d+)$")


class _Logger(Protocol):
    def info(self, msg: str, **kw: Any) -> None: ...
    def error(self, msg: str, **kw: Any) -> None: ...


# Revalide une valeur (schéma strict, puis périmètre d'écriture) et retourne les données à stocker. `old` n'est fourni
# que pour l'entrée d'un CLIENT : il active `read_only_depends_on`, qu'un hook a le droit de contourner.
Finalize = Callable[..., dict[str, Any]]


class DocumentService:
    def __init__(
        self,
        registry: DocTypeRegistry,
        db: Any,
        tables: SystemTables,
        logger: _Logger,
        *,
        hooks: HookRegistry | None = None,
        actions: ActionHooks | None = None,
        events: Any = None,
        hook_settings: HookSettings | None = None,
        timezone: tzinfo = UTC,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        rls: bool = False,
        sources: DocumentSources | None = None,
        sealer: Sealer | None = None,
    ) -> None:
        self._registry = registry
        self._sealer = sealer or Sealer(None)
        self._sources = sources
        self._db = db
        self._tables = tables
        self._log = logger
        self._hooks = hooks or HookRegistry()
        self._actions = actions
        self._events = events
        self._hook_settings = hook_settings or HookSettings()
        # Les parties de date d'une série (`{YYYY}`…) se lisent dans le fuseau de l'organisation, pas en UTC : une facture
        # émise le 1er janvier à 00 h 30 ne doit pas porter l'année précédente. `clock` est injectable pour les tests.
        self._timezone, self._clock = timezone, clock
        self.files: Any = None  # le `FileService`, posé par le plugin après coup (il a besoin de ce service)
        self._rls = rls  # PostgreSQL : le tenant du contexte est posé dans chaque transaction (db/rls.py)

    # ── Méta ──────────────────────────────────────────────────
    def list_meta(self, ctx: RequestContext) -> list[dict[str, Any]]:
        """Seuls les DocTypes sur lesquels l'utilisateur a au moins un droit."""
        self._enter(ctx, "meta.list")
        out = []
        for name in self._registry.names():
            d = self._registry.get(name).definition
            if not d.istable and any(scope_for(d, ctx, a).granted for a in ACTIONS):
                # `create` : l'accueil du Desk propose « Nouveau » seulement là où l'utilisateur peut créer (le serveur reste juge).
                out.append({"name": d.name, "label": d.label or d.name, "module": d.module, "icon": d.icon, "single": d.single,
                            **({"settings_group": d.settings_group} if d.settings_group else {}),
                            **({"virtual": True} if d.source else {}),
                            "create": scope_for(d, ctx, "create").granted and not d.single})
        return out

    # ── Espaces de travail ────────────────────────────────────
    def _readable(self, ctx: RequestContext, doctype: str) -> DocTypeDef | None:
        d = self._registry.get(doctype).definition
        return d if scope_for(d, ctx, "read").granted else None

    def _shortcut(self, ctx: RequestContext, doctype: str, label: str | None, new: bool) -> dict[str, Any] | None:
        d = self._registry.get(doctype).definition
        # Un réglage (document unique) n'est pas un raccourci d'application : tous les réglages vivent dans la page Paramètres du Desk.
        if d.single or not scope_for(d, ctx, "read").granted:
            return None
        return {"type": "shortcut", "doctype": d.name, "label": label or d.label or d.name, "icon": d.icon,
                "create": bool(new and not d.single and scope_for(d, ctx, "create").granted), "single": d.single}

    def _visible_block(self, ctx: RequestContext, b: Any) -> dict[str, Any] | None:
        """Le bloc tel que le Desk le reçoit, ou `None` : il parle d'un type que l'utilisateur ne peut pas lire."""
        if isinstance(b, ShortcutBlock):
            return self._shortcut(ctx, b.doctype, b.label, b.new)
        if isinstance(b, (NumberCardBlock, ChartBlock, QuickListBlock)):
            d = self._readable(ctx, str(b.doctype))
            if d is None:
                return None
            out = b.model_dump(mode="json", exclude_none=True)
            if isinstance(b, QuickListBlock):
                out.update(title_field=d.title_field, submittable=d.submittable, doctype_label=d.label or d.name)
            return out
        if isinstance(b, LinksCardBlock):
            links = []
            for link in b.links:
                d = self._readable(ctx, link.doctype)
                if d is not None:
                    links.append({"doctype": d.name, "label": link.label or d.label or d.name, "icon": d.icon})
            return {"type": "card", "title": b.title, "cols": b.cols, "links": links} if links else None
        return None

    def _serve_blocks(self, ctx: RequestContext, blocks: Sequence[Any]) -> tuple[list[dict[str, Any]], set[int], bool]:
        """Les blocs d'une liste, FILTRÉS par les droits de `ctx` : `(blocs servis, rangs montrés, y a-t-il du contenu)`.

        Titres, espacements et textes n'ont de sens qu'à côté d'une donnée : on les garde seulement si un bloc visible les suit. Les rangs
        montrés servent à retrouver ce que l'utilisateur NE VOIT PAS (pour le conserver quand il enregistre : `_keep_hidden`).
        """
        served: list[dict[str, Any]] = []
        shown: set[int] = set()
        pending: list[tuple[int, dict[str, Any]]] = []
        content = False
        for i, b in enumerate(blocks):
            if isinstance(b, HeaderBlock):
                # Un nouveau titre remplace celui dont la section n'a rien montré (les espacements, eux, se gardent).
                pending = [x for x in pending if x[1]["type"] == "spacer"] + [(i, b.model_dump(mode="json", exclude_none=True))]
            elif isinstance(b, (SpacerBlock, ParagraphBlock)):
                pending.append((i, b.model_dump(mode="json", exclude_none=True)))
            elif (item := self._visible_block(ctx, b)) is not None:
                for j, x in pending:
                    served.append(x)
                    shown.add(j)
                pending = []
                served.append(item)
                shown.add(i)
                content = True
        for j, x in pending:
            if x["type"] == "paragraph":  # une note de bas d'espace reste si l'espace a du contenu
                served.append(x)
                shown.add(j)
        return served, shown, content

    def _generated_workspace(self, ctx: RequestContext, module: str) -> WorkspaceDef | None:
        """L'espace FABRIQUÉ d'un module sans fichier d'espace : un aperçu (un compteur par type, puis les derniers documents du premier type),
        puis un raccourci par type lisible. Un module n'est jamais seulement une liste de liens. `None` si le module n'a rien de lisible."""
        types = [
            d for d in (self._registry.get(n).definition for n in self._registry.names())
            if not d.istable and not d.single and (d.module or "other") == module and scope_for(d, ctx, "read").granted
        ]
        if not types:
            return None
        types.sort(key=lambda d: (d.label or d.name).lower())
        tones = ("blue", "teal", "orange", "purple", "green", "red")
        first = types[0]
        blocks: list[dict[str, Any]] = [{"type": "header", "text": "Aperçu"}]
        blocks += [{"type": "number_card", "label": d.label or d.name, "doctype": d.name, "function": "count", "color": tones[i % len(tones)], "cols": 3} for i, d in enumerate(types[:4])]
        blocks.append({"type": "quick_list", "label": f"Derniers : {first.label or first.name}", "doctype": first.name, "limit": 5, "cols": 12})
        blocks.append({"type": "header", "text": "Accès"})
        blocks += [{"type": "shortcut", "doctype": d.name, "new": True} for d in types]
        return parse_workspace({"schema_version": 1, "name": module, "label": module_label(module), "blocks": blocks})

    def _base_workspace(self, ctx: RequestContext, name: str) -> WorkspaceDef | None:
        """L'espace tel que le plugin le décrit (son YAML), ou l'espace fabriqué du module : ce qui reste quand on retire tout habillage."""
        declared = next((w for w in self._registry.workspaces() if w.name == name), None)
        return declared or self._generated_workspace(ctx, name)

    @staticmethod
    def _layered(base: WorkspaceDef, org: Mapping[str, Any], mine: Mapping[str, Any]) -> tuple[WorkspaceDef, str | None]:
        """Couches, de la plus à l'extérieur à la plus proche de l'utilisateur : YAML -> habillage de l'organisation -> habillage personnel.
        Un habillage devenu invalide (un type a disparu) est ignoré au profit de la couche du dessous, jamais une page cassée."""
        for layer, source in (("user", mine), ("org", org)):
            if base.name in source and (found := workspace_from_override(base.name, source[base.name])) is not None:
                return found, layer
        return base, None

    def _workspace_out(self, ctx: RequestContext, ws: WorkspaceDef, blocks: list[dict[str, Any]], layer: str | None) -> dict[str, Any]:
        targets = {b["doctype"] for b in blocks if b.get("doctype")} | {link["doctype"] for b in blocks for link in b.get("links", [])}
        shareable = bool(targets) and all(scope_for(self._registry.get(t).definition, ctx, "write").granted for t in targets)
        return {"name": ws.name, "label": ws.label, "icon": ws.icon, "description": ws.description, "blocks": blocks,
                "layer": layer, "shareable": shareable}

    def list_workspaces(
        self, ctx: RequestContext, *, overrides: Mapping[str, Any] | None = None, personal: Mapping[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """Les espaces de travail que l'utilisateur voit, dans l'ordre de la barre latérale (voir `workspaces.py`).

        `overrides` : les habillages de l'ORGANISATION par nom d'espace ; `personal` : ceux de l'utilisateur (`xdt_layout_override`, chargés par
        `fetch_workspaces`). Le plus proche de l'utilisateur gagne ; sans habillage, le YAML du plugin s'applique. Un module sans fichier
        d'espace a un espace fabriqué, qui s'habille comme les autres.

        Tout est FILTRÉ par ses droits : un raccourci vers un type qu'il ne peut pas lire disparaît, une section sans raccourci aussi, un
        espace vide aussi. `create` (le « + ») n'est offert qu'à qui peut créer. `layer` dit d'où vient la disposition (`user`, `org`, ou
        `null` = celle du plugin) et `shareable` si l'utilisateur peut l'enregistrer pour toute l'organisation (il peut écrire ce qu'elle montre).
        """
        self._enter(ctx, "meta.workspaces")
        org, mine = overrides or {}, personal or {}
        out: list[dict[str, Any]] = []
        covered: set[str] = set()
        for declared in self._registry.workspaces():
            covered.add(declared.name)
            ws, layer = self._layered(declared, org, mine)
            blocks, _, content = self._serve_blocks(ctx, ws.blocks)
            if content:
                out.append(self._workspace_out(ctx, ws, blocks, layer))
        modules = {(d.module or "other") for d in (self._registry.get(n).definition for n in self._registry.names()) if not d.istable and not d.single}
        generated: list[tuple[str, WorkspaceDef]] = []
        for module in modules - covered:
            base = self._generated_workspace(ctx, module)
            if base is not None:
                generated.append((module, base))
        for module, base in sorted(generated, key=lambda kv: module_label(kv[0]).lower()):
            ws, layer = self._layered(base, org, mine)
            blocks, _, content = self._serve_blocks(ctx, ws.blocks)
            if content:
                out.append(self._workspace_out(ctx, ws, blocks, layer))
        return out

    def get_meta(
        self, ctx: RequestContext, doctype: str, *, override: Mapping[str, Any] | None = None, dashboard: Mapping[str, Any] | None = None,
        dashboard_layer: str | None = None,
    ) -> dict[str, Any]:
        """Définition + droits effectifs (`all` | `restricted` | `none` par action). Les règles ne sont jamais exposées.

        `override` : l'habillage de formulaire (`xdt_layout_override`) pour ce tenant, appliqué à `layout` et aux champs ; `dashboard` : celui
        du tableau de bord, qui remplace `views.dashboard`.
        """
        self._enter(ctx, "meta.get", doctype)
        compiled = self._doctype(doctype)
        d = compiled.definition
        rights = {a: scope_for(d, ctx, a).level for a in ACTIONS}
        if all(level == "none" for level in rights.values()):
            raise PermissionDenied()
        if d.single:
            rights["delete"] = "none"  # un document unique ne se crée ni ne se supprime : on le modifie
        meta = d.model_dump(mode="json", exclude_none=True, exclude={"permissions", "hooks"})
        for field in meta["fields"]:
            # Le navigateur reçoit l'ARBRE compilé (jamais la chaîne) : il n'a pas de parseur, il n'évalue que l'arbre.
            conditions = compiled.conditions.get(field["fieldname"], {})
            for kind in ("depends_on", "mandatory_depends_on", "read_only_depends_on"):
                field.pop(kind, None)
                if kind in conditions:
                    field[kind] = conditions[kind]
        _canonical_defaults(meta["fields"])
        # Les défauts « aujourd'hui » se lisent dans le fuseau de l'organisation, celui des séries de numérotation et des contrôles de date.
        meta["timezone"] = str(self._timezone)
        if d.source:
            meta["track_changes"] = False  # le moteur ne stocke pas ses documents : pas d'historique (la source a le sien)
            meta["virtual"] = True
        meta["layout"] = d.effective_layout()
        meta["list_columns"] = d.list_columns()
        meta["views"] = d.effective_views()
        meta["rights"] = rights
        if compiled.children:
            # La grille d'un champ Table a besoin des champs de ses lignes : ils voyagent avec le parent, jamais seuls.
            meta["children"] = {
                name: {"name": name, "label": c.label or name, "fields": c.model_dump(mode="json", exclude_none=True)["fields"]}
                for name, c in compiled.children.items()
            }
            for cname, child in meta["children"].items():
                _canonical_defaults(child["fields"])
                # Les conditions d'une ligne arrivent en arbres, comme celles du document : le navigateur ne parse jamais de chaîne.
                for cfield in child["fields"]:
                    mine = compiled.row_conditions.get(cname, {}).get(cfield["fieldname"], {})
                    for kind in ("depends_on", "mandatory_depends_on", "read_only_depends_on"):
                        cfield.pop(kind, None)
                        if kind in mine:
                            cfield[kind] = mine[kind]
        if override:
            apply_form_override(meta, override)
        if dashboard and apply_dashboard_override(meta, dashboard, d.name):
            meta["views"]["dashboard_layer"] = dashboard_layer  # d'où vient ce tableau de bord : `user` (le sien) ou `org`
        meta["views"]["dashboard_shareable"] = rights["write"] != "none"  # il peut l'enregistrer pour toute l'organisation
        return meta

    # ── Lecture ───────────────────────────────────────────────
    async def get(self, ctx: RequestContext, doctype: str, name: str) -> dict[str, Any]:
        self._enter(ctx, "doc.get", doctype, name)
        compiled = self._doctype(doctype)  # DocType inconnu : 404 distinct de « document absent »
        scope = self._scope(ctx, compiled, "read")
        if (source := self._source(compiled)) is not None:
            found = await self._peek(ctx, compiled, None, name, scope, source)
            if found is None:
                raise NotFound("document_not_found", f"{doctype} {name!r} introuvable")
            return self._present(found)
        async with self._query(ctx) as q:
            doc = await q.get(doctype, name, scope=scope)
        if doc is None and compiled.definition.single and name == doctype:
            return self._virtual_single(compiled)  # jamais enregistré : ses défauts, version 0
        if doc is None:
            raise NotFound("document_not_found", f"{doctype} {name!r} introuvable")
        return self._present(doc)

    def _virtual_single(self, compiled: CompiledDocType) -> dict[str, Any]:
        """Un document unique qu'on n'a pas encore enregistré : une page de réglages doit s'ouvrir, pleine de ses valeurs par défaut. `version: 0`
        dit qu'il n'existe pas encore ; le premier enregistrement (`update` avec la version 0) le crée."""
        d = compiled.definition
        now = self._clock().isoformat()
        defaults = {
            f.fieldname: (canonical(f.default, f.decimals) if f.fieldtype in ("Decimal", "Currency") else f.default)
            for f in d.fields
            if f.default is not None and f.default not in (TODAY, fieldtypes.NOW) and f.fieldtype != "Table"
        }
        data = self._with_today(compiled, defaults)
        return {"doctype": d.name, "name": d.name, "owner": "", "creation": now, "modified": now, "version": 0, "docstatus": 0, "amended_from": None, "data": data}

    async def list(
        self,
        ctx: RequestContext,
        doctype: str,
        *,
        filters: Mapping[str, Any] | None = None,
        order_by: str = "modified",
        descending: bool = True,
        limit: int = DEFAULT_PAGE,
        offset: int = 0,
    ) -> dict[str, Any]:
        self._enter(ctx, "doc.list", doctype)
        compiled = self._doctype(doctype)
        scope = self._scope(ctx, compiled, "read")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_PAGE:
            raise ValidationFailed(f"limit doit être entre 1 et {MAX_PAGE}")
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
            raise ValidationFailed("offset doit être un entier positif ou nul")
        if order_by not in SYSTEM_SORTABLE and order_by not in compiled.definition.field_map:
            raise ValidationFailed(f"tri impossible sur {order_by!r}")
        tables = {f.fieldname for f in compiled.definition.fields if f.fieldtype == "Table"}
        if filters is None or isinstance(filters, Mapping):
            if order_by in tables:
                raise ValidationFailed(f"tri impossible sur {order_by!r} : un champ Table n'a pas d'ordre")
        coerced = aggregation.validate_filters(compiled, filters)
        if (source := self._source(compiled)) is not None:
            chosen = memory.order(compiled.definition, memory.select(compiled.definition, memory.visible(await self._source_docs(ctx, compiled, source), scope), coerced), order_by, descending)
            return {"items": [self._present(d) for d in memory.page(chosen, limit, offset)], "total": len(chosen), "limit": limit, "offset": offset}
        async with self._query(ctx) as q:
            docs, total = await q.list(
                compiled.definition,
                filters=coerced,
                order_by=order_by,
                descending=descending,
                limit=limit,
                offset=offset,
                scope=scope,
            )
        return {"items": [self._present(d) for d in docs], "total": total, "limit": limit, "offset": offset}

    async def aggregate(
        self,
        ctx: RequestContext,
        doctype: str,
        *,
        group_by: str | None = None,
        bucket: str | None = None,
        metrics: Sequence[str] | None = None,
        filters: Mapping[str, Any] | None = None,
        periods: int | None = None,
        limit: int = aggregation.DEFAULT_GROUPS,
    ) -> dict[str, Any]:
        """Compte, additionne ou moyenne les documents que l'utilisateur peut LIRE, regroupés ou non (voir `aggregation.py`).

        C'est la lecture des cartes de nombres, des graphiques et de la vue Rapport. Le périmètre de lecture s'applique comme à la
        liste (un `if_owner` ne compte que les documents du propriétaire), et un type illisible répond comme à la liste.
        """
        self._enter(ctx, "doc.aggregate", doctype)
        compiled = self._doctype(doctype)
        scope = self._scope(ctx, compiled, "read")
        spec = aggregation.resolve(
            compiled.definition, group_by=group_by, bucket=bucket, metrics=metrics, periods=periods, limit=limit
        )
        coerced = aggregation.validate_filters(compiled, filters)
        keys = aggregation.window(spec, now=self._clock(), tz=self._timezone)
        if (source := self._source(compiled)) is not None:
            chosen = memory.select(compiled.definition, memory.visible(await self._source_docs(ctx, compiled, source), scope), coerced)
            rows, truncated = memory.aggregate(compiled.definition, chosen, group=spec.group, metrics=spec.metrics, keys=keys, limit=spec.limit)
            return aggregation.result(compiled.definition, spec, rows, keys, truncated=truncated)
        async with self._query(ctx) as q:
            rows, truncated = await q.aggregate(
                compiled.definition, filters=coerced, group=spec.group, metrics=spec.metrics, keys=keys, limit=spec.limit, scope=scope
            )
        return aggregation.result(compiled.definition, spec, rows, keys, truncated=truncated)

    async def search(self, ctx: RequestContext, doctype: str, q: str = "", *, limit: int = 10) -> dict[str, Any]:
        """Autocomplétion d'un champ Link : les documents lisibles dont le nom ou le titre contient `q` (D6)."""
        self._enter(ctx, "doc.search", doctype)
        compiled = self._doctype(doctype)
        scope = self._scope(ctx, compiled, "read")
        if not isinstance(q, str) or len(q) > 100:
            raise ValidationFailed("q doit être un texte d'au plus 100 caractères")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_SEARCH:
            raise ValidationFailed(f"limit doit être entre 1 et {MAX_SEARCH}")
        if (source := self._source(compiled)) is not None:
            docs = memory.search(compiled.definition, memory.visible(await self._source_docs(ctx, compiled, source), scope), q, limit)
        else:
            async with self._query(ctx) as query:
                docs = await query.search(compiled.definition, q, limit=limit, scope=scope)
        title = compiled.definition.title_field
        return {"items": [{"name": d.name, "title": d.data.get(title) if title else None} for d in docs]}

    async def global_search(self, ctx: RequestContext, q: str = "", *, limit: int = 10) -> dict[str, Any]:
        """Recherche globale (Awesomebar) : les documents lisibles dont le nom ou le titre contient `q`, TOUS types confondus.

        Seuls les types que l'appelant peut LIRE sont interrogés, et le périmètre de lecture est appliqué DANS chaque requête
        (`Query.search`) : un document hors périmètre ne peut donc jamais apparaître (il est « introuvable », comme partout
        ailleurs). `q` de moins de deux caractères ne cherche rien : une saisie d'un caractère ramènerait le premier type venu.
        """
        self._enter(ctx, "doc.global_search")
        if not isinstance(q, str) or len(q) > 100:
            raise ValidationFailed("q doit être un texte d'au plus 100 caractères")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_SEARCH:
            raise ValidationFailed(f"limit doit être entre 1 et {MAX_SEARCH}")
        needle = q.strip()
        if len(needle) < 2:
            return {"items": []}
        found: list[dict[str, Any]] = []
        async with self._query(ctx) as query:
            for name in self._registry.names():
                compiled = self._registry.get(name)
                d = compiled.definition
                if d.istable:
                    continue
                scope = scope_for(d, ctx, "read")
                if not scope.granted:
                    continue
                title = d.title_field
                if (source := self._source(compiled)) is not None:
                    hits = memory.search(d, memory.visible(await self._source_docs(ctx, compiled, source), scope), needle, limit)
                else:
                    hits = await query.search(d, needle, limit=limit, scope=scope)
                for doc in hits:
                    found.append(
                        {
                            "doctype": d.name,
                            "label": d.label or d.name,
                            "name": doc.name,
                            "title": doc.data.get(title) if title else None,
                        }
                    )
                if len(found) >= limit:
                    break
        return {"items": found[:limit]}

    async def tree(self, ctx: RequestContext, doctype: str) -> dict[str, Any]:
        """La forêt d'un type arborescent : chaque document lisible rattaché à son parent (`views.tree.parent`, un Link vers soi-même).

        Le parent est un Link : l'existence et la lisibilité du parent sont déjà garanties à l'écriture. Un parent absent de la
        page (hors bornes, hors du périmètre) devient une racine ; une boucle aussi (elle serait une donnée fautive, pas une
        raison de ne rien renvoyer). L'ordre est celui des noms, tout comme la pagination de la liste.
        """
        self._enter(ctx, "doc.tree", doctype)
        compiled = self._doctype(doctype)
        scope = self._scope(ctx, compiled, "read")
        defn = compiled.definition
        views = defn.views
        if views is None or views.tree is None:
            raise ValidationFailed(f"{doctype!r} n'est pas arborescent (il n'a pas de `views.tree`)")
        if (source := self._source(compiled)) is not None:
            everything = memory.order(defn, memory.visible(await self._source_docs(ctx, compiled, source), scope), "name", False)
            return _tree(defn, views.tree.parent, memory.page(everything, MAX_PAGE, 0), len(everything))
        async with self._query(ctx) as query:
            docs, total = await query.list(defn, filters={}, order_by="name", descending=False, limit=MAX_PAGE, offset=0, scope=scope)
        return _tree(defn, views.tree.parent, docs, total)

    async def connections(self, ctx: RequestContext, doctype: str, name: str) -> dict[str, Any]:
        """Les documents qui CITENT celui-ci (un client : ses factures, ses contacts…), par DocType et par champ, avec leur nombre.

        Il faut pouvoir lire le document ; chaque DocType n'est compté que dans le périmètre de lecture de l'appelant (un type qu'il ne lit pas
        n'apparaît pas, un document qu'il ne verrait pas n'est pas compté). Les liens dynamiques (`DynamicLink`) comptent aussi.
        """
        self._enter(ctx, "doc.connections", doctype, name)
        compiled = self._doctype(doctype)
        read = self._scope(ctx, compiled, "read")
        scopes: dict[str, Scope] = {}
        usable = []
        for ref in self._registry.referrers(doctype):
            if ref.doctype not in scopes:
                scopes[ref.doctype] = scope_for(self._registry.get(ref.doctype).definition, ctx, "read")
            if scopes[ref.doctype].granted:
                usable.append(ref)
        async with self._query(ctx) as q:
            if await self._peek(ctx, compiled, q, name, read) is None:
                raise NotFound("document_not_found", f"{doctype} {name!r} introuvable")
            counted = await q.connections(doctype, name, [r.key() for r in usable], scopes)
        items = []
        for ref_doctype, fieldname, table, count in sorted(counted, key=lambda c: (c[0], c[1], c[2] or "")):
            d = self._registry.get(ref_doctype).definition
            holder = self._registry.get(ref_doctype).children[str(d.field_map[table].options)].field_map[fieldname] if table else d.field_map[fieldname]
            items.append(
                {
                    "doctype": ref_doctype, "label": d.label or ref_doctype, "field": fieldname, "field_label": holder.label or fieldname, "table": table, "count": count,
                    # Un DynamicLink ne désigne ce document que si son champ de type dit ce DocType : la liste filtrée doit poser les deux.
                    **({"type_field": str(holder.options)} if holder.fieldtype == "DynamicLink" else {}),
                }
            )
        return {"items": items}

    async def history(
        self, ctx: RequestContext, doctype: str, name: str, *, limit: int = DEFAULT_PAGE, offset: int = 0
    ) -> dict[str, Any]:
        """Les modifications d'un document, la plus récente d'abord. Il faut pouvoir LIRE le document.

        Un document supprimé n'est plus en base : son entrée de suppression garde son contenu et son propriétaire, ce qui
        permet de juger, avec le même périmètre de lecture, si l'appelant avait le droit de le voir. Hors de ce périmètre
        (ou sans historique), la réponse est « introuvable », comme pour un document absent.
        """
        self._enter(ctx, "doc.history", doctype, name)
        compiled = self._doctype(doctype)
        read = self._scope(ctx, compiled, "read")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_HISTORY:
            raise ValidationFailed(f"limit doit être entre 1 et {MAX_HISTORY}")
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
            raise ValidationFailed("offset doit être un entier positif ou nul")
        if (source := self._source(compiled)) is not None:
            # Pas d'historique côté moteur : le document doit seulement être LISIBLE (puis la liste est vide ; la source garde sa propre trace).
            if await self._peek(ctx, compiled, None, name, read, source) is None:
                raise NotFound("document_not_found", f"{doctype} {name!r} introuvable")
            return {"items": [], "total": 0, "limit": limit, "offset": offset}
        async with self._query(ctx) as q:
            if await q.get(doctype, name, scope=read) is None:
                gone = await q.last_deletion(doctype, name)
                if gone is None or not read.allows(gone["owner"], snapshot(gone["changes"])):
                    raise NotFound("document_not_found", f"{doctype} {name!r} introuvable")
            items, total = await q.versions(doctype, name, limit=limit, offset=offset)
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    # ── Vues sauvegardées (phase C) ───────────────────────────
    async def list_saved_views(self, ctx: RequestContext, doctype: str) -> dict[str, Any]:
        """Les vues sauvegardées de l'appelant pour ce type. Il faut pouvoir LIRE le type (sinon il n'a pas de liste)."""
        self._enter(ctx, "doc.views.list", doctype)
        self._scope(ctx, self._doctype(doctype), "read")
        async with self._query(ctx) as q:
            items = await q.saved_views(doctype, ctx.owner_id)
        return {"items": items}

    async def save_saved_view(
        self, ctx: RequestContext, doctype: str, name: str, *, label: Any, kind: str = "list",
        config: Any = None, is_default: bool = False, previous: str | None = None,
    ) -> dict[str, Any]:
        """Crée ou remplace une vue de liste privée (configuration rejouable : colonnes, filtres, tri)."""
        self._enter(ctx, "doc.views.save", doctype, str(name))
        compiled = self._doctype(doctype)
        self._scope(ctx, compiled, "read")
        name = _view_name(name)
        if not isinstance(label, str) or not 1 <= len(label) <= 140:
            raise ValidationFailed("label doit être un texte de 1 à 140 caractères")
        if kind not in compiled.definition.available_views():
            raise ValidationFailed(f"vue {kind!r} indisponible pour {doctype!r}")
        if not isinstance(is_default, bool):
            raise ValidationFailed("is_default doit être un booléen")
        cleaned = _view_config(compiled, config)
        async with self._query(ctx) as q:
            if previous is not None and previous != name:
                await q.delete_saved_view(doctype, _view_name(previous), ctx.owner_id)  # renommage : l'ancien nom disparaît
            return await q.put_saved_view(
                doctype, name, owner=ctx.owner_id, label=label, kind=kind, config=cleaned, is_default=is_default
            )

    async def delete_saved_view(self, ctx: RequestContext, doctype: str, name: str) -> dict[str, Any]:
        """Supprime une vue de l'appelant. Une vue d'un autre utilisateur (ou d'un autre tenant) est « introuvable »."""
        self._enter(ctx, "doc.views.delete", doctype, name)
        self._scope(ctx, self._doctype(doctype), "read")
        async with self._query(ctx) as q:
            if await q.get_saved_view(doctype, _view_name(name), ctx.owner_id) is None:
                raise NotFound("saved_view_not_found", f"vue {name!r} introuvable")
            await q.delete_saved_view(doctype, name, ctx.owner_id)
        return {"name": name}

    # ── Habillages (`xdt_layout_override`) ────────────────────
    async def _personal(self, q: Query, kind: str, ctx: RequestContext) -> dict[str, Any]:
        """Les habillages PERSONNELS de l'appelant, par nom d'espace ou de type (jamais ceux d'un autre utilisateur du tenant)."""
        suffix = user_target("", ctx.owner_id)  # « @<empreinte> »
        return {o["target"][: -len(suffix)]: o["config"] for o in await q.overrides(kind, suffix=suffix)}

    async def fetch_workspaces(self, ctx: RequestContext) -> list[dict[str, Any]]:
        """Comme `list_workspaces`, mais lit d'abord les habillages (de l'organisation, puis les siens) : c'est le chemin des routes et de l'action."""
        self._enter(ctx, "meta.workspaces")
        async with self._query(ctx) as q:
            org = {o["target"]: o["config"] for o in await q.overrides(KIND_WORKSPACE)}
            mine = await self._personal(q, KIND_WORKSPACE_USER, ctx)
        return self.list_workspaces(ctx, overrides=org, personal=mine)

    async def fetch_meta(self, ctx: RequestContext, doctype: str) -> dict[str, Any]:
        """Comme `get_meta`, mais applique d'abord l'habillage de formulaire du tenant et le tableau de bord personnalisé (le sien, sinon celui de
        l'organisation) : chemin des routes et de l'action."""
        async with self._query(ctx) as q:
            found = await q.get_override(KIND_FORM, doctype)
            board_org = await q.get_override(KIND_DASHBOARD, doctype)
            board_mine = await q.get_override(KIND_DASHBOARD_USER, user_target(doctype, ctx.owner_id))
        board, layer = (board_mine, "user") if board_mine else (board_org, "org") if board_org else (None, None)
        return self.get_meta(ctx, doctype, override=(found or {}).get("config"), dashboard=(board or {}).get("config"), dashboard_layer=layer)

    async def form_override(self, ctx: RequestContext, doctype: str) -> dict[str, Any]:
        """L'habillage de formulaire de ce type pour ce tenant (vide s'il n'y en a pas). Il faut pouvoir LIRE le type."""
        self._enter(ctx, "doc.override.form.get", doctype)
        self._scope(ctx, self._doctype(doctype), "read")
        async with self._query(ctx) as q:
            found = await q.get_override(KIND_FORM, doctype)
        return {"doctype": doctype, "config": (found or {}).get("config", {})}

    async def save_form_override(self, ctx: RequestContext, doctype: str, config: Any) -> dict[str, Any]:
        """Enregistre l'habillage d'un formulaire. Il faut pouvoir ÉCRIRE le type : cela change sa présentation pour tous."""
        self._enter(ctx, "doc.override.form.save", doctype)
        compiled = self._doctype(doctype)
        self._scope(ctx, compiled, "write")
        cleaned = validate_form_override(compiled, config)
        async with self._query(ctx) as q:
            made = await q.put_override(KIND_FORM, doctype, owner=ctx.owner_id, config=cleaned)
        return {"doctype": doctype, "config": made["config"]}

    async def delete_form_override(self, ctx: RequestContext, doctype: str) -> dict[str, Any]:
        """Retire l'habillage d'un formulaire (le YAML reprend la main). Il faut pouvoir ÉCRIRE le type."""
        self._enter(ctx, "doc.override.form.delete", doctype)
        self._scope(ctx, self._doctype(doctype), "write")
        async with self._query(ctx) as q:
            await q.delete_override(KIND_FORM, doctype)
        return {"doctype": doctype}

    @staticmethod
    def _layer(scope: str | None) -> str:
        if scope in (None, "org"):
            return "org"
        if scope == "user":
            return "user"
        raise ValidationFailed("scope : `user` (ma disposition) ou `org` (toute l'organisation)")

    async def save_dashboard_override(self, ctx: RequestContext, doctype: str, config: Any, scope: str | None = "org") -> dict[str, Any]:
        """Enregistre le tableau de bord d'un DocType. `scope=org` : pour tout le monde, il faut pouvoir ÉCRIRE le type ; `scope=user` : le sien,
        il suffit de pouvoir le LIRE."""
        self._enter(ctx, "doc.override.dashboard.save", doctype)
        layer = self._layer(scope)
        compiled = self._doctype(doctype)
        self._scope(ctx, compiled, "write" if layer == "org" else "read")
        cleaned = validate_dashboard_override(compiled, config)
        kind, target = (KIND_DASHBOARD, doctype) if layer == "org" else (KIND_DASHBOARD_USER, user_target(doctype, ctx.owner_id))
        async with self._query(ctx) as q:
            made = await q.put_override(kind, target, owner=ctx.owner_id, config=cleaned)
        return {"doctype": doctype, "config": made["config"], "layer": layer}

    async def delete_dashboard_override(self, ctx: RequestContext, doctype: str, scope: str | None = "org") -> dict[str, Any]:
        """Retire le tableau de bord personnalisé (le sien, ou celui de l'organisation : alors il faut pouvoir écrire le type)."""
        self._enter(ctx, "doc.override.dashboard.delete", doctype)
        layer = self._layer(scope)
        compiled = self._doctype(doctype)
        self._scope(ctx, compiled, "write" if layer == "org" else "read")
        kind, target = (KIND_DASHBOARD, doctype) if layer == "org" else (KIND_DASHBOARD_USER, user_target(doctype, ctx.owner_id))
        async with self._query(ctx) as q:
            await q.delete_override(kind, target)
        return {"doctype": doctype, "layer": layer}

    def _targets(self, ws: WorkspaceDef) -> list[str]:
        """Tous les DocTypes qu'un espace met en avant : raccourcis, listes, cartes de liens ET cartes de chiffres et graphiques."""
        return sorted({*ws.doctypes(), *(str(b.doctype) for b in ws.measured())})

    async def save_workspace_override(self, ctx: RequestContext, name: str, config: Any, scope: str | None = "org") -> dict[str, Any]:
        """Enregistre la disposition d'un espace de travail (celui d'un plugin, ou l'espace fabriqué d'un module).

        `scope=org` : pour toute l'organisation, il faut pouvoir ÉCRIRE tous les DocTypes que les blocs envoyés mettent en avant ;
        `scope=user` : la sienne seulement (elle passe avant celle de l'organisation), il suffit de pouvoir LIRE ce qu'elle met en avant.

        Les blocs que l'utilisateur ne voit pas (ses droits les lui cachent) ne sont jamais effacés : ils sont conservés tels quels à côté de
        leurs voisins (`overrides.keep_hidden`), il ne peut ni les lire ni les modifier.
        """
        self._enter(ctx, "doc.override.workspace.save", name)
        layer = self._layer(scope)
        base = self._base_workspace(ctx, name)
        if base is None:
            raise NotFound("workspace_not_found", f"espace {name!r} introuvable")
        cleaned = validate_workspace_override(self._registry, name, config)
        submitted = workspace_from_override(name, cleaned)
        targets = self._targets(submitted) if submitted else []
        if not targets:
            raise PermissionDenied()  # un espace sans DocType n'a pas de droit à exiger : on refuse plutôt que d'en inventer un
        for target in targets:
            self._scope(ctx, self._doctype(target), "write" if layer == "org" else "read")
        kind, key = (KIND_WORKSPACE, name) if layer == "org" else (KIND_WORKSPACE_USER, user_target(name, ctx.owner_id))
        async with self._query(ctx) as q:
            org_row = await q.get_override(KIND_WORKSPACE, name)
            mine_row = await q.get_override(KIND_WORKSPACE_USER, key) if layer == "user" else None
            # La disposition qu'on remplace : celle d'en dessous pour l'organisation (YAML), la sienne ou celle de l'organisation pour soi.
            current, _ = self._layered(base, {name: org_row["config"]} if org_row else {}, {name: mine_row["config"]} if mine_row else {})
            # Ce que l'utilisateur ne voit pas dans la disposition qu'il remplace : on le garde.
            _, shown, _ = self._serve_blocks(ctx, current.blocks)
            merged = keep_hidden([b.model_dump(mode="json", exclude_none=True) for b in current.blocks], shown, cleaned["blocks"])
            cleaned = validate_workspace_override(self._registry, name, {**cleaned, "blocks": merged})
            made = await q.put_override(kind, key, owner=ctx.owner_id, config=cleaned)
        return {"name": name, "config": made["config"], "layer": layer}

    async def delete_workspace_override(self, ctx: RequestContext, name: str, scope: str | None = "org") -> dict[str, Any]:
        """Retire la disposition personnalisée d'un espace (la sienne ; ou celle de l'organisation, qui exige d'écrire ce que l'espace du
        plugin met en avant et que l'utilisateur peut lire). Le YAML du plugin, ou l'espace fabriqué, reprend la main."""
        self._enter(ctx, "doc.override.workspace.delete", name)
        layer = self._layer(scope)
        base = self._base_workspace(ctx, name)
        if base is None:
            raise NotFound("workspace_not_found", f"espace {name!r} introuvable")
        if layer == "org":
            for target in self._targets(base):
                if self._readable(ctx, target) is not None:
                    self._scope(ctx, self._doctype(target), "write")
        kind, key = (KIND_WORKSPACE, name) if layer == "org" else (KIND_WORKSPACE_USER, user_target(name, ctx.owner_id))
        async with self._query(ctx) as q:
            await q.delete_override(kind, key)
        return {"name": name, "layer": layer}

    # ── Écriture ──────────────────────────────────────────────
    async def create(self, ctx: RequestContext, doctype: str, data: Any, *, name: Any = None) -> dict[str, Any]:
        """`name` : le nom choisi, seulement pour un DocType `naming: prompt` (et alors obligatoire) ; tout autre nommage le refuse (il vient du système)."""
        self._enter(ctx, "doc.create", doctype)
        compiled = self._doctype(doctype)
        if compiled.definition.single:
            raise ValidationFailed(f"{doctype} est un document unique : on l'enregistre, on ne le crée pas", code="single_document")
        scope = self._scope(ctx, compiled, "create")
        if (source := self._source(compiled)) is not None:
            if name is not None:
                raise ValidationFailed(f"{doctype} est un DocType virtuel : sa source nomme les documents", code="name_not_allowed")
            return await self._create_virtual(ctx, compiled, scope, data, source)
        self._require_hooks(compiled)
        return await self._create(ctx, compiled, scope, data, requested=name)

    @staticmethod
    def _requested_name(compiled: CompiledDocType, requested: Any) -> str | None:
        """Le nom que le client impose : obligatoire pour `naming: prompt`, refusé pour tout autre nommage."""
        if compiled.definition.naming == "prompt":
            if requested is None:
                raise ValidationFailed(details=[{"field": "name", "msg": "Donnez un nom au document", "type": "missing"}])
            return valid_name(requested)
        if requested is not None:
            raise ValidationFailed(f"{compiled.definition.name} nomme ses documents lui-même : `name` n'est pas permis", code="name_not_allowed")
        return None

    async def _create(
        self, ctx: RequestContext, compiled: CompiledDocType, scope: Scope, data: Any,
        *, name: str | None = None, amended_from: str | None = None, requested: Any = None,
    ) -> dict[str, Any]:
        """Création d'un brouillon. `name` : un nom imposé (un amendement), qui remplace la numérotation du DocType. `requested` : le nom que le client demande (`naming: prompt`)."""
        doctype = compiled.definition.name
        naming = compiled.definition.naming
        if name is None:
            name = self._requested_name(compiled, requested)
        finalize = self._finalizer(compiled, scope, ctx.owner_id)
        clean = finalize(self._with_today(compiled, data))
        series = None if name else compiled.definition.series
        by_field = naming if isinstance(naming, NamingField) and not name else None
        # `hash` : le nom existe d'emblée. `owner` : l'identifiant de celui qui crée (un document par utilisateur ; le second est refusé, 409).
        # Série : il n'existe qu'à l'insertion (voir plus bas), les hooks reçoivent `None` ; un nom tiré d'un champ n'existe qu'une fois les hooks passés (ils peuvent le changer).
        name = name or (ctx.owner_id if naming == "owner" else None if (series or by_field) else secrets.token_hex(6))
        clean = await self._action_phase(ctx, compiled, name, clean, None, finalize)
        async with self._query(ctx) as q:
            clean = await self._fetched(ctx, q, compiled, clean, None, finalize)
            clean = await self._trusted_phase(ctx, compiled, q, name, clean, None, finalize)
            clean = await self._fetched(ctx, q, compiled, clean, None, finalize)  # un hook a pu changer un lien
            if by_field is not None:
                name = valid_name(str(clean[by_field.field]), by_field.field)
            clean = self._seal(compiled, clean, None)
            await self._check_links(ctx, q, compiled, clean, None)
            await self._check_files(q, compiled, clean, None)
            if series:
                # En DERNIER dans la transaction : le verrou du compteur n'est tenu que le temps du reste de l'écriture, et
                # une erreur plus haut ne consomme aucun numéro. Le compteur et le document se valident ensemble : pas de trou.
                now = self._clock().astimezone(self._timezone)
                name = series.render(now, await q.next_number(doctype, series.key(now)))
            try:
                doc = await q.insert(doctype, name, clean, amended_from=amended_from)
            except AlreadyExists:
                if amended_from is None:
                    raise
                raise DoctypeError("already_amended", f"{doctype} {amended_from!r} a déjà été amendé", status=409) from None
            await self._sync_unique(q, compiled, doc)
            await self._record(q, ctx, compiled, doc, "create", None, doc.data)
        await self._after_save(ctx, compiled, doc, None)
        return self._present(doc)

    async def update(
        self, ctx: RequestContext, doctype: str, name: str, data: Any, version: Any
    ) -> dict[str, Any]:
        self._enter(ctx, "doc.update", doctype, name)
        compiled = self._doctype(doctype)
        if compiled.definition.single:
            if name != doctype:
                raise NotFound("document_not_found", f"{doctype} {name!r} introuvable")
            if version == 0 and not isinstance(version, bool):
                # Le premier enregistrement d'un document unique : il n'existe pas encore, on le crée sous le nom de son type.
                self._require_hooks(compiled)
                try:
                    return await self._create(ctx, compiled, self._scope(ctx, compiled, "create"), data, name=doctype)
                except AlreadyExists:
                    raise VersionConflict(f"{doctype} a été enregistré entre-temps (rechargez-le)") from None
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            raise ValidationFailed("`version` (entier >= 1) est obligatoire pour une mise à jour", code="version_required")
        current, write = await self._load_for_change(ctx, compiled, name, "write")
        # Un document soumis ne laisse modifier que ses champs `allow_on_submit` ; annulé, il est figé tout à fait.
        after_submit = current.docstatus == SUBMITTED and bool(compiled.definition.editable_after_submit)
        if current.docstatus != DRAFT and not after_submit:
            raise DocumentLocked(f"{doctype} {name!r} est {STATUS_WORDS[current.docstatus]} : il n'est plus modifiable")
        if current.version != version:
            raise VersionConflict(f"{doctype} {name!r} a été modifié (version attendue {version})")
        if (source := self._source(compiled)) is not None:
            return await self._update_virtual(ctx, compiled, write, current, data, version, source)
        self._require_hooks(compiled)
        finalize = self._finalizer(compiled, write, current.owner)
        clean = finalize(data, old=current.data)
        if after_submit:
            self._check_frozen(compiled, clean, current.data)
        clean = await self._action_phase(ctx, compiled, name, clean, current.data, finalize)
        async with self._query(ctx) as q:
            if not after_submit:  # un document soumis ne relit plus ses liens : ce qu'il a copié reste ce qu'il était à la soumission
                clean = await self._fetched(ctx, q, compiled, clean, current.data, finalize)
            clean = await self._trusted_phase(ctx, compiled, q, name, clean, current.data, finalize)
            if after_submit:
                self._check_frozen(compiled, clean, current.data)  # un hook ne dégèle rien non plus
            else:
                clean = await self._fetched(ctx, q, compiled, clean, current.data, finalize)
            clean = self._seal(compiled, clean, current.data)
            await self._check_links(ctx, q, compiled, clean, current.data)
            await self._check_files(q, compiled, clean, current.data)
            # Le périmètre et la version sont dans le WHERE : une modification concurrente échoue en 409.
            doc = await q.update(doctype, name, clean, version, scope=write, docstatus=current.docstatus)
            await self._sync_unique(q, compiled, doc)
            await self._record(q, ctx, compiled, doc, "update", current.data, doc.data)
            if after_submit:
                await self._tx_hooks(ctx, compiled, q, doc, "on_update_after_submit", old=current.data)
        await self._after_save(ctx, compiled, doc, current.data)
        await self._release_files(ctx, compiled, current.data, doc.data)  # un fichier remplacé ou retiré n'a plus de raison d'exister
        return self._present(doc)

    async def delete(self, ctx: RequestContext, doctype: str, name: str) -> None:
        self._enter(ctx, "doc.delete", doctype, name)
        compiled = self._doctype(doctype)
        if compiled.definition.single:
            raise ValidationFailed(f"{doctype} est un document unique : il ne se supprime pas", code="single_document")
        current, scope = await self._load_for_change(ctx, compiled, name, "delete")
        if current.docstatus != DRAFT:
            raise DocumentLocked(f"Suppression impossible : {doctype} {name!r} est {STATUS_WORDS[current.docstatus]} (seul un brouillon se supprime)")
        if (source := self._source(compiled)) is not None:
            async with self._query(ctx) as q:
                await self._check_not_referenced(q, doctype, name, current.data)
            await self._call_source(compiled, source.remove(ctx, compiled.definition, name))
            async with self._query(ctx) as q:
                attached = await q.remove_collaboration(doctype, name)  # commentaires, assignations et pièces jointes ne lui survivent pas
            if attached and self.files is not None:
                try:
                    await self.files.release(ctx, {fid: (doctype, ATTACHMENT_FIELD) for fid in attached})
                except Exception as e:  # noqa: BLE001 - un fichier orphelin vaut mieux qu'une suppression qui échoue après coup
                    self._log.error("attachment release failed", doctype=doctype, error=repr(e))
            return
        async with self._query(ctx) as q:
            await self._check_not_referenced(q, doctype, name, current.data)
            await self._tx_hooks(ctx, compiled, q, current, "on_trash")  # peut refuser la suppression, ou écrire ce qui doit suivre
            removed = await q.delete(doctype, name, scope=scope)
            if not removed:
                # Le document existait et était supprimable à la lecture : il a changé entre-temps.
                raise VersionConflict(f"{doctype} {name!r} a été modifié pendant la suppression")
            await self._record(q, ctx, compiled, current, "delete", current.data, None)
            await q.drop_unique(doctype, name)  # ses valeurs uniques redeviennent libres
            attached = await q.remove_collaboration(doctype, name)  # commentaires, assignations et pièces jointes disparaissent avec lui
        await self._after_delete(ctx, compiled, current)
        await self._release_files(ctx, compiled, current.data, None)
        if attached and self.files is not None:
            try:
                await self.files.release(ctx, {fid: (doctype, ATTACHMENT_FIELD) for fid in attached})
            except Exception as e:  # noqa: BLE001 - un fichier orphelin vaut mieux qu'une suppression qui échoue après coup
                self._log.error("attachment release failed", doctype=doctype, error=repr(e))

    async def bulk(self, ctx: RequestContext, doctype: str, action: Any, names: Any, *, data: Any = None) -> dict[str, Any]:
        """Une même action sur plusieurs documents (la liste, « Actions ») : `update` (poser les valeurs de `data` sur chacun), `delete`, `submit`, `cancel`.

        Chaque document est traité SEUL, par la même porte que d'habitude (droits, validation, hooks, liens, version) : un refus sur l'un
        (introuvable, hook, version périmée…) ne retient pas les autres, il est rendu avec son code. La réponse dit pour chacun s'il est passé.
        Le droit d'effectuer l'action est exigé UNE fois d'abord (un lecteur seul n'obtient pas cent refus). 100 documents au plus.
        """
        self._enter(ctx, f"doc.bulk.{action}", doctype)
        compiled = self._doctype(doctype)
        if action not in BULK_ACTIONS:
            raise ValidationFailed(f"action en lot inconnue : {action!r} (permises : {', '.join(BULK_ACTIONS)})", code="bulk_action")
        if not isinstance(names, list) or not names or len(names) > MAX_BULK or not all(isinstance(n, str) and n for n in names):
            raise ValidationFailed(f"names : une liste de 1 à {MAX_BULK} noms", code="bulk_names")
        if action in ("submit", "cancel"):
            self._submittable(doctype)
        self._scope(ctx, compiled, {"update": "write"}.get(action, action))  # type: ignore[arg-type]
        if compiled.definition.single:
            raise ValidationFailed(f"{doctype} est un document unique : pas d'action en lot", code="single_document")
        patch: dict[str, Any] = {}
        if action == "update":
            patch = self._bulk_patch(compiled, data)
        results = []
        for name in dict.fromkeys(names):
            try:
                current = await self.get(ctx, doctype, name)
                if action == "update":
                    await self.update(ctx, doctype, name, {**current["data"], **patch}, current["version"])
                elif action == "delete":
                    await self.delete(ctx, doctype, name)
                elif action == "submit":
                    await self.submit(ctx, doctype, name, current["version"])
                else:
                    await self.cancel(ctx, doctype, name, current["version"])
                results.append({"name": name, "ok": True})
            except DoctypeError as e:
                results.append({"name": name, "ok": False, "code": e.code, "message": e.message, **({"details": e.details} if e.details else {})})
            except Exception as e:  # noqa: BLE001 - un document qui échoue pour une raison interne ne fait pas perdre le compte rendu des autres
                self._log.error("bulk operation failed", op=action, doctype=doctype, name=name, error=repr(e))
                results.append({"name": name, "ok": False, "code": "internal_error", "message": "Erreur interne"})
        done = sum(1 for r in results if r["ok"])
        return {"results": results, "done": done, "failed": len(results) - done}

    @staticmethod
    def _bulk_patch(compiled: CompiledDocType, data: Any) -> dict[str, Any]:
        """Les valeurs à poser : des champs SIMPLES et connus (ni tableau, ni secret, ni fichier, ni champ copié d'un autre document)."""
        if not isinstance(data, dict) or not data:
            raise ValidationFailed("data : les champs à modifier, au moins un", code="bulk_data")
        by_name = compiled.definition.field_map
        errors = []
        for key in data:
            f = by_name.get(key)
            if f is None:
                errors.append({"field": key, "msg": "Champ inconnu", "type": "extra_forbidden"})
            elif f.fieldtype == "Table" or f.fieldtype == "Password" or f.fieldtype in FILE_TYPES or f.fetch_from or f.read_only or f.hidden:
                errors.append({"field": key, "msg": "Ce champ ne se modifie pas en lot", "type": "bulk_field"})
        if errors:
            raise ValidationFailed(details=errors)
        return dict(data)

    async def rename(self, ctx: RequestContext, doctype: str, name: str, new_name: Any) -> dict[str, Any]:
        """Change le NOM d'un brouillon (`allow_rename: true`) : tous les documents qui le citent par un Link suivent, dans la même transaction.

        Il faut pouvoir écrire le document (pas de droit distinct). Le renommage est tout ou rien : un nom déjà pris (409), un document qui cite l'ancien
        nom et que le nouveau rendrait invalide, une panne au milieu, et rien n'a bougé. Les commentaires, assignations, pièces jointes, valeurs uniques et
        l'historique du document le suivent ; une entrée `rename` y garde l'ancien nom. Les documents qui le citent sont réécrits sans contrôle de leurs
        droits (c'est la conséquence du renommage, pas une modification de l'utilisateur), mais chacun a son entrée d'historique à son nom.
        """
        self._enter(ctx, "doc.rename", doctype, name)
        compiled = self._doctype(doctype)
        d = compiled.definition
        if not d.allow_rename:
            raise ValidationFailed(f"{doctype} ne se renomme pas (`allow_rename` n'est pas activé)", code="rename_not_allowed")
        new = valid_name(new_name, "new_name")
        current, write = await self._load_for_change(ctx, compiled, name, "write")
        if new == name:
            raise ValidationFailed("Le nouveau nom est le nom actuel", code="rename_same")
        if current.docstatus != DRAFT:
            raise DocumentLocked(f"{doctype} {name!r} est {STATUS_WORDS[current.docstatus]} : il ne se renomme plus")
        referrers = self._registry.referrers(doctype)
        async with self._query(ctx) as q:
            await q.lock_for_delete(doctype, name)  # comme une suppression : une écriture qui crée un lien vers lui attend, ou a fini avant que nous cherchions
            try:
                renamed = await q.rename(doctype, name, new, scope=write)
            except AlreadyExists:
                raise DoctypeError("already_exists", f"{doctype} {new!r} existe déjà", status=409) from None
            for ref_doc in await q.referrers_of(doctype, name, [r.key() for r in referrers]):
                ref_compiled = self._registry.get(ref_doc.doctype)
                data = _relabel(ref_compiled, [r for r in referrers if r.doctype == ref_doc.doctype], ref_doc.data, doctype, name, new)
                if data != ref_doc.data:
                    updated = await q.relink(ref_doc.doctype, ref_doc.name, data)
                    await self._record(q, ctx, ref_compiled, updated, "update", ref_doc.data, updated.data)
            doc = await q.get(doctype, new, scope=Scope.everything("read"))  # relu : il a pu se citer lui-même
            assert doc is not None  # renommé dans cette transaction
            await self._record(q, ctx, compiled, doc, "rename", {"name": name}, {"name": new})
        await self._emit(
            f"doctype.{doctype}.after_rename",
            {"doctype": doctype, "old_name": name, "name": new, "tenant_id": ctx.tenant_id, "user": ctx.user_id},
        )
        return self._present(doc)

    # ── Soumission (documents soumettables) ───────────────────
    async def submit(self, ctx: RequestContext, doctype: str, name: str, version: Any) -> dict[str, Any]:
        """Brouillon -> soumis. Le document est figé ; ses hooks `on_submit` (le stock, la comptabilité…) tournent dans la même transaction."""
        return await self._transition(ctx, doctype, name, version, "submit")

    async def cancel(self, ctx: RequestContext, doctype: str, name: str, version: Any) -> dict[str, Any]:
        """Soumis -> annulé. On n'efface rien : ses hooks `on_cancel` écrivent l'inverse de ce que `on_submit` a écrit."""
        return await self._transition(ctx, doctype, name, version, "cancel")

    async def amend(self, ctx: RequestContext, doctype: str, name: str) -> dict[str, Any]:
        """Copie en brouillon d'un document ANNULÉ (`amended_from` le désigne), pour le corriger et le soumettre à nouveau.

        Un document annulé ne s'amende qu'une fois : le nom de l'amendement se déduit de celui du document (`FA-1` -> `FA-1-1`),
        un second amendement du même document trouverait ce nom pris. Il faut le droit de créer (c'est une création) et de lire.
        """
        self._enter(ctx, "doc.amend", doctype, name)
        compiled = self._submittable(doctype)
        create, read = self._scope(ctx, compiled, "create"), self._scope(ctx, compiled, "read")
        async with self._query(ctx) as q:
            source = await q.get(doctype, name, scope=read)
        if source is None:
            raise NotFound("document_not_found", f"{doctype} {name!r} introuvable")
        if source.docstatus != CANCELLED:
            raise InvalidDocStatus(f"{doctype} {name!r} est {STATUS_WORDS[source.docstatus]} : seul un document annulé s'amende")
        self._require_hooks(compiled)
        new_name = _amended_name(source)
        if len(new_name) > MAX_NAME:
            raise ValidationFailed(f"le nom de l'amendement dépasse {MAX_NAME} caractères", code="name_too_long")
        skipped = set(compiled.definition.secret_fields) | {f.fieldname for f in compiled.definition.fields if f.no_copy}
        carried = {k: v for k, v in source.data.items() if k not in skipped}  # un secret ne se recopie jamais (on le saisit à nouveau), ni un champ `no_copy`
        return await self._create(ctx, compiled, create, carried, name=new_name, amended_from=source.name)

    async def _transition(self, ctx: RequestContext, doctype: str, name: str, version: Any, action: str) -> dict[str, Any]:
        self._enter(ctx, f"doc.{action}", doctype, name)
        compiled = self._submittable(doctype)
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            raise ValidationFailed("`version` (entier >= 1) est obligatoire", code="version_required")
        current, scope = await self._load_for_change(ctx, compiled, name, action)  # type: ignore[arg-type]
        source, target, hook = _TRANSITIONS[action]
        if current.docstatus != source:
            raise InvalidDocStatus(f"{doctype} {name!r} est {STATUS_WORDS[current.docstatus]}")
        if current.version != version:
            raise VersionConflict(f"{doctype} {name!r} a été modifié (version attendue {version})")
        self._require_hooks(compiled)
        if action == "submit":
            try:  # la définition a pu changer depuis la saisie du brouillon : on ne fige pas ce qu'elle n'accepterait plus
                compiled.validate_data(current.data)
            except ValidationFailed as e:
                raise ValidationFailed("Ce brouillon ne respecte plus la définition : corrigez-le avant de le soumettre", details=e.details) from None
        async with self._query(ctx) as q:
            # L'état D'ABORD : cet UPDATE conditionnel est ce qui rend la transition unique (idempotence), et sur SQLite
            # c'est l'écriture qui prend le verrou de la base avant que les hooks ne lisent quoi que ce soit.
            doc = await q.transition(doctype, name, source=source, target=target, expected_version=version, scope=scope)
            await self._record(q, ctx, compiled, doc, action, {"docstatus": source}, {"docstatus": target})
            if action == "cancel":
                await q.drop_unique(doctype, name)  # un document annulé ne retient plus ses valeurs : son amendement peut les reprendre
                await self._tx_hooks(ctx, compiled, q, doc, "before_cancel")
            await self._tx_hooks(ctx, compiled, q, doc, hook)
        await self._emit(
            f"doctype.{doctype}.after_{action}",
            {"doctype": doctype, "name": doc.name, "tenant_id": ctx.tenant_id, "version": doc.version, "user": ctx.user_id, "docstatus": doc.docstatus},
        )
        return self._present(doc)

    async def _tx_hooks(
        self, ctx: RequestContext, compiled: CompiledDocType, q: Query, doc: StoredDoc, event: str, *, old: Mapping[str, Any] | None = None
    ) -> None:
        """`on_submit`, `on_cancel`, `before_cancel`, `on_update_after_submit`, `on_trash` : dans la transaction, avec le droit d'écrire d'autres documents. Un refus défait tout."""
        refs = compiled.definition.hooks.for_event(event)
        if not refs:
            return
        doctype = compiled.definition.name

        async def get(dt: str, nm: str) -> dict[str, Any] | None:
            found = await q.get(dt, nm, scope=Scope.everything("read"))
            return self._present(found) if found else None

        tx = _Transaction(self, ctx, q, event)
        view = MappingProxyType(self._mask_data(compiled, doc.data) or {})
        old_view = MappingProxyType(self._mask_data(compiled, old) or {}) if old is not None else None
        for ref in refs:
            fn = self._hooks.get(ref)
            if fn is None:  # désenregistré entre le contrôle initial et maintenant
                raise DoctypeError("hook_unavailable", "des hooks de ce DocType sont indisponibles", status=503)
            await self._call_trusted(ref, fn(HookEvent(event, ctx, doctype, doc.name, view, old_view, get, tx)))  # type: ignore[arg-type]

    # ── Interne ───────────────────────────────────────────────
    def _with_today(self, compiled: CompiledDocType, data: Any) -> Any:
        """Pose la date du jour dans les champs Date dont le défaut est `today` ET que le payload n'a pas renseignés.

        Côté serveur (et non dans le modèle) : la date dépend du jour de la création et du fuseau de l'organisation, pas de la définition.
        Un payload qui n'est pas un objet est laissé tel quel : la validation le refusera avec son message.
        """
        dates = [f.fieldname for f in compiled.definition.fields if f.fieldtype == "Date" and f.default == TODAY]
        instants = [f.fieldname for f in compiled.definition.fields if f.fieldtype == "Datetime" and f.default == fieldtypes.NOW]
        if not (dates or instants) or not isinstance(data, dict):
            return data
        today = self._clock().astimezone(self._timezone).date().isoformat()
        now = fieldtypes.format_instant(self._clock())  # un Datetime est TOUJOURS en UTC
        return {**{k: today for k in dates if k not in data}, **{k: now for k in instants if k not in data}, **data}

    def _submittable(self, name: str) -> CompiledDocType:
        compiled = self._doctype(name)
        if not compiled.definition.submittable:
            raise ValidationFailed(f"{name} n'est pas un DocType soumettable", code="not_submittable")
        return compiled

    def _doctype(self, name: str) -> CompiledDocType:
        """Un DocType atteignable depuis l'API. Un DocType enfant (`istable`) n'existe que dans son parent : il répond
        exactement comme un DocType inconnu, sans révéler qu'il existe."""
        compiled = self._registry.get(name)
        if compiled.definition.istable:
            raise NotFound("doctype_not_found", f"DocType inconnu : {name!r}")
        return compiled

    def _enter(self, ctx: RequestContext, op: str, doctype: str | None = None, name: str | None = None) -> None:
        ctx.require_identity()
        if ctx.is_system:
            # D20 : un contexte système est explicite et journalisé.
            self._log.info(
                "system context operation",
                op=op,
                tenant=ctx.tenant_id,
                reason=ctx.reason,
                doctype=doctype,
                name=name,
            )

    # ── Propriétés de champ (palier 70 %, phase H2) ───────────────────────
    @staticmethod
    def _check_frozen(compiled: CompiledDocType, clean: Mapping[str, Any], old: Mapping[str, Any]) -> None:
        """Un document SOUMIS ne laisse changer que ses champs `allow_on_submit` : tout autre champ doit être resté tel qu'il était."""
        allowed = set(compiled.definition.editable_after_submit)
        errors = [
            {"field": f.fieldname, "msg": "Champ figé : le document est soumis", "type": "locked_after_submit"}
            for f in compiled.definition.fields
            if f.fieldname not in allowed and clean.get(f.fieldname) != old.get(f.fieldname)
        ]
        if errors:
            raise ValidationFailed(details=errors)

    async def _sync_unique(self, q: Query, compiled: CompiledDocType, doc: StoredDoc) -> None:
        """Réserve les valeurs `unique` du document (et libère celles qu'il a quittées). La base est juge : une valeur déjà prise ailleurs refuse l'écriture entière."""
        fields = compiled.definition.unique_fields
        if not fields:
            return
        wanted: dict[str, str] = {}
        errors = []
        for field in fields:
            value = doc.data.get(field)
            if value is None:
                continue
            text = str(value)
            if len(text) > MAX_UNIQUE_VALUE:
                errors.append({"field": field, "msg": f"Valeur trop longue pour être unique ({MAX_UNIQUE_VALUE} caractères au plus)", "type": "unique"})
            wanted[field] = text
        if errors:
            raise ValidationFailed(details=errors)
        try:
            await q.sync_unique(compiled.definition.name, doc.name, wanted)
        except UniqueViolation as e:
            raise ValidationFailed(
                details=[{"field": e.field, "msg": f"Cette valeur est déjà utilisée : {wanted[e.field]!r}", "type": "unique"}]
            ) from None

    async def _fetched(
        self, ctx: RequestContext, q: Query, compiled: CompiledDocType, clean: dict[str, Any], old: Mapping[str, Any] | None, finalize: Finalize | None = None,
    ) -> dict[str, Any]:
        """Remplit les champs `fetch_from` (`lien.champ`) depuis le document que le Link désigne, à CHAQUE écriture : la valeur envoyée par le client est écrasée.

        Le document visé se lit avec les droits de l'appelant. Introuvable ou illisible : la valeur déjà enregistrée reste si le lien n'a pas bougé (la
        copie existait avant), sinon le champ reste vide. Un lien changé vers un document illisible est refusé plus loin, par le contrôle des liens.
        """
        d = compiled.definition
        has_rows = any(f.fieldtype == "Table" and any(cf.fetch_from for cf in compiled.children[str(f.options)].fields) for f in d.fields)
        if not has_rows and not any(f.fetch_from for f in d.fields):
            return clean
        cache: dict[tuple[str, str], StoredDoc | None] = {}

        async def target(link: FieldDef, value: str) -> StoredDoc | None:
            key = (str(link.options), value)
            if key not in cache:
                tcompiled = self._registry.get(str(link.options))
                tscope = scope_for(tcompiled.definition, ctx, "read")
                cache[key] = await self._peek(ctx, tcompiled, q, value, tscope) if tscope.granted else None
            return cache[key]

        async def fill(container: dict[str, Any], fields: Sequence[FieldDef], before: Mapping[str, Any] | None) -> None:
            by_name = {f.fieldname: f for f in fields}
            for f in fields:
                if f.fetch_from is None:
                    continue
                link_name, _, source = f.fetch_from.partition(".")
                link_value = container.get(link_name)
                found = await target(by_name[link_name], link_value) if link_value is not None else None
                if found is not None:
                    value = found.data.get(source)
                elif before is not None and link_value is not None and before.get(link_name) == link_value:
                    value = before.get(f.fieldname)  # le document visé n'est plus lisible, la copie d'avant demeure
                else:
                    value = None
                if value is None:
                    container.pop(f.fieldname, None)
                else:
                    container[f.fieldname] = value

        out = dict(clean)
        await fill(out, d.fields, old)
        for f in d.fields:
            if f.fieldtype != "Table" or not out.get(f.fieldname):
                continue
            child = compiled.children[str(f.options)]
            if not any(cf.fetch_from for cf in child.fields):
                continue
            previous = (old or {}).get(f.fieldname) or []
            rows = []
            for i, row in enumerate(out[f.fieldname]):
                row = dict(row)
                await fill(row, child.fields, previous[i] if i < len(previous) else None)
                rows.append(row)
            out[f.fieldname] = rows
        if out == clean:
            return clean
        if finalize is not None:
            try:
                return finalize(out)
            except ValidationFailed as e:  # une copie refusée par le champ qui la reçoit : défaut de la définition, pas une saisie
                self._log.error("fetch_from produced an invalid value", doctype=d.name, errors=e.details)
                raise DoctypeError("fetch_from_invalid", f"un champ rempli par fetch_from de {d.name} est invalide", status=500, details=e.details) from None
        return out

    # ── Secrets (champs Password : sealing.py) ────────────────
    def _present(self, doc: StoredDoc) -> dict[str, Any]:
        """Un document tel que l'API le rend : un secret posé s'y lit `********`, jamais en clair ni chiffré."""
        out = doc.as_dict()
        secrets_ = self._registry.get(doc.doctype).definition.secret_fields
        if secrets_:
            out["data"] = self._masked(secrets_, doc.data)
        return out

    @staticmethod
    def _masked(secret_fields: Sequence[str], data: Mapping[str, Any]) -> dict[str, Any]:
        return {k: (MASK if k in secret_fields and v else v) for k, v in data.items()}

    def _mask_data(self, compiled: CompiledDocType, data: Mapping[str, Any] | None) -> dict[str, Any] | None:
        """Ce qu'on montre à un code qui n'a pas à lire un secret (hook sandboxé, `old` d'un hook) : le document, secrets masqués."""
        if data is None:
            return None
        secret_fields = compiled.definition.secret_fields
        return self._masked(secret_fields, data) if secret_fields else dict(data)

    def _seal(self, compiled: CompiledDocType, clean: dict[str, Any], old: Mapping[str, Any] | None) -> dict[str, Any]:
        """Avant l'écriture : `********` garde le secret enregistré, toute autre valeur est un NOUVEAU secret, chiffré. Sans clé, jamais de secret en clair."""
        fields = compiled.definition.secret_fields
        if not fields:
            return clean
        out = dict(clean)
        for field in fields:
            value = out.get(field)
            if value is None:
                continue
            if value == MASK:
                kept = (old or {}).get(field)
                if kept is None:
                    raise ValidationFailed(details=[{"field": field, "msg": "Aucun secret enregistré : saisissez-le", "type": "secret_placeholder"}])
                out[field] = kept
            elif not self._sealer.available:
                self._log.error("secret refused: no doctype.secret_key", doctype=compiled.definition.name, field=field)
                raise DoctypeError("secret_key_missing", "le chiffrement des secrets n'est pas configuré (doctype.secret_key)", status=503)
            else:
                out[field] = self._sealer.seal(value)
        return out

    def reveal_secret(self, ctx: RequestContext, compiled: CompiledDocType, stored: Mapping[str, Any], field: str) -> str | None:
        """Le secret EN CLAIR : réservé au contexte système (un plugin de confiance), journalisé à chaque lecture."""
        if not ctx.is_system:
            raise PermissionDenied()
        if field not in compiled.definition.secret_fields:
            raise ValidationFailed(f"{field!r} n'est pas un champ Password de {compiled.definition.name}")
        value = stored.get(field)
        if value is None:
            return None
        self._log.info("secret revealed", doctype=compiled.definition.name, field=field, reason=ctx.reason, tenant=ctx.tenant_id)
        try:
            return self._sealer.unseal(value)
        except ValueError as e:
            raise DoctypeError("secret_unreadable", str(e), status=500) from None

    # ── Contrôle rétroactif de l'unicité (D25) ────────────────
    async def check_unique(self, *, doctypes: Sequence[str] | None = None, tenant: str | None = None) -> list[dict[str, Any]]:
        """Les valeurs `unique` déjà en double : `[{doctype, tenant, field, value, names}]` (voir `integrity.py`).

        L'installation entière, ou les seuls types nommés. Rien n'est écrit ni corrigé : c'est un contrôle, pas un
        nettoyage. Sous RLS, le scan doit nommer l'organisation (la politique ne laisserait voir qu'elle sans cela).
        """
        targets: list[tuple[str, tuple[str, ...]]] = []
        for name in self._registry.names() if doctypes is None else doctypes:
            compiled = self._registry.get(name)
            fields = compiled.definition.unique_fields
            if fields and not compiled.definition.istable and compiled.definition.source is None:
                targets.append((compiled.definition.name, fields))
        out: list[dict[str, Any]] = []
        async with self._db.session() as session:
            if self._rls:
                if tenant is None:
                    raise DoctypeError("tenant_required", "RLS actif : nommez l'organisation (tenant) à contrôler", status=400)
                await bind_tenant(session, tenant)  # AVANT toute autre instruction, comme partout sous RLS
            for name, fields in sorted(targets):
                out.extend(await integrity.unique_conflicts(session, name, fields, tenant=tenant))
        return out

    async def ensure_unique_clean(self, compiled: CompiledDocType) -> None:
        """Avant d'ACTIVER un champ `unique` sur un type qui a déjà des documents : un double existant refuse le chargement.

        Rien n'est écrit ni corrigé : le message dit le champ et le nombre de documents, jamais les noms ni l'organisation
        (un chargement n'a pas d'appelant à qui les nommer). Sous RLS, le scan ne verrait qu'un tenant : prévenir faux
        serait pire que ne pas prévenir, on préfère ne pas prévenir du tout.
        """
        fields = compiled.definition.unique_fields
        if not fields or compiled.definition.source is not None:
            return
        if self._rls:
            self._log.warning("retroactive unique check skipped (RLS would hide other organisations)", doctype=compiled.definition.name)
            return
        async with self._db.session() as session:
            conflicts = await integrity.unique_conflicts(session, compiled.definition.name, fields)
        if conflicts:
            summary = ", ".join(f"{c['field']} ({len(c['names'])} documents)" for c in conflicts[:5])
            more = f" et {len(conflicts) - 5} autres" if len(conflicts) > 5 else ""
            raise InvalidDefinition(
                f"{compiled.definition.name}: `unique: true` rétroactif rencontre des valeurs déjà prises — "
                f"{summary}{more} : corrigez les données avant d'activer le champ"
            )

    async def purge_history(self, tenant: str, before: datetime, doctype: str | None = None) -> int:
        """L'historique du tenant plus vieux que `before` disparaît (action opérateur `doc.history.purge`, D24).

        La rétention par document (`max_versions`) purge à chaque écriture ; celle-ci rattrape le trop-plein d'avant
        une augmentation de `max_versions`. Renvoie le nombre d'entrées supprimées.
        """
        ctx = RequestContext.system(tenant, "history purge")
        async with self._query(ctx) as q:
            return await q.purge_versions_before(before, doctype=doctype)

    def _finalizer(self, compiled: CompiledDocType, scope: Scope, owner: str) -> Finalize:
        def finalize(data: Any, *, old: Mapping[str, Any] | None = None) -> dict[str, Any]:
            clean = compiled.validate_data(data, old=old)
            self._check_written(scope, owner, clean)
            return clean

        return finalize

    async def _record(
        self, q: Query, ctx: RequestContext, compiled: CompiledDocType, doc: StoredDoc, action: str,
        old: Mapping[str, Any] | None, new: Mapping[str, Any] | None,
    ) -> None:
        """Une entrée d'historique, dans la transaction du document : écrite et annulée avec lui.

        Une modification qui ne change rien (mêmes valeurs) n'en produit pas : l'historique ne garde que ce qui a bougé.
        """
        if not compiled.definition.track_changes:
            return
        changes = diff(old, new)
        for secret_field in compiled.definition.secret_fields:
            if secret_field in changes:  # l'historique dit QU'un secret a changé, jamais sa valeur (ni claire ni chiffrée)
                changes[secret_field] = {"old": MASK if changes[secret_field]["old"] else None, "new": MASK if changes[secret_field]["new"] else None}
        if not changes and action == "update":
            return
        who = ctx.user_id or f"system:{ctx.reason}"
        await q.add_version(
            compiled.definition.name, doc.name, version=doc.version, action=action, user=who, doc_owner=doc.owner, changes=changes
        )
        # Rétention (D24) : les `max_versions` entrées les plus récentes survivent dans la MÊME transaction.
        await q.purge_versions(compiled.definition.name, doc.name, keep=compiled.definition.max_versions)

    async def _check_links(
        self, ctx: RequestContext, q: Query, compiled: CompiledDocType, clean: Mapping[str, Any], old: Mapping[str, Any] | None
    ) -> None:
        """Chaque champ Link modifié doit viser un document qui existe ET que l'utilisateur peut lire (D6, doc §7.2).

        Cela vaut aussi pour les Link des LIGNES d'un champ Table (`items.0.item`). Une valeur inchangée n'est pas
        recontrôlée : le lien est déjà là, et un document que l'utilisateur ne peut plus lire ne doit pas l'empêcher de
        corriger autre chose. « Introuvable » couvre aussi « illisible » : on ne révèle pas l'existence d'un document hors
        périmètre. Les lignes cibles sont verrouillées (`lock`) jusqu'au commit : voir `_check_not_referenced`.
        Quand un champ porte `target_status` (D23), l'état de la cible est lu dans la même requête et un état refusé
        échoue avec le type `link_status` (distinct de `link_not_found` : la cible existe, c'est son état qui gêne).
        """
        # DocType cible -> valeur -> où elle est portée (chemin du champ : `country` ou `items.0.item`) et l'état exigé de la cible
        wanted: dict[str, dict[str, list[tuple[str, frozenset[int] | None]]]] = {}
        errors: list[dict[str, str]] = []

        def want(target: Any, value: Any, path: str, allowed: frozenset[int] | None = None) -> None:
            wanted.setdefault(str(target), {}).setdefault(value, []).append((path, allowed))

        def want_dynamic(kind: Any, value: Any, path: str, allowed: frozenset[int] | None = None) -> None:
            """Un DynamicLink vise le DocType que dit son champ de type : il doit exister et être un DocType qu'on peut citer."""
            try:
                known = isinstance(kind, str) and not self._registry.get(kind).definition.istable
            except NotFound:
                known = False
            if known:
                want(kind, value, path, allowed)
            else:
                errors.append({"field": path, "msg": "Choisissez d'abord le type de document visé", "type": "link_not_found"})

        for f in compiled.definition.fields:
            value = clean.get(f.fieldname)
            if value is None:
                continue
            if f.fieldtype == "Link" and not (old is not None and old.get(f.fieldname) == value):
                want(f.options, value, f.fieldname, f.allowed_docstatus)
            elif f.fieldtype == "DynamicLink" and not (old is not None and old.get(f.fieldname) == value and old.get(str(f.options)) == clean.get(str(f.options))):
                want_dynamic(clean.get(str(f.options)), value, f.fieldname, f.allowed_docstatus)
            elif f.fieldtype == "Table":
                child = compiled.children[str(f.options)]
                # Une ligne déplacée garde son lien : on compare à l'ensemble des valeurs déjà présentes, pas à la même position.
                # Un DynamicLink se compare avec son type : le même nom visant un autre DocType est un autre lien.
                before = {
                    cf.fieldname: {(r.get(cf.fieldname), r.get(str(cf.options)) if cf.fieldtype == "DynamicLink" else None) for r in (old or {}).get(f.fieldname, [])}
                    for cf in child.fields
                    if cf.fieldtype in ("Link", "DynamicLink")
                }
                for i, row in enumerate(value):
                    for cf in child.fields:
                        v = row.get(cf.fieldname)
                        if v is None or cf.fieldtype not in ("Link", "DynamicLink"):
                            continue
                        kind = row.get(str(cf.options)) if cf.fieldtype == "DynamicLink" else None
                        if (v, kind) in before[cf.fieldname]:
                            continue
                        if cf.fieldtype == "Link":
                            want(cf.options, v, f"{f.fieldname}.{i}.{cf.fieldname}", cf.allowed_docstatus)
                        else:
                            want_dynamic(kind, v, f"{f.fieldname}.{i}.{cf.fieldname}", cf.allowed_docstatus)
        for target, values in wanted.items():
            target_compiled = self._registry.get(target)
            target_def = target_compiled.definition
            scope = scope_for(target_def, ctx, "read")
            if not scope.granted:
                found, statuses = set(), {}
            elif self._source(target_compiled) is not None:
                # Un DocType virtuel n'a pas d'état que le moteur pourrait juger : l'existence suffit (la source porte sa
                # vérité). Ses documents sont des brouillons pour l'état : `not_cancelled` les accepte, `submitted` non.
                found = await self._existing(ctx, target_compiled, q, list(values), scope)
                statuses = {name: DRAFT for name in found}
            elif any(allowed is not None for paths in values.values() for _, allowed in paths):
                statuses = await q.existing_docstatus(target, list(values), scope=scope, lock=True)
                found = set(statuses)
            else:
                found, statuses = await self._existing(ctx, target_compiled, q, list(values), scope), {}
            for value, paths in values.items():
                if value not in found:
                    errors.extend(
                        {"field": path, "msg": f"{target_def.label or target} introuvable : {value!r}", "type": "link_not_found"}
                        for path, _ in paths
                    )
                    continue
                state = statuses.get(value)
                errors.extend(
                    {"field": path, "msg": f"{target_def.label or target} {value!r} est {STATUS_WORDS[state]} : ce lien n'accepte pas cet état", "type": "link_status"}
                    for path, allowed in paths
                    if allowed is not None and state not in allowed
                )
        if errors:
            raise ValidationFailed(details=errors)

    async def _check_files(
        self, q: Query, compiled: CompiledDocType, clean: Mapping[str, Any], old: Mapping[str, Any] | None
    ) -> None:
        """Chaque champ Image ou Attach MODIFIÉ doit citer un fichier téléversé, dans ce tenant, POUR ce champ de ce type (jamais le fichier
        d'un autre champ, d'un autre type ou d'un autre tenant : « introuvable » dans tous les cas)."""
        wanted = {
            f.fieldname: clean[f.fieldname]
            for f in compiled.definition.fields
            if f.fieldtype in FILE_TYPES and clean.get(f.fieldname) is not None and not (old is not None and old.get(f.fieldname) == clean[f.fieldname])
        }
        if not wanted:
            return
        found = await q.get_files(set(wanted.values()))
        errors = []
        for fieldname, file_id in wanted.items():
            row = found.get(file_id)
            if row is None or row["doctype"] != compiled.definition.name or row["fieldname"] != fieldname:
                errors.append({"field": fieldname, "msg": "Fichier introuvable : téléversez-le d'abord", "type": "file_not_found"})
        if errors:
            raise ValidationFailed(details=errors)

    async def _release_files(self, ctx: RequestContext, compiled: CompiledDocType, before: Mapping[str, Any], after: Mapping[str, Any] | None) -> None:
        """Après une modification ou une suppression : les fichiers que le document ne cite plus. Au mieux (voir `FileService.release`)."""
        if self.files is None:
            return
        gone: dict[str, tuple[str, str]] = {}
        for f in compiled.definition.fields:
            if f.fieldtype in FILE_TYPES and before.get(f.fieldname) and before.get(f.fieldname) != (after or {}).get(f.fieldname):
                gone[before[f.fieldname]] = (compiled.definition.name, f.fieldname)
        if not gone:
            return
        try:
            await self.files.release(ctx, gone)
        except Exception as e:  # noqa: BLE001 - un fichier orphelin vaut mieux qu'une suppression de document qui échoue après coup
            self._log.error("file release failed", doctype=compiled.definition.name, error=repr(e))

    async def _check_not_referenced(self, q: Query, doctype: str, name: str, data: Mapping[str, Any]) -> None:
        """Refuse de supprimer un document que d'autres documents du tenant citent encore (champs Link).

        Pas de clé étrangère dans la table générique : c'est ici que l'intégrité se tient. La ligne est d'abord verrouillée
        (`FOR UPDATE`) : une écriture concurrente qui créerait une référence a pris son `FOR KEY SHARE` avant (elle attend la
        fin de cette suppression, ou on attend la sienne puis on voit sa référence). Sur SQLite, qui n'a pas de verrou de
        ligne, une fenêtre très étroite subsiste entre ce contrôle et la suppression.
        La réponse nomme les types et champs, jamais les documents : l'appelant n'a peut-être pas le droit de les lire.
        """
        referrers = self._registry.referrers(doctype)
        if not referrers:
            return
        await q.lock_for_delete(doctype, name)
        used = [(d, f, t) for d, f, t, *_ in await q.references_to(doctype, name, [r.key() for r in referrers])]
        if not used:
            return
        labels = []
        for ref_doctype, fieldname, table in used:
            d = self._registry.get(ref_doctype).definition
            if table is None:
                labels.append(f"{d.label or d.name} ({d.field_map[fieldname].label or fieldname})")
            else:  # un Link dans les lignes : « Facture (Lignes › Article) »
                child = self._registry.get(ref_doctype).children[str(d.field_map[table].options)]
                labels.append(f"{d.label or d.name} ({d.field_map[table].label or table} › {child.field_map[fieldname].label or fieldname})")
        target = self._registry.get(doctype).definition
        # L'utilisateur reconnaît « Vis à bois », pas « 2a120aa438f9 » : le titre quand le DocType en a un, sinon le nom.
        title = data.get(target.title_field) if target.title_field else None
        shown = f"« {title} »" if isinstance(title, str) and title else repr(name)
        raise DocumentInUse(
            f"Suppression impossible : {target.label or doctype} {shown} est utilisé par {', '.join(labels)}",
            details=[{"doctype": d, "field": f, **({"table": t} if t else {})} for d, f, t in used],
        )

    def _require_hooks(self, compiled: CompiledDocType) -> None:
        """Échec fermé (D16) : tout hook référencé doit être disponible, sinon on ne sauvegarde pas.

        Après un reload d'xcore-doctype, le registre est vide jusqu'à ce que les plugins dépendants se
        réenregistrent ; ignorer le hook reviendrait à sauvegarder SANS la validation que la définition promet.
        """
        refs = compiled.definition.hooks.all_refs()
        missing = [r for r in refs if not r.startswith("action:") and self._hooks.get(r) is None]
        if any(r.startswith("action:") for r in refs) and self._actions is None:
            missing.append("action:*")
        if missing:
            self._log.error("hooks unavailable, save refused", doctype=compiled.definition.name, hooks=missing)
            raise DoctypeError("hook_unavailable", "des hooks de ce DocType sont indisponibles", status=503)

    async def _action_phase(
        self, ctx: RequestContext, compiled: CompiledDocType, name: str | None, data: dict[str, Any],
        old: Mapping[str, Any] | None, finalize: Finalize,
    ) -> dict[str, Any]:
        """Hooks `action:` : hors transaction (D21). La réponse d'un plugin sandboxé est revalidée comme une entrée client."""
        hooks = compiled.definition.hooks
        original = data  # les nouveaux secrets en clair : un plugin sandboxé n'en reçoit que le masque
        for event in ("validate", "before_save"):
            for ref in hooks.for_event(event):
                if not ref.startswith("action:"):
                    continue
                returned = await self._actions.run(  # type: ignore[union-attr]  # présence vérifiée par _require_hooks
                    ref, event=event, ctx=ctx, doctype=compiled.definition.name, name=name,
                    data=self._mask_data(compiled, data) or {}, old=self._mask_data(compiled, old),
                )
                try:
                    data = finalize(returned)
                    for secret_field in compiled.definition.secret_fields:
                        if data.get(secret_field) == MASK and original.get(secret_field) not in (None, MASK):
                            data[secret_field] = original[secret_field]
                except ValidationFailed as e:
                    self._log.error("action hook returned an invalid document", hook=ref, errors=e.details)
                    raise DoctypeError("hook_invalid_result", f"le hook {ref} a renvoyé un document invalide", status=502, details=e.details) from None
        return data

    async def _trusted_phase(
        self, ctx: RequestContext, compiled: CompiledDocType, q: Query, name: str | None, data: dict[str, Any],
        old: Mapping[str, Any] | None, finalize: Finalize,
    ) -> dict[str, Any]:
        """Hooks de confiance `validate`, `before_save` puis, à la création, `before_insert`, DANS la transaction, appelés directement (D2)."""
        hooks, doctype = compiled.definition.hooks, compiled.definition.name
        old_view = MappingProxyType(self._mask_data(compiled, old)) if old is not None else None

        async def get(dt: str, nm: str) -> dict[str, Any] | None:
            doc = await q.get(dt, nm, scope=Scope.everything("read"))
            return self._present(doc) if doc else None

        work = dict(data)
        for event in ("validate", "before_save", *(("before_insert",) if old is None else ())):
            for ref in hooks.for_event(event):
                if ref.startswith("action:"):
                    continue
                fn = self._hooks.get(ref)
                if fn is None:  # désenregistré entre le contrôle initial et maintenant
                    raise DoctypeError("hook_unavailable", "des hooks de ce DocType sont indisponibles", status=503)
                view = MappingProxyType(dict(work)) if event == "validate" else work  # validate: lecture seule
                await self._call_trusted(ref, fn(HookEvent(event, ctx, doctype, name, view, old_view, get)))
            work = finalize(work)  # un before_save peut tout modifier : le schéma et le périmètre font foi
        return work

    async def _call_trusted(self, ref: str, call: Awaitable[None]) -> None:
        try:
            await asyncio.wait_for(call, timeout=self._hook_settings.timeout_seconds)
        except DoctypeError:
            raise  # DocumentValidationError & co : refus voulu, message destiné à l'utilisateur
        except TimeoutError:
            raise DoctypeError("hook_timeout", f"le hook {ref} n'a pas répondu à temps", status=504) from None
        except Exception as e:  # noqa: BLE001
            self._log.error("hook failed", hook=ref, error=repr(e))
            raise DoctypeError("hook_error", "un hook a échoué", status=500) from None

    async def _after_save(
        self, ctx: RequestContext, compiled: CompiledDocType, doc: StoredDoc, old: Mapping[str, Any] | None
    ) -> None:
        """Après le commit : rien ne peut défaire l'écriture. Les erreurs sont journalisées, jamais levées (D2)."""
        doctype = compiled.definition.name

        async def get(dt: str, nm: str) -> dict[str, Any] | None:
            async with self._query(ctx) as q:
                found = await q.get(dt, nm, scope=Scope.everything("read"))
            return self._present(found) if found else None

        old_view = MappingProxyType(dict(old)) if old is not None else None
        for kind in (("after_insert",) if old is None else ()) + ("after_save",):
            for ref in compiled.definition.hooks.for_event(kind):
                fn = self._hooks.get(ref)
                if fn is None:
                    self._log.error(f"{kind} hook unavailable", hook=ref, doctype=doctype)
                    continue
                event = HookEvent(kind, ctx, doctype, doc.name, MappingProxyType(dict(doc.data)), old_view, get)  # type: ignore[arg-type]
                try:
                    await asyncio.wait_for(fn(event), timeout=self._hook_settings.timeout_seconds)
                except Exception as e:  # noqa: BLE001 - y compris TimeoutError
                    self._log.error(f"{kind} hook failed", hook=ref, doctype=doctype, name=doc.name, error=repr(e))
        await self._emit(
            f"doctype.{doctype}.after_save",
            {"doctype": doctype, "name": doc.name, "tenant_id": ctx.tenant_id, "version": doc.version,
             "user": ctx.user_id, "created": old is None},
        )

    async def _after_delete(self, ctx: RequestContext, compiled: CompiledDocType, doc: StoredDoc) -> None:
        """Après le commit de la suppression : `after_delete` reçoit ce que le document contenait. Les erreurs sont journalisées, jamais levées."""
        doctype = compiled.definition.name

        async def get(dt: str, nm: str) -> dict[str, Any] | None:
            async with self._query(ctx) as q:
                found = await q.get(dt, nm, scope=Scope.everything("read"))
            return self._present(found) if found else None

        for ref in compiled.definition.hooks.after_delete:
            fn = self._hooks.get(ref)
            if fn is None:
                self._log.error("after_delete hook unavailable", hook=ref, doctype=doctype)
                continue
            event = HookEvent("after_delete", ctx, doctype, doc.name, MappingProxyType(self._mask_data(compiled, doc.data) or {}), None, get)
            try:
                await asyncio.wait_for(fn(event), timeout=self._hook_settings.timeout_seconds)
            except Exception as e:  # noqa: BLE001 - y compris TimeoutError
                self._log.error("after_delete hook failed", hook=ref, doctype=doctype, name=doc.name, error=repr(e))
        await self._emit(f"doctype.{doctype}.after_delete", {"doctype": doctype, "name": doc.name, "tenant_id": ctx.tenant_id, "user": ctx.user_id})

    async def _emit(self, topic: str, payload: dict[str, Any]) -> None:
        # Pas de contenu du document : l'EventBus est global au processus, ses abonnés ne sont pas filtrés par tenant.
        if self._events is None:
            return
        try:
            await self._events.emit(topic, payload, source="xcore-doctype")
        except Exception as e:  # noqa: BLE001 - une notification ne défait rien
            self._log.error("event failed", topic=topic, name=payload.get("name"), error=repr(e))

    def _scope(self, ctx: RequestContext, compiled: Any, action: Action) -> Scope:
        return require(scope_for(compiled.definition, ctx, action))

    @staticmethod
    def _check_written(scope: Scope, owner: str, data: Mapping[str, Any]) -> None:
        """La valeur écrite doit rester dans le périmètre (D6) : on ne crée ni ne déplace hors de son périmètre."""
        if not scope.allows(owner, data):
            raise PermissionDenied()

    async def _load_for_change(
        self, ctx: RequestContext, compiled: Any, name: str, action: Action
    ) -> tuple[Any, Scope]:
        """Charge le document à modifier/supprimer. 404 s'il n'est pas lisible, 403 s'il n'est pas modifiable."""
        doctype = compiled.definition.name
        read, scope = self._scope(ctx, compiled, "read"), self._scope(ctx, compiled, action)
        if (source := self._source(compiled)) is not None:
            current = await self._peek(ctx, compiled, None, name, read, source)
        else:
            async with self._query(ctx) as q:
                current = await q.get(doctype, name, scope=read)
        if current is None:
            raise NotFound("document_not_found", f"{doctype} {name!r} introuvable")
        if not scope.allows(current.owner, current.data):
            raise PermissionDenied()
        return current, scope

    # ── DocTypes virtuels (sources.py) ────────────────────────
    def _source(self, compiled: CompiledDocType) -> DocumentSource | None:
        """La source d'un DocType virtuel, `None` pour un DocType ordinaire. Une source non enregistrée REFUSE la requête (503), jamais un résultat vide :
        une liste de rôles qui s'afficherait vide parce que le plugin est arrêté serait pire qu'une erreur."""
        name = compiled.definition.source
        if name is None:
            return None
        found = self._sources.get(name) if self._sources is not None else None
        if found is None:
            self._log.error("document source unavailable, request refused", doctype=compiled.definition.name, source=name)
            raise DoctypeError("source_unavailable", "la source de ce DocType est indisponible", status=503)
        return found

    async def _call_source(self, compiled: CompiledDocType, call: Awaitable[Any]) -> Any:
        """Un appel à la source : ses erreurs du moteur passent telles quelles, tout autre échec est une panne de la source (502, rien du détail ne sort)."""
        try:
            return await call
        except DoctypeError:
            raise
        except Exception as e:  # noqa: BLE001 - la source est un plugin : son exception ne doit pas fuiter (SQL, URL, jeton…)
            self._log.error("document source failed", doctype=compiled.definition.name, source=compiled.definition.source, error=repr(e))
            raise DoctypeError("source_failed", "la source de ce DocType ne répond pas", status=502) from None

    def _checked(self, compiled: CompiledDocType, doc: Any) -> StoredDoc:
        if not isinstance(doc, StoredDoc) or doc.doctype != compiled.definition.name or not isinstance(doc.data, dict):
            self._log.error("document source answered something unexpected", doctype=compiled.definition.name, source=compiled.definition.source)
            raise DoctypeError("source_invalid", "la source de ce DocType a répondu de travers", status=502)
        return doc

    async def _source_docs(self, ctx: RequestContext, compiled: CompiledDocType, source: DocumentSource) -> list[StoredDoc]:
        docs = await self._call_source(compiled, source.fetch_all(ctx, compiled.definition))
        return [self._checked(compiled, d) for d in docs]

    async def _peek(
        self, ctx: RequestContext, compiled: CompiledDocType, q: Query | None, name: str, scope: Scope, source: DocumentSource | None = None
    ) -> StoredDoc | None:
        """Un document que `scope` permet de LIRE, ou `None` : dans la table générique (`q`) ou chez la source du DocType virtuel. Un document hors
        périmètre est « introuvable » dans les deux cas."""
        source = source or self._source(compiled)
        if source is None:
            assert q is not None
            return await q.get(compiled.definition.name, name, scope=scope)
        if not isinstance(name, str):
            return None
        doc = await self._call_source(compiled, source.fetch(ctx, compiled.definition, name))
        if doc is None:
            return None
        doc = self._checked(compiled, doc)
        return doc if scope.allows(doc.owner, doc.data) else None

    async def _existing(self, ctx: RequestContext, compiled: CompiledDocType, q: Query | None, names: Sequence[str], scope: Scope) -> set[str]:
        """Parmi `names`, ceux qui existent ET que `scope` permet de lire (contrôle d'un champ Link), où que vive le DocType ciblé."""
        source = self._source(compiled)
        if source is None:
            assert q is not None
            return await q.existing_names(compiled.definition.name, names, scope=scope, lock=True)
        wanted = set(names)
        return {d.name for d in memory.visible(await self._source_docs(ctx, compiled, source), scope) if d.name in wanted}

    async def read_document(self, ctx: RequestContext, q: Query, doctype: str, name: str, scope: Scope) -> StoredDoc | None:
        """Pour la collaboration et les fichiers : le document, s'il est lisible, quel que soit son stockage."""
        return await self._peek(ctx, self._registry.get(doctype), q, name, scope)

    async def existing_documents(self, ctx: RequestContext, q: Query, doctype: str, names: Sequence[str], scope: Scope) -> set[str]:
        compiled = self._registry.get(doctype)
        if self._source(compiled) is None:
            return await q.existing_names(doctype, set(names), scope=scope)  # sans verrou : on ne fait que LIRE
        return await self._existing(ctx, compiled, q, names, scope)

    async def _create_virtual(self, ctx: RequestContext, compiled: CompiledDocType, scope: Scope, data: Any, source: DocumentSource) -> dict[str, Any]:
        """Création chez la source : schéma strict, périmètre d'écriture et liens vérifiés ICI, puis la source applique à sa vérité (et la revérifie).
        Aucune connexion n'est tenue pendant l'appel de la source (elle peut appeler un autre plugin, voire écrire dans la même base)."""
        finalize = self._finalizer(compiled, scope, ctx.owner_id)
        clean = finalize(data)
        async with self._query(ctx) as q:
            await self._check_links(ctx, q, compiled, clean, None)
        doc = self._checked(compiled, await self._call_source(compiled, source.insert(ctx, compiled.definition, clean)))
        return self._present(doc)

    async def _update_virtual(
        self, ctx: RequestContext, compiled: CompiledDocType, write: Scope, current: StoredDoc, data: Any, version: int, source: DocumentSource
    ) -> dict[str, Any]:
        finalize = self._finalizer(compiled, write, current.owner)
        clean = finalize(data, old=current.data)
        async with self._query(ctx) as q:
            await self._check_links(ctx, q, compiled, clean, current.data)
        doc = self._checked(compiled, await self._call_source(compiled, source.update(ctx, compiled.definition, current.name, clean, version)))
        return self._present(doc)

    @asynccontextmanager
    async def _query(self, ctx: RequestContext) -> AsyncIterator[Query]:
        # `session()` commite à la sortie du bloc (les futurs `after_*` seront émis après).
        async with self._db.session() as session:
            if self._rls:
                await bind_tenant(session, ctx.tenant_id)  # AVANT toute autre instruction : sans lui, la politique ne laisse rien passer
            yield Query(session, ctx, self._tables)


def _canonical_defaults(fields: list[dict[str, Any]]) -> None:
    """Le défaut d'un champ Decimal part sous sa forme canonique (`0` écrit en YAML -> `"0.000"`) : le Desk le traite comme n'importe
    quelle valeur du champ, une chaîne, et ne doit pas connaître les règles de conversion."""
    for f in fields:
        if f["fieldtype"] in ("Decimal", "Currency") and f.get("default") is not None:
            f["default"] = canonical(f["default"], f["decimals"])


_VIEW_KEYS = {"columns", "filters", "order_by", "descending"}


def _view_name(name: Any) -> str:
    """Nom d'une vue sauvegardee : un texte court, sans caractere de controle (colonne indexee, jamais du SQL brut)."""
    if not isinstance(name, str):
        raise ValidationFailed("le nom d'une vue doit etre un texte")
    clean = name.strip()
    if not 1 <= len(clean) <= 140:
        raise ValidationFailed("le nom d'une vue doit faire de 1 a 140 caracteres")
    if any(ord(c) < 32 for c in clean):
        raise ValidationFailed("le nom d'une vue ne doit pas contenir de caractere de controle")
    return clean


def _view_config(compiled: CompiledDocType, config: Any) -> dict[str, Any]:
    """Valide la configuration d'une liste (colonnes, filtres, tri) contre la definition : rien d'inconnu n'est stocke.

    Les filtres passent par le meme validateur que la liste (`aggregation.validate_filters`) : une vue enregistree ne peut pas
    porter un filtre que la liste refuserait, et la cle stockee est une valeur canonique.
    """
    d = compiled.definition
    if config is None:
        config = {}
    if not isinstance(config, Mapping):
        raise ValidationFailed("config doit être un objet")
    unknown = set(config) - _VIEW_KEYS
    if unknown:
        raise ValidationFailed(f"config : clés inconnues {sorted(unknown)}")
    allowed = set(SYSTEM_SORTABLE) | set(d.field_map)
    columns = config.get("columns", d.list_columns())
    if not isinstance(columns, list) or not all(isinstance(c, str) for c in columns):
        raise ValidationFailed("config.columns doit être une liste de noms de champs")
    if len(columns) > 50:
        raise ValidationFailed("config.columns : 50 colonnes au plus")
    for c in columns:
        if c not in allowed:
            raise ValidationFailed(f"config.columns : champ inconnu {c!r}")
    order_by = config.get("order_by", "modified")
    if not isinstance(order_by, str) or (order_by not in SYSTEM_SORTABLE and order_by not in d.field_map):
        raise ValidationFailed(f"config.order_by : tri impossible sur {order_by!r}")
    descending = config.get("descending", True)
    if not isinstance(descending, bool):
        raise ValidationFailed("config.descending doit être un booléen")
    filters = aggregation.validate_filters(compiled, config.get("filters"))
    return {"columns": columns, "order_by": order_by, "descending": descending, "filters": filters}


def _tree(defn: DocTypeDef, parent_field: str, docs: list[StoredDoc], total: int) -> dict[str, Any]:
    """Rattache chaque document à son parent et rend les racines. Un parent hors page, soi-même ou une boucle -> racine."""
    parents: dict[str, str | None] = {}
    for doc in docs:
        raw = doc.data.get(parent_field)
        parents[doc.name] = raw if isinstance(raw, str) and raw else None
    nodes = {
        doc.name: {
            "name": doc.name,
            "title": (doc.data.get(defn.title_field) if defn.title_field else None) or doc.name,
            "children": [],
        }
        for doc in docs
    }

    def reaches(start: str, target: str) -> bool:
        seen: set[str] = set()
        cur: str | None = start
        while cur is not None and cur not in seen:
            if cur == target:
                return True
            seen.add(cur)
            cur = parents.get(cur)
        return False

    roots: list[dict[str, Any]] = []
    for doc in docs:
        node = nodes[doc.name]
        parent = parents[doc.name]
        if parent is None or parent == doc.name or parent not in nodes or reaches(parent, doc.name):
            roots.append(node)
        else:
            nodes[parent]["children"].append(node)
    return {"parent": parent_field, "items": roots, "total": total, "truncated": total > len(docs)}


def _relabel(compiled: CompiledDocType, referrers: Sequence[Any], data: Mapping[str, Any], target: str, old: str, new: str) -> dict[str, Any]:
    """Le contenu d'un document dont chaque Link (ou ligne de table) qui citait `target`/`old` cite désormais `new`. Ne modifie pas `data`."""
    out = dict(data)
    for ref in referrers:
        def hit(holder: Mapping[str, Any]) -> bool:
            return holder.get(ref.fieldname) == old and (ref.type_field is None or holder.get(ref.type_field) == target)

        if ref.table is None:
            if hit(out):
                out[ref.fieldname] = new
        elif out.get(ref.table):
            out[ref.table] = [{**row, ref.fieldname: new} if hit(row) else row for row in out[ref.table]]
    return out


def _amended_name(source: StoredDoc) -> str:
    """Le nom de l'amendement d'un document annulé : `FA-1` -> `FA-1-1`, et l'amendement `FA-1-1` -> `FA-1-2`."""
    m = _AMENDMENT.match(source.name) if source.amended_from else None
    return f"{m['root']}-{int(m['n']) + 1}" if m else f"{source.name}-1"


class _Transaction:
    """`HookTransaction` : les écritures d'un hook `on_submit` / `on_cancel`, dans la transaction ouverte par le service.

    Le service les a toutes sous la main (validation, liens, historique) : cette classe ne fait que les enchaîner dans l'ordre de
    `DocumentService._create` / `update`, sans permission (code de confiance) et sans hooks (voir le protocole).
    """

    def __init__(self, service: DocumentService, ctx: RequestContext, q: Query, event: str) -> None:
        self._svc, self._ctx, self._q = service, ctx, q
        # Les liens se vérifient comme le ferait le système : la cible doit exister, pas être lisible par celui qui soumet
        # (un magasinier soumet une sortie sans avoir le droit de lire les articles).
        self._system = RequestContext.system(ctx.tenant_id, f"hook:{event}")

    @property
    def today(self) -> date:
        return self._svc._clock().astimezone(self._svc._timezone).date()

    async def get(self, doctype: str, name: str, *, lock: bool = False) -> dict[str, Any] | None:
        compiled = self._svc._doctype(doctype)
        if (source := self._svc._source(compiled)) is not None:
            found = await self._svc._peek(self._system, compiled, None, name, Scope.everything("read"), source)
            return self._svc._present(found) if found else None
        doc = await self._q.get(doctype, name, scope=Scope.everything("read"), lock=lock)
        return self._svc._present(doc) if doc else None

    async def create(self, doctype: str, data: Mapping[str, Any], *, name: str | None = None) -> dict[str, Any]:
        compiled = self._writable(doctype)
        clean = self._svc._seal(compiled, await self._valid(compiled, self._svc._with_today(compiled, data), None), None)
        series = compiled.definition.series
        if name is None and series:
            now = self._svc._clock().astimezone(self._svc._timezone)
            name = series.render(now, await self._q.next_number(doctype, series.key(now)))
        owner_named = compiled.definition.naming == "owner"
        doc = await self._q.insert(doctype, name or (self._ctx.owner_id if owner_named else secrets.token_hex(6)), clean)
        await self._unique(compiled, doc)
        await self._svc._record(self._q, self._ctx, compiled, doc, "create", None, doc.data)
        return self._svc._present(doc)

    async def ensure(self, doctype: str, name: str, data: Mapping[str, Any]) -> bool:
        compiled = self._writable(doctype)
        clean = self._svc._seal(compiled, await self._valid(compiled, self._svc._with_today(compiled, data), None), None)
        if not await self._q.insert_if_absent(doctype, name, clean):
            return False
        doc = await self._q.get(doctype, name, scope=Scope.everything("read"))
        assert doc is not None  # insérée à l'instant, dans cette transaction
        await self._unique(compiled, doc)
        await self._svc._record(self._q, self._ctx, compiled, doc, "create", None, doc.data)
        return True

    async def update(self, doctype: str, name: str, data: Mapping[str, Any], version: int) -> dict[str, Any]:
        compiled = self._writable(doctype)
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            raise ValidationFailed("`version` (entier >= 1) est obligatoire pour une mise à jour", code="version_required")
        current = await self._q.get(doctype, name, scope=Scope.everything("read"))
        if current is None:
            raise NotFound("document_not_found", f"{doctype} {name!r} introuvable")
        clean = self._svc._seal(compiled, await self._valid(compiled, data, current.data), current.data)
        doc = await self._q.update(doctype, name, clean, version, scope=Scope.everything("write"))
        await self._unique(compiled, doc)
        await self._svc._record(self._q, self._ctx, compiled, doc, "update", current.data, doc.data)
        return self._svc._present(doc)

    async def _unique(self, compiled: CompiledDocType, doc: StoredDoc) -> None:
        """Les valeurs `unique` d'un document écrit par un hook : une valeur déjà prise est un défaut du hook (500), comme tout document invalide écrit d'ici."""
        try:
            await self._svc._sync_unique(self._q, compiled, doc)
        except ValidationFailed as e:
            self._svc._log.error("hook wrote a duplicate", doctype=compiled.definition.name, errors=e.details)
            raise DoctypeError("hook_invalid_write", f"un hook a écrit un {compiled.definition.name} en double", status=500, details=e.details) from None

    async def _valid(self, compiled: CompiledDocType, data: Mapping[str, Any], old: Mapping[str, Any] | None) -> dict[str, Any]:
        """Schéma strict, puis liens. Un document invalide ici est un défaut du HOOK, pas une saisie de l'utilisateur : 500, pas 422."""
        try:
            clean = compiled.validate_data(dict(data))
            clean = await self._svc._fetched(self._system, self._q, compiled, clean, old)
            await self._svc._check_links(self._system, self._q, compiled, clean, old)
            await self._svc._check_files(self._q, compiled, clean, old)
        except ValidationFailed as e:
            self._svc._log.error("hook wrote an invalid document", doctype=compiled.definition.name, errors=e.details)
            raise DoctypeError(
                "hook_invalid_write", f"un hook a écrit un {compiled.definition.name} invalide", status=500, details=e.details
            ) from None
        return clean

    def _writable(self, doctype: str) -> CompiledDocType:
        compiled = self._svc._doctype(doctype)
        if compiled.definition.source:
            raise DoctypeError(
                "hook_not_supported",
                f"{doctype} est un DocType virtuel (source externe) : un hook n'y écrit pas dans sa transaction, écrivez-le par l'API",
                status=500,
            )
        if compiled.definition.hooks.all_refs():
            raise DoctypeError(
                "hook_not_supported",
                f"{doctype} déclare des hooks, qui ne s'exécutent pas pour une écriture faite depuis un hook : écrivez-le par l'API",
                status=500,
            )
        return compiled


class SystemReader:
    """Lecture SEULE des documents d'un tenant, en contexte système : ce que voit un plugin de confiance qui vérifie ou rapporte.

    Un contrôle (recalculer des soldes depuis le journal, comparer deux totaux) doit lire TOUS les documents du tenant, pas ceux
    qu'un utilisateur a le droit de voir. Le contexte système (D20) est fait pour cela : explicite, nommé par sa raison, et journalisé
    à chaque appel. Aucune écriture n'est exposée ici : un plugin qui doit écrire passe par un hook de transition (`HookTransaction`)
    ou par l'API avec l'identité d'un utilisateur.
    """

    def __init__(self, service: DocumentService, ctx: RequestContext) -> None:
        self._svc, self._ctx = service, ctx

    async def get(self, doctype: str, name: str) -> dict[str, Any]:
        return await self._svc.get(self._ctx, doctype, name)

    async def list(self, doctype: str, **kw: Any) -> dict[str, Any]:
        return await self._svc.list(self._ctx, doctype, **kw)

    async def history(self, doctype: str, name: str, **kw: Any) -> dict[str, Any]:
        return await self._svc.history(self._ctx, doctype, name, **kw)

    async def secret(self, doctype: str, name: str, field: str) -> str | None:
        """Le secret d'un champ `Password`, EN CLAIR (`None` s'il n'est pas posé) : la seule porte vers un secret, réservée au code de confiance, journalisée."""
        compiled = self._svc._doctype(doctype)
        async with self._svc._query(self._ctx) as q:
            stored = await q.get(doctype, name, scope=Scope.everything("read"))
        if stored is None:
            raise NotFound("document_not_found", f"{doctype} {name!r} introuvable")
        return self._svc.reveal_secret(self._ctx, compiled, stored.data, field)


class SystemReaders:
    """Le service exporté `doctype_documents` : `self.get_service("doctype_documents").system(tenant, "ma raison")`.

    Même confiance que `doctype_hooks` ou `doctype_registry` : n'importe quel plugin de confiance chargé dans le processus peut l'appeler
    (il pourrait de toute façon ouvrir la base lui-même). Le tenant est validé comme partout (`RequestContext.system`).
    """

    def __init__(self, service: DocumentService) -> None:
        self._svc = service

    def system(self, tenant_id: str, reason: str) -> SystemReader:
        return SystemReader(self._svc, RequestContext.system(tenant_id, reason))
