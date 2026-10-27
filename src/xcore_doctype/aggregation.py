"""Agrégats : compter, additionner, regrouper (palier 70 %, phases I et J, `doc/palier70.md`).

C'est ce qui alimente les cartes de nombres, les graphiques, la vue Rapport (regroupements, totaux) et le tableau de bord d'un type
de document. Une requête d'agrégat se décrit par :

- un REGROUPEMENT (`Group`) : un champ Select, Link, Boolean ou Date (avec un `bucket` : `day`, `month`, `year`), ou une colonne système
  (`docstatus`, `owner`, et `creation` / `modified` avec un `bucket`, lus en UTC) ; ou aucun, et le résultat est une ligne unique ;
- des MESURES (`Metric`) : `count`, ou `sum` / `avg` / `min` / `max` d'un champ Number ou Decimal ;
- des filtres typés, EXACTEMENT ceux de la liste (`CompiledDocType.coerce_filter`) ;
- une fenêtre de temps (`periods`) pour un regroupement par date : les N derniers jours, mois ou années, l'actuel compris.

Rien ici ne construit de SQL (`query.py` est le seul à le faire) : ce module valide, calcule la fenêtre, comble les périodes vides et
remet les valeurs sous leur forme canonique (un Decimal reste une chaîne exacte, jamais un flottant). La même validation sert au
chargement d'un tableau de bord (`resolve` sur la définition, une faute est une erreur de chargement) et à chaque requête.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, tzinfo
from decimal import ROUND_HALF_EVEN, Decimal
from typing import TYPE_CHECKING, Any, Mapping, Sequence

from .decimals import canonical
from .errors import ValidationFailed
from .tags import TAG_FILTER, supports_tags

if TYPE_CHECKING:  # pragma: no cover - schema importe dashboards, qui importe ce module
    from .schema import DocTypeDef

FUNCTIONS = ("count", "sum", "avg", "min", "max")
BUCKETS = ("day", "month", "year")
# Types de champ sur lesquels on regroupe : des valeurs en nombre limité (un texte libre donnerait un groupe par document).
GROUP_TYPES = ("Select", "Link", "Boolean", "Date", "Datetime")
METRIC_TYPES = ("Number", "Decimal", "Currency", "Rating", "Int", "Percent", "Duration")
# Colonnes système : `docstatus` et `owner` sont des catégories ; `creation` et `modified` sont des instants (UTC), donc à découper.
SYSTEM_CATEGORIES = ("docstatus", "owner")
SYSTEM_TIMES = ("creation", "modified")

MAX_GROUPS = 400
DEFAULT_GROUPS = 50
# Combien de périodes au plus, selon le découpage : un an de jours, dix ans de mois, cinquante ans d'années.
MAX_PERIODS = {"day": 366, "month": 120, "year": 50}
BUCKET_WIDTH = {"year": 4, "month": 7, "day": 10}  # « 2026 », « 2026-10 », « 2026-10-17 » : l'ordre des chaînes est l'ordre du temps


@dataclass(frozen=True, slots=True)
class Metric:
    function: str
    field: str | None = None  # `count` n'en a pas ; les autres exigent un champ Number ou Decimal

    @classmethod
    def parse(cls, text: Any) -> "Metric":
        """`count`, `sum:qty`. Une autre forme est refusée telle quelle (jamais devinée)."""
        if not isinstance(text, str):
            raise ValidationFailed("une mesure s'écrit « count » ou « fonction:champ » (ex. sum:qty)")
        function, sep, field = text.partition(":")
        if function not in FUNCTIONS or bool(sep) != (function != "count") or (sep and not field):
            raise ValidationFailed(f"mesure invalide {text!r} : « count » ou « sum|avg|min|max:champ »")
        return cls(function, field or None)

    def __str__(self) -> str:
        return self.function if self.field is None else f"{self.function}:{self.field}"


@dataclass(frozen=True, slots=True)
class Group:
    field: str
    bucket: str | None = None  # seulement pour une date ou un instant

    @property
    def is_time(self) -> bool:
        return self.bucket is not None


@dataclass(frozen=True, slots=True)
class AggSpec:
    group: Group | None
    metrics: tuple[Metric, ...]
    periods: int | None
    limit: int


def resolve(
    defn: DocTypeDef,
    *,
    group_by: str | None = None,
    bucket: str | None = None,
    metrics: Sequence[Metric | str] | None = None,
    periods: int | None = None,
    limit: int = DEFAULT_GROUPS,
) -> AggSpec:
    """Valide une demande d'agrégat contre la définition du DocType, ou lève `ValidationFailed` (message pour un humain)."""
    fields = defn.field_map
    parsed = tuple(m if isinstance(m, Metric) else Metric.parse(m) for m in (metrics or ["count"]))
    if not 1 <= len(parsed) <= 8:
        raise ValidationFailed("de 1 à 8 mesures")
    if len(set(parsed)) != len(parsed):
        raise ValidationFailed("une mesure est demandée deux fois")
    for m in parsed:
        if m.function == "count":
            continue
        target = fields.get(m.field or "")
        if target is None:
            raise ValidationFailed(f"{m}: champ inconnu {m.field!r}")
        if target.fieldtype not in METRIC_TYPES:
            raise ValidationFailed(f"{m}: {m.field!r} est un {target.fieldtype}, seul un Number, un Decimal, une Currency, un Int, un Percent, une Duration ou un Rating s'additionne ou se moyenne")

    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_GROUPS:
        raise ValidationFailed(f"limit doit être entre 1 et {MAX_GROUPS}")

    group: Group | None = None
    if group_by is None:
        if bucket is not None or periods is not None:
            raise ValidationFailed("`bucket` et `periods` exigent un regroupement (`group_by`)")
    else:
        if group_by in SYSTEM_CATEGORIES:
            kind = "category"
        elif group_by in SYSTEM_TIMES:
            kind = "time"
        else:
            target = fields.get(group_by)
            if target is None:
                raise ValidationFailed(f"regroupement sur un champ inconnu : {group_by!r}")
            if target.fieldtype not in GROUP_TYPES:
                raise ValidationFailed(
                    f"regroupement impossible sur {group_by!r} ({target.fieldtype}) : "
                    f"seulement {', '.join(GROUP_TYPES)}, des valeurs en nombre limité"
                )
            kind = "time" if target.fieldtype in ("Date", "Datetime") else "category"
        if kind == "time":
            bucket = bucket or "month"
            if bucket not in BUCKETS:
                raise ValidationFailed(f"bucket inconnu {bucket!r} (permis : {', '.join(BUCKETS)})")
        elif bucket is not None:
            raise ValidationFailed(f"`bucket` n'a de sens que pour une date : {group_by!r} se regroupe par valeur")
        group = Group(group_by, bucket)

    if periods is not None:
        if group is None or not group.is_time:
            raise ValidationFailed("`periods` n'a de sens que pour un regroupement par date")
        cap = MAX_PERIODS[group.bucket or "month"]
        if isinstance(periods, bool) or not isinstance(periods, int) or not 1 <= periods <= cap:
            raise ValidationFailed(f"periods doit être entre 1 et {cap} (découpage {group.bucket})")
    # Une fenêtre de N périodes rend N lignes : un `limit` plus petit en retrancherait les plus anciennes sans le dire.
    return AggSpec(group, parsed, periods, max(limit, periods or 0))


# ── Fenêtre de temps ──────────────────────────────────────────────────────────────────────────


def _shift_months(year: int, month: int, delta: int) -> tuple[int, int]:
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1


def bucket_keys(bucket: str, today: date, periods: int) -> list[str]:
    """Les `periods` découpages qui se terminent par celui d'aujourd'hui, du plus ancien au plus récent."""
    if bucket == "day":
        return [(today - timedelta(days=n)).isoformat() for n in range(periods - 1, -1, -1)]
    if bucket == "month":
        return ["%04d-%02d" % _shift_months(today.year, today.month, -n) for n in range(periods - 1, -1, -1)]
    return [f"{today.year - n:04d}" for n in range(periods - 1, -1, -1)]


def window(spec: AggSpec, *, now: datetime, tz: tzinfo) -> list[str] | None:
    """Les clés de la fenêtre demandée (`None` : pas de fenêtre). Une colonne système se découpe en UTC, une date dans le fuseau de
    l'organisation : « ce mois » est celui du bureau, pas celui d'un serveur à l'autre bout du monde."""
    if spec.group is None or spec.periods is None:
        return None
    zone = UTC if spec.group.field in SYSTEM_TIMES else tz
    return bucket_keys(spec.group.bucket or "month", now.astimezone(zone).date(), spec.periods)


# ── Résultat ──────────────────────────────────────────────────────────────────────────────────


def _metric_decimals(defn: DocTypeDef, m: Metric) -> int | None:
    field = defn.field_map.get(m.field or "")
    return field.decimals if field is not None and field.fieldtype in ("Decimal", "Currency") else None


def _finish(value: Any, m: Metric, decimals: int | None) -> Any:
    """La valeur d'une mesure sous sa forme d'API : un entier pour `count`, une chaîne exacte pour un Decimal, un nombre sinon."""
    if m.function == "count":
        return int(value or 0)
    if value is None:
        return None if m.function != "sum" else (canonical(0, decimals) if decimals is not None else 0)
    if decimals is not None:
        exact = value if isinstance(value, Decimal) else Decimal(str(value))  # SQLite additionne en double : on rend la valeur lisible
        return canonical(exact.quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_EVEN), decimals)
    number = float(value)
    return int(number) if number.is_integer() and abs(number) < 2**53 else number


def _zero_row(spec: AggSpec, defn: DocTypeDef) -> list[Any]:
    return [_finish(0 if m.function in ("count", "sum") else None, m, _metric_decimals(defn, m)) for m in spec.metrics]


def describe(defn: DocTypeDef, spec: AggSpec) -> dict[str, Any]:
    """Ce dont un client a besoin pour lire les lignes sans connaître le DocType : le type du regroupement et les décimales des mesures."""
    group: dict[str, Any] | None = None
    if spec.group is not None:
        field = defn.field_map.get(spec.group.field)
        group = {"field": spec.group.field, "bucket": spec.group.bucket, "fieldtype": field.fieldtype if field else None}
        if field is not None and field.fieldtype in ("Select", "Link"):
            group["options"] = field.options
        if field is not None:
            group["label"] = field.label or field.fieldname
    metrics = []
    for m in spec.metrics:
        field = defn.field_map.get(m.field or "")
        metrics.append(
            {"function": m.function, "field": m.field, "label": (field.label or field.fieldname) if field else None,
             "decimals": _metric_decimals(defn, m)}
        )
    return {"group": group, "metrics": metrics}


def result(defn: DocTypeDef, spec: AggSpec, rows: Sequence[Sequence[Any]], keys: list[str] | None, *, truncated: bool) -> dict[str, Any]:
    """Assemble la réponse. `rows` : `(clé, *valeurs)` par groupe, ou `(*valeurs)` sans regroupement.

    Un regroupement par date comble ses périodes vides (un graphique doit montrer le mois sans mouvement, à zéro) ; il se lit du plus
    ancien au plus récent. Un autre regroupement garde l'ordre de la requête (la plus grosse valeur d'abord).
    """
    decimals = [_metric_decimals(defn, m) for m in spec.metrics]

    def values(raw: Sequence[Any]) -> list[Any]:
        return [_finish(v, m, d) for v, m, d in zip(raw, spec.metrics, decimals, strict=True)]

    out: list[dict[str, Any]]
    if spec.group is None:
        out = [{"key": None, "values": values(rows[0] if rows else [None] * len(spec.metrics))}]
    else:
        out = [{"key": _key(r[0]), "values": values(r[1:])} for r in rows]
        if spec.group.is_time:
            out.sort(key=lambda r: str(r["key"]))
            if keys is not None:
                have = {r["key"]: r for r in out}
                out = [have.get(k) or {"key": k, "values": _zero_row(spec, defn)} for k in keys]
    return {**describe(defn, spec), "rows": out, "truncated": truncated}


def _key(raw: Any) -> Any:
    return raw if raw is None or isinstance(raw, (bool, int)) else str(raw)


def validate_filters(compiled: Any, filters: Mapping[str, Any] | None) -> dict[str, Any]:
    """Les filtres de la liste, validés par type. `compiled` : le `CompiledDocType` du DocType interrogé."""
    from .builder import SYSTEM_FILTERS  # import tardif : builder importe schema, comme ce module

    defn = compiled.definition
    if filters is None:
        return {}
    if not isinstance(filters, Mapping):
        raise ValidationFailed("filters doit être un objet")
    tables = {f.fieldname for f in defn.fields if f.fieldtype == "Table"}
    if tables & set(filters):
        raise ValidationFailed(f"filtre impossible sur un champ Table : {sorted(tables & set(filters))}")
    # `_tag` : les étiquettes du document (`tags.py`), là seulement où un document peut en porter.
    tagged = {TAG_FILTER} if supports_tags(defn) else set()
    # Filtrer par état sur un type sans workflow ne rendrait jamais rien : mieux vaut le dire que de servir une liste vide.
    if "workflow_state" in filters and defn.workflow is None:
        raise ValidationFailed(f"{defn.name} n'a pas de workflow : filtrer par état n'a pas de sens", code="no_workflow")
    unknown = sorted(set(filters) - set(compiled.adapters) - set(SYSTEM_FILTERS) - tagged)
    if unknown:
        raise ValidationFailed(f"filtre sur des champs inconnus : {unknown}")
    return {k: compiled.coerce_filter(k, v) for k, v in filters.items()}
