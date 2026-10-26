"""Étiquettes d'un document (programme groupe 1, élément 3) : ce qu'est un nom d'étiquette, et le filtre de liste qui les cherche.

Une étiquette vit dans `xdt_tag`, autour du document comme la collaboration (`collaboration.py` dit qui voit et qui pose). Ce module ne
connaît que les règles d'un NOM, partagées par le service qui pose une étiquette et par le filtre `_tag` de la liste : on ne cherche pas
autre chose que ce qu'on aurait pu poser.
"""

from __future__ import annotations

import re
from typing import Any

# Le filtre de la liste : `{"_tag": "Urgent"}` (a l'étiquette), `["!=", "Urgent"]` (ne l'a pas), `["in", [...]]` (en a au moins une).
# Le soulignement le sépare des champs (un nom de champ commence par une lettre) : aucun DocType ne peut le déclarer.
TAG_FILTER = "_tag"
TAG_OPS = frozenset({"=", "!=", "in"})
MAX_TAG = 50
MAX_TAGS_PER_DOC = 20
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_SPACES = re.compile(r"\s+")


def normalize_tag(value: Any) -> str:
    """Le nom d'une étiquette : un texte de 1 à `MAX_TAG` caractères, blancs de bord retirés et blancs intérieurs réduits à un espace, sans
    caractère de contrôle ni virgule (le séparateur d'une saisie). La casse est gardée telle qu'écrite. Lève `ValueError` sinon."""
    if not isinstance(value, str):
        raise ValueError("une étiquette est un texte")
    tag = _SPACES.sub(" ", value).strip()
    if not tag or len(tag) > MAX_TAG:
        raise ValueError(f"une étiquette fait de 1 à {MAX_TAG} caractères")
    if _CONTROL.search(tag) or "," in tag:
        raise ValueError("une étiquette ne contient ni virgule ni caractère de contrôle")
    return tag


def supports_tags(definition: Any) -> bool:
    """Un document unique n'a qu'un document (rien à classer), un DocType virtuel garde ses documents ailleurs (le filtre est du SQL),
    une table enfant n'a pas de documents à elle."""
    return not (definition.single or definition.istable or definition.source is not None)
