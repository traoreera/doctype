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
from .errors import DoctypeError, ValidationFailed
from .overrides import user_target
from .file_service import FileService

SETTINGS_DOCTYPE = "System Settings"
# Champ -> ce que le Desk en fait. Une liste fermée : on ne renvoie jamais « tout le document ».
TEXT_FIELDS = ("organization_name", "brand_color", "date_format", "language")
# Les langues que le Desk sait parler (ses dictionnaires, ui/src/i18n) : la préférence personnelle n'accepte que celles-là.
DESK_LANGUAGES = ("fr", "en")
# La langue PERSONNELLE : un réglage par utilisateur, rangé avec ses habillages personnels (même table, clé `language@<empreinte>`).
KIND_LANGUAGE_USER = "language_user"
LOGO_FIELD = "logo"


class Preferences:
    def __init__(self, documents: DocumentService, files: FileService) -> None:
        self._docs, self._files = documents, files

    async def get(self, ctx: RequestContext) -> dict[str, Any]:
        """Les préférences du tenant de l'appelant (vide si le plugin `system` n'est pas chargé). Les valeurs absentes sont absentes."""
        ctx.require_identity()
        mine = await self.my_language(ctx)
        if SETTINGS_DOCTYPE not in self._docs._registry.names():
            return {"my_language": mine} if mine else {}
        sys = RequestContext.system(ctx.tenant_id, "organisation display preferences")
        try:
            record = await self._docs.get(sys, SETTINGS_DOCTYPE, SETTINGS_DOCTYPE)  # jamais enregistré : ses valeurs par défaut (version 0)
        except DoctypeError:
            return {"my_language": mine} if mine else {}
        data = record.get("data", {})
        out: dict[str, Any] = {k: data[k] for k in TEXT_FIELDS if isinstance(data.get(k), str) and data[k]}
        if mine:
            out["my_language"] = mine  # la sienne passe avant celle de l'organisation (le Desk tranche, il connaît aussi le navigateur)
        logo = data.get(LOGO_FIELD)
        if isinstance(logo, str):
            link = await self._files.public_link(ctx.tenant_id, logo)
            if link is not None:
                out["logo"] = link
        return out

    async def my_language(self, ctx: RequestContext) -> str | None:
        """La langue que l'utilisateur a choisie (« Mon profil »), ou None : celle de l'organisation s'applique."""
        if ctx.is_system or ctx.owner_id is None:
            return None
        async with self._docs._query(ctx) as q:
            found = await q.get_override(KIND_LANGUAGE_USER, user_target("language", ctx.owner_id))
        lang = ((found or {}).get("config") or {}).get("language")
        return lang if lang in DESK_LANGUAGES else None

    async def set_my_language(self, ctx: RequestContext, language: Any) -> dict[str, Any]:
        """Choisir sa langue (`fr`, `en`), ou `None` pour revenir à celle de l'organisation. Chacun la sienne : aucun droit n'est exigé."""
        ctx.require_identity()
        if ctx.is_system:
            raise ValidationFailed("une langue se choisit pour un utilisateur", code="invalid_language")
        if language is not None and language not in DESK_LANGUAGES:
            raise ValidationFailed(f"langue inconnue : {language!r} (permises : {', '.join(DESK_LANGUAGES)})", code="invalid_language")
        target = user_target("language", ctx.owner_id)
        async with self._docs._query(ctx) as q:
            if language is None:
                await q.delete_override(KIND_LANGUAGE_USER, target)
            else:
                await q.put_override(KIND_LANGUAGE_USER, target, owner=ctx.owner_id, config={"language": language})
        return {"my_language": language}
