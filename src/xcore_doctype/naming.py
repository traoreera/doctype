"""Numérotation par série (process/doctype.md D7, D13) : `FA-{YYYY}-{#####}` -> `FA-2026-00001`.

Une série est un motif : du texte littéral, des parties de date (`{YYYY}`, `{YY}`, `{MM}`, `{DD}`) et UN compteur
(`{#####}`, le nombre de `#` est la largeur minimale, complétée de zéros ; au-delà, le nombre s'allonge).

La CLÉ du compteur est le motif dont les dates sont résolues et dont le compteur est laissé en `{#}` : `FA-2026-{#}`.
Un motif qui contient `{YYYY}` repart donc de 1 chaque année, sans rien à planifier. Deux motifs différents ne partagent
jamais un compteur, même s'ils ont le même préfixe.

Ce module ne sait rien de la base : l'incrément atomique vit dans `Query.next_number`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

MAX_PATTERN = 100
MAX_WIDTH = 10
_TOKEN = re.compile(r"\{(YYYY|YY|MM|DD|#+)\}")
# Un nom sert de segment d'URL (`/doc/Invoice/FA-2026-00001`) : pas d'espace, de `/`, ni de caractère à échapper.
_LITERAL = re.compile(r"^[A-Za-z0-9_.-]*$")


@dataclass(frozen=True, slots=True)
class SeriesPattern:
    # ("lit", texte) | ("date", "YYYY"|"YY"|"MM"|"DD") | ("counter", largeur)
    parts: tuple[tuple[str, str | int], ...]

    @classmethod
    def parse(cls, source: str) -> "SeriesPattern":
        if not isinstance(source, str) or not source:
            raise ValueError("la série est vide")
        if len(source) > MAX_PATTERN:
            raise ValueError(f"la série dépasse {MAX_PATTERN} caractères")
        parts: list[tuple[str, str | int]] = []
        pos, counters = 0, 0
        for m in _TOKEN.finditer(source):
            _push_literal(parts, source[pos : m.start()])
            token = m.group(1)
            if token.startswith("#"):
                if len(token) > MAX_WIDTH:
                    raise ValueError(f"le compteur est limité à {MAX_WIDTH} # (trouvé {len(token)})")
                parts.append(("counter", len(token)))
                counters += 1
            else:
                parts.append(("date", token))
            pos = m.end()
        _push_literal(parts, source[pos:])
        if counters != 1:
            raise ValueError(f"la série doit contenir exactement un compteur {{#####}} (trouvé {counters})")
        return cls(tuple(parts))

    def key(self, now: datetime) -> str:
        """Identifie le compteur : dates résolues, compteur laissé en `{#}`."""
        return "".join("{#}" if kind == "counter" else self._text(kind, value, now) for kind, value in self.parts)

    def render(self, now: datetime, number: int) -> str:
        out = []
        for kind, value in self.parts:
            out.append(str(number).zfill(int(value)) if kind == "counter" else self._text(kind, value, now))
        return "".join(out)

    @staticmethod
    def _text(kind: str, value: str | int, now: datetime) -> str:
        if kind == "lit":
            return str(value)
        return {"YYYY": f"{now.year:04d}", "YY": f"{now.year % 100:02d}", "MM": f"{now.month:02d}", "DD": f"{now.day:02d}"}[str(value)]


def _push_literal(parts: list[tuple[str, str | int]], text: str) -> None:
    if not text:
        return
    if not _LITERAL.match(text):
        # Couvre aussi les accolades inconnues (`{foo}`, `{`) : on ne devine jamais ce que l'auteur voulait.
        raise ValueError(
            f"texte littéral invalide {text!r} (permis : lettres, chiffres, `_`, `-`, `.` ; "
            "parties de date {YYYY} {YY} {MM} {DD} ; compteur {#####})"
        )
    parts.append(("lit", text))
