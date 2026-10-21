"""Extensions JavaScript du Desk (D28, `doc/extensions.md`) : un plugin livre un module ES que le Desk charge au démarrage.

C'est du code de CONFIANCE dans la page de l'utilisateur (sa session, ses droits) : l'opérateur nomme les plugins autorisés
(`doctype.desk.extensions`, liste de noms ou `["*"]`) ; absent ou vide, rien n'est servi. Un plugin déclare son dossier par
`doctype_registry.load_desk(self.ctx.name, dossier)` (il contient `index.js`, le point d'entrée, et ce qu'il importe) ; le routeur
PUBLIC du Desk le sert sous `desk/ext/<plugin>/…` (même origine : la CSP reste `script-src 'self'`) et `config.json` le liste.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .errors import InvalidDefinition

ENTRY = "index.js"
_NAME_RE = re.compile(r"^[a-z0-9_]+$")  # un nom de plugin qui peut aussi préfixer ses composants (`sales.pipeline`)
MAX_FILES = 500
MAX_BYTES = 20 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class DeskExtension:
    name: str
    directory: Path
    # Empreinte du contenu : l'URL du point d'entrée la porte (`?v=`), un navigateur ne garde donc jamais une version périmée.
    version: str

    @property
    def url(self) -> str:
        """Relative à `desk/` (là où le Desk lit `config.json`)."""
        return f"ext/{self.name}/{ENTRY}?v={self.version}"


def _fingerprint(directory: Path) -> str:
    digest = hashlib.sha256()
    count = size = 0
    for path in sorted(p for p in directory.rglob("*") if p.is_file()):
        count += 1
        data = path.read_bytes()
        size += len(data)
        if count > MAX_FILES or size > MAX_BYTES:
            raise InvalidDefinition(f"{directory}: extension du Desk trop volumineuse (au plus {MAX_FILES} fichiers et {MAX_BYTES // 1024 // 1024} Mo)")
        digest.update(path.relative_to(directory).as_posix().encode() + b"\0" + data + b"\0")
    return digest.hexdigest()[:16]


class DeskExtensions:
    """Les extensions déclarées par les plugins, et celles que l'opérateur laisse servir. Porté par l'instance du plugin (D8)."""

    def __init__(self, allowed: Iterable[str] = (), logger: Any = None) -> None:
        allowed = tuple(allowed)
        self._all = "*" in allowed
        self._allowed = frozenset(a for a in allowed if a != "*")
        self._items: dict[str, DeskExtension] = {}
        self._log = logger

    def allows(self, name: str) -> bool:
        return self._all or name in self._allowed

    def register(self, owner: str, directory: str | Path) -> bool:
        """Déclare l'extension d'un plugin. Vrai si elle sera servie (l'opérateur l'autorise) ; une extension mal formée est une erreur de
        chargement du plugin, une extension non autorisée est seulement journalisée (le plugin reste utilisable sans elle)."""
        if not isinstance(owner, str) or not _NAME_RE.match(owner):
            raise InvalidDefinition(f"plugin {owner!r}: son nom ne peut pas nommer une extension du Desk (minuscules, chiffres et `_`)")
        if owner in self._items:
            raise InvalidDefinition(f"plugin {owner!r}: extension du Desk déjà déclarée")
        root = Path(directory).resolve()
        if not (root / ENTRY).is_file():
            raise InvalidDefinition(f"{root}: une extension du Desk a un point d'entrée `{ENTRY}`")
        self._items[owner] = DeskExtension(owner, root, _fingerprint(root))
        served = self.allows(owner)
        if self._log is not None:
            if served:
                self._log.info("desk extension registered", extension=owner, version=self._items[owner].version)
            else:
                self._log.warning("desk extension not allowed by the operator (doctype.desk.extensions), not served", extension=owner)
        return served

    def unregister(self, owner: str) -> None:
        self._items.pop(owner, None)

    def get(self, name: str) -> DeskExtension | None:
        """L'extension servie sous ce nom, ou `None` (inconnue OU non autorisée : indiscernables de l'extérieur)."""
        ext = self._items.get(name)
        return ext if ext is not None and self.allows(name) else None

    def served(self) -> list[DeskExtension]:
        return [e for n, e in sorted(self._items.items()) if self.allows(n)]
