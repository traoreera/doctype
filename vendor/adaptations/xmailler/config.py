from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class EmailConfig:
    smtp_host: str = ""  # vide : SMTP non configuré, l'envoi est SIMULÉ (journalisé, jamais envoyé)
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    from_address: str = "noreply@example.com"
    from_name: str = "xcore App"
    use_tls: bool = True
    timeout: int = 10
    max_retries: int = 3
    queue_size: int = 100
    requeue_delay: int = 30  # secondes, doublé à chaque remise en file (plafond x16)
    max_requeues: int = 6  # remises en file avant abandon journalisé
    # Poids total des pièces jointes d'UN message, en octets. Une facture fait quelques dizaines de kilo-octets ; au-delà, la file
    # (qui garde les messages en mémoire) et le serveur SMTP du destinataire en pâtissent. 0 : aucune pièce jointe acceptée.
    max_attachment_bytes: int = 10 * 1024 * 1024

    @property
    def configured(self) -> bool:
        """Un serveur SMTP est-il désigné ? Sinon l'extension simule l'envoi (développement) au lieu d'échouer trois fois."""
        return bool(self.smtp_host)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "EmailConfig":
        # Une valeur absente ou vide du YAML retombe sur la variable d'environnement `XAUTH_SMTP_<CLÉ>` (`.env`) : le YAML de l'hôte n'exige pas de
        # `${VAR}` (xcore refuse de démarrer si une variable listée n'existe pas) et le mot de passe SMTP reste hors du dépôt.
        names = {"smtp_host": "HOST", "smtp_port": "PORT", "smtp_user": "USER", "smtp_password": "PASSWORD", "from_address": "FROM", "from_name": "FROM_NAME", "use_tls": "USE_TLS"}

        def _get(key: str, default: Any) -> Any:
            value = d.get(key)
            if value is None or (isinstance(value, str) and not value.strip()) or (isinstance(value, str) and re.fullmatch(r"\$\{[^}]*\}", value.strip())):
                value = os.environ.get(f"XAUTH_SMTP_{names[key]}") if key in names else None
            return default if value is None or str(value).strip() == "" else value

        def _str(key: str, default: str) -> str:
            return str(_get(key, default)).strip()

        def _int(key: str, default: int) -> int:
            return int(str(_get(key, default)).strip())

        def _bool(key: str, default: bool) -> bool:
            v = str(_get(key, default)).strip().lower()
            return v not in ("false", "0", "no", "")

        return cls(
            smtp_host=_str("smtp_host", ""),
            smtp_port=_int("smtp_port", 587),
            smtp_user=_str("smtp_user", ""),
            smtp_password=_str("smtp_password", ""),
            from_address=_str("from_address", "noreply@example.com"),
            from_name=_str("from_name", "xcore App"),
            use_tls=_bool("use_tls", True),
            timeout=_int("timeout", 10),
            max_retries=_int("max_retries", 3),
            queue_size=_int("queue_size", 100),
            requeue_delay=_int("requeue_delay", 30),
            max_requeues=_int("max_requeues", 6),
            max_attachment_bytes=_int("max_attachment_bytes", 10 * 1024 * 1024),
        )


@dataclass
class EmailMessage:
    to: str | list[str]
    subject: str
    body: str
    is_html: bool = False
    cc: list[str] = field(default_factory=list)
    bcc: list[str] = field(default_factory=list)
    reply_to: str | None = None
    attachments: list[dict] = field(default_factory=list)
    id: str = field(default_factory=lambda: str(time.time_ns()))
    attempts: int = 0
    requeues: int = 0
    created_at: float = field(default_factory=time.time)

    @property
    def recipients(self) -> list[str]:
        to = [self.to] if isinstance(self.to, str) else self.to
        return list(set(to + self.cc + self.bcc))


_FILENAME_RE = re.compile(r"^[^\r\n\x00/\\]{1,255}$")
_MIMETYPE_RE = re.compile(r"^[a-z0-9][a-z0-9.+-]{0,63}/[a-z0-9][a-z0-9.+-]{0,63}$")


def check_attachments(attachments: Any, max_bytes: int) -> list[dict]:
    """Les pièces jointes d'un message, vérifiées AVANT tout envoi et toute mise en file : `{"filename", "content", "mimetype"?}`.

    `content` est des OCTETS (le fichier lui-même, jamais un chemin : le service ne lit rien sur le disque pour l'appelant) ;
    `filename` finit dans un en-tête MIME, donc ni retour à la ligne ni séparateur de dossier ; `mimetype` vaut
    `application/octet-stream` par défaut. Lève `ValueError` en disant quelle pièce et pourquoi : mieux vaut refuser le message que
    l'envoyer sans la facture que son destinataire attend.
    """
    if not attachments:
        return []
    if not isinstance(attachments, (list, tuple)):
        raise ValueError("attachments : une liste de pièces {filename, content, mimetype} est attendue")
    out: list[dict] = []
    total = 0
    for i, item in enumerate(attachments):
        if not isinstance(item, dict):
            raise ValueError(f"pièce jointe n°{i + 1} : un objet {{filename, content, mimetype}} est attendu")
        name, content = item.get("filename"), item.get("content")
        mimetype = item.get("mimetype") or "application/octet-stream"
        if not isinstance(name, str) or _FILENAME_RE.match(name) is None:
            raise ValueError(f"pièce jointe n°{i + 1} : nom de fichier invalide {name!r}")
        if not isinstance(content, (bytes, bytearray)):
            raise ValueError(f"pièce jointe {name!r} : `content` doit être des octets (bytes), pas {type(content).__name__}")
        if not isinstance(mimetype, str) or _MIMETYPE_RE.match(mimetype.lower()) is None:
            raise ValueError(f"pièce jointe {name!r} : type MIME invalide {mimetype!r}")
        total += len(content)
        out.append({"filename": name, "content": bytes(content), "mimetype": mimetype.lower()})
    if total > max_bytes:
        raise ValueError(f"pièces jointes trop lourdes : {total} octets, {max_bytes} au plus (réglage `max_attachment_bytes`)")
    return out
