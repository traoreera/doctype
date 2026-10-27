"""Routes HTTP génériques (doc/doctype.md §10).

Ces routes contournent le pipeline xcore (rate limit, audit IPC, `ContextVar`). Toutes dépendent
de `http_context` : une route ajoutée sans paramètre `ctx` reste couverte par la dépendance de routeur.
"""

from __future__ import annotations

import json
from typing import Any, Awaitable, Callable, NoReturn, TypeVar

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict

from ..context import ContextSettings, RequestContext, http_context
from ..csv_import import import_csv
from .. import devtools
from ..document import DEFAULT_PAGE, DocumentService
from ..errors import DoctypeError

T = TypeVar("T")


class CreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data: dict[str, Any]
    name: Any = None  # le nom choisi, pour un DocType `naming: prompt` (refusé pour tout autre nommage)


class DefinitionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    definition: Any


class ImportBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: Any
    mode: Any = "insert"


class BulkBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Any
    names: Any
    data: Any = None


class RenameBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    new_name: Any


class UpdateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int
    data: dict[str, Any]


class TransitionBody(BaseModel):
    """Soumettre ou annuler : la version lue par le client, comme pour une modification (D15)."""

    model_config = ConfigDict(extra="forbid")
    version: int


class WorkflowBody(TransitionBody):
    """Une transition de workflow : son nom, et la version lue — le serveur refuse si le document a bougé depuis."""

    action: str


class SaveViewBody(BaseModel):
    """Vue sauvegardée (phase C) : configuration d'une liste, privée à son propriétaire."""

    model_config = ConfigDict(extra="forbid")
    label: str
    kind: str = "list"
    config: dict[str, Any] | None = None
    is_default: bool = False
    previous: str | None = None  # renommage : l'ancien nom est supprimé


class OverrideBody(BaseModel):
    """Habillage (override) : disposition d'un formulaire ou corps d'un espace de travail, validé à l'écriture."""

    model_config = ConfigDict(extra="forbid")
    config: dict[str, Any]


def _raise(e: DoctypeError) -> NoReturn:
    raise HTTPException(status_code=e.status, detail=e.as_detail()) from e


async def _guard(call: Awaitable[T]) -> T:
    try:
        return await call
    except DoctypeError as e:
        _raise(e)


def _guard_sync(fn: Callable[[], T]) -> T:
    try:
        return fn()
    except DoctypeError as e:
        _raise(e)


def build_router(documents: DocumentService, settings: ContextSettings, *, developer: bool = False) -> APIRouter:
    ctx_dep = http_context(settings)
    router = APIRouter(dependencies=[Depends(ctx_dep)])

    @router.get("/meta")
    async def meta_list(ctx: RequestContext = Depends(ctx_dep)):
        return _guard_sync(lambda: documents.list_meta(ctx))

    @router.get("/workspaces")
    async def workspaces(ctx: RequestContext = Depends(ctx_dep)):
        return await _guard(documents.fetch_workspaces(ctx))

    @router.get("/meta/{doctype}")
    async def meta_get(doctype: str, ctx: RequestContext = Depends(ctx_dep)):
        return await _guard(documents.fetch_meta(ctx, doctype))

    @router.get("/list/{doctype}")
    async def doc_list(
        doctype: str,
        filters: str | None = Query(None, description="objet JSON d'égalités, ex. {\"type\": \"Entreprise\"}"),
        order_by: str = "modified",
        order: str = Query("desc", pattern="^(asc|desc)$"),
        limit: int = DEFAULT_PAGE,
        offset: int = 0,
        ctx: RequestContext = Depends(ctx_dep),
    ):
        try:
            parsed = json.loads(filters) if filters else None
        except ValueError:
            raise HTTPException(
                status_code=422, detail={"code": "validation_failed", "msg": "filters n'est pas du JSON valide"}
            ) from None
        return await _guard(
            documents.list(
                ctx, doctype, filters=parsed, order_by=order_by, descending=order == "desc", limit=limit, offset=offset
            )
        )

    @router.get("/aggregate/{doctype}")
    async def doc_aggregate(
        doctype: str,
        group_by: str | None = Query(None, description="champ Select, Link, Boolean, Date, ou docstatus / owner / creation / modified"),
        bucket: str | None = Query(None, description="day | month | year, pour un regroupement par date"),
        metrics: str = Query("count", description="mesures séparées par des virgules : count, sum:champ, avg:champ, min:champ, max:champ"),
        filters: str | None = Query(None, description="les filtres de /list"),
        periods: int | None = Query(None, description="les N dernières périodes, l'actuelle comprise (regroupement par date)"),
        limit: int = 50,
        ctx: RequestContext = Depends(ctx_dep),
    ):
        try:
            parsed = json.loads(filters) if filters else None
        except ValueError:
            raise HTTPException(
                status_code=422, detail={"code": "validation_failed", "msg": "filters n'est pas du JSON valide"}
            ) from None
        return await _guard(
            documents.aggregate(
                ctx, doctype, group_by=group_by, bucket=bucket, metrics=[m for m in metrics.split(",") if m],
                filters=parsed, periods=periods, limit=limit,
            )
        )

    @router.get("/search")
    async def global_search(q: str = "", limit: int = 10, ctx: RequestContext = Depends(ctx_dep)):
        return await _guard(documents.global_search(ctx, q, limit=limit))

    @router.get("/search/{doctype}")
    async def doc_search(
        doctype: str,
        q: str = "",
        limit: int = 10,
        docstatus: str | None = Query(None, description="états admis, séparés par des virgules"),
        filters: str | None = Query(None, description="filtres JSON appliqués à la recherche"),
        ctx: RequestContext = Depends(ctx_dep),
    ):
        states = None if docstatus is None else [int(s) if s.strip().isdigit() else s for s in docstatus.split(",")]
        try:
            parsed = json.loads(filters) if filters else None
        except ValueError:
            raise HTTPException(status_code=422, detail={"code": "validation_failed", "msg": "filters n'est pas du JSON valide"}) from None
        return await _guard(documents.search(ctx, doctype, q, limit=limit, docstatus=states, filters=parsed))

    @router.get("/history/{doctype}/{name}")
    async def doc_history(
        doctype: str, name: str, limit: int = DEFAULT_PAGE, offset: int = 0, ctx: RequestContext = Depends(ctx_dep)
    ):
        return await _guard(documents.history(ctx, doctype, name, limit=limit, offset=offset))

    @router.get("/connections/{doctype}/{name}")
    async def connections(doctype: str, name: str, ctx: RequestContext = Depends(ctx_dep)):
        return await _guard(documents.connections(ctx, doctype, name))

    @router.get("/tree/{doctype}")
    async def doc_tree(doctype: str, ctx: RequestContext = Depends(ctx_dep)):
        return await _guard(documents.tree(ctx, doctype))

    @router.get("/views/{doctype}")
    async def saved_view_list(doctype: str, ctx: RequestContext = Depends(ctx_dep)):
        return await _guard(documents.list_saved_views(ctx, doctype))

    @router.post("/views/{doctype}/{name}")
    async def saved_view_save(doctype: str, name: str, body: SaveViewBody, ctx: RequestContext = Depends(ctx_dep)):
        return await _guard(
            documents.save_saved_view(
                ctx, doctype, name, label=body.label, kind=body.kind, config=body.config,
                is_default=body.is_default, previous=body.previous,
            )
        )

    @router.delete("/views/{doctype}/{name}", status_code=204)
    async def saved_view_delete(doctype: str, name: str, ctx: RequestContext = Depends(ctx_dep)):
        await _guard(documents.delete_saved_view(ctx, doctype, name))
        return Response(status_code=204)

    @router.get("/override/form/{doctype}")
    async def form_override_get(doctype: str, ctx: RequestContext = Depends(ctx_dep)):
        return await _guard(documents.form_override(ctx, doctype))

    @router.put("/override/form/{doctype}")
    async def form_override_put(doctype: str, body: OverrideBody, ctx: RequestContext = Depends(ctx_dep)):
        return await _guard(documents.save_form_override(ctx, doctype, body.config))

    @router.delete("/override/form/{doctype}", status_code=204)
    async def form_override_delete(doctype: str, ctx: RequestContext = Depends(ctx_dep)):
        await _guard(documents.delete_form_override(ctx, doctype))
        return Response(status_code=204)

    @router.get("/override/workflow/{doctype}")
    async def workflow_override_get(doctype: str, ctx: RequestContext = Depends(ctx_dep)):
        return await _guard(documents.workflow_override(ctx, doctype))

    @router.put("/override/workflow/{doctype}")
    async def workflow_override_put(doctype: str, body: OverrideBody, ctx: RequestContext = Depends(ctx_dep)):
        return await _guard(documents.save_workflow_override(ctx, doctype, body.config))

    @router.delete("/override/workflow/{doctype}", status_code=204)
    async def workflow_override_delete(doctype: str, ctx: RequestContext = Depends(ctx_dep)):
        await _guard(documents.delete_workflow_override(ctx, doctype))

    @router.put("/override/dashboard/{doctype}")
    async def dashboard_override_put(doctype: str, body: OverrideBody, scope: str = Query("org"), ctx: RequestContext = Depends(ctx_dep)):
        return await _guard(documents.save_dashboard_override(ctx, doctype, body.config, scope))

    @router.delete("/override/dashboard/{doctype}", status_code=204)
    async def dashboard_override_delete(doctype: str, scope: str = Query("org"), ctx: RequestContext = Depends(ctx_dep)):
        await _guard(documents.delete_dashboard_override(ctx, doctype, scope))
        return Response(status_code=204)

    @router.put("/override/workspace/{name}")
    async def workspace_override_put(name: str, body: OverrideBody, scope: str = Query("org"), ctx: RequestContext = Depends(ctx_dep)):
        return await _guard(documents.save_workspace_override(ctx, name, body.config, scope))

    @router.delete("/override/workspace/{name}", status_code=204)
    async def workspace_override_delete(name: str, scope: str = Query("org"), ctx: RequestContext = Depends(ctx_dep)):
        await _guard(documents.delete_workspace_override(ctx, name, scope))
        return Response(status_code=204)

    @router.get("/doc/{doctype}/{name}")
    async def doc_get(doctype: str, name: str, ctx: RequestContext = Depends(ctx_dep)):
        return await _guard(documents.get(ctx, doctype, name))

    @router.post("/doc/{doctype}", status_code=201)
    async def doc_create(doctype: str, body: CreateBody, ctx: RequestContext = Depends(ctx_dep)):
        return await _guard(documents.create(ctx, doctype, body.data, name=body.name))

    @router.put("/doc/{doctype}/{name}")
    async def doc_update(doctype: str, name: str, body: UpdateBody, ctx: RequestContext = Depends(ctx_dep)):
        return await _guard(documents.update(ctx, doctype, name, body.data, body.version))

    @router.post("/doc/{doctype}/{name}/submit")
    async def doc_submit(doctype: str, name: str, body: TransitionBody, ctx: RequestContext = Depends(ctx_dep)):
        return await _guard(documents.submit(ctx, doctype, name, body.version))

    @router.get("/transitions/{doctype}/{name}")
    async def doc_transitions(doctype: str, name: str, ctx: RequestContext = Depends(ctx_dep)):
        """Ce que l'appelant peut faire maintenant sur ce document : l'état courant et les transitions ouvertes."""
        return await _guard(documents.available_transitions(ctx, doctype, name))

    @router.post("/doc/{doctype}/{name}/transition")
    async def doc_workflow_transition(doctype: str, name: str, body: WorkflowBody, ctx: RequestContext = Depends(ctx_dep)):
        """Fait passer le document à l'état suivant du workflow. C'est ce qui remplace `/submit` sur un type qui en a un."""
        return await _guard(documents.workflow_transition(ctx, doctype, name, body.action, body.version))

    @router.post("/doc/{doctype}/{name}/cancel")
    async def doc_cancel(doctype: str, name: str, body: TransitionBody, ctx: RequestContext = Depends(ctx_dep)):
        return await _guard(documents.cancel(ctx, doctype, name, body.version))

    @router.post("/bulk/{doctype}")
    async def doc_bulk(doctype: str, body: BulkBody, ctx: RequestContext = Depends(ctx_dep)):
        return await _guard(documents.bulk(ctx, doctype, body.action, body.names, data=body.data))

    # ── Mode développeur (devtools.py) : fermé par défaut (`doctype.desk.developer_mode`), il ne fait que lire et juger, jamais enregistrer ──
    @router.get("/devtools/doctype/{doctype}")
    async def devtools_definition(doctype: str, ctx: RequestContext = Depends(ctx_dep)):
        return _guard_sync(lambda: devtools.definition(documents, ctx, doctype, enabled=developer))

    @router.post("/devtools/check")
    async def devtools_check(body: DefinitionBody, ctx: RequestContext = Depends(ctx_dep)):
        return _guard_sync(lambda: devtools.check(documents, ctx, body.definition, enabled=developer))

    @router.post("/import/{doctype}")
    async def doc_import(doctype: str, body: ImportBody, ctx: RequestContext = Depends(ctx_dep)):
        return await _guard(import_csv(documents, ctx, doctype, body.content, body.mode))

    @router.post("/doc/{doctype}/{name}/rename")
    async def doc_rename(doctype: str, name: str, body: RenameBody, ctx: RequestContext = Depends(ctx_dep)):
        return await _guard(documents.rename(ctx, doctype, name, body.new_name))

    @router.post("/doc/{doctype}/{name}/amend", status_code=201)
    async def doc_amend(doctype: str, name: str, ctx: RequestContext = Depends(ctx_dep)):
        return await _guard(documents.amend(ctx, doctype, name))

    @router.delete("/doc/{doctype}/{name}", status_code=204)
    async def doc_delete(doctype: str, name: str, ctx: RequestContext = Depends(ctx_dep)):
        await _guard(documents.delete(ctx, doctype, name))
        return Response(status_code=204)

    return router
