"""Import CSV (groupe 2 du programme, élément 5, `doc/programme.md` §2.2).

Le principe : JAMAIS un chemin d'écriture parallèle. Ce module ne fait que LIRE un fichier (colonnes, cellules) et traduire chaque cellule de
texte dans la valeur que l'API attend pour son champ ; chaque ligne passe ensuite SEULE par `DocumentService.create` ou `.update`, la même
porte que le Desk et l'API (droits, restrictions, schéma strict, conditions, hooks, liens, unicité, numérotation, historique, règles de
notification). Une transaction par ligne : une ligne refusée ne retient pas les autres, et le rapport dit, ligne par ligne, ce qui est passé
et pourquoi le reste a été refusé (comme l'action en lot). Bornes : `MAX_ROWS` lignes et `MAX_CHARS` caractères par import.

- **Colonnes** : le nom du champ ou son libellé (sans tenir compte de la casse) ; sinon `name` (ou « Nom », l'en-tête de l'export du Desk)
  désigne le document : obligatoire pour mettre à jour, permis à la création d'un type `naming: prompt` seulement ; les colonnes système
  d'un export (propriétaire, dates, statut) sont ignorées : un fichier exporté se réimporte tel quel en mise à jour. Une colonne inconnue, en double, ou d'un champ qui ne s'importe
  pas (table, mot de passe, fichier, champ calculé `fetch_from`) refuse TOUT l'import avant la première ligne : rien n'est à moitié compris.
- **Cellules** : du texte, converti selon le champ (`cell_value`) ; une cellule vide n'est pas envoyée (à la création le défaut s'applique,
  à la mise à jour la valeur enregistrée reste). Un nombre français (`1 234,5`) et une date française (`31/12/2026`) sont acceptés ; rien
  n'est arrondi ni deviné au-delà (un décimal de trop est refusé comme par l'API).
"""

from __future__ import annotations

import csv
import io
import json
import math
import re
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

from .decimals import canonical
from .errors import DoctypeError, ValidationFailed
from .schema import DECIMAL_TYPES, FILE_TYPES, FieldDef

if TYPE_CHECKING:
    from .context import RequestContext
    from .document import DocumentService

MAX_ROWS = 1000
MAX_CHARS = 2_000_000
MODES = ("insert", "update")
NAME_COLUMNS = ("name", "nom")  # « Nom » : l'en-tête que l'export du Desk donne au nom du document
# Les colonnes système qu'un export du Desk contient (nom technique ou libellé) : lues, jamais écrites, donc ignorées à l'import.
SYSTEM_COLUMNS = frozenset({"owner", "creation", "modified", "docstatus", "propriétaire", "création", "modification", "statut"})
NAME, SKIP = "name", "skip"
_NEUTRALISED = re.compile(r"^'[=+\-@\t\r]")  # l'export du Desk désamorce une formule par une apostrophe : on la retire au retour
NOT_IMPORTED = ("Table", "Password", *FILE_TYPES)
_SPACES = re.compile(r"[\s\u00a0\u202f]")
_FR_DATE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")
_TIME = re.compile(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?$")
TRUE_WORDS = frozenset({"1", "oui", "vrai", "true", "yes", "o", "y", "x"})
FALSE_WORDS = frozenset({"0", "non", "faux", "false", "no", "n"})


# ── Lire le fichier ─────────────────────────────────────────────────────────────────────────────


def read_csv(content: str) -> tuple[list[str], list[list[str]]]:
    """L'en-tête et les lignes. Le séparateur est celui de l'en-tête (`,`, `;` — celui d'Excel en français — ou tabulation) ; le BOM qu'Excel
    ajoute est retiré ; une ligne entièrement vide est ignorée."""
    if not isinstance(content, str):
        raise ValidationFailed("content : le texte du fichier CSV", code="csv_invalid")
    if len(content) > MAX_CHARS:
        raise ValidationFailed(f"Fichier trop gros : {MAX_CHARS // 1_000_000} millions de caractères au plus par import", code="csv_too_large")
    content = content.lstrip("\ufeff")
    first = content.split("\n", 1)[0]
    delimiter = max((";", ",", "\t"), key=first.count) if any(d in first for d in ";,\t") else ","
    try:
        records = [r for r in csv.reader(io.StringIO(content, newline=""), delimiter=delimiter) if any(c.strip() for c in r)]
    except csv.Error as e:
        raise ValidationFailed(f"CSV illisible : {e}", code="csv_invalid") from None
    if not records:
        raise ValidationFailed("Le fichier est vide : il faut au moins un en-tête et une ligne", code="csv_empty")
    header, rows = [c.strip() for c in records[0]], records[1:]
    if not rows:
        raise ValidationFailed("Le fichier n'a que son en-tête : aucune ligne à importer", code="csv_empty")
    if len(rows) > MAX_ROWS:
        raise ValidationFailed(f"{len(rows)} lignes : {MAX_ROWS} au plus par import (découpez le fichier)", code="csv_too_large")
    return header, rows


def map_columns(definition: Any, header: list[str], mode: str) -> list[FieldDef | str]:
    """Chaque colonne -> son champ, `NAME` (le nom du document) ou `SKIP` (une colonne système, ignorée). Un champ passe avant tout. Tout problème d'en-tête refuse l'import entier, colonne par colonne."""
    by_name = {f.fieldname: f for f in definition.fields}
    by_label: dict[str, list[FieldDef]] = {}
    for f in definition.fields:
        if f.label:
            by_label.setdefault(f.label.casefold(), []).append(f)
    out: list[FieldDef | str] = []
    seen: set[str] = set()
    seen_names: set[str] = set()
    errors: list[dict[str, str]] = []
    for i, raw in enumerate(header):
        column = raw.strip()
        where = f"colonne {i + 1} ({column or 'sans titre'})"
        found = [by_name[column]] if column in by_name else by_label.get(column.casefold(), [])
        if column.casefold() in NAME_COLUMNS and [c.strip().casefold() for c in header].count(column.casefold()) > 1 and column.casefold() not in seen_names:
            found = []  # « Nom » deux fois (le nom du document puis un champ libellé « Nom », comme l'export l'écrit) : le premier est le nom
        seen_names.add(column.casefold())
        if len(found) > 1:
            errors.append({"field": where, "msg": "Ce libellé désigne plusieurs champs : utilisez le nom du champ", "type": "csv_column"})
            out.append(SKIP)
            continue
        if found:
            field = found[0]
            key = field.fieldname
            if field.fieldtype in NOT_IMPORTED or field.fetch_from:
                errors.append({"field": where, "msg": "Ce champ ne s'importe pas (table, mot de passe, fichier ou valeur copiée d'un autre document)", "type": "csv_column"})
        elif column.casefold() in NAME_COLUMNS:
            key = NAME
        elif column.casefold() in SYSTEM_COLUMNS:
            out.append(SKIP)
            continue
        else:
            errors.append({"field": where, "msg": "Aucun champ de ce nom ni de ce libellé", "type": "csv_column"})
            out.append(SKIP)
            continue
        if key in seen:
            errors.append({"field": where, "msg": "Colonne en double", "type": "csv_column"})
        seen.add(key)
        out.append(found[0] if found else NAME)
    if "name" in seen and mode == "insert" and definition.naming != "prompt":
        errors.append({"field": "name", "msg": "Ce type nomme lui-même ses documents : retirez la colonne du nom pour créer", "type": "csv_column"})
    if "name" not in seen and mode == "update":
        errors.append({"field": "name", "msg": "Mettre à jour exige une colonne « name » (le document visé)", "type": "csv_column"})
    if errors:
        raise ValidationFailed("L'en-tête du fichier ne correspond pas au type", code="csv_columns", details=errors)
    return out


# ── Une cellule ─────────────────────────────────────────────────────────────────────────────────


def _number_text(text: str) -> str:
    """`1 234,5` -> `1234.5` : les espaces (même insécables) sont des séparateurs de milliers, la virgule française est la décimale."""
    cleaned = _SPACES.sub("", text)
    if "," in cleaned and "." in cleaned:
        raise ValueError("nombre ambigu (virgule ET point) : écrivez 1234.5 ou 1234,5")
    return cleaned.replace(",", ".")


def cell_value(field: FieldDef, text: str) -> Any:
    """La valeur API d'une cellule de texte non vide, ou `ValueError` (message pour un humain). Le service revalide tout ensuite."""
    kind = field.fieldtype
    if kind == "Duration":
        clock = re.fullmatch(r"(\d+):([0-5]\d)(?::([0-5]\d))?", text.strip())  # « 1:30:00 », la forme de l'export
        if clock:
            return int(clock[1]) * 3600 + int(clock[2]) * 60 + int(clock[3] or 0)
    if kind in ("Int", "Rating", "Duration"):
        cleaned = _number_text(text)
        if not re.fullmatch(r"-?\d+", cleaned):
            raise ValueError("durée attendue (secondes ou h:mm:ss)" if kind == "Duration" else "nombre entier attendu")
        return int(cleaned)
    if kind in ("Number", "Percent"):
        cleaned = _number_text(text)
        if re.fullmatch(r"-?\d+", cleaned):
            return int(cleaned)
        if not re.fullmatch(r"-?(\d+\.?\d*|\.\d+)", cleaned):
            raise ValueError("nombre attendu")
        number = float(cleaned)
        if not math.isfinite(number):
            raise ValueError("nombre fini attendu")
        return number
    if kind in DECIMAL_TYPES:
        return canonical(_number_text(text), field.decimals or 0)
    if kind == "Boolean":
        word = text.strip().casefold()
        if word in TRUE_WORDS:
            return True
        if word in FALSE_WORDS:
            return False
        raise ValueError("oui ou non attendu (oui/non, vrai/faux, 1/0)")
    if kind == "Date":
        value = text.strip()
        french = _FR_DATE.match(value)
        try:
            return date(int(french[3]), int(french[2]), int(french[1])).isoformat() if french else date.fromisoformat(value).isoformat()
        except ValueError:
            raise ValueError("date attendue (AAAA-MM-JJ ou JJ/MM/AAAA)") from None
    if kind == "Datetime":
        try:
            moment = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
        except ValueError:
            raise ValueError("date-heure ISO attendue (AAAA-MM-JJThh:mm:ssZ)") from None
        if moment.tzinfo is None:
            raise ValueError("précisez le fuseau (Z pour UTC, ou +01:00…)")
        return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    if kind == "Time":
        found = _TIME.match(text.strip())
        if not found:
            raise ValueError("heure attendue (hh:mm ou hh:mm:ss)")
        return f"{int(found[1]):02d}:{found[2]}:{found[3] or '00'}"
    if kind == "JSON":
        try:
            return json.loads(text)
        except ValueError:
            raise ValueError("JSON valide attendu") from None
    if kind == "Color":
        return text.strip().lower()
    text = text.strip()
    return text[1:] if _NEUTRALISED.match(text) else text  # Text, LongText, Select, Link, DynamicLink, Code, TextEditor : le texte tel quel (bords retirés)


def row_data(columns: list[FieldDef | str], row: list[str]) -> tuple[str | None, dict[str, Any], list[dict[str, str]]]:
    """(nom, données, erreurs) d'une ligne. Une cellule vide n'est pas envoyée ; une ligne plus longue que l'en-tête est une erreur."""
    name: str | None = None
    data: dict[str, Any] = {}
    errors: list[dict[str, str]] = []
    if len(row) > len(columns):
        errors.append({"field": "", "msg": f"{len(row)} cellules pour {len(columns)} colonnes", "type": "csv_row"})
    for field, cell in zip(columns, row):
        if not cell.strip() or field == SKIP:
            continue
        if field == NAME:
            name = cell.strip()
            continue
        assert isinstance(field, FieldDef)
        try:
            data[field.fieldname] = cell_value(field, cell)
        except ValueError as e:
            errors.append({"field": field.fieldname, "msg": str(e), "type": "csv_value"})
    return name, data, errors


# ── L'import ────────────────────────────────────────────────────────────────────────────────────


async def import_csv(documents: "DocumentService", ctx: "RequestContext", doctype: str, content: Any, mode: Any = "insert") -> dict[str, Any]:
    """Crée (`insert`) ou met à jour (`update`) un document par ligne, chacun par la porte ordinaire. Le droit de créer (ou d'écrire) le type est
    exigé UNE fois d'abord. Réponse : `{results: [{row, ok, name | code, message, details}], done, failed}` ; `row` est le numéro de la ligne
    dans le fichier, en-tête compris (la première ligne de données est la 2)."""
    documents._enter(ctx, "doc.import", doctype)
    if mode not in MODES:
        raise ValidationFailed(f"mode : {' ou '.join(MODES)}", code="csv_mode")
    compiled = documents._doctype(doctype)
    definition = compiled.definition
    if definition.single:
        raise ValidationFailed(f"{doctype} est un document unique : rien à importer", code="single_document")
    documents._scope(ctx, compiled, "create" if mode == "insert" else "write")
    header, rows = read_csv(content)
    columns = map_columns(definition, header, mode)
    results: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        number = index + 2
        name, data, errors = row_data(columns, row)
        if errors:
            results.append({"row": number, "ok": False, "code": "invalid_value", "message": "Valeur illisible", "details": errors})
            continue
        try:
            if mode == "insert":
                made = await documents.create(ctx, doctype, data, name=name)
            else:
                current = await documents.get(ctx, doctype, name or "")
                made = await documents.update(ctx, doctype, current["name"], {**current["data"], **data}, current["version"])
            results.append({"row": number, "ok": True, "name": made["name"]})
        except DoctypeError as e:
            results.append({"row": number, "ok": False, "code": e.code, "message": e.message, **({"details": e.details} if e.details else {})})
        except Exception as e:  # noqa: BLE001 - une ligne qui échoue pour une raison interne ne fait pas perdre le rapport des autres
            documents._log.error("import row failed", doctype=doctype, row=number, error=repr(e))
            results.append({"row": number, "ok": False, "code": "internal_error", "message": "Erreur interne"})
    done = sum(1 for r in results if r["ok"])
    return {"results": results, "done": done, "failed": len(results) - done}
