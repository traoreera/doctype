"""Routes d'impression (process/impression.md §7). Trois routes, toutes en lecture : imprimer, c'est lire.

Un routeur à part, comme les fichiers, parce que l'une d'elles ne rend pas du JSON mais des octets — et qu'une
réponse binaire mérite ses propres en-têtes (`nosniff`, aucune mise en cache publique).

Les règles tenues ici sont celles du service (`print_service.py`) : le droit de LIRE le document et rien
d'autre, aucune écriture, et le PDF seul dépend du paquet optionnel — l'aperçu HTML marche toujours.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict

from ..context import ContextSettings, RequestContext, http_context
from ..errors import DoctypeError
from ..files import PRINT_IMAGE_MAX_MB, content_disposition
from ..print_service import PrintService

# Le nom d'un gabarit et celui d'un papier à en-tête : bornés ici pour que rien de démesuré n'atteigne le service.
_NAME = {"max_length": 140}


class PrintOverrideBody(BaseModel):
    """Le gabarit que l'éditeur enregistre. `doctype` n'y est PAS : c'est celui de la cible, sinon on ferait
    imprimer un autre type que celui qu'on édite (`overrides.validate_print_override`)."""

    model_config = ConfigDict(extra="forbid")
    template: dict


class PrintPreviewBody(BaseModel):
    """Un gabarit en BROUILLON, plus le papier à en-tête à employer pour l'aperçu (`none` pour aucun)."""

    model_config = ConfigDict(extra="forbid")
    template: dict
    letterhead: str | None = None


def build_print_router(printer: PrintService, settings: ContextSettings) -> APIRouter:
    ctx_dep = http_context(settings)
    router = APIRouter(dependencies=[Depends(ctx_dep)])

    @router.get("/print-templates/{doctype}")
    async def print_templates(doctype: str, ctx: RequestContext = Depends(ctx_dep)):
        """Les gabarits de ce type (livrés, de l'organisation, ou le fabriqué), et si le moteur PDF est là."""
        try:
            return await printer.templates(ctx, doctype)
        except DoctypeError as e:
            _raise(e)

    @router.get("/print/{doctype}/{name}")
    async def print_html(
        doctype: str,
        name: str,
        template: str | None = Query(None, **_NAME),
        letterhead: str | None = Query(None, **_NAME),
        ctx: RequestContext = Depends(ctx_dep),
    ):
        """L'aperçu : le document en HTML. Ne demande pas le moteur PDF."""
        try:
            return await printer.render(ctx, doctype, name, template=template, letterhead=letterhead)
        except DoctypeError as e:
            _raise(e)

    @router.get("/print/{doctype}/{name}/pdf")
    async def print_pdf(
        doctype: str,
        name: str,
        template: str | None = Query(None, **_NAME),
        letterhead: str | None = Query(None, **_NAME),
        ctx: RequestContext = Depends(ctx_dep),
    ):
        """Le PDF, en pièce jointe. `503 pdf_engine_missing` si le paquet optionnel manque (l'aperçu, lui, marche)."""
        try:
            content, filename = await printer.pdf(ctx, doctype, name, template=template, letterhead=letterhead)
        except DoctypeError as e:
            _raise(e)
        return Response(
            content,
            media_type="application/pdf",
            headers={
                "Content-Disposition": content_disposition(filename, inline=False),
                "X-Content-Type-Options": "nosniff",
                # Un document d'une organisation ne se met jamais en cache ailleurs que chez son lecteur.
                "Cache-Control": "private, no-store",
            },
        )

    # ── L'éditeur de gabarits (§10) ───────────────────────────
    @router.get("/override/print/{doctype}")
    async def print_override(doctype: str, template: str | None = Query(None, **_NAME), ctx: RequestContext = Depends(ctx_dep)):
        """Ce que l'éditeur ouvre : le gabarit à éditer, d'où il vient, et si l'appelant peut l'enregistrer."""
        try:
            return await printer.override(ctx, doctype, template)
        except DoctypeError as e:
            _raise(e)

    @router.post("/override/print/{doctype}")
    async def save_print_override(doctype: str, body: PrintOverrideBody, ctx: RequestContext = Depends(ctx_dep)):
        """Enregistre le gabarit de l'organisation. Exige un périmètre d'écriture TOTAL sur le type (§10)."""
        try:
            return await printer.save_override(ctx, doctype, body.template)
        except DoctypeError as e:
            _raise(e)

    @router.delete("/override/print/{doctype}")
    async def delete_print_override(doctype: str, template: str | None = Query(None, **_NAME), ctx: RequestContext = Depends(ctx_dep)):
        """Retire le gabarit de l'organisation : celui du plugin, ou le fabriqué, reprend la main."""
        try:
            return await printer.delete_override(ctx, doctype, template)
        except DoctypeError as e:
            _raise(e)

    @router.post("/print-preview/{doctype}/{name}")
    async def print_preview(doctype: str, name: str, body: PrintPreviewBody, ctx: RequestContext = Depends(ctx_dep)):
        """L'aperçu d'un gabarit en BROUILLON (non enregistré). N'écrit rien, et exige de LIRE le document d'exemple."""
        try:
            return await printer.preview(ctx, doctype, name, body.template, body.letterhead)
        except DoctypeError as e:
            _raise(e)

    @router.post("/print-image/{doctype}", status_code=201)
    async def print_image(doctype: str, file: UploadFile = File(...), ctx: RequestContext = Depends(ctx_dep)):
        """Dépose une image pour un gabarit de ce type (§12). Le verrou de l'enregistrement : périmètre d'écriture total."""
        content = await file.read(PRINT_IMAGE_MAX_MB * 1024 * 1024 + 1)  # un octet de trop suffit à refuser
        try:
            return await printer.upload_image(ctx, doctype, content=content, filename=file.filename, content_type=file.content_type)
        except DoctypeError as e:
            _raise(e)

    @router.get("/print-example/{doctype}")
    async def print_example(doctype: str, ctx: RequestContext = Depends(ctx_dep)):
        """Un document d'exemple pour l'aperçu de l'éditeur, ou `null` : un gabarit s'édite alors sans aperçu."""
        try:
            return await printer.example(ctx, doctype)
        except DoctypeError as e:
            _raise(e)

    return router


def _raise(e: DoctypeError) -> None:
    raise HTTPException(status_code=e.status, detail=e.as_detail()) from e
