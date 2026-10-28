"""`compute:` — le calcul déclaratif, à vocabulaire FERMÉ (décision 2 de `process/programme.md` §3.7).

La question est née d'un fait mesuré : **une facture ne peut pas avoir de total.** Sur le jeu d'ERP, 129 champs
de montant clés (`grand_total`, `amount`, `total`, `net_amount`…) sont `read_only`, donc l'utilisateur ne peut
pas les saisir, et aucun mécanisme de calcul n'existait. `fetch_from` ne sait que COPIER la valeur d'un document
lié. Sans `compute:`, écrire un hook de comptabilisation c'est poster un total que rien ne remplit.

**Trois formes, et pas un langage.** `expr.py` vaut par deux choses : il est minuscule, et il est JUMELÉ au
navigateur (même arbre, même sémantique, vecteurs partagés). Y ajouter l'arithmétique, ce serait la grammaire,
l'évaluateur Python, son jumeau TypeScript, les vecteurs, et des règles de promotion numérique — en risquant
d'embrouiller la sémantique volontairement stricte des conditions (`1 == true` est faux). Or le besoin n'est pas
un langage :

    compute: qty * rate                  # produit de deux champs voisins (typiquement sur la ligne)
    compute: sum(items.amount)           # somme d'une colonne de lignes (sur le parent)
    compute: grand_total - paid_amount   # somme ou différence de champs voisins

Fermé, donc testable exhaustivement. Aucune division, aucun littéral, aucune parenthèse, aucun mélange de `*`
avec `+`/`-` : ce qui n'est pas dans ces trois formes est refusé AU CHARGEMENT, pas à la première écriture.

**Les deux décisions que l'écriture a tranchées**, et qui étaient laissées « à préciser » par §3.7 :

1. **Un produit ARRONDIT à l'échelle du champ qui reçoit**, en `ROUND_HALF_UP` (la convention commerciale).
   `qty` (3 décimales) × `rate` (2) donne 5 décimales exactes, et `amount` en déclare 2 : refuser l'arrondi
   rendrait la forme canonique de l'ERP impossible. Déclarer `decimals: 2` sur un montant, c'est précisément
   dire « cette valeur a deux décimales ». C'est le SEUL endroit où le moteur arrondit.
2. **Une addition ne perd JAMAIS de précision** : si le champ qui reçoit a moins de décimales qu'un de ses
   termes, c'est refusé au chargement. Additionner des montants à 2 décimales dans un total à 0 décimale perdrait
   des centimes — et un total faux est pire qu'un chargement qui échoue. La règle « pas d'arrondi silencieux »
   du moteur (`decimals.canonical`) reste donc entière partout sauf au produit, où elle est explicite.

Une somme de table vide vaut **zéro**, pas `null` : un total absent sur une facture sans ligne serait un trou,
pas une information.
"""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Mapping, NamedTuple, Sequence

from .decimals import canonical, to_decimal
from .schema import DECIMAL_TYPES

# Les genres numériques, repris de `mapping.py` : un flottant et un décimal exact ne se mélangent jamais.
FLOAT_KINDS = ("Number", "Int", "Percent")
COMPUTABLE = (*FLOAT_KINDS, *DECIMAL_TYPES)

_NAME = r"[a-z][a-z0-9_]{0,62}"
_SUM = re.compile(rf"^sum\(\s*({_NAME})\.({_NAME})\s*\)$")
_PRODUCT = re.compile(rf"^({_NAME})\s*\*\s*({_NAME})$")
_TERMS = re.compile(rf"^({_NAME})(\s*[-+]\s*{_NAME})+$")
_TERM = re.compile(rf"([-+])\s*({_NAME})")
MAX_TERMS = 10  # une somme reste lisible d'un coup d'œil ; au-delà, c'est un champ de plus à déclarer


class Product(NamedTuple):
    """`a * b` : le produit de deux champs voisins. Arrondi à l'échelle du champ qui reçoit."""

    left: str
    right: str

    @property
    def reads(self) -> tuple[str, ...]:
        return (self.left, self.right)


class Terms(NamedTuple):
    """`a + b - c` : une somme signée de champs voisins. Jamais d'arrondi (refusé au chargement)."""

    terms: tuple[tuple[int, str], ...]  # (+1 ou -1, nom du champ)

    @property
    def reads(self) -> tuple[str, ...]:
        return tuple(name for _, name in self.terms)


class TableSum(NamedTuple):
    """`sum(items.amount)` : la somme d'une colonne des lignes. Vide = zéro."""

    table: str
    field: str

    @property
    def reads(self) -> tuple[str, ...]:
        return (self.table,)


Compute = Product | Terms | TableSum


def parse_compute(text: Any) -> Compute:
    """Rend la forme compilée, ou lève `ValueError` (le schéma en fait une erreur de définition).

    Aucun `eval`, aucune grammaire générale : trois motifs, essayés dans l'ordre.
    """
    if not isinstance(text, str) or not text.strip():
        raise ValueError("`compute` attend une expression : `a * b`, `a + b - c` ou `sum(table.champ)`")
    expression = text.strip()
    if m := _SUM.match(expression):
        return TableSum(m.group(1), m.group(2))
    if m := _PRODUCT.match(expression):
        return Product(m.group(1), m.group(2))
    if _TERMS.match(expression):
        head = re.match(rf"^({_NAME})", expression)
        assert head is not None  # le motif `_TERMS` vient de passer : il commence par un nom de champ
        terms: list[tuple[int, str]] = [(1, head.group(1))]
        terms += [(1 if sign == "+" else -1, name) for sign, name in _TERM.findall(expression[head.end() :])]
        if len(terms) > MAX_TERMS:
            raise ValueError(f"`compute` : {MAX_TERMS} termes au plus (déclarez un champ intermédiaire)")
        return Terms(tuple(terms))
    raise ValueError(
        "`compute` n'accepte que trois formes : `a * b` (produit de deux champs), `a + b - c` (somme signée) "
        f"ou `sum(table.champ)` (somme d'une colonne de lignes) — reçu {expression!r}"
    )


def compute_problems(field: Any, computed: Compute, definition: Any, child_of: Any) -> list[str]:
    """Tout ce qui rendrait le calcul impossible, dit AU CHARGEMENT. `child_of(champ_table)` rend la définition des lignes, ou `None`."""
    problems: list[str] = []
    name = field.fieldname
    fields = definition.field_map
    if field.fieldtype not in COMPUTABLE:
        return [f"{name}: un champ {field.fieldtype} ne se calcule pas (types permis : {', '.join(COMPUTABLE)})"]

    def numeric(where: str, operand: Any, operand_name: str) -> None:
        if operand is None:
            problems.append(f"{name}: {where} {operand_name!r} n'est pas un champ de {definition.name}")
        elif operand.fieldtype not in COMPUTABLE:
            problems.append(f"{name}: {where} {operand_name!r} est un {operand.fieldtype}, qui ne se calcule pas")
        elif (operand.fieldtype in DECIMAL_TYPES) != (field.fieldtype in DECIMAL_TYPES):
            kind = "décimal exact" if field.fieldtype in DECIMAL_TYPES else "flottant"
            problems.append(
                f"{name}: {where} {operand_name!r} ({operand.fieldtype}) ne se mélange pas avec un {kind} "
                f"({field.fieldtype}) — un flottant et un décimal exact ne se multiplient ni ne s'additionnent"
            )

    if isinstance(computed, TableSum):
        table = fields.get(computed.table)
        if table is None or table.fieldtype != "Table":
            problems.append(f"{name}: `sum({computed.table}.{computed.field})` — {computed.table!r} n'est pas un champ Table de {definition.name}")
            return problems
        child = child_of(computed.table)
        if child is None:
            problems.append(f"{name}: les lignes de {computed.table!r} sont introuvables")
            return problems
        column = child.field_map.get(computed.field)
        if column is None:
            problems.append(f"{name}: {computed.field!r} n'est pas un champ de {child.name}")
            return problems
        numeric("la colonne", column, f"{computed.table}.{computed.field}")
        problems += _no_precision_loss(field, [column], name)
        return problems

    for operand_name in computed.reads:
        if operand_name == name:
            problems.append(f"{name}: un champ calculé ne peut pas se lire lui-même")
            continue
        numeric("le champ", fields.get(operand_name), operand_name)
    if isinstance(computed, Terms):
        operands = [fields[n] for n in computed.reads if n in fields]
        problems += _no_precision_loss(field, operands, name)
    return problems


def _no_precision_loss(field: Any, operands: Sequence[Any], name: str) -> list[str]:
    """Une ADDITION ne perd jamais de précision : le champ qui reçoit a au moins autant de décimales que ses termes.

    Additionner des montants à 2 décimales dans un total à 0 décimale perdrait des centimes. Le produit, lui,
    arrondit à l'échelle déclarée : c'est son cas explicite (voir l'en-tête du module).
    """
    if field.fieldtype not in DECIMAL_TYPES:
        return []
    short = [o for o in operands if o.fieldtype in DECIMAL_TYPES and (o.decimals or 0) > (field.decimals or 0)]
    return [
        f"{name}: additionner {o.fieldname!r} ({o.decimals} décimales) dans un champ à {field.decimals} "
        "perdrait de la précision ; une somme n'arrondit pas"
        for o in short
    ]


def evaluate(computed: Compute, field: Any, container: Mapping[str, Any]) -> Any:
    """La valeur du champ calculé, dans la forme que le champ stocke (chaîne canonique, ou flottant).

    `container` : le document (ou la ligne) tel qu'il va être écrit. Une valeur manquante compte pour zéro — un
    total partiel vaut mieux qu'un trou, et un champ obligatoire est de toute façon exigé ailleurs.
    """
    exact = field.fieldtype in DECIMAL_TYPES
    if isinstance(computed, TableSum):
        rows = container.get(computed.table)
        rows = [r for r in rows if isinstance(r, Mapping)] if isinstance(rows, list) else []
        values = [r.get(computed.field) for r in rows]
        total = _sum(values, exact)  # une table vide vaut zéro, pas `null`
    elif isinstance(computed, Product):
        a, b = (_one(container.get(n), exact) for n in (computed.left, computed.right))
        total = a * b
    else:
        total = _sum([container.get(n) for _, n in computed.terms], exact, signs=[s for s, _ in computed.terms])
    if not exact:
        return float(total)
    # L'arrondi n'a lieu QUE pour un produit (les autres formes sont refusées au chargement si elles en
    # demanderaient un) : `canonical` refuserait une valeur trop précise, donc on quantifie d'abord.
    quantum = Decimal(1).scaleb(-(field.decimals or 0))
    return canonical(Decimal(total).quantize(quantum, rounding=ROUND_HALF_UP), field.decimals or 0)


def compute_order(fields: Sequence[Any]) -> tuple[tuple[str, ...], list[str]]:
    """L'ordre d'évaluation des champs calculés d'UN conteneur, et les boucles trouvées.

    Un calcul LIT un autre calcul, et il le faut : la facture de l'ERP est exactement cette chaîne —
    `amount = qty * rate` sur la ligne, `total = sum(items.amount)`, `grand_total = total + taxes`,
    `outstanding = grand_total - paid`. L'interdire rendrait `compute:` inutile pour le cas qui l'a fait naître
    (c'est l'erreur qu'une première version de ce module a commise, et qu'un test a attrapée).

    Ce qui est refusé, c'est la BOUCLE — `a = b + x` avec `b = a + y` —, détectée ici au chargement et nommée.
    L'ordre, lui, est calculé (tri topologique) : il n'est pas à deviner ni à déclarer.
    """
    parsed: dict[str, Compute] = {}
    for f in fields:
        if f.compute is None:
            continue
        try:
            parsed[f.fieldname] = parse_compute(f.compute)
        except ValueError:
            continue  # la forme est signalée ailleurs (schéma, `compute_problems`) : ne pas doubler le message
    # Les dépendances d'un calcul, réduites aux champs eux-mêmes calculés : les autres sont déjà là.
    needs = {name: {r for r in c.reads if r in parsed and r != name} for name, c in parsed.items()}
    order: list[str] = []
    ready = sorted(n for n, deps in needs.items() if not deps)
    remaining = {n: set(deps) for n, deps in needs.items() if deps}
    while ready:
        current = ready.pop(0)
        order.append(current)
        freed = []
        for name, deps in remaining.items():
            deps.discard(current)
            if not deps:
                freed.append(name)
        for name in sorted(freed):
            del remaining[name]
            ready.append(name)
        ready.sort()
    if remaining:
        looped = ", ".join(sorted(remaining))
        return tuple(order), [f"calcul en boucle entre les champs : {looped}"]
    return tuple(order), []


def has_computes(definition: Any, child_of: Any) -> bool:
    """Ce DocType (ou une de ses lignes) a-t-il un champ calculé ? Évite de recopier un document pour rien."""
    if any(f.compute for f in definition.fields):
        return True
    return any(
        child_of(f.fieldname) is not None and any(cf.compute for cf in child_of(f.fieldname).fields)
        for f in definition.fields
        if f.fieldtype == "Table"
    )


def apply_computes(definition: Any, child_of: Any, data: Mapping[str, Any]) -> dict[str, Any]:
    """Écrit tous les champs calculés du document et de ses lignes. Pur : aucune base, aucun droit.

    **Les LIGNES d'abord, le document ensuite**, et cet ordre est la règle : `total = sum(items.amount)` lit un
    `amount` qui est lui-même `qty * rate`. L'inverse donnerait un total calculé sur les valeurs d'avant — faux
    d'une écriture de retard, donc faux tout le temps pour qui lit juste après avoir enregistré.

    À l'intérieur d'un conteneur, l'ordre vient de `compute_order` (tri topologique) : un calcul peut lire un
    autre calcul — `grand_total = total + taxes` où `total = sum(items.amount)` — et c'est le cas qui a motivé
    tout le mécanisme. Une boucle est refusée au chargement, pas ici.
    """
    out = dict(data)
    for f in definition.fields:
        if f.fieldtype != "Table":
            continue
        child = child_of(f.fieldname)
        if child is None or not any(cf.compute for cf in child.fields):
            continue
        rows = out.get(f.fieldname)
        if not isinstance(rows, list):
            continue
        out[f.fieldname] = [_row(child, row) if isinstance(row, Mapping) else row for row in rows]
    _fill(definition.fields, out)
    return out


def _fill(fields: Sequence[Any], container: dict[str, Any]) -> None:
    by_name = {f.fieldname: f for f in fields}
    order, _ = compute_order(fields)
    for name in order:
        field = by_name[name]
        container[name] = evaluate(parse_compute(field.compute), field, container)


def _row(child: Any, row: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(row)
    _fill(child.fields, out)
    return out


def _one(value: Any, exact: bool) -> Any:
    if value is None:
        return Decimal(0) if exact else 0.0
    if exact:
        return to_decimal(value)
    return float(value) if not isinstance(value, bool) else 0.0


def _sum(values: Sequence[Any], exact: bool, signs: Sequence[int] | None = None) -> Any:
    total = Decimal(0) if exact else 0.0
    for i, value in enumerate(values):
        sign = signs[i] if signs is not None else 1
        total = total + sign * _one(value, exact)
    return total
