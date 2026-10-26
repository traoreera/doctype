"""Routes de la collaboration : commentaires, assignations, pièces jointes et étiquettes d'un document (voir `collaboration.py`).

Toutes exigent une identité ; le droit de lire le DOCUMENT est jugé par le service à chaque appel (un document hors périmètre est « introuvable »).
"""

from __future__ import annotations

import json
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


class TagBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    add: list[Any] | None = None
    remove: list[Any] | None = None


class TagManyBody(TagBody):
    names: list[str]


class NamesBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    names: list[str]


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

    @router.get("/tags/{doctype}/{name}")
    async def tags(doctype: str, name: str, ctx: RequestContext = Depends(ctx_dep)):
        """Les étiquettes du document (il faut le lire) et `can_tag` (peut-il en poser : l'écrire)."""
        return await _guard(collab.tags(ctx, doctype, name))

    @router.post("/tags/{doctype}/{name}")
    async def tag(doctype: str, name: str, body: TagBody, ctx: RequestContext = Depends(ctx_dep)):
        """`{add: [...], remove: [...]}` en une transaction ; répond les étiquettes du document après."""
        return await _guard(collab.tag(ctx, doctype, name, add=body.add, remove=body.remove))

    @router.post("/tags-of/{doctype}")
    async def tags_of(doctype: str, body: NamesBody, ctx: RequestContext = Depends(ctx_dep)):
        """Les étiquettes d'une page de liste : `{items: {nom: [étiquettes]}}`, les documents lisibles seulement."""
        return await _guard(collab.tags_of(ctx, doctype, body.names))

    @router.get("/tag-counts/{doctype}")
    async def tag_counts(
        doctype: str,
        q: str = Query(""),
        filters: str | None = Query(None, description="les filtres de /list"),
        limit: int = Query(20),
        ctx: RequestContext = Depends(ctx_dep),
    ):
        """Les étiquettes en usage, avec leur nombre de documents lisibles que `filters` retient (barre latérale, propositions de saisie)."""
        try:
            parsed = json.loads(filters) if filters else None
        except ValueError:
            raise HTTPException(status_code=422, detail={"code": "validation_failed", "msg": "filters n'est pas du JSON valide"}) from None
        return await _guard(collab.tag_counts(ctx, doctype, q=q, filters=parsed, limit=limit))

    @router.post("/tag-many/{doctype}")
    async def tag_many(doctype: str, body: TagManyBody, ctx: RequestContext = Depends(ctx_dep)):
        """Étiqueter une sélection : chaque document est jugé seul, la réponse donne `ok`/code/message par document."""
        return await _guard(collab.tag_many(ctx, doctype, body.names, add=body.add, remove=body.remove))

    @router.post("/attachments/{doctype}/{name}", status_code=201)
    async def attach(doctype: str, name: str, file: UploadFile = File(...), ctx: RequestContext = Depends(ctx_dep)):
        content = await file.read(_READ_LIMIT)
        return await _guard(collab.attach(ctx, doctype, name, content=content, filename=file.filename, content_type=file.content_type))

    @router.delete("/attachments/{attachment_id}", status_code=204)
    async def detach(attachment_id: str, ctx: RequestContext = Depends(ctx_dep)):
        await _guard(collab.detach(ctx, attachment_id))
        return Response(status_code=204)

    return router
