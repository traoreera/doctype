"""Sources de documents : où vivent les documents d'un DocType VIRTUEL (`source: <nom>` dans sa définition).

Un DocType ordinaire stocke ses documents dans la table générique (`xdt_document`). Un DocType virtuel les laisse là où ils sont déjà : les comptes,
rôles et permissions d'xauth, par exemple. Le moteur ne les copie pas (une seule vérité, rien à resynchroniser) ; il les LIT et les ÉCRIT par une
**source** que le plugin propriétaire enregistre sous un nom (`doctype_sources`, comme `doctype_hooks` pour les hooks). Tout le reste est inchangé :
droits par grants, filtres typés, tri, recherche, champs Link, tableaux de bord, rapport et export, commentaires.

Ce que fait le moteur, ce que fait la source :

- la source ne connaît ni les droits d'un DocType ni les filtres : `fetch_all` rend TOUS les documents de l'organisation (`StoredDoc`), et le moteur applique
  le périmètre de lecture, les filtres, le tri, la pagination, la recherche et les agrégats (`memory.py`, mêmes règles que la table générique, vérifiées
  par `tests/unit/test_memory_parity.py`). Prévu pour des ensembles de quelques milliers de lignes au plus (membres, rôles, permissions) ;
- les écritures (`insert`, `update`, `remove`) arrivent déjà validées (schéma strict, droits, liens) ; la source les applique à SA vérité et la
  REVÉRIFIE : le moteur autorise, la source reste juge de ce qu'elle accepte (xauth refuse ce que ses propres règles interdisent) ;
- une refus se dit par les erreurs du moteur (`ValidationFailed`, `NotFound`, `VersionConflict`, `PermissionDenied`, `DocumentInUse`), jamais par un
  texte à interpréter ;
- `version` : un entier >= 1 qui CHANGE quand le contenu change (un condensat du contenu convient) : c'est le contrôle de modification concurrente ;
- le contexte porte l'organisation (`ctx.tenant_id`) et l'acteur (`ctx.owner_id`) : une source qui appelle un autre plugin les lui passe
  EXPLICITEMENT (jamais d'état ambiant).

Pas d'historique, pas de pièce jointe, pas de hook ni de numérotation : voir `schema.DocTypeDef._source_rules`.
"""

from __future__ import annotations

import binascii
import json
from typing import Any, Mapping, Protocol

from .context import RequestContext
from .errors import ValidationFailed
from .query import StoredDoc
from .schema import DocTypeDef


class DocumentSource(Protocol):
    async def fetch_all(self, ctx: RequestContext, doctype: DocTypeDef) -> list[StoredDoc]:
        """Tous les documents de l'organisation `ctx.tenant_id` pour ce DocType (le moteur filtre, trie et pagine)."""

    async def fetch(self, ctx: RequestContext, doctype: DocTypeDef, name: str) -> StoredDoc | None:
        """Un document, ou `None`. Par défaut (`ReadOnlySource`) : un parcours de `fetch_all`."""

    async def insert(self, ctx: RequestContext, doctype: DocTypeDef, data: dict[str, Any]) -> StoredDoc:
        """Crée un document (le nom est celui que la source choisit)."""

    async def update(self, ctx: RequestContext, doctype: DocTypeDef, name: str, data: dict[str, Any], version: int) -> StoredDoc:
        """Remplace le contenu si `version` est celle qu'on a lue (`VersionConflict` sinon)."""

    async def remove(self, ctx: RequestContext, doctype: DocTypeDef, name: str) -> None:
        """Supprime le document."""


class ReadOnlySource:
    """Base commode : `fetch` par parcours, aucune écriture. Une source n'écrit que ce qu'elle redéfinit."""

    async def fetch_all(self, ctx: RequestContext, doctype: DocTypeDef) -> list[StoredDoc]:  # pragma: no cover - abstraite
        raise NotImplementedError

    async def fetch(self, ctx: RequestContext, doctype: DocTypeDef, name: str) -> StoredDoc | None:
        return next((d for d in await self.fetch_all(ctx, doctype) if d.name == name), None)

    async def insert(self, ctx: RequestContext, doctype: DocTypeDef, data: dict[str, Any]) -> StoredDoc:
        raise ValidationFailed(f"{doctype.name} est en lecture seule : on ne le crée pas ici", code="source_read_only")

    async def update(self, ctx: RequestContext, doctype: DocTypeDef, name: str, data: dict[str, Any], version: int) -> StoredDoc:
        raise ValidationFailed(f"{doctype.name} est en lecture seule : on ne le modifie pas ici", code="source_read_only")

    async def remove(self, ctx: RequestContext, doctype: DocTypeDef, name: str) -> None:
        raise ValidationFailed(f"{doctype.name} est en lecture seule : on ne le supprime pas ici", code="source_read_only")


def content_version(data: Mapping[str, Any]) -> int:
    """Une version qui change avec le contenu (CRC32 de sa forme canonique, jamais 0) : le contrôle de modification concurrente d'une source sans colonne `version`."""
    raw = json.dumps(data, sort_keys=True, separators=(",", ":"), default=str).encode()
    return (binascii.crc32(raw) & 0x7FFFFFFF) or 1


class DocumentSources:
    """Le registre des sources, par nom. Un service exporté (`doctype_sources`) : le plugin propriétaire enregistre la sienne dans `on_load`, la retire dans `on_unload`.

    Comme pour les hooks, la propriété se fait par le NOM de plugin passé : elle prévient les erreurs, elle ne défend pas contre un plugin de confiance hostile."""

    def __init__(self) -> None:
        self._sources: dict[str, tuple[str, DocumentSource]] = {}

    def register(self, owner: str, name: str, source: DocumentSource) -> None:
        held = self._sources.get(name)
        if held is not None and held[0] != owner:
            raise ValueError(f"la source {name!r} appartient au plugin {held[0]!r}")
        self._sources[name] = (owner, source)

    def unregister_owner(self, owner: str) -> int:
        gone = [n for n, (o, _) in self._sources.items() if o == owner]
        for n in gone:
            del self._sources[n]
        return len(gone)

    def get(self, name: str) -> DocumentSource | None:
        held = self._sources.get(name)
        return held[1] if held else None

    def names(self) -> list[str]:
        return sorted(self._sources)
