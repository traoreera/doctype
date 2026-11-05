"""Les CANAUX de notification qu'apportent les plugins (service `doctype_channels`) : WhatsApp, SMS, Slack, un webhook…

Le moteur prévient les gens de trois façons — la cloche d'xauth, l'e-mail d'une règle, et le temps réel d'xpulse — et aucune n'était
ouverte aux plugins : une assignation, une @mention ou une règle partaient vers `xauth.notify` sans qu'un plugin puisse les voir passer.
Ce module est cette porte. Un plugin (de confiance, dans le processus) y déclare un canal :

    channels = self.get_service("doctype_channels")
    channels.register(self.ctx.name, "whatsapp", self.send, label="WhatsApp", listen_all=True)
    ...
    channels.unregister_owner(self.ctx.name)            # dans on_unload

et son `send(notification)` reçoit une `Notification` :

- `listen_all=True` : TOUTES celles qui font sonner la cloche (règles, assignations, mentions, partages), après la cloche. Le plugin
  filtre lui-même (« seulement les factures », « seulement les membres qui ont un numéro ») : c'est son métier, pas celui du moteur ;
- et, quel que soit `listen_all`, celles des RÈGLES de notification qui le nomment dans leur champ « Canal d'un plugin »
  (`plugin_channel`), avec ou sans cloche.

La politique est celle de `notifier.py` : un canal est un EFFET DE BORD. L'envoi part en tâche de fond (jamais dans la requête qui l'a
causé), borné par `SEND_TIMEOUT`, et une panne, un refus ou une lenteur sont journalisés puis oubliés — jamais remontés. Ce qui voyage
est ce que la cloche montrerait déjà : titre, message, lien, destinataires (identifiants et, quand l'annuaire les connaît, adresses) ;
jamais le document. Un plugin qui veut le document le relit avec `doctype_documents`, sous SA raison (D20).

Les noms sont `[a-z][a-z0-9_]{0,31}` (ils s'écrivent dans une règle) et appartiennent au plugin qui les a déclarés, comme les hooks :
ça empêche une erreur, pas un plugin de confiance hostile.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Sequence
from urllib.parse import unquote

from .notifier import Notifier

SEND_TIMEOUT = 15.0  # secondes par envoi : au-delà, l'envoi est abandonné et journalisé
MAX_CHANNELS = 32
NAME_RE = re.compile(r"[a-z][a-z0-9_]{0,31}")
_APP_LINK = re.compile(r"#/app/([^/]+)/([^/]+)")


@dataclass(frozen=True, slots=True)
class Notification:
    """Ce qu'un canal reçoit. `kind` : RULE, ASSIGNMENT, MENTION, SHARE (ce que la cloche connaît). `emails` : les adresses des
    destinataires que l'annuaire connaît (peut être vide). `doctype`/`name` : le document concerné, quand il y en a un. `rule` : le
    nom de la règle qui l'envoie (kind RULE)."""

    tenant_id: str
    user_ids: tuple[str, ...]
    title: str
    message: str | None = None
    link: str | None = None
    kind: str = "DOCUMENT"
    emails: tuple[str, ...] = ()
    doctype: str | None = None
    name: str | None = None
    rule: str | None = None


Send = Callable[[Notification], Awaitable[Any]]


@dataclass(frozen=True, slots=True)
class Channel:
    owner: str
    name: str
    label: str
    send: Send = field(repr=False)
    listen_all: bool = False


class ChannelError(ValueError):
    pass


class ChannelRegistry:
    """Les canaux déclarés, et les envois en cours (des tâches suivies : `settled()` pour les tests, `close()` au déchargement)."""

    def __init__(self, logger: Any = None, *, timeout: float = SEND_TIMEOUT) -> None:
        self._channels: dict[str, Channel] = {}
        self._log, self._timeout = logger, timeout
        self._pending: set[asyncio.Task[None]] = set()
        self._closed = False

    # ── Déclarer ────────────────────────────────────────────────────────────────────────────────

    def register(self, owner: str, name: str, send: Send, *, label: str | None = None, listen_all: bool = False) -> None:
        if not isinstance(owner, str) or not owner:
            raise ChannelError("un canal appartient au plugin qui le déclare : passez self.ctx.name")
        if not isinstance(name, str) or not NAME_RE.fullmatch(name):
            raise ChannelError(f"nom de canal invalide : {name!r} (minuscules, chiffres, _ ; 32 caractères au plus)")
        if not callable(send):
            raise ChannelError("send doit être une coroutine qui reçoit une Notification")
        current = self._channels.get(name)
        if current is not None and current.owner != owner:
            raise ChannelError(f"le canal {name!r} appartient déjà au plugin {current.owner!r}")
        if current is None and len(self._channels) >= MAX_CHANNELS:
            raise ChannelError(f"{MAX_CHANNELS} canaux au plus")
        self._channels[name] = Channel(owner, name, (label or name)[:60], send, bool(listen_all))

    def unregister_owner(self, owner: str) -> list[str]:
        gone = sorted(n for n, c in self._channels.items() if c.owner == owner)
        for n in gone:
            del self._channels[n]
        return gone

    def names(self) -> list[str]:
        return sorted(self._channels)

    def has(self, name: str) -> bool:
        return name in self._channels

    def describe(self) -> list[dict[str, Any]]:
        return [{"name": c.name, "label": c.label, "owner": c.owner, "listen_all": c.listen_all} for c in sorted(self._channels.values(), key=lambda c: c.name)]

    # ── Envoyer ─────────────────────────────────────────────────────────────────────────────────

    def deliver(self, name: str, notification: Notification) -> None:
        """Vers UN canal (celui qu'une règle nomme). Absent (plugin déchargé) : journalisé, rien d'autre."""
        channel = self._channels.get(name)
        if channel is None:
            self._warn("notification channel missing", channel=name, kind=notification.kind)
            return
        self._spawn([channel], notification)

    def broadcast(self, notification: Notification, *, skip: Sequence[str] = ()) -> None:
        """Vers tous les canaux `listen_all` (sauf `skip` : un canal qui l'a déjà reçu au titre d'une règle ne la reçoit pas deux fois)."""
        targets = [c for c in self._channels.values() if c.listen_all and c.name not in skip]
        if targets:
            self._spawn(targets, notification)

    def _spawn(self, channels: list[Channel], notification: Notification) -> None:
        if self._closed or not notification.user_ids:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        for channel in channels:
            task = loop.create_task(self._send(channel, notification))
            self._pending.add(task)
            task.add_done_callback(self._pending.discard)

    async def _send(self, channel: Channel, notification: Notification) -> None:
        try:
            await asyncio.wait_for(channel.send(notification), self._timeout)
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            self._warn("notification channel timed out", channel=channel.name, owner=channel.owner, kind=notification.kind)
        except Exception as e:  # noqa: BLE001 - un canal ne défait rien
            self._warn("notification channel failed", channel=channel.name, owner=channel.owner, kind=notification.kind, error=repr(e))

    async def settled(self) -> None:
        while self._pending:
            await asyncio.gather(*list(self._pending), return_exceptions=True)

    async def close(self) -> None:
        self._closed = True
        for task in list(self._pending):
            task.cancel()
        await asyncio.gather(*list(self._pending), return_exceptions=True)
        self._channels.clear()

    def _warn(self, message: str, **fields: Any) -> None:
        if self._log is None:
            return
        try:
            self._log.warning(message, **fields)  # pas de champ `plugin` ni `msg` : le logger d'xcore les réserve
        except Exception:  # noqa: BLE001
            pass


def document_of(link: str | None) -> tuple[str | None, str | None]:
    """Le document d'un lien du Desk `#/app/<type>/<nom>` (ceux que le moteur fabrique), sinon (None, None)."""
    m = _APP_LINK.fullmatch(link or "")
    return (unquote(m.group(1)), unquote(m.group(2))) if m else (None, None)


class ChannelNotifier:
    """Le notificateur de l'hôte, qui passe aussi chaque notification aux canaux `listen_all`, APRÈS la cloche. Les règles de
    notification ne passent pas par lui : elles ont le notificateur nu et font leur propre diffusion (avec le nom de la règle, et sans
    envoyer deux fois à un canal qu'elles nomment déjà)."""

    def __init__(self, inner: Notifier, channels: ChannelRegistry, directory: Any = None) -> None:
        self._inner, self._channels, self._directory = inner, channels, directory

    async def notify(self, tenant_id: str, user_ids: Sequence[str], *, title: str, message: str | None = None, link: str | None = None,
                     kind: str = "DOCUMENT") -> None:
        await self._inner.notify(tenant_id, user_ids, title=title, message=message, link=link, kind=kind)
        ids = tuple(dict.fromkeys(u for u in user_ids if u))
        if not ids or not any(c["listen_all"] for c in self._channels.describe()):
            return
        doctype, name = document_of(link)
        emails = await emails_of(self._directory, tenant_id, ids)
        self._channels.broadcast(Notification(tenant_id, ids, title, message, link, kind, emails, doctype, name))

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


async def emails_of(directory: Any, tenant_id: str, user_ids: Sequence[str]) -> tuple[str, ...]:
    """Les adresses que l'annuaire connaît pour ces membres (vide sans annuaire ; une panne de l'annuaire ne bloque rien)."""
    if directory is None:
        return ()
    try:
        members = await directory.members(tenant_id)
    except Exception:  # noqa: BLE001
        return ()
    by_id = {p.id: p.email for p in members or [] if p.email}
    return tuple(by_id[u] for u in user_ids if u in by_id)
