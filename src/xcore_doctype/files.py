"""Fichiers : les champs `Image` et `Attach` (palier 70 %, phase H).

Un champ Image ou Attach d'un document porte l'IDENTIFIANT d'un fichier (32 chiffres hexadécimaux), jamais le fichier. Le fichier vit dans un
magasin de blobs ; ses métadonnées (nom d'origine, type, taille, où il est rangé, pour quel champ) vivent dans `xdt_file`, une ligne par fichier
et par tenant. Le magasin est :

- l'extension `ext.storage` d'xcore quand l'hôte la fournit (`local`, `s3`, `r2`, `supabase`) : on n'écrit pas de second stockage ;
- sinon un dossier local (`doctype.files.dir`), pour que le Desk marche sans configuration.

Sécurité, dans l'ordre où elle s'applique :
1. **Qui peut téléverser** : qui peut créer ou modifier le DocType (jugé par `document.FileDocuments`, pas ici).
2. **Ce qui entre** : un fichier non vide, de taille bornée ; une IMAGE est reconnue à son contenu (PNG, JPEG, GIF, WebP : jamais SVG, qui porte
   du script), pas à son nom ni à l'en-tête que le client a déclaré ; un nom de fichier est nettoyé (pas de chemin, pas de caractère de contrôle).
3. **Qui peut lire** : qui peut lire un document qui cite le fichier (le périmètre `if_owner` s'applique), ou celui qui l'a téléversé tant qu'aucun
   document ne le cite. Le contenu se sert par une URL SIGNÉE de courte durée (HMAC du tenant, du fichier et de l'échéance) : une balise `<img>`
   n'envoie pas de jeton, et l'URL ne se devine pas, ne se rallonge pas, et ne vaut pas pour un autre tenant.
4. **Comment on sert** : toujours `X-Content-Type-Options: nosniff` et une CSP qui interdit tout ; une image en ligne, tout le reste en pièce jointe.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time
from pathlib import Path
from typing import Any, Protocol

from .errors import DoctypeError, ValidationFailed

DEFAULT_MAX_MB = 10
MAX_MB = 50
SIGNED_URL_TTL = 300  # secondes : le temps de charger une page, pas de se partager un lien
MAX_IDS = 100
FILE_ID_RE = re.compile(r"^[0-9a-f]{32}$")
# Le « champ » des fichiers JOINTS à un document (pas à un de ses champs) dans `xdt_file` : réservé, un nom de champ ne commence jamais par `_`.
ATTACHMENT_FIELD = "_attachment"

# Les formats d'image qu'on accepte, reconnus à leurs premiers octets. SVG est exclu exprès : c'est du XML qui peut porter du script.
_IMAGE_SIGNATURES: tuple[tuple[bytes, str, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png", ".png"),
    (b"\xff\xd8\xff", "image/jpeg", ".jpg"),
    (b"GIF87a", "image/gif", ".gif"),
    (b"GIF89a", "image/gif", ".gif"),
)

class FileError(DoctypeError):
    def __init__(self, code: str, message: str, status: int) -> None:
        super().__init__(code, message, status=status)


class FileTooLarge(FileError):
    def __init__(self, limit_mb: int) -> None:
        super().__init__("file_too_large", f"Fichier trop volumineux (maximum {limit_mb} Mo)", 413)


class FileTypeRefused(FileError):
    def __init__(self, message: str) -> None:
        super().__init__("file_type_not_allowed", message, 415)


def sniff_image(content: bytes) -> tuple[str, str] | None:
    """`(type de média, extension)` d'une image reconnue à son contenu, sinon `None`."""
    for magic, mime, ext in _IMAGE_SIGNATURES:
        if content.startswith(magic):
            return mime, ext
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp", ".webp"
    return None


_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def clean_filename(name: str | None) -> str:
    """Le nom d'origine, sans chemin ni caractère de contrôle, borné : il ne sert qu'à l'affichage et au téléchargement."""
    base = (name or "").replace("\\", "/").rsplit("/", 1)[-1]
    base = _CONTROL.sub("", base).strip().strip(".")
    return (base[:200] or "fichier")


def content_disposition(filename: str, inline: bool) -> str:
    """En-tête `Content-Disposition` avec un nom ASCII sûr ET le nom exact (RFC 5987) : aucun guillemet ni saut de ligne ne s'y glisse."""
    from urllib.parse import quote

    ascii_name = re.sub(r'[^A-Za-z0-9._ -]', "_", filename) or "fichier"
    return f"{'inline' if inline else 'attachment'}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename, safe='')}"


class BlobStore(Protocol):
    async def save(self, content: bytes, filename: str, namespace: str) -> Any: ...
    async def read(self, blob_id: str, namespace: str, stored_name: str | None = None) -> bytes | None: ...
    async def delete(self, blob_id: str, namespace: str, stored_name: str | None = None) -> bool: ...


class LocalBlobStore:
    """Un dossier sur le disque, rangé par espace de noms. Même interface qu'`ext.storage` (save, read, delete).

    Le chemin est CONSTRUIT par nous (un espace de noms nettoyé, un nom tiré au hasard) : rien de ce que le client envoie n'y entre.
    """

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).expanduser().resolve()

    def _path(self, namespace: str, stored_name: str) -> Path:
        safe = "/".join(re.sub(r"[^A-Za-z0-9_-]", "_", part) for part in namespace.split("/") if part)
        target = (self._root / safe / re.sub(r"[^A-Za-z0-9._-]", "_", stored_name)).resolve()
        if self._root not in target.parents:
            raise FileError("file_path_refused", "Chemin de stockage refusé", 400)  # pragma: no cover - les noms sont déjà nettoyés
        return target

    async def save(self, content: bytes, filename: str, namespace: str) -> Any:
        blob_id = secrets.token_hex(16)
        stored = f"{blob_id}{Path(filename).suffix.lower()[:12]}"
        target = self._path(namespace, stored)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        return type("Blob", (), {"file_id": blob_id, "stored_name": stored, "size_bytes": len(content)})()

    async def read(self, blob_id: str, namespace: str, stored_name: str | None = None) -> bytes | None:
        target = self._path(namespace, stored_name or blob_id)
        return target.read_bytes() if target.is_file() else None

    async def delete(self, blob_id: str, namespace: str, stored_name: str | None = None) -> bool:
        target = self._path(namespace, stored_name or blob_id)
        if not target.is_file():
            return False
        target.unlink()
        return True


class UrlSigner:
    """Signe `tenant|fichier|échéance` : une URL qui n'a pas la bonne signature, ou qui a expiré, ne sert rien."""

    def __init__(self, secret: str | bytes, ttl: int = SIGNED_URL_TTL, clock=time.time) -> None:
        self._key = secret.encode() if isinstance(secret, str) else secret
        self._ttl, self._clock = ttl, clock

    def sign(self, tenant: str, file_id: str) -> tuple[int, str]:
        exp = int(self._clock()) + self._ttl
        return exp, self._mac(tenant, file_id, exp)

    def _mac(self, tenant: str, file_id: str, exp: int) -> str:
        return hmac.new(self._key, f"{tenant}|{file_id}|{exp}".encode(), hashlib.sha256).hexdigest()

    def verify(self, tenant: str, file_id: str, exp: int, sig: str) -> bool:
        if exp < int(self._clock()):
            return False
        return hmac.compare_digest(self._mac(tenant, file_id, exp), sig)


def check_upload(*, kind: str, content: bytes, filename: str, declared_type: str | None, limit_mb: int) -> tuple[str, str]:
    """Valide un téléversement. Retourne `(type de média, nom nettoyé)` à enregistrer. `kind` : `Image` ou `Attach`."""
    if not content:
        raise ValidationFailed("Le fichier est vide", code="file_empty")
    if len(content) > limit_mb * 1024 * 1024:
        raise FileTooLarge(limit_mb)
    name = clean_filename(filename)
    if kind == "Image":
        seen = sniff_image(content)
        if seen is None:
            raise FileTypeRefused("Image attendue : PNG, JPEG, GIF ou WebP (le contenu est vérifié, pas le nom)")
        return seen[0], name
    # Un fichier joint : on garde le type déclaré, nettoyé, mais il ne sera servi qu'en téléchargement.
    declared = (declared_type or "").split(";", 1)[0].strip().lower()
    if not re.fullmatch(r"[a-z0-9.+-]+/[a-z0-9.+-]+", declared):
        declared = "application/octet-stream"
    return declared, name
