"""Notifier des gens : « X vous a assigné ce document », « X vous a mentionné ». La cloche du Desk, alimentée par xauth.

Avec **xauth** (le système d'authentification par défaut), l'action `xauth.notify` crée une notification pour des membres d'une organisation. Ce module
la demande par l'appel de plugin d'xcore (comme `directory.py`, avec le même réglage `allowed_callers` côté xauth). Une notification est un
**effet de bord** : un échec (xauth absent, arrêté, lent, refus) est journalisé et ne fait JAMAIS échouer l'assignation ou le commentaire qui l'a provoquée.

Rien du contenu d'un document n'y voyage au-delà de ce que l'auteur a écrit POUR le destinataire (la description d'une assignation) : le type, le nom
et un lien (`#/app/…`) que le Desk ne suit que si le destinataire a le droit de lire le document.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Protocol, Sequence

DEFAULT_PLUGIN = "auth"
DEFAULT_ACTION = "xauth.notify"
MAX_RECIPIENTS = 50


class Notifier(Protocol):
    async def notify(self, tenant_id: str, user_ids: Sequence[str], *, title: str, message: str | None = None, link: str | None = None, kind: str = "DOCUMENT") -> None:
        """Prévient `user_ids` (membres de `tenant_id`). Ne lève jamais."""


class NoNotifier:
    """`doctype.notifications.provider: none` : personne n'est prévenu."""

    async def notify(self, tenant_id: str, user_ids: Sequence[str], *, title: str, message: str | None = None, link: str | None = None, kind: str = "DOCUMENT") -> None:
        return None


Call = Callable[[str, str, dict[str, Any], str], Awaitable[Any]]


class XauthNotifier:
    def __init__(self, call: Call, *, plugin: str = DEFAULT_PLUGIN, action: str = DEFAULT_ACTION, logger: Any = None) -> None:
        self._call, self._plugin, self._action, self._log = call, plugin, action, logger

    async def notify(self, tenant_id: str, user_ids: Sequence[str], *, title: str, message: str | None = None, link: str | None = None, kind: str = "DOCUMENT") -> None:
        ids = list(dict.fromkeys(u for u in user_ids if u))[:MAX_RECIPIENTS]
        if not ids:
            return
        payload: dict[str, Any] = {"tenant_id": tenant_id, "user_ids": ids, "title": title[:255], "type": kind}
        if message:
            payload["message"] = message[:2000]
        if link:
            payload["link"] = link
        try:
            result = await self._call(self._plugin, self._action, payload, tenant_id)
        except Exception as e:  # noqa: BLE001 - une notification ne défait rien
            self._warn("notification not sent", error=repr(e))
            return
        if isinstance(result, dict) and result.get("status", "ok") != "ok":
            self._warn("notification refused", notify_code=result.get("code"), answer=str(result.get("msg"))[:300])

    def _warn(self, message: str, **fields: Any) -> None:
        if self._log is None:
            return
        try:
            self._log.warning(message, notify_action=self._action, **fields)  # pas de champ `plugin` ni `msg` : le logger d'xcore les réserve
        except Exception:  # noqa: BLE001
            pass
