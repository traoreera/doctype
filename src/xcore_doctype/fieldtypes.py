"""Les types de champ « simples » ajoutés au palier 70 % (phase H) : date-heure, devise, texte long, note, couleur, et les formats d'un texte
(courriel, téléphone, URL) ; puis entier, pourcentage, heure, durée, code, JSON, texte enrichi et mot de passe. Un module à part, sans dépendance :
le schéma, le validateur d'écriture et les tests partagent ces règles.

Écriture STRICTE, comme partout (aucune coercion, forme canonique unique) :
- `Datetime` : `AAAA-MM-JJThh:mm:ssZ`, TOUJOURS en UTC (le Desk convertit depuis et vers l'heure locale). La forme est de largeur fixe, donc
  l'ordre des chaînes est l'ordre du temps (tri, filtres et regroupements portables, comme pour `Date`) ;
- `Currency` : un `Decimal` (chaîne exacte, 2 décimales par défaut) dont `options` peut porter le code ISO 4217 (`EUR`) pour l'affichage ;
- `Rating` : un entier de 0 à `stars` (5 par défaut) ;
- `Color` : `#rrggbb` en minuscules ;
- `LongText` : un texte brut, plusieurs lignes (jamais du HTML : le Desk l'affiche comme du texte) ;
- `format` d'un Text : `email`, `phone`, `url` (http et https seulement : jamais `javascript:` ni `data:`) ;
- `Int` : un entier (jamais un flottant, jamais un booléen), borné à ±(2^53 - 1) pour rester exact côté navigateur ;
- `Percent` : un nombre fini de 0 à 100 ;
- `Time` : `hh:mm:ss`, de 00:00:00 à 23:59:59, sans fuseau (une heure du jour : l'ouverture d'un magasin) ; l'ordre des chaînes est l'ordre du temps ;
- `Duration` : un entier de SECONDES, positif ou nul (100 ans au plus) : il s'additionne (un total d'heures) ;
- `Code` : du texte brut monospace ; `options` nomme le langage (affichage seulement) ;
- `JSON` : un objet ou un tableau JSON (profondeur 16, 100 000 octets au plus, jamais NaN ni infini) ;
- `TextEditor` : du HTML d'un sous-ensemble FERMÉ (voir `rich_text`) : toute balise ou tout attribut hors liste est refusé, jamais « nettoyé en silence » ;
- `Password` : un secret, chiffré au repos par le service (document.py) et jamais renvoyé (`********`) ; il ne se filtre, ne se trie ni ne s'agrège.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
COLOR_RE = re.compile(r"^#[0-9a-f]{6}$")
# Un courriel « raisonnable » (pas la grammaire entière de la RFC 5322, qu'aucun formulaire ne respecte) : une partie locale, un @, un domaine à point.
EMAIL_RE = re.compile(r"^[^@\s,;<>()\[\]\\\"]{1,64}@[^@\s,;<>()\[\]\\\"]{1,190}\.[^@\s,;<>()\[\]\\\"]{2,63}$")
PHONE_RE = re.compile(r"^\+?[0-9 ().\-]{4,25}$")
URL_RE = re.compile(r"^https?://[^\s<>\"']{1,2040}$", re.IGNORECASE)
CURRENCY_CODE_RE = re.compile(r"^[A-Z]{3}$")

MAX_STARS = 10
MAX_FILE_MB = 50
DEFAULT_STARS = 5
DEFAULT_CURRENCY_DECIMALS = 2
MAX_LONG_TEXT = 100_000
MAX_INT = 2**53 - 1
MAX_DURATION = 100 * 366 * 24 * 3600
MAX_JSON_BYTES = 100_000
MAX_JSON_DEPTH = 16
MAX_PASSWORD = 1000
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d:[0-5]\d$")
CODE_LANGUAGES = ("text", "python", "javascript", "json", "yaml", "html", "sql", "markdown", "shell")
TEXT_FORMATS = ("email", "phone", "url")
NOW = "now"  # défaut d'un Datetime : l'instant de la création (comme `today` pour une Date)


def iso_datetime(value: str) -> str:
    """`2026-10-18T14:30:00Z` ou `ValueError`. Une date qui n'existe pas (30 février) est refusée."""
    if not DATETIME_RE.match(value):
        raise ValueError("date-heure ISO en UTC attendue (AAAA-MM-JJThh:mm:ssZ)")
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        raise ValueError("date-heure inexistante") from None
    return value


def format_instant(moment: datetime) -> str:
    """Un instant (aware ou UTC) sous la forme canonique d'un Datetime."""
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def color(value: str) -> str:
    if not COLOR_RE.match(value):
        raise ValueError("couleur attendue : #rrggbb en minuscules")
    return value


def text_format(kind: str, value: str) -> str:
    """Le texte est au format demandé ou `ValueError` (message pour un humain). Aucune normalisation : on refuse, on ne corrige pas."""
    if kind == "email" and (len(value) > 254 or not EMAIL_RE.match(value)):
        raise ValueError("adresse e-mail attendue")
    if kind == "phone" and not (PHONE_RE.match(value) and sum(c.isdigit() for c in value) >= 4):
        raise ValueError("numéro de téléphone attendu (chiffres, espaces, + ( ) . -)")
    if kind == "url" and not URL_RE.match(value):
        raise ValueError("adresse web attendue (http:// ou https://)")
    return value


FILE_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def file_id(value: str) -> str:
    if not FILE_ID_RE.match(value):
        raise ValueError("identifiant de fichier attendu (téléversez le fichier d'abord)")
    return value


def rating(value: Any, stars: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("entier attendu")
    if not 0 <= value <= stars:
        raise ValueError(f"note de 0 à {stars} attendue")
    return value


def long_text(value: str) -> str:
    if len(value) > MAX_LONG_TEXT:
        raise ValueError(f"texte trop long ({MAX_LONG_TEXT} caractères au plus)")
    return value


def integer(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("entier attendu")
    if abs(value) > MAX_INT:
        raise ValueError(f"entier trop grand (±{MAX_INT} au plus)")
    return value


def percent(value: Any) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("nombre attendu")
    if value != value or not 0 <= value <= 100:  # `value != value` : NaN
        raise ValueError("pourcentage de 0 à 100 attendu")
    return value


def time_of_day(value: str) -> str:
    if not TIME_RE.match(value):
        raise ValueError("heure attendue : hh:mm:ss (00:00:00 à 23:59:59)")
    return value


def duration(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("durée attendue : un nombre entier de secondes")
    if not 0 <= value <= MAX_DURATION:
        raise ValueError("durée de 0 seconde à 100 ans attendue")
    return value


def code(value: str) -> str:
    return long_text(value)


def json_value(value: Any) -> Any:
    """Un objet ou un tableau JSON. Récursif : des clés textuelles, des nombres finis, 16 niveaux et 100 000 octets au plus."""
    import json

    if not isinstance(value, (dict, list)):
        raise ValueError("objet ou tableau JSON attendu")

    def walk(node: Any, depth: int) -> None:
        if depth > MAX_JSON_DEPTH:
            raise ValueError(f"JSON trop profond ({MAX_JSON_DEPTH} niveaux au plus)")
        if isinstance(node, dict):
            for k, v in node.items():
                if not isinstance(k, str):
                    raise ValueError("clés textuelles attendues")
                walk(v, depth + 1)
        elif isinstance(node, list):
            for v in node:
                walk(v, depth + 1)
        elif isinstance(node, float):
            if node != node or node in (float("inf"), float("-inf")):
                raise ValueError("NaN et infini ne sont pas du JSON")
        elif node is not None and not isinstance(node, (str, int, bool)):
            raise ValueError("valeur JSON attendue")

    walk(value, 0)
    if len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()) > MAX_JSON_BYTES:
        raise ValueError(f"JSON trop gros ({MAX_JSON_BYTES} octets au plus)")
    return value


def password(value: str) -> str:
    if not 1 <= len(value) <= MAX_PASSWORD:
        raise ValueError(f"secret de 1 à {MAX_PASSWORD} caractères attendu")
    return value


# ── Texte enrichi ──────────────────────────────────────────────────────────────────────────────
# Le HTML d'un éditeur, réduit à un sous-ensemble FERMÉ : ce que l'éditeur du Desk produit et rien d'autre. Un attribut (sauf `href` d'un lien http, https ou
# mailto), un style, un script, une image, un formulaire, un commentaire : refusés (422), jamais « nettoyés » : ce qui est stocké est ce qui est envoyé,
# et le Desk peut l'afficher tel quel sans jamais exécuter ce qu'un client a écrit.
RICH_TAGS = frozenset({"p", "br", "strong", "em", "u", "s", "code", "pre", "blockquote", "ul", "ol", "li", "h1", "h2", "h3", "h4", "hr", "a"})
RICH_VOID = frozenset({"br", "hr"})
RICH_HREF_RE = re.compile(r"^(https?://[^\s<>\"']{1,2040}|mailto:[^\s<>\"']{3,254})$", re.IGNORECASE)


def rich_text(value: str) -> str:
    from html.parser import HTMLParser

    if len(value) > MAX_LONG_TEXT:
        raise ValueError(f"texte trop long ({MAX_LONG_TEXT} caractères au plus)")
    stack: list[str] = []
    problems: list[str] = []
    seen = [0]  # balises, commentaires et déclarations rencontrés : chaque `<` brut doit en ouvrir une (sinon c'est une balise tronquée)

    class Checker(HTMLParser):
        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            seen[0] += 1
            if tag not in RICH_TAGS:
                problems.append(f"balise interdite <{tag}>")
                return
            for name, val in attrs:
                if not (tag == "a" and name == "href" and val is not None and RICH_HREF_RE.match(val)):
                    problems.append(f"attribut interdit {name!r} sur <{tag}>")
            if tag not in RICH_VOID:
                stack.append(tag)

        def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            self.handle_starttag(tag, attrs)  # compte déjà la balise
            if tag in RICH_TAGS and tag not in RICH_VOID:
                problems.append(f"balise <{tag}/> mal formée")

        def handle_endtag(self, tag: str) -> None:
            seen[0] += 1
            if tag in RICH_VOID:
                return
            if not stack or stack[-1] != tag:
                problems.append(f"balise </{tag}> sans ouverture")
            else:
                stack.pop()

        def handle_data(self, data: str) -> None:
            if "<" in data:  # un `<` brut n'est jamais du texte (il s'écrit `&lt;`) : c'est une balise tronquée, qu'un navigateur interpréterait
                problems.append("« < » brut interdit (écrivez &lt;)")

        def handle_entityref(self, name: str) -> None:
            pass

        def handle_charref(self, name: str) -> None:
            pass

        def handle_comment(self, data: str) -> None:
            seen[0] += 1
            problems.append("commentaire interdit")

        def handle_decl(self, decl: str) -> None:
            seen[0] += 1
            problems.append("déclaration interdite")

        def handle_pi(self, data: str) -> None:
            seen[0] += 1
            problems.append("instruction interdite")

        def unknown_decl(self, data: str) -> None:
            seen[0] += 1
            problems.append("section interdite")

    checker = Checker(convert_charrefs=False)
    checker.feed(value)
    checker.close()
    if value.count("<") != seen[0]:
        problems.append("balise tronquée ou « < » brut")
    if stack:
        problems.append(f"balise <{stack[-1]}> jamais fermée")
    if problems:
        raise ValueError(f"HTML refusé : {problems[0]}")
    return value
