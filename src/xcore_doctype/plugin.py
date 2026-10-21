"""Classe `Plugin` d'xcore-doctype (appelée par le shim `plugin_dir/src/main.py`).

Tout l'état (registre, MetaData, service) est créé dans `on_load` et rendu dans `on_unload` (D8) :
au reload, xcore ne purge de `sys.modules` que le shim `xcore_plugin_xcore-doctype.*`, jamais ce package.
"""

from __future__ import annotations

import os
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter
from xcore.kernel.tenancy.services import TenantAwareDB
from xcore.sdk import AutoDispatchMixin, TrustedBase, action, error, ok

from .api.desk import DeskSettings, build_desk_router
from .api.collab import build_collab_router
from .api.files import build_files_router, build_preferences_router, build_public_files_router
from .api.http import build_router
from .context import ContextSettings, RequestContext
from .db.access import create_system_tables, ensure_async_database
from .db.rls import ensure_rls
from .db.tables import SystemTables, build_system_tables
from .document import DocumentService, SystemReaders
from .file_service import FileService
from .files import BlobStore, LocalBlobStore, UrlSigner
from .errors import ConfigurationError, DoctypeError, NotFound, ValidationFailed
from .collaboration import CollaborationService
from .directory import DEFAULT_ACTION, DEFAULT_PLUGIN, DEFAULT_TTL, Directory, NoDirectory, XauthDirectory
from .grants import GrantCatalog
from .notifier import DEFAULT_ACTION as NOTIFY_ACTION, DEFAULT_PLUGIN as NOTIFY_PLUGIN, NoNotifier, Notifier, XauthNotifier
from .preferences import Preferences
from .hooks import ActionHooks, HookRegistry, HookSettings
from .registry import DocTypeChannel, DocTypeRegistry
from .sealing import Sealer
from .sources import DocumentSources


HOOKS_SERVICE = "doctype_hooks"  # service exporté : `self.get_service("doctype_hooks").register(...)`
GRANTS_SERVICE = "doctype_grants"  # service exporté : `self.get_service("doctype_grants").for_plugin(nom)`
DOCUMENTS_SERVICE = "doctype_documents"  # service exporté : lecture système, `.system(tenant, raison)` (contrôles, rapports)
SOURCES_SERVICE = "doctype_sources"  # service exporté : `self.get_service("doctype_sources").register(nom_plugin, "ma_source", source)` (DocTypes virtuels)
REGISTRY_SERVICE = "doctype_registry"  # service exporté : `self.get_service("doctype_registry").load_dir(nom, dossier)`


def _need(payload: dict, key: str) -> Any:
    if not isinstance(payload, dict) or payload.get(key) is None:
        raise ValidationFailed(f"champ obligatoire manquant : {key!r}", code="missing_field")
    return payload[key]


class Plugin(AutoDispatchMixin, TrustedBase):
    registry: DocTypeRegistry | None = None
    hook_registry: HookRegistry | None = None
    grant_catalog: GrantCatalog | None = None
    collaboration: CollaborationService | None = None
    documents: DocumentService | None = None
    _tables: SystemTables | None = None
    _settings: ContextSettings | None = None
    _router: APIRouter | None = None

    # ── Cycle de vie ──────────────────────────────────────────
    def _directory(self, raw: Any) -> Directory:
        """L'annuaire des utilisateurs (`doctype.directory`) : xauth par défaut (`provider: xauth`), ou aucun (`provider: none` : identités observées)."""
        raw = raw if raw is not None else {}
        if not isinstance(raw, dict):
            raise ConfigurationError("doctype.directory doit être un objet")
        provider = raw.get("provider", "xauth")
        if provider == "none":
            return NoDirectory()
        if provider != "xauth":
            raise ConfigurationError("doctype.directory.provider : `xauth` ou `none`")
        plugin, action, ttl = raw.get("plugin", DEFAULT_PLUGIN), raw.get("action", DEFAULT_ACTION), raw.get("ttl_seconds", DEFAULT_TTL)
        if not isinstance(plugin, str) or not isinstance(action, str) or isinstance(ttl, bool) or not isinstance(ttl, (int, float)) or ttl < 0:
            raise ConfigurationError("doctype.directory : `plugin` et `action` sont des textes, `ttl_seconds` un nombre positif")
        caller = self.ctx.caller
        if caller is None:
            return NoDirectory()  # plugin sans appel de plugin (test) : pas d'annuaire

        me = self.ctx.name  # des VALEURS, pas `self` : l'annuaire vit dans les routes, et l'instance du plugin doit pouvoir être libérée au reload (D8)

        async def call(target: str, act: str, payload: dict[str, Any], tenant_id: str) -> Any:
            return await caller(target, act, payload, caller=me, tenant_id=tenant_id, principal=None)

        return XauthDirectory(call, plugin=plugin, action=action, ttl=float(ttl), logger=self.logger)

    def _notifier(self, raw: Any) -> Notifier:
        """Qui prévient les gens (`doctype.notifications`) : xauth par défaut (`provider: xauth`, action `xauth.notify`), ou personne (`provider: none`)."""
        raw = raw if raw is not None else {}
        if not isinstance(raw, dict):
            raise ConfigurationError("doctype.notifications doit être un objet")
        provider = raw.get("provider", "xauth")
        if provider == "none":
            return NoNotifier()
        if provider != "xauth":
            raise ConfigurationError("doctype.notifications.provider : `xauth` ou `none`")
        plugin, act = raw.get("plugin", NOTIFY_PLUGIN), raw.get("action", NOTIFY_ACTION)
        if not isinstance(plugin, str) or not isinstance(act, str):
            raise ConfigurationError("doctype.notifications : `plugin` et `action` sont des textes")
        caller = self.ctx.caller
        if caller is None:
            return NoNotifier()
        me = self.ctx.name

        async def call(target: str, act_: str, payload: dict[str, Any], tenant_id: str) -> Any:
            return await caller(target, act_, payload, caller=me, tenant_id=tenant_id, principal=None)

        return XauthNotifier(call, plugin=plugin, action=act, logger=self.logger)

    async def on_load(self) -> None:
        config = self.ctx.config or {}
        block = config.get("doctype") or {}
        self._settings = ContextSettings.from_plugin_config(config)

        db = self.get_service("db")
        dialect = await ensure_async_database(db)  # D11 : échoue ici, clairement, sinon
        if dialect == "postgresql" and isinstance(db, TenantAwareDB):
            # Mesuré (tests/integration/test_postgres.py) : les routes HTTP ne posent jamais le tenant
            # dans la ContextVar, donc TenantAwareDB y isole sur « default » ; le chemin IPC isole sur
            # le vrai tenant. Avec des schémas de tenant existants, HTTP et IPC divergent.
            self.logger.warning(
                "tenancy.isolate_db est actif sur PostgreSQL : mettez-le à false. "
                "L'isolation d'xcore-doctype est la colonne tenant_id (D3) ; le search_path d'xcore "
                "ne s'applique pas de façon cohérente aux routes HTTP du plugin."
            )

        dirs = block.get("doctypes_dirs", [])
        if not isinstance(dirs, list) or not all(isinstance(d, str) for d in dirs):
            raise ConfigurationError("doctype.doctypes_dirs doit être une liste de chemins")

        registry = DocTypeRegistry()
        for d in dirs:
            registry.load_dir(Path(d).expanduser())
        registry.validate_links()  # toutes les définitions sont là : une cible de Link inconnue échoue au chargement

        tables = build_system_tables()
        await create_system_tables(db, tables)
        rls = block.get("rls", False)
        if not isinstance(rls, bool):
            raise ConfigurationError("doctype.rls doit être un booléen")
        if rls:
            await ensure_rls(db, tables, dialect)  # échoue fermé : base non PostgreSQL, ou rôle qui contourne la RLS

        hook_settings = HookSettings.from_plugin_config(config)
        hook_registry = HookRegistry()
        actions = ActionHooks(self.ctx.caller, self.ctx.name, hook_settings, self.logger)
        self.registry, self._tables, self.hook_registry = registry, tables, hook_registry
        sources = DocumentSources()
        self.documents = DocumentService(
            registry, db, tables, self.logger,
            hooks=hook_registry, actions=actions, events=self.ctx.events, hook_settings=hook_settings,
            timezone=_timezone(block.get("timezone", "UTC")), rls=rls, sources=sources, sealer=self._sealer(block.get("secret_key")),
        )
        file_service = FileService(self.documents, self._blob_store(block.get("files")), self._url_signer(block.get("files")))
        self.documents.files = file_service  # pour libérer les fichiers qu'un document ne cite plus
        desk = DeskSettings.from_plugin_config(config)
        # Deux routeurs frères : l'API exige une identité sur chaque route, le Desk est public (fichiers statiques seulement).
        root = APIRouter()
        root.include_router(build_router(self.documents, self._settings))
        root.include_router(build_files_router(file_service, self._settings))  # téléverser, liens signés : une identité est exigée
        collab = CollaborationService(self.documents, file_service, self._directory(block.get("directory")), self._notifier(block.get("notifications")))
        self.collaboration = collab
        root.include_router(build_collab_router(collab, self._settings))  # commentaires, assignations, pièces jointes
        root.include_router(build_preferences_router(Preferences(self.documents, file_service), self._settings))  # nom, logo, couleur de l'organisation
        root.include_router(build_public_files_router(file_service))  # servir : la signature de l'URL fait foi (pas de jeton dans une balise <img>)
        if desk.enabled:
            root.include_router(build_desk_router(desk))
        self._router = root
        # Les plugins qui dépendent de nous (`requires: [xcore-doctype]`) y enregistrent leurs hooks de confiance...
        self.ctx.services[HOOKS_SERVICE] = hook_registry
        # ...et y enregistrent la SOURCE des DocTypes virtuels qu'ils apportent (leurs documents vivent ailleurs, voir sources.py).
        self.ctx.services[SOURCES_SERVICE] = sources
        # ...et en tirent les grants à déclarer dans auth (source unique : le YAML des DocTypes, voir grants.py).
        catalog = GrantCatalog(registry)
        self.grant_catalog = catalog
        self.ctx.services[GRANTS_SERVICE] = catalog
        # ...et y apportent leurs propres DocTypes (module == leur nom, voir registry.DocTypeChannel).
        self.ctx.services[REGISTRY_SERVICE] = DocTypeChannel(registry, self.logger, unique_check=self.documents.ensure_unique_clean)
        # ...et lisent les documents d'un tenant en contexte système, pour vérifier ou rapporter (lecture seule).
        self.ctx.services[DOCUMENTS_SERVICE] = SystemReaders(self.documents)
        # Un champ `unique` posé sur un type qui a déjà des documents refuse le chargement si des valeurs sont déjà en double (D25).
        for name in registry.names():
            compiled = registry.get(name)
            if compiled.definition.unique_fields and not compiled.definition.istable:
                await self.documents.ensure_unique_clean(compiled)
        for name in registry.names():
            d = registry.get(name).definition
            if d.istable:
                continue  # les lignes héritent des droits de leur parent
            if not any(d.rules_for(a) for a in ("read", "write", "create", "delete", "submit", "cancel")):
                self.logger.warning(
                    "doctype without permission rules (no `module`, no `permissions`), denied to every user", doctype=name
                )
        grants = catalog.cited()
        self.logger.info(
            "xcore-doctype loaded",
            doctypes=registry.names(),
            grants=grants,
            hooks=sorted({r for n in registry.names() for r in registry.get(n).definition.hooks.all_refs()}),
            dialect=dialect,
            rls=rls,
            tenant_binding=self._settings.enforce_tenant_binding,
        )

    def _sealer(self, raw: Any) -> Sealer:
        """La clé des champs `Password` : `doctype.secret_key` du manifeste, sinon la variable d'environnement `XCORE_DOCTYPE_SECRET_KEY` (16 caractères au moins, jamais commitée :
        `make env` en tire une au hasard dans `.env`) ; absente, une écriture qui porte un secret est refusée (503)."""
        raw = raw if raw is not None else (os.environ.get("XCORE_DOCTYPE_SECRET_KEY") or None)
        if raw is None:
            return Sealer(None)
        if not isinstance(raw, str):
            raise ConfigurationError("doctype.secret_key doit être un texte")
        try:
            return Sealer(raw)
        except ValueError as e:
            raise ConfigurationError(str(e)) from None

    def _blob_store(self, raw: Any) -> BlobStore:
        """Le magasin de fichiers : l'extension `ext.storage` de l'hôte si elle existe (local, s3, r2, supabase), sinon un dossier local."""
        cfg = raw if isinstance(raw, dict) else {}
        if raw is not None and not isinstance(raw, dict):
            raise ConfigurationError("doctype.files doit être un objet")
        if not isinstance(cfg.get("dir", ""), str):
            raise ConfigurationError("doctype.files.dir doit être un chemin")
        if not cfg.get("local_only"):
            try:
                shared = self.get_service("ext.storage")
            except Exception:  # noqa: BLE001 - l'absence de l'extension n'est pas une erreur : on retombe sur le dossier local
                shared = None
            if shared is not None:
                self.logger.info("files use the shared storage extension", backend=getattr(shared, "backend", lambda: "?")())
                return shared
        root = cfg.get("dir") or "data/doctype-files"
        self.logger.info("files use a local directory", dir=str(root))
        return LocalBlobStore(root)

    def _url_signer(self, raw: Any) -> UrlSigner:
        cfg = raw if isinstance(raw, dict) else {}
        secret = cfg.get("secret")
        if secret is not None and (not isinstance(secret, str) or len(secret) < 16):
            raise ConfigurationError("doctype.files.secret doit être un texte d'au moins 16 caractères")
        if not secret:
            # Sans secret configuré, un secret par processus : les liens (valables 5 minutes) ne survivent pas à un redémarrage, et plusieurs
            # processus ne se comprennent pas. Un déploiement à plusieurs processus DOIT fixer `doctype.files.secret`.
            self.logger.warning("doctype.files.secret absent: signed file links are valid for this process only")
            secret = secrets.token_hex(32)
        return UrlSigner(secret)

    async def on_unload(self) -> None:
        if self.registry is not None:
            self.registry.clear()
        if self.hook_registry is not None:
            self.hook_registry.clear()
            self.ctx.services.pop(HOOKS_SERVICE, None)
            self.ctx.services.pop(SOURCES_SERVICE, None)
        if self.grant_catalog is not None:
            self.ctx.services.pop(GRANTS_SERVICE, None)
            self.ctx.services.pop(REGISTRY_SERVICE, None)
            self.ctx.services.pop(DOCUMENTS_SERVICE, None)
        self.registry = self.hook_registry = self.grant_catalog = self.documents = self._tables = self._router = self.collaboration = None

    def get_router(self) -> APIRouter | None:
        return self._router

    # ── Actions xcore (doc/doctype.md §10) ────────────────────
    # Les droits sont vérifiés par le service Document, pas par @action(permissions=...).

    @action("meta.list")
    async def meta_list(self, payload: dict) -> dict:
        return await self._run("meta.list", lambda ctx: _done(items=self.documents.list_meta(ctx)))

    @action("meta.workspaces")
    async def meta_workspaces(self, payload: dict) -> dict:
        async def op(ctx: RequestContext) -> dict:
            return {"items": await self.documents.fetch_workspaces(ctx)}

        return await self._run("meta.workspaces", op)

    @action("meta.get")
    async def meta_get(self, payload: dict) -> dict:
        async def op(ctx: RequestContext) -> dict:
            return {"meta": await self.documents.fetch_meta(ctx, _need(payload, "doctype"))}

        return await self._run("meta.get", op)

    @action("doc.get")
    async def doc_get(self, payload: dict) -> dict:
        async def op(ctx: RequestContext) -> dict:
            return {"doc": await self.documents.get(ctx, _need(payload, "doctype"), _need(payload, "name"))}

        return await self._run("doc.get", op)

    @action("doc.list")
    async def doc_list(self, payload: dict) -> dict:
        async def op(ctx: RequestContext) -> dict:
            return await self.documents.list(
                ctx,
                _need(payload, "doctype"),
                filters=payload.get("filters"),
                order_by=payload.get("order_by", "modified"),
                descending=payload.get("order", "desc") != "asc",
                limit=payload.get("limit", 20),
                offset=payload.get("offset", 0),
            )

        return await self._run("doc.list", op)

    @action("doc.aggregate")
    async def doc_aggregate(self, payload: dict) -> dict:
        async def op(ctx: RequestContext) -> dict:
            return await self.documents.aggregate(
                ctx,
                _need(payload, "doctype"),
                group_by=payload.get("group_by"),
                bucket=payload.get("bucket"),
                metrics=payload.get("metrics"),
                filters=payload.get("filters"),
                periods=payload.get("periods"),
                limit=payload.get("limit", 50),
            )

        return await self._run("doc.aggregate", op)

    @action("doc.search")
    async def doc_search(self, payload: dict) -> dict:
        async def op(ctx: RequestContext) -> dict:
            return await self.documents.search(
                ctx, _need(payload, "doctype"), payload.get("q", ""), limit=payload.get("limit", 10)
            )

        return await self._run("doc.search", op)

    @action("doc.global_search")
    async def doc_global_search(self, payload: dict) -> dict:
        async def op(ctx: RequestContext) -> dict:
            return await self.documents.global_search(ctx, payload.get("q", ""), limit=payload.get("limit", 10))

        return await self._run("doc.global_search", op)

    @action("doc.history")
    async def doc_history(self, payload: dict) -> dict:
        async def op(ctx: RequestContext) -> dict:
            return await self.documents.history(
                ctx, _need(payload, "doctype"), _need(payload, "name"),
                limit=payload.get("limit", 20), offset=payload.get("offset", 0),
            )

        return await self._run("doc.history", op)

    @action("doc.check_unique")
    async def doc_check_unique(self, payload: dict) -> dict:
        """Contrôle rétroactif de l'unicité (D25) : les valeurs `unique` déjà en double, l'installation entière ou un type.

        Appel d'opérateur (clé du plugin) : rien n'est écrit ni corrigé, le résultat nomme les documents fautifs.
        Charge : `{"doctypes"?: ["…"], "tenant"?: …}` (sous RLS, `tenant` est obligatoire).
        """
        body = payload or {}
        doctypes = body.get("doctypes")
        if doctypes is not None and (not isinstance(doctypes, list) or not doctypes or not all(isinstance(d, str) for d in doctypes)):
            return error("`doctypes` doit être une liste de noms de DocTypes", "invalid_payload")
        tenant = body.get("tenant")
        if tenant is not None and (not isinstance(tenant, str) or not tenant):
            return error("`tenant` doit être l'identifiant d'une organisation", "invalid_payload")
        try:
            conflicts = await self.documents.check_unique(doctypes=doctypes, tenant=tenant)
        except NotFound:
            return error("DocType inconnu", "doctype_not_found")
        except DoctypeError as e:  # RLS actif sans `tenant`
            return error(e.message, e.code)
        return ok(conflicts=conflicts)

    @action("doc.history.purge")
    async def doc_history_purge(self, payload: dict) -> dict:
        """Purge l'historique du tenant plus vieux qu'une date (D24) : `{"tenant": …, "before": "2026-01-01T00:00:00+00:00", "doctype"?: …}`.

        Appel d'opérateur (clé du plugin). La rétention par document (`max_versions`) purge à chaque écriture ;
        celle-ci rattrape le trop-plein d'avant une augmentation de `max_versions`. Renvoie le nombre d'entrées supprimées.
        """
        body = payload or {}
        tenant = body.get("tenant")
        if not isinstance(tenant, str) or not tenant:
            return error("`tenant` est obligatoire", "missing_field")
        raw = body.get("before")
        if not isinstance(raw, str) or not raw:
            return error("`before` est obligatoire (ISO 8601, ex. 2026-01-01T00:00:00+00:00)", "missing_field")
        try:
            before = datetime.fromisoformat(raw)
        except ValueError:
            return error("`before` doit être une date ISO 8601", "invalid_payload")
        if before.tzinfo is None:
            before = before.replace(tzinfo=UTC)
        doctype = body.get("doctype")
        if doctype is not None and (not isinstance(doctype, str) or not doctype):
            return error("`doctype` doit être un nom de DocTypes", "invalid_payload")
        deleted = await self.documents.purge_history(tenant, before, doctype=doctype)
        return ok(deleted=deleted)

    @action("doc.tree")
    async def doc_tree(self, payload: dict) -> dict:
        async def op(ctx: RequestContext) -> dict:
            return await self.documents.tree(ctx, _need(payload, "doctype"))

        return await self._run("doc.tree", op)

    @action("doc.views.list")
    async def doc_views_list(self, payload: dict) -> dict:
        async def op(ctx: RequestContext) -> dict:
            return await self.documents.list_saved_views(ctx, _need(payload, "doctype"))

        return await self._run("doc.views.list", op)

    @action("doc.views.save")
    async def doc_views_save(self, payload: dict) -> dict:
        async def op(ctx: RequestContext) -> dict:
            return await self.documents.save_saved_view(
                ctx, _need(payload, "doctype"), _need(payload, "name"),
                label=payload.get("label"), kind=payload.get("kind", "list"), config=payload.get("config"),
                is_default=payload.get("is_default", False), previous=payload.get("previous"),
            )

        return await self._run("doc.views.save", op)

    @action("doc.views.delete")
    async def doc_views_delete(self, payload: dict) -> dict:
        async def op(ctx: RequestContext) -> dict:
            return await self.documents.delete_saved_view(ctx, _need(payload, "doctype"), _need(payload, "name"))

        return await self._run("doc.views.delete", op)

    @action("doc.override.form.get")
    async def doc_override_form_get(self, payload: dict) -> dict:
        async def op(ctx: RequestContext) -> dict:
            return await self.documents.form_override(ctx, _need(payload, "doctype"))

        return await self._run("doc.override.form.get", op)

    @action("doc.override.form.save")
    async def doc_override_form_save(self, payload: dict) -> dict:
        async def op(ctx: RequestContext) -> dict:
            return await self.documents.save_form_override(ctx, _need(payload, "doctype"), payload.get("config"))

        return await self._run("doc.override.form.save", op)

    @action("doc.override.form.delete")
    async def doc_override_form_delete(self, payload: dict) -> dict:
        async def op(ctx: RequestContext) -> dict:
            return await self.documents.delete_form_override(ctx, _need(payload, "doctype"))

        return await self._run("doc.override.form.delete", op)

    @action("doc.override.dashboard.save")
    async def doc_override_dashboard_save(self, payload: dict) -> dict:
        async def op(ctx: RequestContext) -> dict:
            return await self.documents.save_dashboard_override(ctx, _need(payload, "doctype"), payload.get("config"), payload.get("scope", "org"))

        return await self._run("doc.override.dashboard.save", op)

    @action("doc.override.dashboard.delete")
    async def doc_override_dashboard_delete(self, payload: dict) -> dict:
        async def op(ctx: RequestContext) -> dict:
            return await self.documents.delete_dashboard_override(ctx, _need(payload, "doctype"), payload.get("scope", "org"))

        return await self._run("doc.override.dashboard.delete", op)

    @action("doc.override.workspace.save")
    async def doc_override_workspace_save(self, payload: dict) -> dict:
        async def op(ctx: RequestContext) -> dict:
            return await self.documents.save_workspace_override(ctx, _need(payload, "name"), payload.get("config"), payload.get("scope", "org"))

        return await self._run("doc.override.workspace.save", op)

    @action("doc.override.workspace.delete")
    async def doc_override_workspace_delete(self, payload: dict) -> dict:
        async def op(ctx: RequestContext) -> dict:
            return await self.documents.delete_workspace_override(ctx, _need(payload, "name"), payload.get("scope", "org"))

        return await self._run("doc.override.workspace.delete", op)

    @action("doc.collab.get")
    async def doc_collab_get(self, payload: dict) -> dict:
        async def op(ctx: RequestContext) -> dict:
            return await self.collaboration.get(ctx, _need(payload, "doctype"), _need(payload, "name"))

        return await self._run("doc.collab.get", op)

    @action("doc.comment.add")
    async def doc_comment_add(self, payload: dict) -> dict:
        async def op(ctx: RequestContext) -> dict:
            return await self.collaboration.add_comment(ctx, _need(payload, "doctype"), _need(payload, "name"), payload.get("content"))

        return await self._run("doc.comment.add", op)

    @action("doc.users.resolve")
    async def doc_users_resolve(self, payload: dict) -> dict:
        async def op(ctx: RequestContext) -> dict:
            return await self.collaboration.resolve(ctx, payload.get("ids"))

        return await self._run("doc.users.resolve", op)

    @action("doc.mentionable")
    async def doc_mentionable(self, payload: dict) -> dict:
        async def op(ctx: RequestContext) -> dict:
            return await self.collaboration.mentionable(ctx, _need(payload, "doctype"), _need(payload, "name"), q=payload.get("q", ""))

        return await self._run("doc.mentionable", op)

    @action("doc.assign")
    async def doc_assign(self, payload: dict) -> dict:
        async def op(ctx: RequestContext) -> dict:
            return await self.collaboration.assign(
                ctx, _need(payload, "doctype"), _need(payload, "name"),
                assignee=payload.get("assignee"), description=payload.get("description"), due_date=payload.get("due_date"),
            )

        return await self._run("doc.assign", op)

    @action("doc.assign.many")
    async def doc_assign_many(self, payload: dict) -> dict:
        async def op(ctx: RequestContext) -> dict:
            return await self.collaboration.assign_many(
                ctx, _need(payload, "doctype"), payload.get("names"),
                assignee=payload.get("assignee"), description=payload.get("description"), due_date=payload.get("due_date"),
            )

        return await self._run("doc.assign.many", op)

    @action("doc.assignment.update")
    async def doc_assignment_update(self, payload: dict) -> dict:
        async def op(ctx: RequestContext) -> dict:
            fields = {k: v for k, v in payload.items() if k != "id"}
            return await self.collaboration.update_assignment(ctx, _need(payload, "id"), **fields)

        return await self._run("doc.assignment.update", op)

    @action("doc.assignments.mine")
    async def doc_assignments_mine(self, payload: dict) -> dict:
        async def op(ctx: RequestContext) -> dict:
            return await self.collaboration.mine(ctx, status=payload.get("status", "open"), limit=payload.get("limit", 100))

        return await self._run("doc.assignments.mine", op)

    @action("doc.save")
    async def doc_save(self, payload: dict) -> dict:
        """Sans `name` : création (`new_name` : le nom choisi d'un DocType `naming: prompt`). Avec `name` : mise à jour, `version` obligatoire (D15)."""

        async def op(ctx: RequestContext) -> dict:
            doctype, data = _need(payload, "doctype"), _need(payload, "data")
            name = payload.get("name")
            if name is None:
                return {"doc": await self.documents.create(ctx, doctype, data, name=payload.get("new_name"))}
            return {"doc": await self.documents.update(ctx, doctype, name, data, payload.get("version"))}

        return await self._run("doc.save", op)

    @action("doc.submit")
    async def doc_submit(self, payload: dict) -> dict:
        async def op(ctx: RequestContext) -> dict:
            return {"doc": await self.documents.submit(ctx, _need(payload, "doctype"), _need(payload, "name"), payload.get("version"))}

        return await self._run("doc.submit", op)

    @action("doc.cancel")
    async def doc_cancel(self, payload: dict) -> dict:
        async def op(ctx: RequestContext) -> dict:
            return {"doc": await self.documents.cancel(ctx, _need(payload, "doctype"), _need(payload, "name"), payload.get("version"))}

        return await self._run("doc.cancel", op)

    @action("doc.rename")
    async def doc_rename(self, payload: dict) -> dict:
        async def op(ctx: RequestContext) -> dict:
            return {"doc": await self.documents.rename(ctx, _need(payload, "doctype"), _need(payload, "name"), payload.get("new_name"))}

        return await self._run("doc.rename", op)

    @action("doc.connections")
    async def doc_connections(self, payload: dict) -> dict:
        async def op(ctx: RequestContext) -> dict:
            return await self.documents.connections(ctx, _need(payload, "doctype"), _need(payload, "name"))

        return await self._run("doc.connections", op)

    @action("doc.bulk")
    async def doc_bulk(self, payload: dict) -> dict:
        """Une action sur plusieurs documents : `action` (`update`|`delete`|`submit`|`cancel`), `names`, `data` (pour `update`)."""

        async def op(ctx: RequestContext) -> dict:
            return await self.documents.bulk(ctx, _need(payload, "doctype"), payload.get("action"), payload.get("names"), data=payload.get("data"))

        return await self._run("doc.bulk", op)

    @action("doc.amend")
    async def doc_amend(self, payload: dict) -> dict:
        async def op(ctx: RequestContext) -> dict:
            return {"doc": await self.documents.amend(ctx, _need(payload, "doctype"), _need(payload, "name"))}

        return await self._run("doc.amend", op)

    @action("doc.delete")
    async def doc_delete(self, payload: dict) -> dict:
        async def op(ctx: RequestContext) -> dict:
            await self.documents.delete(ctx, _need(payload, "doctype"), _need(payload, "name"))
            return {}

        return await self._run("doc.delete", op)

    # ── Interne ───────────────────────────────────────────────
    async def _run(self, action_name: str, op: Callable[[RequestContext], Awaitable[dict]]) -> dict:
        try:
            ctx = RequestContext.from_ipc(self._settings)
            return ok(**await op(ctx))
        except DoctypeError as e:
            return error(e.message, e.code)
        except Exception as e:
            # Une erreur inattendue (base, bug) ne doit pas atteindre l'appelant : xcore renverrait
            # `str(e)`, soit ici le SQL et ses paramètres liés. On journalise côté serveur seulement.
            self.logger.error("action failed", action=action_name, error=repr(e))
            return error("Erreur interne", "internal_error")


def _timezone(name: Any) -> ZoneInfo:
    """Fuseau des parties de date d'une série de numérotation (`doctype.timezone`, nom IANA)."""
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, TypeError, OSError):
        raise ConfigurationError(f"doctype.timezone: fuseau inconnu {name!r} (attendu : nom IANA, ex. Europe/Paris)") from None


async def _done(**kw: Any) -> dict:
    return kw
