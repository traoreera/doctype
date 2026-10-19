"""Les champs `Password` : chiffrés au repos, jamais renvoyés.

Un champ `Password` porte un secret (le mot de passe d'un serveur SMTP, une clé d'API) qu'un plugin de confiance doit pouvoir RELIRE, donc un condensat ne suffit pas :
il est chiffré (Fernet : AES-128-CBC + HMAC-SHA256, jeton authentifié) avec une clé dérivée de `doctype.secret_key` du manifeste. La forme stockée est `enc:v1:<jeton>`.

- le service n'envoie JAMAIS la valeur : une lecture, une liste, l'historique, un hook sandboxé reçoivent `********` (`MASK`) tant qu'un secret est posé ;
- à l'écriture, `********` veut dire « inchangé » (le formulaire renvoie ce qu'il a reçu) ; toute autre valeur est un NOUVEAU secret, chiffré dans la transaction ;
- sans `doctype.secret_key`, une écriture qui porte un secret est REFUSÉE (503), jamais stockée en clair ;
- seul un plugin de confiance, en contexte système, relit le secret (`SystemReader.secret`), et chaque lecture est journalisée ;
- changer la clé rend les secrets existants illisibles (pas de rotation automatique) : à noter dans l'exploitation.
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

PREFIX = "enc:v1:"
MASK = "********"
MIN_KEY_LENGTH = 16


class Sealer:
    def __init__(self, secret: str | None) -> None:
        if secret is not None and len(secret) < MIN_KEY_LENGTH:
            raise ValueError(f"doctype.secret_key doit faire au moins {MIN_KEY_LENGTH} caractères")
        self._fernet = Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())) if secret else None

    @property
    def available(self) -> bool:
        return self._fernet is not None

    def seal(self, plain: str) -> str:
        if self._fernet is None:
            raise RuntimeError("aucune clé de chiffrement")
        return PREFIX + self._fernet.encrypt(plain.encode()).decode()

    def unseal(self, stored: str) -> str:
        if self._fernet is None or not stored.startswith(PREFIX):
            raise ValueError("secret illisible")
        try:
            return self._fernet.decrypt(stored[len(PREFIX):].encode()).decode()
        except InvalidToken:
            raise ValueError("secret illisible (la clé a changé ?)") from None
