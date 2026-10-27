"""Champs `Decimal` : des quantités et des montants EXACTS (palier 40 %, phase A, process/palier40.md §4.3).

Un `Number` est un flottant : `0.1 + 0.2` ne vaut pas `0.3`, et un stock qui additionne des dixièmes dérive. Un `Decimal` est stocké dans
le document comme une CHAÎNE canonique à `decimals` chiffres après la virgule (`"12.500"`), jamais comme un nombre JSON que n'importe quel
lecteur transformerait en flottant. Le code qui calcule (un hook de stock) lit `decimal.Decimal(valeur)`.

Écriture stricte, comme partout (pas de coercion, pas d'arrondi silencieux) :
- une chaîne `"12.5"` ou un entier `12` (ou un `decimal.Decimal`, côté code Python) ; un flottant JSON (`12.5`) est REFUSÉ : il est déjà inexact avant d'arriver ici ;
- au plus `decimals` chiffres significatifs après la virgule (`"0.0005"` pour 3 décimales est refusé, pas arrondi) ; des zéros en trop
  (`"1.50000"`) ne changent pas la valeur et sont acceptés ;
- 18 chiffres au plus (l'équivalent de `NUMERIC(18, d)`) ; ni exposant, ni espace, ni `+`, ni NaN / Infinity.
La forme canonique est unique : deux valeurs égales sont deux chaînes égales (utile à l'historique, aux comparaisons de documents).
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

MAX_DIGITS = 18
MAX_DECIMALS = 9
_PATTERN = re.compile(r"^-?(0|[1-9][0-9]*)(\.[0-9]+)?$")


def canonical(value: Any, decimals: int) -> str:
    """La forme canonique de `value` avec `decimals` décimales, ou `ValueError` (message destiné à l'utilisateur)."""
    if isinstance(value, Decimal):  # le code d'un hook calcule en Decimal ; JSON, lui, n'en livre jamais
        if not value.is_finite():
            raise ValueError("décimal fini attendu (NaN et Infinity sont refusés)")
        value = format(value, "f")
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError("décimal attendu : une chaîne (« 12.5 ») ou un entier (un nombre à virgule est inexact, il est refusé)")
    text = str(value)
    if not _PATTERN.match(text):
        raise ValueError("décimal attendu (ex. « 12.5 ») : sans exposant, sans espace, sans « + »")
    whole, _, fraction = text.lstrip("-").partition(".")
    if len(fraction.rstrip("0")) > decimals:
        raise ValueError(f"au plus {decimals} décimale{'s' if decimals > 1 else ''}")
    if len(whole.lstrip("0")) + decimals > MAX_DIGITS:
        raise ValueError(f"valeur trop grande ({MAX_DIGITS} chiffres au plus, décimales comprises)")
    exact = Decimal(text).quantize(Decimal(1).scaleb(-decimals))  # sans perte : les chiffres en trop sont des zéros
    if exact == 0:
        exact = abs(exact)  # « -0.000 » n'est pas une autre valeur que « 0.000 »
    return format(exact, "f")


def to_decimal(stored: Any) -> Decimal:
    """Lit la valeur stockée d'un champ Decimal (une chaîne canonique)."""
    return Decimal(stored)
