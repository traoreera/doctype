"""Routes d'impression (process/impression.md §7). Trois routes, toutes en lecture : imprimer, c'est lire.

Un routeur à part, comme les fichiers, parce que l'une d'elles ne rend pas du JSON mais des octets — et qu'une
réponse binaire mérite ses propres en-têtes (`nosniff`, aucune mise en cache publique).

Les règles tenues ici sont celles du service (`print_service.py`) : le droit de LIRE le document et rien
d'autre, aucune écriture, et le PDF seul dépend du paquet optionnel — l'aperçu HTML marche toujours.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from ..context import ContextSettings, RequestContext, http_context
from ..errors import DoctypeError
from ..files import content_disposition
from ..print_service import PrintService

# Le nom d'un gabarit et celui d'un papier à en-tête : bornés ici pour que rien de démesuré n'atteigne le service.
_NAME = {"max_length": 140}


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

    return router


def _raise(e: DoctypeError) -> None:
    raise HTTPException(status_code=e.status, detail=e.as_detail()) from e
