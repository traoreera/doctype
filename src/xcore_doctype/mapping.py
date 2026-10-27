"""« Créer depuis » : la logique PURE du préremplissage (doc/mapper.md).

Rien ici ne touche à la base ni aux droits. Le service lui apporte le document source et, pour chaque ligne, ce
qu'il reste à faire ; ce module décide des VALEURS du brouillon. Cette séparation est le point : la recopie se
teste alors sur des dictionnaires, sans hôte ni transaction, et le service ne garde que ce qui exige la base.

Deux règles gouvernent tout :

- **Recopie par convention.** Un champ de la cible qui porte le même `fieldname` et un type compatible sur la
  source est repris. `fields:` ne sert qu'aux renommages. Pas de second YAML qui redit le premier.
- **Ce qui ne se recopie jamais** est la même liste que « Dupliquer » (`ui/src/lib/form.duplicateValues`) :
  un secret se ressaisit, un fichier appartient à UN document, un `fetch_from` est recalculé par le serveur, un
  `unique` ferait doublon, et le champ de nommage désigne un autre document.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Mapping

from .decimals import canonical, to_decimal
from .schema import DECIMAL_TYPES, CreatedFrom, DocTypeDef, FieldDef, MappingLines

# Les types dont la valeur ne traverse jamais une recopie, quelle que soit la déclaration.
_NEVER_COPIED = ("Password", "Image", "Attach", "Table")
_NUMBER_KINDS = ("Number", "Int")


def mappable_types(src: FieldDef, dst: FieldDef) -> bool:
    """La valeur de `src` sera-t-elle acceptée telle quelle par `dst` ?

    Aucune conversion n'est faite nulle part : le moteur écrit en types stricts, donc une recopie n'est permise
    que si la valeur est déjà de la forme attendue. Un `Decimal` à 2 décimales ne remplit pas un `Decimal` à 3 :
    sa chaîne canonique n'aurait pas le bon nombre de chiffres.
    """
    if src.fieldtype in _NEVER_COPIED or dst.fieldtype in _NEVER_COPIED:
        return False
    if src.fieldtype in DECIMAL_TYPES and dst.fieldtype in DECIMAL_TYPES:
        return src.decimals == dst.decimals
    if src.fieldtype != dst.fieldtype:
        return False
    if src.fieldtype in ("Link", "DynamicLink"):
        return str(src.options) == str(dst.options)
    return True


def same_number_kind(a: FieldDef, b: FieldDef) -> bool:
    """Deux champs entre lesquels une SOUSTRACTION reste exacte (le reste à faire).

    Un `Number` est un flottant, un `Decimal` une chaîne canonique : les mélanger ferait passer un montant exact
    par un double. On exige donc le même genre, et pour un `Decimal` le même nombre de décimales.
    """
    if a.fieldtype in _NUMBER_KINDS and b.fieldtype in _NUMBER_KINDS:
        return True
    return a.fieldtype in DECIMAL_TYPES and b.fieldtype in DECIMAL_TYPES and a.decimals == b.decimals


def _naming_field(d: DocTypeDef) -> str | None:
    return getattr(d.naming, "field", None) if not isinstance(d.naming, str) else None


def copied_fields(target: DocTypeDef, source: DocTypeDef, m: CreatedFrom) -> dict[str, str]:
    """`{champ de la cible: champ de la source}` : la convention, plus les exceptions déclarées.

    Les `read_only` sont écartés — un préremplissage est une saisie client, que le serveur refuserait au retour.
    Les champs de trace (`reference`, `document`) sont la seule exception, et ils ne passent pas par ici : le
    service les pose lui-même sur la ligne, puis les vérifie à l'écriture (doc/mapper.md §4).
    """
    out: dict[str, str] = {}
    secrets = set(target.secret_fields)
    naming = _naming_field(target)
    for f in target.fields:
        if f.fieldname in m.exclude or f.fieldname in secrets or f.fieldname == naming:
            continue
        if f.no_copy or f.unique or f.fetch_from is not None or f.read_only or f.fieldtype in _NEVER_COPIED:
            continue
        source_name = m.fields.get(f.fieldname, f.fieldname)
        sf = source.field_map.get(source_name)
        if sf is not None and mappable_types(sf, f):
            out[f.fieldname] = source_name
    # Une exception déclarée vaut ordre : elle passe même si la convention ne l'aurait pas prise (un `read_only`
    # de la cible reste exclu, lui, parce que le serveur le refuserait).
    for target_field, source_field in m.fields.items():
        tf, sf = target.field_map.get(target_field), source.field_map.get(source_field)
        if tf is not None and sf is not None and not tf.read_only and mappable_types(sf, tf):
            out[target_field] = source_field
    return out


def copied_row_fields(target_child: DocTypeDef, source_child: DocTypeDef, lines: MappingLines) -> dict[str, str]:
    """La même règle pour une LIGNE. Les champs de trace sont retirés : le service les pose."""
    trace = {lines.reference, lines.document} - {None}
    fake = CreatedFrom(source=source_child.name, fields=lines.fields, exclude=list(lines.exclude))
    return {t: s for t, s in copied_fields(target_child, source_child, fake).items() if t not in trace}


def remaining_quantity(ordered: Any, done: Decimal, field: FieldDef) -> Any:
    """Ce qu'il reste à faire sur une ligne, dans le TYPE du champ (chaîne canonique pour un Decimal, nombre sinon).

    Jamais négatif : si on a déjà fait plus que demandé, il ne reste rien (et non « −5 », qui préremplirait un
    mouvement en sens inverse).
    """
    left = to_decimal(ordered) - done
    if left < 0:
        left = Decimal(0)
    if field.fieldtype in DECIMAL_TYPES:
        return canonical(left, int(field.decimals or 0))
    if field.fieldtype == "Int":
        return int(left)
    return float(left)


def already_done(rows_per_document: list[list[Any]], lines: MappingLines, source_name: str) -> dict[int, Decimal]:
    """Ce qui a déjà été fait pour chaque ligne source, par INDICE : la somme des quantités cibles qui la citent.

    Reste pur : le service apporte les lignes lues en base (`Query.cited_rows`), la somme se fait ici, en `Decimal`,
    donc exacte sur toutes les bases. Une ligne dont la référence n'est pas un indice entier est ignorée plutôt que
    de faire échouer le calcul : une référence forgée ne doit pas empêcher de servir un brouillon (elle est refusée
    à l'écriture, c'est là sa place).
    """
    done: dict[int, Decimal] = {}
    if lines.quantity is None or lines.reference is None:
        return done
    for rows in rows_per_document:
        for row in rows:
            if not isinstance(row, Mapping) or (lines.document and row.get(lines.document) != source_name):
                continue
            raw = row.get(lines.reference)
            if not isinstance(raw, str) or not raw.isdigit():
                continue
            try:
                done[int(raw)] = done.get(int(raw), Decimal(0)) + to_decimal(row.get(lines.quantity.target) or 0)
            except (ArithmeticError, ValueError, TypeError):
                continue  # une quantité illisible dans une ligne déjà écrite : on ne la compte pas, on ne casse pas
    return done


def map_rows(
    target_child: DocTypeDef,
    source_child: DocTypeDef,
    lines: MappingLines,
    source_rows: list[Mapping[str, Any]],
    *,
    source_name: str,
    done: Mapping[int, Decimal] | None = None,
) -> list[dict[str, Any]]:
    """Les lignes du brouillon. `done` dit, par INDICE de ligne source, ce qui a déjà été fait.

    Une ligne entièrement servie n'est pas reprise : c'est tout l'intérêt du couple de quantités — sans lui, le
    mapper recopierait des quantités pleines et rien n'empêcherait de transférer deux fois.
    """
    copied = copied_row_fields(target_child, source_child, lines)
    qty_target = target_child.field_map.get(lines.quantity.target) if lines.quantity else None
    out: list[dict[str, Any]] = []
    for index, row in enumerate(source_rows):
        if not isinstance(row, Mapping):
            continue
        values: dict[str, Any] = {t: row[s] for t, s in copied.items() if s in row}
        if lines.quantity is not None and qty_target is not None:
            ordered = row.get(lines.quantity.source)
            if ordered is None:
                continue
            left = remaining_quantity(ordered, (done or {}).get(index, Decimal(0)), qty_target)
            if to_decimal(left) <= 0:
                continue  # ligne soldée : elle n'a rien à faire dans le brouillon
            values[lines.quantity.target] = left
        if lines.reference is not None:
            values[lines.reference] = str(index)
        if lines.document is not None:
            values[lines.document] = source_name
        out.append(values)
    return out


def map_document(
    target: DocTypeDef,
    source: DocTypeDef,
    m: CreatedFrom,
    source_doc: Mapping[str, Any],
    *,
    source_name: str,
    children: Mapping[str, tuple[DocTypeDef, DocTypeDef]],
    done: Mapping[str, Mapping[int, Decimal]] | None = None,
) -> dict[str, Any]:
    """Les valeurs du brouillon prérempli. `children` donne, par champ Table de la cible, (lignes cible, lignes source)."""
    values: dict[str, Any] = {t: source_doc[s] for t, s in copied_fields(target, source, m).items() if s in source_doc}
    for target_table, lines in m.lines.items():
        pair = children.get(target_table)
        if pair is None:
            continue
        target_child, source_child = pair
        rows = source_doc.get(lines.from_)
        values[target_table] = map_rows(
            target_child, source_child, lines, list(rows) if isinstance(rows, list) else [],
            source_name=source_name, done=(done or {}).get(target_table),
        )
    return values


def nothing_left(values: Mapping[str, Any], m: CreatedFrom) -> bool:
    """Un mapping à quantités dont toutes les tables sortent vides : la source est soldée, le bouton n'a plus lieu d'être."""
    measured = [t for t, lines in m.lines.items() if lines.quantity is not None]
    return bool(measured) and all(not values.get(t) for t in measured)
