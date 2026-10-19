"""Les préférences d'AFFICHAGE de l'organisation : ce que le Desk applique à tous ses utilisateurs (nom, logo, couleur, format de date).

Elles vivent dans le document unique `System Settings` (plugin `system`), que seul un administrateur peut lire et modifier. Mais le nom, le logo
et la couleur s'affichent pour TOUT le monde : ce service lit ce document en contexte système, pour le tenant de l'appelant, et n'en renvoie que
des champs d'une liste FERMÉE ci-dessous. Un réglage sensible ajouté plus tard à `System Settings` ne sort donc jamais par ici sans qu'on l'ajoute
exprès. Une identité est exigée (comme toute l'API) : rien n'est public.
"""

from __future__ import annotations

from typing import Any

from .context import RequestContext
from .document import DocumentService
from .errors import DoctypeError
from .file_service import FileService

SETTINGS_DOCTYPE = "System Settings"
# Champ -> ce que le Desk en fait. Une liste fermée : on ne renvoie jamais « tout le document ».
TEXT_FIELDS = ("organization_name", "brand_color", "date_format")
LOGO_FIELD = "logo"


class Preferences:
    def __init__(self, documents: DocumentService, files: FileService) -> None:
        self._docs, self._files = documents, files

    async def get(self, ctx: RequestContext) -> dict[str, Any]:
        """Les préférences du tenant de l'appelant (vide si le plugin `system` n'est pas chargé). Les valeurs absentes sont absentes."""
        ctx.require_identity()
        if SETTINGS_DOCTYPE not in self._docs._registry.names():
            return {}
        sys = RequestContext.system(ctx.tenant_id, "organisation display preferences")
        try:
            record = await self._docs.get(sys, SETTINGS_DOCTYPE, SETTINGS_DOCTYPE)  # jamais enregistré : ses valeurs par défaut (version 0)
        except DoctypeError:
            return {}
        data = record.get("data", {})
        out: dict[str, Any] = {k: data[k] for k in TEXT_FIELDS if isinstance(data.get(k), str) and data[k]}
        logo = data.get(LOGO_FIELD)
        if isinstance(logo, str):
            link = await self._files.public_link(ctx.tenant_id, logo)
            if link is not None:
                out["logo"] = link
        return out
