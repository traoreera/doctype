"""Routes de la collaboration : commentaires, assignations, pièces jointes d'un document (voir `collaboration.py`).

Toutes exigent une identité ; le droit de lire le DOCUMENT est jugé par le service à chaque appel (un document hors périmètre est « introuvable »).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel, ConfigDict

from ..collaboration import CollaborationService
from ..context import ContextSettings, RequestContext, http_context
from ..errors import DoctypeError
from ..files import MAX_MB

_READ_LIMIT = MAX_MB * 1024 * 1024 + 1  # lu en mémoire : borné AVANT de le garder (comme les champs fichier)


class CommentBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str


class AssignBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    assignee: str
    description: str | None = None
    due_date: str | None = None


class AssignManyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    names: list[str]
    assignee: str
    description: str | None = None
    due_date: str | None = None


class AssignmentPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: str | None = None
    description: str | None = None
    due_date: str | None = None


def _raise(e: DoctypeError) -> None:
    raise HTTPException(status_code=e.status, detail=e.as_detail()) from e


async def _guard(awaitable: Any) -> Any:
    try:
        return await awaitable
    except DoctypeError as e:
        _raise(e)


def build_collab_router(collab: CollaborationService, settings: ContextSettings) -> APIRouter:
    ctx_dep = http_context(settings)
    router = APIRouter(dependencies=[Depends(ctx_dep)])

    @router.get("/collab/{doctype}/{name}")
    async def around(doctype: str, name: str, ctx: RequestContext = Depends(ctx_dep)):
        return await _guard(collab.get(ctx, doctype, name))

    @router.post("/comments/{doctype}/{name}", status_code=201)
    async def comment_add(doctype: str, name: str, body: CommentBody, ctx: RequestContext = Depends(ctx_dep)):
        return await _guard(collab.add_comment(ctx, doctype, name, body.content))

    @router.put("/comments/{comment_id}")
    async def comment_edit(comment_id: str, body: CommentBody, ctx: RequestContext = Depends(ctx_dep)):
        return await _guard(collab.edit_comment(ctx, comment_id, body.content))

    @router.delete("/comments/{comment_id}", status_code=204)
    async def comment_delete(comment_id: str, ctx: RequestContext = Depends(ctx_dep)):
        await _guard(collab.delete_comment(ctx, comment_id))
        return Response(status_code=204)

    @router.post("/assignments/{doctype}/{name}", status_code=201)
    async def assign(doctype: str, name: str, body: AssignBody, ctx: RequestContext = Depends(ctx_dep)):
        return await _guard(collab.assign(ctx, doctype, name, assignee=body.assignee, description=body.description, due_date=body.due_date))

    @router.post("/assign-many/{doctype}")
    async def assign_many(doctype: str, body: AssignManyBody, ctx: RequestContext = Depends(ctx_dep)):
        return await _guard(collab.assign_many(ctx, doctype, body.names, assignee=body.assignee, description=body.description, due_date=body.due_date))

    @router.put("/assignments/{assignment_id}")
    async def assignment_update(assignment_id: str, body: AssignmentPatch, ctx: RequestContext = Depends(ctx_dep)):
        return await _guard(collab.update_assignment(ctx, assignment_id, **body.model_dump(exclude_unset=True)))

    @router.get("/my-assignments")
    async def mine(status: str | None = Query("open"), limit: int = Query(100), ctx: RequestContext = Depends(ctx_dep)):
        return await _guard(collab.mine(ctx, status=None if status == "all" else status, limit=limit))

    @router.get("/users")
    async def users(ids: list[str] = Query(default=[]), ctx: RequestContext = Depends(ctx_dep)):
        """De quoi afficher des identifiants (`?ids=a&ids=b`) : `{items: {id: {id, email, name}}}`, les membres de l'organisation de l'appelant."""
        return await _guard(collab.resolve(ctx, ids))

    @router.get("/mentionable/{doctype}/{name}")
    async def mentionable(doctype: str, name: str, q: str = Query(""), ctx: RequestContext = Depends(ctx_dep)):
        """Des membres à citer dans un commentaire de ce document (`@adresse`) : il suffit de pouvoir le lire."""
        return await _guard(collab.mentionable(ctx, doctype, name, q=q))

    @router.get("/people/{doctype}")
    async def people(doctype: str, q: str = Query(""), ctx: RequestContext = Depends(ctx_dep)):
        return await _guard(collab.people(ctx, doctype, q=q))

    @router.post("/attachments/{doctype}/{name}", status_code=201)
    async def attach(doctype: str, name: str, file: UploadFile = File(...), ctx: RequestContext = Depends(ctx_dep)):
        content = await file.read(_READ_LIMIT)
        return await _guard(collab.attach(ctx, doctype, name, content=content, filename=file.filename, content_type=file.content_type))

    @router.delete("/attachments/{attachment_id}", status_code=204)
    async def detach(attachment_id: str, ctx: RequestContext = Depends(ctx_dep)):
        await _guard(collab.detach(ctx, attachment_id))
        return Response(status_code=204)

    return router
