"""Contexte de requête explicite (D17, D20, annexe A).

Le service `Document` ne lit jamais les ContextVar d'xcore : chaque entrée (route HTTP
ou action xcore) construit un `RequestContext` immuable et le passe en premier argument.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any, Callable, Literal, Mapping

from fastapi import Depends, HTTPException, Request
from xcore.kernel.api import get_current_user
from xcore.kernel.api.auth import get_current_principal
from xcore.kernel.tenancy import _current_tenant_id

# Import privé : à exposer publiquement côté xcore (doc/doctype.md §14, point 3).
from xcore.kernel.tenancy.services import _validate_tenant

from .errors import ConfigurationError, ContextError

Source = Literal["http", "ipc", "system"]


def _str_set(raw: Any) -> frozenset[str]:
    """Une collection de chaînes, sinon rien : une chaîne unique n'est pas une liste (« abc » deviendrait {a, b, c})."""
    if isinstance(raw, (list, tuple, set, frozenset)):
        return frozenset(r for r in raw if isinstance(r, str))
    return frozenset()


@dataclass(frozen=True, slots=True)
class ContextSettings:
    """Lu dans le bloc `doctype:` du manifeste.

    xcore n'expose pas `tenancy.enabled` aux plugins : `enforce_tenant_binding` est donc un
    réglage explicite, vrai par défaut (échec fermé). Il ne doit être mis à faux que si la
    tenancy d'xcore est désactivée.
    """

    enforce_tenant_binding: bool = True
    tenant_claim: str = "tenant_id"
    require_tenant_claim: bool = True
    cross_tenant_grant: str = "doctype:cross_tenant"

    @classmethod
    def from_plugin_config(cls, config: Mapping[str, Any]) -> "ContextSettings":
        block = dict((config or {}).get("doctype") or {})
        known = {f.name for f in fields(cls)}
        # Les autres clés du bloc (ex. doctypes_dirs) ne concernent pas ce réglage.
        wanted = {k: v for k, v in block.items() if k in known}
        for key in ("enforce_tenant_binding", "require_tenant_claim"):
            if key in wanted and not isinstance(wanted[key], bool):
                raise ConfigurationError(f"doctype.{key} doit être un booléen")
        for key in ("tenant_claim", "cross_tenant_grant"):
            if key in wanted and not (isinstance(wanted[key], str) and wanted[key]):
                raise ConfigurationError(f"doctype.{key} doit être une chaîne non vide")
        return cls(**wanted)


@dataclass(frozen=True, slots=True)
class RequestContext:
    tenant_id: str
    principal: Mapping[str, Any] | None
    source: Source
    request_id: str | None = None
    reason: str | None = None  # renseigné uniquement pour source == "system"

    # ── Accesseurs ────────────────────────────────────────────
    @property
    def is_system(self) -> bool:
        return self.source == "system"

    @property
    def user_id(self) -> str | None:
        return (self.principal or {}).get("sub")

    @property
    def owner_id(self) -> str:
        """Valeur écrite dans la colonne `owner` à la création."""
        return self.user_id or "system"

    @property
    def permissions(self) -> frozenset[str]:
        """Grants du principal (`plugin:ressource:action`). C'est ce que les règles de DocType comparent."""
        return _str_set((self.principal or {}).get("permissions"))

    @property
    def roles(self) -> frozenset[str]:
        """Noms de rôle : informatifs (libres et par tenant chez auth). Aucune règle de DocType ne s'y fie."""
        return _str_set((self.principal or {}).get("roles"))

    def user_attr(self, name: str) -> Any:
        return ((self.principal or {}).get("user") or {}).get(name)

    def require_identity(self) -> None:
        if not self.is_system and self.user_id is None:
            raise ContextError("unauthenticated", 401)

    # ── Constructeurs ─────────────────────────────────────────
    @classmethod
    def system(cls, tenant_id: str, reason: str) -> "RequestContext":
        """Jobs et appels service à service : explicite, journalisé par le service."""
        if not reason or not reason.strip():
            raise ContextError("system_reason_required", 500)
        return cls(_checked_tenant(tenant_id), None, "system", reason=reason)

    @classmethod
    def from_ipc(cls, settings: ContextSettings) -> "RequestContext":
        """Appelé dans une action : lit les ContextVar posées par `Supervisor._dispatch`."""
        principal = get_current_principal()
        if principal is None:
            raise ContextError("unauthenticated", 401)
        tenant = _bind_tenant(_current_tenant_id.get(), principal, settings)
        return cls(tenant, principal, "ipc")

    @classmethod
    def from_http(
        cls, request: Request, principal: Mapping[str, Any], settings: ContextSettings
    ) -> "RequestContext":
        # Posé par TenantMiddleware, que seul `Xcore.setup(app)` installe.
        requested = getattr(request.state, "tenant_id", None)
        if requested is None:
            if settings.enforce_tenant_binding:
                # Échec fermé : sans middleware, retomber sur "default" masquerait le tenant demandé.
                raise ContextError(
                    "tenant_middleware_missing", 500,
                    "TenantMiddleware n'est pas installé : ajoutez `xcore.setup(app)` dans main.py (voir `xcore-doctype doctor`)",
                )
            requested = "default"
        tenant = _bind_tenant(requested, principal, settings)
        return cls(tenant, principal, "http", request.headers.get("x-request-id"))


def _checked_tenant(tenant_id: str) -> str:
    try:
        return _validate_tenant(tenant_id)
    except ValueError as e:
        raise ContextError("invalid_tenant", 400) from e


def _bind_tenant(requested: str, principal: Mapping[str, Any], s: ContextSettings) -> str:
    """Le tenant du header n'est qu'une demande : il doit être couvert par l'identité."""
    tenant = _checked_tenant(requested)
    if not s.enforce_tenant_binding:
        return tenant
    claim = principal.get(s.tenant_claim) or (principal.get("user") or {}).get(s.tenant_claim)
    if claim is None:
        if s.require_tenant_claim:
            raise ContextError("tenant_claim_missing")
        return tenant
    if claim != tenant:
        # Un grant vit dans `permissions`, jamais dans `roles` (noms libres) : même règle que les règles de DocType.
        if s.cross_tenant_grant not in _str_set(principal.get("permissions")):
            raise ContextError("tenant_mismatch")
    return tenant


def ipc_operator(settings: ContextSettings) -> bool:
    """Dans une action : le principal de l'appel porte-t-il le grant transverse (`cross_tenant_grant`) ? C'est ce qu'exige une action
    d'opérateur (`ops.*`), qui nomme le tenant dans sa charge. Sans principal (pas de jeton, un plugin qui appelle hors requête) : non."""
    return settings.cross_tenant_grant in _str_set((get_current_principal() or {}).get("permissions"))


def http_context(settings: ContextSettings) -> Callable[..., Any]:
    """Dépendance FastAPI à placer sur TOUT le routeur du plugin."""

    async def dep(request: Request, user=Depends(get_current_user)) -> RequestContext:
        try:
            ctx = RequestContext.from_http(request, user, settings)
            ctx.require_identity()
            return ctx
        except ContextError as e:
            raise HTTPException(status_code=e.status, detail=e.as_detail()) from e

    return dep
