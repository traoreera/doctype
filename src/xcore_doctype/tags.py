"""Étiquettes d'un document (programme groupe 1, élément 3) : ce qu'est un nom d'étiquette, et le filtre de liste qui les cherche.

Une étiquette vit dans `xdt_tag`, autour du document comme la collaboration (`collaboration.py` dit qui voit et qui pose). Ce module ne
connaît que les règles d'un NOM, partagées par le service qui pose une étiquette et par le filtre `_tag` de la liste : on ne cherche pas
autre chose que ce qu'on aurait pu poser.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

# Le filtre de la liste : `{"_tag": "Urgent"}` (a l'étiquette), `["!=", "Urgent"]` (ne l'a pas), `["in", [...]]` (en a au moins une).
# Le soulignement le sépare des champs (un nom de champ commence par une lettre) : aucun DocType ne peut le déclarer.
TAG_FILTER = "_tag"
TAG_OPS = frozenset({"=", "!=", "in"})
MAX_TAG = 50
MAX_TAGS_PER_DOC = 20
# Les blancs, écrits un à un : `\s` ne veut pas dire la même chose en Python (il y met \x1c-\x1f et \x85, des contrôles) et en JavaScript
# (il y met le BOM). Le Desk a la même classe (`lib/tags.ts`) ; `tests/vectors/tag_cases.json` le vérifie des deux côtés.
_SPACES = re.compile("[ \t\n\r\f\v\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+")
# Contrôles (Cc), invisibles de format (Cf : espace sans chasse, inversion bidirectionnelle, BOM, trait d'union conditionnel), demi-codets
# (Cs) : une étiquette qui en porte ressemble à une autre sans l'être, et partagerait les filtres et les comptes en deux.
_FORBIDDEN = frozenset({"Cc", "Cf", "Cs"})


def normalize_tag(value: Any) -> str:
    """Le nom d'une étiquette : un texte en forme composée (NFC : « é » décomposé et composé font la même étiquette) de 1 à `MAX_TAG`
    caractères, blancs de bord retirés et blancs intérieurs réduits à un espace, sans virgule (le séparateur d'une saisie) ni caractère de
    contrôle ou invisible. La casse est gardée telle qu'écrite. Lève `ValueError` sinon."""
    if not isinstance(value, str):
        raise ValueError("une étiquette est un texte")
    tag = _SPACES.sub(" ", unicodedata.normalize("NFC", value)).strip(" ")
    if not tag or len(tag) > MAX_TAG:
        raise ValueError(f"une étiquette fait de 1 à {MAX_TAG} caractères")
    if "," in tag or any(unicodedata.category(c) in _FORBIDDEN for c in tag):
        raise ValueError("une étiquette ne contient ni virgule, ni caractère de contrôle ou invisible")
    return tag


def supports_tags(definition: Any) -> bool:
    """Un document unique n'a qu'un document (rien à classer), un DocType virtuel garde ses documents ailleurs (le filtre est du SQL),
    une table enfant n'a pas de documents à elle."""
    return not (definition.single or definition.istable or definition.source is not None)
