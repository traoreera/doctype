"""Backend d'authentification de TEST (process/doctype.md D12) : il ne sert qu'aux tests du moteur. xauth (`app/auth`) est le système d'authentification.

xcore ne stocke ni utilisateurs ni rôles : il faut un plugin d'auth. Celui-ci vérifie des jetons
signés en HMAC-SHA256 avec un secret configuré, pour que les tests du moteur puissent fabriquer n'importe quel
principal (grants, claim de tenant) sans démarrer xauth. Le fichier est AUTONOME (il ne dépend que d'xcore et de
`xcore_doctype.errors`) : les tests le posent tel quel comme plugin `auth` dans un hôte de test (c'est le nom d'xauth : les plugins du dépôt le `requires`).

Le plugin refuse de se charger sans secret explicite : pas de secret par défaut.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from pathlib import Path
from typing import Any

from xcore.sdk import AuthPayload, TrustedBase, error, ok, register_auth_backend, unregister_auth_backend

from xcore_doctype.errors import ConfigurationError


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(secret: bytes, payload: str) -> str:
    return hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()


def mint_token(secret: str | bytes, claims: dict[str, Any], *, ttl: int | None = 3600) -> str:
    """Fabrique un jeton. `claims` suit `AuthPayload` : `sub`, `roles`, `permissions` (les grants), `user`, + `tenant_id`."""
    key = secret.encode() if isinstance(secret, str) else secret
    body = dict(claims)
    if ttl is not None:
        body["exp"] = int(time.time()) + ttl
    payload = _b64(json.dumps(body, separators=(",", ":"), sort_keys=True).encode())
    return f"{payload}.{_sign(key, payload)}"


class DevAuthBackend:
    def __init__(self, secret: bytes) -> None:
        self._secret = secret

    async def extract_token(self, request: Any) -> str | None:
        header = request.headers.get("authorization", "")
        scheme, _, token = header.partition(" ")
        return token.strip() or None if scheme.lower() == "bearer" else None

    async def decode_token(self, token: str) -> AuthPayload | None:
        payload, sep, signature = token.partition(".")
        if not sep or not hmac.compare_digest(_sign(self._secret, payload), signature):
            return None
        try:
            claims = json.loads(_unb64(payload))
        except ValueError:
            return None
        if not isinstance(claims, dict) or not isinstance(claims.get("sub"), str):
            return None
        exp = claims.get("exp")
        if exp is not None and (not isinstance(exp, (int, float)) or exp < time.time()):
            return None
        return claims  # type: ignore[return-value]

    async def has_permission(self, payload: AuthPayload, permission: str) -> bool:
        # Comme XAuthBackend : appartenance à `permissions` (les grants), jamais aux noms de rôle.
        return permission in (payload.get("permissions") or [])


class Plugin(TrustedBase):
    """Plugin xcore qui enregistre `DevAuthBackend`. Secret lu dans `env.DEV_AUTH_SECRET`."""

    async def on_load(self) -> None:
        secret = (self.ctx.env or {}).get("DEV_AUTH_SECRET", "")
        # Variable absente : xcore rejette déjà le manifeste (« invalid manifest »). Reste le cas d'une
        # variable définie mais vide, ou d'un `plugin.yaml` qui porte le secret en clair.
        if not secret or secret.startswith("${"):
            raise ConfigurationError("dev-auth: DEV_AUTH_SECRET doit être défini (aucun secret par défaut)")
        register_auth_backend(DevAuthBackend(secret.encode()))
        self.logger.warning("DEV AUTH ACTIF : n'importe quel porteur du secret peut forger un principal")

    async def on_unload(self) -> None:
        unregister_auth_backend()

    async def handle(self, action: str, payload: dict) -> dict:
        # L'annuaire d'xauth, imité pour les tests du moteur : `members` (manifeste, par organisation) -> `xauth.users.list.tenant`.
        if action == "xauth.users.list.tenant" and isinstance((self.ctx.config or {}).get("members"), dict):
            return ok(users=(self.ctx.config["members"].get(payload.get("tenant_id")) or []))
        # Les notifications d'xauth (`xauth.notify`), imitées : chaque demande est ajoutée à `notifications.jsonl`, dans le dossier du plugin (les tests la relisent).
        if action == "xauth.notify":
            if not isinstance(payload, dict) or not isinstance(payload.get("user_ids"), list):
                return error("user_ids obligatoire", "invalid")
            with (Path(__file__).resolve().parent.parent / "notifications.jsonl").open("a", encoding="utf-8") as out:
                out.write(json.dumps(payload, sort_keys=True) + "\n")
            return ok(sent=len(payload["user_ids"]))
        return error(f"dev-auth n'expose aucune action ({action!r})", "unknown_action")
