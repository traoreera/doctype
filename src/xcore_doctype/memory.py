"""Filtres, tri, recherche et agrégats EN MÉMOIRE, pour les DocTypes virtuels (voir sources.py).

La table générique fait tout cela en SQL (`query.py`). Une source externe rend ses documents tels quels ; ce module applique les MÊMES règles sur eux :
les opérateurs permis par type (`FILTER_OPS`), `!=` qui retient aussi ce qui n'a pas de valeur, `like` qui cherche sans casse, `in` vide qui ne retient rien,
les valeurs vides toujours en dernier au tri et `name` pour départager, les groupes les plus gros d'abord, les périodes de temps découpées sur la chaîne ISO.
`tests/unit/test_memory_parity.py` compare les deux interprétations sur les mêmes documents : changer l'une sans l'autre le fait échouer.

Le périmètre de lecture s'applique AVANT tout (`Scope.allows`, l'interpréteur Python du même `Scope` que la requête SQL) : on ne trie, ne compte ni ne
cherche jamais que ce que l'appelant pourrait ouvrir.
"""

from __future__ import annotations

from datetime import UTC
from decimal import Decimal
from typing import Any, Mapping, Sequence

from .aggregation import BUCKET_WIDTH, SYSTEM_CATEGORIES, SYSTEM_TIMES, Group, Metric
from .builder import SYSTEM_FILTERS, Filter
from .errors import ValidationFailed
from .permissions import Scope
from .query import SYSTEM_SORTABLE, StoredDoc
from .schema import DECIMAL_TYPES, DocTypeDef

_STRFTIME = {"year": "%Y", "month": "%Y-%m", "day": "%Y-%m-%d"}


def visible(docs: Sequence[StoredDoc], scope: Scope) -> list[StoredDoc]:
    """Ce que `scope` permet de lire."""
    return [d for d in docs if scope.allows(d.owner, d.data)]


def _value(defn: DocTypeDef, doc: StoredDoc, fieldname: str) -> Any:
    """La valeur d'un champ comme la requête SQL la lirait (`as_float`, `as_numeric`, `as_boolean`, `as_string`)."""
    if fieldname == "name":
        return doc.name
    if fieldname == "docstatus":
        return doc.docstatus
    if fieldname in ("owner", "creation", "modified"):
        return getattr(doc, fieldname)
    raw = doc.data.get(fieldname)
    if raw is None:
        return None
    match defn.field_map[fieldname].fieldtype:
        case "Number" | "Rating" | "Percent":
            return float(raw)
        case "Int" | "Duration":
            return int(raw)
        case "Decimal" | "Currency":
            return Decimal(str(raw))
        case "Boolean":
            return bool(raw)
        case _:
            return str(raw)


def _typed(ftype: str, value: Any) -> Any:
    """Un filtre Decimal arrive en chaîne canonique mais se compare en nombre exact (comme `query._typed`)."""
    if ftype not in DECIMAL_TYPES or value is None:
        return value
    if isinstance(value, Filter):
        return Filter(value.op, [Decimal(v) for v in value.value] if value.op == "in" else Decimal(value.value))
    return Decimal(value)


def _matches(actual: Any, f: Filter) -> bool:
    v = f.value
    match f.op:
        case "!=":
            return actual is None or actual != v  # un document sans valeur est « différent »
        case "like":
            return actual is not None and v.lower() in str(actual).lower()
        case "in":
            return actual is not None and actual in v
    if actual is None:
        return False  # une comparaison avec « rien » ne retient jamais
    match f.op:
        case ">":
            return actual > v
        case ">=":
            return actual >= v
        case "<":
            return actual < v
        case "<=":
            return actual <= v
    raise ValidationFailed(f"opérateur inconnu {f.op!r}")  # pragma: no cover


def select(defn: DocTypeDef, docs: Sequence[StoredDoc], filters: Mapping[str, Any]) -> list[StoredDoc]:
    """Les documents qui passent TOUS les filtres (déjà validés par type, voir `CompiledDocType.coerce_filter`)."""
    checks: list[tuple[str, Any]] = []
    for fieldname, value in filters.items():
        if fieldname not in SYSTEM_FILTERS and fieldname not in defn.field_map:
            raise ValidationFailed(f"filtre sur un champ inconnu : {fieldname!r}")
        ftype = SYSTEM_FILTERS[fieldname] if fieldname in SYSTEM_FILTERS else defn.field_map[fieldname].fieldtype
        checks.append((fieldname, _typed(ftype, value)))
    out = []
    for doc in docs:
        for fieldname, value in checks:
            actual = _value(defn, doc, fieldname)
            if isinstance(value, Filter):
                ok = _matches(actual, value)
            else:
                ok = actual is None if value is None else actual == value
            if not ok:
                break
        else:
            out.append(doc)
    return out


def order(defn: DocTypeDef, docs: Sequence[StoredDoc], order_by: str, descending: bool) -> list[StoredDoc]:
    """`ORDER BY valeur [DESC] NULLS LAST, name ASC` : les valeurs vides en dernier, `name` pour départager (pagination stable)."""
    if order_by not in SYSTEM_SORTABLE and order_by not in defn.field_map:
        raise ValidationFailed(f"tri impossible sur {order_by!r}")
    ordered = sorted(docs, key=lambda d: d.name)  # tri STABLE : l'ordre des ex æquo est celui des noms
    present = [d for d in ordered if _value(defn, d, order_by) is not None]
    empty = [d for d in ordered if _value(defn, d, order_by) is None]
    present.sort(key=lambda d: _value(defn, d, order_by), reverse=descending)  # `reverse` garde la stabilité
    return present + empty


def page(docs: Sequence[StoredDoc], limit: int, offset: int) -> list[StoredDoc]:
    return list(docs[offset : offset + limit])


def search(defn: DocTypeDef, docs: Sequence[StoredDoc], q: str, limit: int) -> list[StoredDoc]:
    """Le nom ou le `title_field` CONTIENT `q` (sans casse), le titre d'abord (vides en dernier), `name` pour départager."""
    needle = q.lower()
    title = defn.title_field
    hits = [
        d for d in docs
        if needle in d.name.lower() or (title is not None and d.data.get(title) is not None and needle in str(d.data[title]).lower())
    ]
    hits.sort(key=lambda d: d.name)
    if title is not None:
        with_title = sorted((d for d in hits if d.data.get(title) is not None), key=lambda d: str(d.data[title]))
        hits = with_title + [d for d in hits if d.data.get(title) is None]
    return hits[:limit]


# ── Agrégats ──────────────────────────────────────────────────────────────────────────────────


def _group_key(defn: DocTypeDef, doc: StoredDoc, group: Group) -> Any:
    if group.field in SYSTEM_CATEGORIES:
        return getattr(doc, group.field)
    if group.field in SYSTEM_TIMES:
        # Un instant système se découpe en UTC (`creation`, `modified`), comme la requête SQL.
        return getattr(doc, group.field).astimezone(UTC).strftime(_STRFTIME[group.bucket or "month"])
    raw = doc.data.get(group.field)
    if raw is None:
        return None
    match defn.field_map[group.field].fieldtype:
        case "Date" | "Datetime":
            return str(raw)[: BUCKET_WIDTH[group.bucket or "month"]]
        case "Boolean":
            return bool(raw)
        case _:
            return str(raw)


def _measure(defn: DocTypeDef, docs: Sequence[StoredDoc], m: Metric) -> Any:
    if m.function == "count":
        return len(docs)
    values = [v for v in (_value(defn, d, str(m.field)) for d in docs) if v is not None]
    if not values:
        return None
    match m.function:
        case "sum":
            return sum(values[1:], values[0])
        case "avg":
            return sum(values[1:], values[0]) / len(values)
        case "min":
            return min(values)
        case "max":
            return max(values)
    raise ValidationFailed(f"fonction inconnue {m.function!r}")  # pragma: no cover


def aggregate(
    defn: DocTypeDef,
    docs: Sequence[StoredDoc],
    *,
    group: Group | None,
    metrics: Sequence[Metric],
    keys: Sequence[str] | None,
    limit: int,
) -> tuple[list[tuple[Any, ...]], bool]:
    """Même contrat que `Query.aggregate` : `(lignes, tronqué)`, une ligne étant `(clé, *mesures)` avec un regroupement, `(*mesures)` sans."""
    if group is None:
        return [tuple(_measure(defn, docs, m) for m in metrics)], False
    buckets: dict[Any, list[StoredDoc]] = {}
    for doc in docs:
        key = _group_key(defn, doc, group)
        if group.is_time:
            if key is None or (keys and not keys[0] <= key <= keys[-1]):
                continue  # un document sans date n'a pas de place sur une ligne de temps ; une période hors fenêtre non plus
        buckets.setdefault(key, []).append(doc)
    rows = [(key, *(_measure(defn, members, m) for m in metrics)) for key, members in buckets.items()]
    if group.is_time:
        rows.sort(key=lambda r: r[0], reverse=True)  # tronquer garde les périodes les plus récentes
    else:
        rows.sort(key=lambda r: (r[0] is None, r[0]))  # clé croissante, vide en dernier (le tri est stable)
        present = [r for r in rows if r[1] is not None]
        empty = [r for r in rows if r[1] is None]
        present.sort(key=lambda r: r[1], reverse=True)  # les plus gros groupes d'abord
        rows = present + empty
    return rows[:limit], len(rows) > limit
