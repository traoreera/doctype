"""Le service exporté `doctype_writer` : la porte d'ÉCRITURE des plugins (M4 de `process/plan-erp.md`).

Un plugin de module (comptabilité, immobilisations…) écrit des documents hors de toute transition : un BOUTON serveur
(« Créer le paiement », « Rapprocher ») ou une TÂCHE planifiée (amortissements du mois, factures récurrentes). Deux portes,
et pas une de plus :

- `await writer.as_request(request)` : depuis une route HTTP du plugin, le contexte de l'UTILISATEUR — la même
  vérification que `http_context` (jeton, tenant couvert par le principal, D17) ; ses droits s'appliquent exactement
  comme sur l'API : 403 sans le grant, 404 pour un document hors de son périmètre.
- `writer.system(plugin, tenant, reason)` : pour une tâche, le périmètre système (D20), journalisé, l'historique
  signé `system:<reason>`, et **limité en écriture aux DocTypes du module du plugin** (`module == plugin`) : une tâche
  d'`assets` soumet SA pièce d'amortissement, dont les hooks écrivent le grand livre par `tx` ; elle n'écrit pas une
  facture de `accounts`. Comme partout (`doctype_registry`, `doctype_hooks`), le nom du plugin est celui qu'il PASSE
  (`self.ctx.name`) : c'est une garde contre les erreurs, pas contre un plugin de confiance hostile.

Les méthodes sont celles de `DocumentService` (`create`, `update`, `submit`, `cancel`, `amend`, `transition`, `delete`,
plus `get`/`list` pour lire avant d'écrire) : aucun chemin d'écriture parallèle, donc les mêmes contrôles, hooks, historique,
numérotation, règles de notification.

`ensure` rend une tâche REJOUABLE : relancée (un second processus, une reprise après panne), elle n'écrit pas deux fois.
Il ne lit PAS avant d'écrire (deux processus liraient « absent » ensemble) : il crée, et c'est la base qui refuse le
doublon — la clé primaire de `xdt_unique` pour un champ `unique: true`, celle du document pour un nom choisi —, puis
il rend le document existant. La transaction refusée est entièrement défaite, compteur de série compris (pas de trou).

Les tâches elles-mêmes passent par l'ordonnanceur d'xcore (`from sdk import cron`, le paquet `xcdk`). Son backend par
défaut `memory` n'a AUCUN verrou : chaque processus uvicorn lance la tâche. En production, `backend: redis` (verrou
`xcore:sched:lock:<job>`) ou un seul processus — et `ensure`, qui rend le doublon inoffensif de toute façon.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request

from .context import ContextError, ContextSettings, RequestContext, get_current_user
from .document import DocumentService
from .errors import AlreadyExists, DoctypeError, NotFound, ValidationFailed


class DocumentWriter:
    """Un contexte (utilisateur ou système) et les méthodes d'écriture du service. `module` : la seule écriture permise en système."""

    def __init__(self, service: DocumentService, ctx: RequestContext, *, module: str | None = None) -> None:
        self._svc, self._ctx, self._module = service, ctx, module

    @property
    def context(self) -> RequestContext:
        return self._ctx

    def _writable(self, doctype: str) -> None:
        if self._module is None:
            return  # un utilisateur : ses grants décident, comme sur l'API
        compiled = self._svc._doctype(doctype)  # inconnu : 404 comme ailleurs
        if compiled.definition.module != self._module:
            raise DoctypeError(
                "writer_foreign_module",
                f"une tâche du plugin {self._module!r} n'écrit que les DocTypes de son module, pas {doctype!r} "
                f"(module {compiled.definition.module!r}) : soumettez votre pièce, ses hooks écrivent le reste",
                status=403,
            )

    # ── Lire avant d'écrire ───────────────────────────────────
    async def get(self, doctype: str, name: str) -> dict[str, Any]:
        return await self._svc.get(self._ctx, doctype, name)

    async def list(self, doctype: str, **kw: Any) -> dict[str, Any]:
        return await self._svc.list(self._ctx, doctype, **kw)

    # ── Écrire ────────────────────────────────────────────────
    async def create(self, doctype: str, data: Any, *, name: Any = None) -> dict[str, Any]:
        self._writable(doctype)
        return await self._svc.create(self._ctx, doctype, data, name=name)

    async def ensure(self, doctype: str, data: Any, *, unique: str | None = None, name: Any = None) -> tuple[dict[str, Any], bool]:
        """`(document, créé)` : crée, ou rend celui qui existe déjà avec la même clé — un champ `unique: true` (`unique=`) ou le
        nom choisi (`name=`, pour un type `naming: prompt`). Jamais « lire puis créer » : la base tranche, sous concurrence aussi."""
        self._writable(doctype)
        compiled = self._svc._doctype(doctype)
        if (unique is None) == (name is None):
            raise ValidationFailed("ensure : donnez la clé du document, `unique=<champ>` OU `name=`", code="ensure_key_required")
        if unique is not None:
            field = compiled.definition.field_map.get(unique)
            if field is None or not field.unique:
                raise ValidationFailed(
                    f"ensure : {doctype}.{unique} n'est pas un champ `unique: true` — sans contrainte en base, deux tâches simultanées créeraient deux documents",
                    code="ensure_key_not_unique",
                )
            if not isinstance(data, dict) or data.get(unique) in (None, ""):
                raise ValidationFailed(f"ensure : la clé {unique!r} doit être renseignée", code="ensure_key_required")
        try:
            return await self._svc.create(self._ctx, doctype, data, name=name), True
        except AlreadyExists:
            if name is None:
                raise
            return await self._svc.get(self._ctx, doctype, name), False
        except ValidationFailed as e:
            if unique is None or not any(d.get("type") == "unique" and d.get("field") == unique for d in e.details or []):
                raise
        found = await self._svc.list(self._ctx, doctype, filters={unique: data[unique]}, limit=1)
        if not found["items"]:
            # La valeur est prise par un document que CET appelant ne lit pas : on ne le lui montre pas.
            raise NotFound("document_not_found", f"{doctype} : la valeur {unique} existe déjà hors de votre périmètre")
        return found["items"][0], False

    async def update(self, doctype: str, name: str, data: Any, version: Any) -> dict[str, Any]:
        self._writable(doctype)
        return await self._svc.update(self._ctx, doctype, name, data, version)

    async def submit(self, doctype: str, name: str, version: Any) -> dict[str, Any]:
        self._writable(doctype)
        return await self._svc.submit(self._ctx, doctype, name, version)

    async def cancel(self, doctype: str, name: str, version: Any) -> dict[str, Any]:
        self._writable(doctype)
        return await self._svc.cancel(self._ctx, doctype, name, version)

    async def amend(self, doctype: str, name: str) -> dict[str, Any]:
        self._writable(doctype)
        return await self._svc.amend(self._ctx, doctype, name)

    async def transition(self, doctype: str, name: str, action: Any, version: Any) -> dict[str, Any]:
        self._writable(doctype)
        return await self._svc.workflow_transition(self._ctx, doctype, name, action, version)

    async def delete(self, doctype: str, name: str) -> None:
        self._writable(doctype)
        await self._svc.delete(self._ctx, doctype, name)


class DocumentWriters:
    """Le service `doctype_writer` : `await self.get_service("doctype_writer").as_request(request)` ou `.system(self.ctx.name, tenant, raison)`."""

    def __init__(self, service: DocumentService, settings: ContextSettings) -> None:
        self._svc, self._settings = service, settings

    async def as_request(self, request: Request) -> DocumentWriter:
        """Le contexte de l'utilisateur de la requête, vérifié comme `http_context` : à appeler dans une route du plugin."""
        user = await get_current_user(request, None)  # 401 sans jeton valable, comme toute route protégée
        try:
            ctx = RequestContext.from_http(request, user, self._settings)
            ctx.require_identity()
        except ContextError as e:
            raise HTTPException(status_code=e.status, detail=e.as_detail()) from e
        return DocumentWriter(self._svc, ctx)

    def system(self, plugin: str, tenant_id: str, reason: str) -> DocumentWriter:
        """Le périmètre système pour une tâche du plugin `plugin` (son `self.ctx.name`), qui n'écrit que les types de SON module."""
        if not isinstance(plugin, str) or not plugin:
            raise DoctypeError("writer_plugin_required", "doctype_writer.system : nommez le plugin (self.ctx.name)", status=500)
        return DocumentWriter(self._svc, RequestContext.system(tenant_id, reason), module=plugin)
