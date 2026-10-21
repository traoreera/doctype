"""La source `xauth` : les DocTypes `Permission`, `Role` et `Member` du plugin `iam` lisent et écrivent les accès d'une organisation CHEZ xauth.

Rien n'est copié : chaque lecture est un appel `xauth.iam.*` (voir `app/auth/src/services/iam.py`), chaque écriture aussi. Le moteur a déjà vérifié les grants
`iam:…`, le schéma strict et les liens ; xauth REVÉRIFIE tout (propriétaire de l'organisation ou administrateur de la plateforme, permissions délégables, rôle
de la plateforme en lecture seule, pas d'auto-retrait d'accès) : le moteur autorise, xauth reste l'autorité. L'organisation et l'acteur sont passés
EXPLICITEMENT dans chaque appel.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Awaitable, Callable

from xcore_doctype.context import RequestContext
from xcore_doctype.errors import DoctypeError, NotFound, PermissionDenied, ValidationFailed, VersionConflict
from xcore_doctype.fieldtypes import format_instant
from xcore_doctype.query import StoredDoc
from xcore_doctype.schema import DocTypeDef
from xcore_doctype.sources import ReadOnlySource, content_version

NEVER = datetime(1970, 1, 1, tzinfo=UTC)  # xauth ne date ni ses permissions ni ses rôles : le Desk n'affiche pas ces colonnes pour un DocType virtuel
SCOPES = {"platform": "Plateforme", "organisation": "Organisation"}

Call = Callable[[str, dict[str, Any], str], Awaitable[Any]]


def _rows(field: str, values: list[str]) -> list[dict[str, str]]:
    return [{field: v} for v in values]


def _row_values(data: dict[str, Any], table: str, field: str) -> list[str]:
    return [str(r[field]) for r in data.get(table, []) or [] if isinstance(r, dict) and r.get(field)]


def _clean(data: dict[str, Any]) -> dict[str, Any]:
    """Le moteur ne garde jamais un `None` ni une liste vide : « aucune valeur » et « champ absent » sont le même état stocké."""
    return {k: v for k, v in data.items() if v is not None and v != []}


def permission_doc(p: dict[str, Any]) -> StoredDoc:
    data = _clean({"code": p["name"], "description": p.get("description"), "plugin": p.get("source_plugin"), "grant_group": p.get("group"),
                   "tenant_grantable": bool(p.get("tenant_grantable")), "active": bool(p.get("active", True))})
    return StoredDoc("Permission", p["name"], data, "xauth", NEVER, NEVER, content_version(data))


def role_doc(r: dict[str, Any]) -> StoredDoc:
    data = _clean({"role_name": r["name"], "description": r.get("description"), "scope": SCOPES.get(r.get("scope"), "Organisation"),
                   "permissions": _rows("permission", r.get("permissions", []))})
    return StoredDoc("Role", r["id"], data, "xauth", NEVER, NEVER, content_version(data))


def member_doc(m: dict[str, Any]) -> StoredDoc:
    joined = datetime.fromisoformat(m["joined_at"]) if m.get("joined_at") else NEVER
    if joined.tzinfo is None:
        joined = joined.replace(tzinfo=UTC)
    data = _clean({
        "email": m.get("email"), "username": m.get("username"), "is_owner": bool(m.get("is_owner")), "is_active": bool(m.get("is_active", True)),
        "primary_role": m.get("primary_role_id"), "roles": _rows("role", m.get("role_ids", [])),
        "permissions": _rows("permission", m.get("permissions", [])), "joined_at": format_instant(joined) if m.get("joined_at") else None,
    })
    return StoredDoc("Member", m["user_id"], data, m["user_id"], joined, joined, content_version(data))


class XauthSource(ReadOnlySource):
    def __init__(self, call: Call) -> None:
        self._call = call

    async def _ask(self, ctx: RequestContext, action: str, **payload: Any) -> dict[str, Any]:
        if ctx.user_id is None:
            raise PermissionDenied()  # le contexte système n'a pas d'acteur : xauth juge toujours QUI demande
        result = await self._call(action, {"tenant_id": ctx.tenant_id, "actor_id": ctx.user_id, **payload}, ctx.tenant_id)
        if isinstance(result, dict) and result.get("status", "ok") == "ok":
            return result
        code = result.get("code") if isinstance(result, dict) else None
        said = str(result.get("msg") or "refusé par xauth") if isinstance(result, dict) else "réponse inattendue d'xauth"
        if code == "forbidden":
            raise PermissionDenied()
        if code == "not_found":
            raise NotFound("document_not_found", said)
        if code == "invalid":
            raise ValidationFailed(said, code="iam_refused")
        raise DoctypeError("source_failed", "xauth ne répond pas", status=502)  # le détail est dans le journal d'xauth, jamais ici

    # ── Lecture ───────────────────────────────────────────────
    async def fetch_all(self, ctx: RequestContext, doctype: DocTypeDef) -> list[StoredDoc]:
        match doctype.name:
            case "Permission":
                return [permission_doc(p) for p in (await self._ask(ctx, "xauth.iam.permissions"))["items"]]
            case "Role":
                return [role_doc(r) for r in (await self._ask(ctx, "xauth.iam.roles"))["items"]]
            case "Member":
                return [member_doc(m) for m in (await self._ask(ctx, "xauth.iam.members"))["items"]]
        raise ValidationFailed(f"la source xauth ne connaît pas {doctype.name}")

    async def fetch(self, ctx: RequestContext, doctype: DocTypeDef, name: str) -> StoredDoc | None:
        if doctype.name == "Member":
            try:
                return member_doc((await self._ask(ctx, "xauth.iam.member", user_id=name))["member"])
            except NotFound:
                return None
        return await super().fetch(ctx, doctype, name)

    # ── Écriture ──────────────────────────────────────────────
    async def insert(self, ctx: RequestContext, doctype: DocTypeDef, data: dict[str, Any]) -> StoredDoc:
        if doctype.name != "Role":
            raise ValidationFailed(f"{doctype.name} ne se crée pas ici : un membre rejoint l'organisation par invitation", code="source_read_only")
        saved = await self._ask(
            ctx, "xauth.iam.role.save", role_id=None, name=data.get("role_name"), description=data.get("description"),
            permissions=_row_values(data, "permissions", "permission"),
        )
        return role_doc(saved["role"])

    async def update(self, ctx: RequestContext, doctype: DocTypeDef, name: str, data: dict[str, Any], version: int) -> StoredDoc:
        current = await self.fetch(ctx, doctype, name)
        if current is None:
            raise NotFound("document_not_found", f"{doctype.name} {name!r} introuvable")
        if current.version != version:
            raise VersionConflict(f"{doctype.name} {name!r} a été modifié (version attendue {version})")
        match doctype.name:
            case "Role":
                saved = await self._ask(
                    ctx, "xauth.iam.role.save", role_id=name, name=data.get("role_name"), description=data.get("description"),
                    permissions=_row_values(data, "permissions", "permission"),
                )
                return role_doc(saved["role"])
            case "Member":
                saved = await self._ask(
                    ctx, "xauth.iam.member.save", user_id=name, primary_role_id=data.get("primary_role"),
                    role_ids=_row_values(data, "roles", "role"), permissions=_row_values(data, "permissions", "permission"),
                    is_active=data.get("is_active"),
                )
                return member_doc(saved["member"])
        raise ValidationFailed(f"{doctype.name} est en lecture seule", code="source_read_only")

    async def remove(self, ctx: RequestContext, doctype: DocTypeDef, name: str) -> None:
        match doctype.name:
            case "Role":
                await self._ask(ctx, "xauth.iam.role.delete", role_id=name)
            case "Member":
                await self._ask(ctx, "xauth.iam.member.remove", user_id=name)
            case _:
                raise ValidationFailed(f"{doctype.name} est en lecture seule", code="source_read_only")
