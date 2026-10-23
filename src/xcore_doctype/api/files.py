"""Routes des fichiers : le téléversement et les liens exigent une identité (comme l'API) ; le CONTENU se sert par une URL signée, sans jeton
(une balise `<img>` n'en envoie pas). Voir `files.py` pour les règles de sécurité.

Deux routeurs frères, montés séparément (comme le Desk) : une route publique ne peut pas hériter par accident de la dépendance d'identité,
et une route protégée ne peut pas l'oublier.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict

from ..context import ContextSettings, RequestContext, http_context
from ..document import DEFAULT_PAGE
from ..errors import DoctypeError
from ..file_library import FileLibrary
from ..file_service import FileService
from ..files import MAX_MB, content_disposition
from ..preferences import Preferences

# Un téléversement est lu en mémoire : on borne la lecture à la plus grande taille permise (+1 octet pour détecter l'excès), AVANT de le garder.
_READ_LIMIT = MAX_MB * 1024 * 1024 + 1


class LinksBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ids: list[str]


class DetachBody(BaseModel):
    """Détacher un fichier (écran « Fichiers ») : `attachment` seul pour une pièce jointe, sinon le champ du document qui le cite et la version lue."""

    model_config = ConfigDict(extra="forbid")
    attachment: str | None = None
    doctype: str | None = None
    name: str | None = None
    field: str | None = None
    file_id: str | None = None
    version: int | None = None


def _raise(e: DoctypeError) -> None:
    raise HTTPException(status_code=e.status, detail=e.as_detail()) from e


def build_files_router(files: FileService, settings: ContextSettings) -> APIRouter:
    ctx_dep = http_context(settings)
    router = APIRouter(dependencies=[Depends(ctx_dep)])

    @router.post("/upload/{doctype}/{fieldname}", status_code=201)
    async def upload(doctype: str, fieldname: str, file: UploadFile = File(...), ctx: RequestContext = Depends(ctx_dep)):
        content = await file.read(_READ_LIMIT)
        try:
            return await files.upload(ctx, doctype, fieldname, content=content, filename=file.filename, content_type=file.content_type)
        except DoctypeError as e:
            _raise(e)

    @router.post("/file-links")
    async def links(body: LinksBody, ctx: RequestContext = Depends(ctx_dep)):
        try:
            return await files.links(ctx, body.ids)
        except DoctypeError as e:
            _raise(e)

    return router


def build_file_library_router(library: FileLibrary, settings: ContextSettings) -> APIRouter:
    """L'écran « Fichiers » (voir `file_library.py`) : lister ce que l'appelant peut lire, détacher par l'écriture ordinaire. Une identité est exigée."""
    ctx_dep = http_context(settings)
    router = APIRouter(dependencies=[Depends(ctx_dep)])

    @router.get("/file-library")
    async def listing(
        doctype: str | None = Query(None, description="les fichiers que ce DocType cite"),
        q: str = Query("", description="un morceau du nom de fichier (sans distinction de casse)"),
        order_by: str = "creation",
        order: str = Query("desc", pattern="^(asc|desc)$"),
        limit: int = DEFAULT_PAGE,
        offset: int = 0,
        ctx: RequestContext = Depends(ctx_dep),
    ):
        try:
            return await library.list(ctx, doctype=doctype or None, q=q, order_by=order_by, descending=order == "desc", limit=limit, offset=offset)
        except DoctypeError as e:
            _raise(e)

    @router.post("/file-library/detach")
    async def detach(body: DetachBody, ctx: RequestContext = Depends(ctx_dep)):
        try:
            return await library.detach(ctx, **body.model_dump())
        except DoctypeError as e:
            _raise(e)

    return router


def build_preferences_router(prefs: "Preferences", settings: ContextSettings) -> APIRouter:
    """`GET /preferences` : nom, logo, couleur et format de date de l'organisation, pour tout utilisateur identifié (voir `preferences.py`)."""
    ctx_dep = http_context(settings)
    router = APIRouter(dependencies=[Depends(ctx_dep)])

    @router.get("/preferences")
    async def preferences(ctx: RequestContext = Depends(ctx_dep)):
        try:
            return await prefs.get(ctx)
        except DoctypeError as e:
            _raise(e)

    return router


def build_public_files_router(files: FileService) -> APIRouter:
    router = APIRouter()

    @router.get("/files/{tenant}/{file_id}")
    async def serve(tenant: str, file_id: str, exp: int = Query(...), sig: str = Query(..., min_length=64, max_length=64)):
        try:
            content, mime, name, inline = await files.serve(tenant, file_id, exp, sig)
        except DoctypeError as e:
            _raise(e)
        return Response(
            content,
            media_type=mime,
            headers={
                "Content-Disposition": content_disposition(name, inline),
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "default-src 'none'; sandbox",
                "Cache-Control": "private, max-age=60",
                "Cross-Origin-Resource-Policy": "cross-origin",
            },
        )

    return router
