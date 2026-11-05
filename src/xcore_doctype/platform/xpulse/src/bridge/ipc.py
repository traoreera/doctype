"""xpulse/src/bridge/ipc.py — Actions IPC du plugin xpulse."""

from __future__ import annotations

import html
import json
import re
from typing import Any

from xcore.sdk import action, error, get_logger, ok, schema

from ..utils import InvalidChannel, validate_channels

logger = get_logger("xpulse.ipc")

# Ce qu'un plugin publie en plus du texte : un TYPE (que les extensions du Desk écoutent par `desk.realtime.on(type, …)`) et des
# données JSON. Bornées : un message traverse Redis et chaque flux ouvert ; les clés d'enveloppe ne s'écrasent pas.
_EVENT_TYPE = re.compile(r"[a-z0-9_.:-]{1,64}")
_RESERVED_TYPES = {"doc.changed", "bell.changed"}  # les signaux du moteur (realtime.py) : un plugin ne les imite pas
_ENVELOPE = {"user_id", "tenant_id", "event_type", "text", "channel"}
MAX_DATA_BYTES = 8192


class InvalidMessage(ValueError):
    pass


def message_extras(event_type: Any, data: Any) -> dict[str, Any]:
    """`event_type` et `data` d'un message, vérifiés ; `{}` quand le plugin n'en donne pas (le Desk y voit le type `message`)."""
    out: dict[str, Any] = {}
    if event_type not in (None, ""):
        if not isinstance(event_type, str) or not _EVENT_TYPE.fullmatch(event_type):
            raise InvalidMessage("event_type : [a-z0-9_.:-], 64 caractères au plus")
        if event_type in _RESERVED_TYPES:
            raise InvalidMessage(f"event_type {event_type!r} est réservé au moteur")
        out["event_type"] = event_type
    if data:
        if not isinstance(data, dict):
            raise InvalidMessage("data : un objet JSON")
        clash = sorted(set(data) & _ENVELOPE)
        if clash:
            raise InvalidMessage(f"data ne peut pas porter {', '.join(clash)}")
        try:
            size = len(json.dumps(data, ensure_ascii=False, allow_nan=False).encode())
        except (TypeError, ValueError):
            raise InvalidMessage("data : JSON seulement (ni NaN ni objet)") from None
        if size > MAX_DATA_BYTES:
            raise InvalidMessage(f"data : {MAX_DATA_BYTES} octets au plus")
        out.update(data)
    return out


class IPCActionsMixin:
    """Mixin qui expose les actions IPC xpulse."""

    # ── Helpers ───────────────────────────────────────────────────────────

    def _normalize_channels(self, raw: Any) -> list[str]:
        if isinstance(raw, str):
            return raw.split(",")
        return list(raw) if raw else ["notification"]

    # ── Actions IPC ───────────────────────────────────────────────────────

    @action("xpulse.publish")
    @schema(
        version="1.0",
        input={"channels": (list, ["notification"]), "user_id": (str, ...), "text": (str, ...), "event_type": (str, ""), "data": (dict, {})},
        output={"channels": list, "failed": list},
        type_response="model",
        unset=False,
    )
    async def ipc_publish(self, payload) -> dict:
        svc = getattr(self, "_pubsub", None)
        if not svc:
            return error("pubsub_unavailable")

        try:
            channels = validate_channels(self._normalize_channels(payload.channels))
            extras = message_extras(payload.event_type, payload.data)
        except InvalidChannel as exc:
            return error(str(exc), code="invalid_channel")
        except InvalidMessage as exc:
            return error(str(exc), code="invalid_message")

        results = await svc.publish_many(
            channels, {**extras, "user_id": payload.user_id, "text": html.escape(payload.text)}
        )
        failed = [ch for ch, s in results.items() if not s]
        return ok(channels=[ch for ch in channels if ch not in failed], failed=failed)

    @action("xpulse.broadcast")
    @schema(
        version="1.0",
        input={"channels": (list, ["notification"]), "text": (str, ...), "event_type": (str, ""), "data": (dict, {})},
        output={"channels": list, "failed": list},
        type_response="model",
        unset=False,
    )
    async def ipc_broadcast(self, payload) -> dict:
        svc = getattr(self, "_pubsub", None)
        if not svc:
            return error("pubsub_unavailable")

        try:
            channels = validate_channels(self._normalize_channels(payload.channels))
            extras = message_extras(payload.event_type, payload.data)
        except InvalidChannel as exc:
            return error(str(exc), code="invalid_channel")
        except InvalidMessage as exc:
            return error(str(exc), code="invalid_message")

        results = await svc.publish_many(channels, {**extras, "text": html.escape(payload.text)})
        failed = [ch for ch, s in results.items() if not s]
        return ok(channels=[ch for ch in channels if ch not in failed], failed=failed)

    @action("xpulse.stream")
    @schema(
        version="1.0",
        input={"channels": (list, ["notification"]), "user_id": (str, ...)},
        output={"channels": list, "failed": list},
        type_response="model",
        unset=False,
    )
    async def ipc_stream(self, payload) -> dict:
        svc = getattr(self, "_pubsub", None)
        if not svc:
            return error("pubsub_unavailable")

        try:
            channels = validate_channels(self._normalize_channels(payload.channels))
        except InvalidChannel as exc:
            return error(str(exc), code="invalid_channel")

        results = await svc.publish_many(
            channels, {"user_id": payload.user_id}
        )
        failed = [ch for ch, s in results.items() if not s]
        return ok(channels=[ch for ch in channels if ch not in failed], failed=failed)

    @action("xpulse.subscribers")
    @schema(
        version="1.0",
        input={"channel": (str, ...)},
        output={"channel": str, "active_streams": int},
        type_response="model",
        unset=False,
    )
    async def ipc_subscribers(self, payload) -> dict:
        svc = getattr(self, "_pubsub", None)
        if not svc:
            return error("pubsub_unavailable")
        return ok(
            channel=payload.channel, active_streams=svc.active_streams
        )

    @action("xpulse.email")
    @schema(
        version="1.0",
        input={"to": (list, []), "subject": (str, ...), "template": (str, ...), "html_parser": (bool, True)},
        output={"message": str},
        type_response="model",
        unset=False,
    )
    async def send_and_forget_mail(self, payload):
        try:
            email = self.get_service("ext.email")
        except Exception as exc:
            logger.warning("xpulse.email : service ext.email indisponible : %s", exc)
            return error("Service email indisponible.", code="service_unavailable")

        try:
            response = email.queue(
                to=payload.to,
                subject=payload.subject,
                is_html=payload.html_parser,
                body=payload.template,
            )
        except Exception as exc:
            logger.warning("xpulse.email : envoi échoué : %s", exc)
            return error("Échec de l'envoi de l'email.", code="send_failed")

        if not response:
            return error("Échec de l'envoi de l'email.", code="send_failed")
        return ok(message="Email envoyé.", response=response)
