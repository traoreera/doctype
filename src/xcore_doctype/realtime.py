"""Le temps réel : ce que le moteur signale au navigateur, par l'extension `ext.pubsub` que relaie le plugin `xpulse` (SSE).

Deux signaux, et aucun ne porte de contenu :

- `doc.changed` sur le canal de l'ORGANISATION (`tenant-<id>`, celui qu'xpulse n'ouvre qu'à ses membres) : « un document de tel TYPE a
  changé ». Ni son nom ni ses valeurs : le canal est commun à tous les membres, et un membre qui ne peut pas lire ce document ne doit
  rien en apprendre — pas même qu'il existe. Le Desk relit ce qu'il montre AVEC LES DROITS de l'utilisateur : une liste se recharge,
  une fiche compare sa version.
- `bell.changed` sur le canal personnel `notification` (filtré par `user_id` par xpulse) : « ta cloche a changé » ; le Desk relit ses
  notifications chez xauth, qui reste seul juge de ce qu'il montre.

Une publication ne fait JAMAIS échouer l'écriture qui l'a causée : elle a lieu après le commit, bornée dans le temps, et une panne
du bus est journalisée puis oubliée (la règle de `notifier.py`). Sans `ext.pubsub` (hôte sans l'extension), tout est sans effet.
"""

from __future__ import annotations

import asyncio
from typing import Any, Iterable, Sequence

DOC_CHANGED, BELL_CHANGED = "doc.changed", "bell.changed"
PUBLISH_TIMEOUT = 2.0  # secondes : au-delà, le signal est perdu, la requête ne l'attend pas


def tenant_channel(tenant_id: str) -> str:
    """Le canal d'une organisation, au nom qu'xpulse autorise pour ses seuls membres (`xpulse/src/utils.authorize_channels`)."""
    return f"tenant-{tenant_id}"


class Realtime:
    def __init__(self, pubsub: Any, logger: Any) -> None:
        self._pubsub, self._log = pubsub, logger

    async def _publish(self, channel: str, data: dict[str, Any]) -> None:
        try:
            await asyncio.wait_for(self._pubsub.publish(channel, data), PUBLISH_TIMEOUT)
        except Exception as e:  # noqa: BLE001 - un signal perdu ne défait rien
            self._log.warning("realtime signal lost", channel=channel, event_type=data.get("event_type"), error=repr(e))

    async def doc_changed(self, tenant_id: str, doctypes: Iterable[str]) -> None:
        for doctype in sorted(set(doctypes)):
            await self._publish(tenant_channel(tenant_id), {"event_type": DOC_CHANGED, "tenant_id": tenant_id, "doctype": doctype})

    async def bell(self, tenant_id: str, user_ids: Sequence[str]) -> None:
        for user_id in dict.fromkeys(u for u in user_ids if u):
            await self._publish("notification", {"event_type": BELL_CHANGED, "tenant_id": tenant_id, "user_id": user_id})


class NoRealtime:
    async def doc_changed(self, tenant_id: str, doctypes: Iterable[str]) -> None:
        return None

    async def bell(self, tenant_id: str, user_ids: Sequence[str]) -> None:
        return None


class RealtimeNotifier:
    """Le notificateur de l'hôte (`notifier.py`), qui sonne aussi la cloche en temps réel une fois la notification créée."""

    def __init__(self, inner: Any, realtime: Any) -> None:
        self._inner, self._realtime = inner, realtime

    async def notify(self, tenant_id: str, user_ids: Sequence[str], **kw: Any) -> None:
        await self._inner.notify(tenant_id, user_ids, **kw)
        await self._realtime.bell(tenant_id, user_ids)

    def __getattr__(self, name: str) -> Any:  # le reste du notificateur (fermeture, état) passe tel quel
        return getattr(self._inner, name)
