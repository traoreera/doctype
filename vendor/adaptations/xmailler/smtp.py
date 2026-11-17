from __future__ import annotations

import re
from email.mime.base import MIMEBase
from email import encoders
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import EmailConfig, EmailMessage


async def make_smtp(cfg: "EmailConfig"):
    """Ouvre une connexion SMTP authentifiée (STARTTLS si configuré)."""
    import aiosmtplib

    smtp = aiosmtplib.SMTP(
        hostname=cfg.smtp_host,
        port=cfg.smtp_port,
        timeout=cfg.timeout,
    )
    await smtp.connect()
    if cfg.use_tls:
        try:
            await smtp.starttls()
        except aiosmtplib.SMTPException as e:
            if "already" not in str(e).lower():
                raise
    if cfg.smtp_user:
        await smtp.login(cfg.smtp_user, cfg.smtp_password)
    return smtp


async def test_connection(cfg: "EmailConfig") -> None:
    """Vérifie que la connexion SMTP est opérationnelle."""
    smtp = await make_smtp(cfg)
    await smtp.quit()


async def send_message(cfg: "EmailConfig", msg: "EmailMessage") -> None:
    """Construit le MIME et envoie via SMTP."""
    mime = build_mime(cfg, msg)
    smtp = await make_smtp(cfg)
    try:
        await smtp.send_message(mime, recipients=msg.recipients)
    finally:
        await smtp.quit()


def build_mime(cfg: "EmailConfig", msg: "EmailMessage") -> MIMEMultipart:
    """Le message tel qu'il part. Sans pièce jointe : un `alternative` strict (texte + HTML), comme avant. Avec : une enveloppe
    `mixed` qui porte cet `alternative` puis chaque pièce — jamais un `mixed` superflu, que certains clients affichent moins bien."""
    _validate_headers(msg)
    body = MIMEMultipart("alternative")
    text_body = html_to_text(msg.body) if msg.is_html else msg.body
    body.attach(MIMEText(text_body, "plain", "utf-8"))
    if msg.is_html:
        body.attach(MIMEText(msg.body, "html", "utf-8"))

    mime = body
    if msg.attachments:
        mime = MIMEMultipart("mixed")
        mime.attach(body)
        for item in msg.attachments:
            maintype, _, subtype = (item.get("mimetype") or "application/octet-stream").partition("/")
            part = MIMEBase(maintype, subtype or "octet-stream")
            part.set_payload(item["content"])
            encoders.encode_base64(part)
            # `filename=` en paramètre : `email` l'encode (RFC 2231) quand il n'est pas ASCII (« Reçu-2026.pdf »).
            part.add_header("Content-Disposition", "attachment", filename=item["filename"])
            mime.attach(part)

    mime["From"] = f"{cfg.from_name} <{cfg.from_address}>"
    mime["To"] = ", ".join([msg.to] if isinstance(msg.to, str) else msg.to)
    mime["Subject"] = msg.subject
    if msg.cc:
        mime["Cc"] = ", ".join(msg.cc)
    if msg.reply_to:
        mime["Reply-To"] = msg.reply_to
    return mime


_HEADER_VALUE_RE = re.compile(r"^[^\r\n]*$")


def _validate_headers(msg: "EmailMessage") -> None:
    """Défense en profondeur anti header-injection : To/Subject/Cc/Reply-To
    finissent en en-têtes MIME bruts (politique compat32, sans validation
    native) — un "\\nBcc: ..." y injecterait des en-têtes arbitraires.
    Lève ValueError (attrapée par _send_with_retry côté service, et refusée
    en amont par le plugin chat) plutôt que d'envoyer un message piégé."""
    recipients = [msg.to] if isinstance(msg.to, str) else list(msg.to)
    for addr in recipients + list(msg.cc or []) + list(msg.bcc or []):
        if not isinstance(addr, str) or _HEADER_VALUE_RE.match(addr) is None or "@" not in addr:
            raise ValueError(f"Adresse email invalide ou dangereuse : {addr!r}")
    for label, value in (("Subject", msg.subject), ("Reply-To", msg.reply_to or "")):
        if _HEADER_VALUE_RE.match(value) is None:
            raise ValueError(f"En-tête {label} invalide (retour à la ligne interdit)")
    for item in msg.attachments or []:
        name = item.get("filename") if isinstance(item, dict) else None
        if not isinstance(name, str) or _HEADER_VALUE_RE.match(name) is None:
            raise ValueError(f"Nom de pièce jointe invalide ou dangereux : {name!r}")


def html_to_text(html: str) -> str:
    """Conversion HTML → texte simple (sans dépendance externe)."""
    text = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)
    text = re.sub(r"<p[^>]*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
