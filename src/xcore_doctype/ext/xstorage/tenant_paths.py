"""
Chemins de stockage isoles par tenant.

Modele : toute cle backend servie via HTTP est ``<tenant>/<namespace>/<stored_name>``.
Le tenant vient du principal verifie, jamais du client ; le namespace et le nom
fournis par le client sont valides ici (segments surs, longueur bornee).
"""
from __future__ import annotations

import re

MAX_SIGNED_URL_TTL = 3600  # secondes, plafond cote serveur
MAX_NAMESPACE_LEN = 200
MAX_SEGMENT_LEN = 128

_SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@=+-]*$")


class InvalidStoragePath(ValueError):
    """Chemin, namespace ou tenant invalide (traversee, caractere interdit...)."""


def validate_segment(value: object, *, what: str = "segment") -> str:
    if not isinstance(value, str) or not value or len(value) > MAX_SEGMENT_LEN:
        raise InvalidStoragePath(f"{what} invalide")
    if value in (".", "..") or ".." in value or not _SEGMENT_RE.match(value):
        raise InvalidStoragePath(f"{what} invalide")
    return value


def normalize_namespace(namespace: object) -> str:
    """Valide un namespace relatif (``a/b/c``) et le renvoie tel quel."""
    if not isinstance(namespace, str) or not namespace or len(namespace) > MAX_NAMESPACE_LEN:
        raise InvalidStoragePath("namespace invalide")
    parts = namespace.split("/")
    for part in parts:  # rejette aussi '', absolu, '//' et '\\'
        validate_segment(part, what="namespace")
    return "/".join(parts)


def tenant_namespace(tenant_id: object, namespace: object) -> str:
    """``<tenant>/<namespace>`` avec validation des deux parties."""
    tenant = validate_segment(tenant_id, what="tenant")
    return f"{tenant}/{normalize_namespace(namespace)}"


def key_belongs_to_tenant(key: str, tenant_id: str) -> bool:
    return key.startswith(f"{tenant_id}/")


def clamp_ttl(expires_in: object) -> int:
    try:
        ttl = int(expires_in)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return MAX_SIGNED_URL_TTL
    return max(1, min(ttl, MAX_SIGNED_URL_TTL))


async def resolve_file_namespace(
    storage, tenant_id: object, namespace: str, file_id: str, stored_name: str
) -> tuple[str, bool] | None:
    """Namespace reel d'un fichier : chemin d'entreprise, sinon ancien chemin.

    Renvoie ``(namespace, historique)`` ou ``None`` si le fichier n'existe sous
    aucun des deux. Le repli n'est qu'un second essai pour le MEME enregistrement :
    l'appelant doit avoir verifie, AVANT, que l'enregistrement appartient au tenant
    de l'appelant (jamais de devinette de chemin a partir d'une entree client).
    """
    try:
        current = tenant_namespace(tenant_id, namespace)
    except InvalidStoragePath:
        current = None
    if current and await storage.exists(file_id, current, stored_name=stored_name):
        return current, False
    if await storage.exists(file_id, namespace, stored_name=stored_name):
        return namespace, True
    return None
