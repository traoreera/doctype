"""Conditions `depends_on` (doc/doctype.md D22) : un langage minuscule, jamais d'`eval`.

Dans le YAML, une condition s'écrit en chaîne, comme dans Frappe :

    depends_on: "type == 'Entreprise' and credit_limit > 1000"

Le SEUL parseur est celui-ci. Il produit un arbre JSON à liste blanche, que `/meta` envoie au navigateur : le navigateur
n'évalue que cet arbre (voir `ui/src/lib/expr.ts`) et le serveur réévalue le même arbre à l'écriture. Aucun arbre ne
vient jamais d'un client.

Grammaire (comparaison non chaînable, pas d'arithmétique, pas d'appel, pas d'attribut) :

    expr    := or ;  or := and ('or' and)* ;  and := not ('and' not)* ;  not := 'not' not | cmp
    cmp     := primary [ ('==' | '!=' | '<' | '<=' | '>' | '>=') primary | ['not'] 'in' liste ]
    primary := nombre | chaîne | true | false | null | nom_de_champ | '(' expr ')'
    liste   := '[' [ littéral (',' littéral)* ] ']'

Sémantique, identique dans les deux langages (`tests/vectors/` en répond sur les deux côtés) :

- une valeur est null, booléen, nombre ou chaîne ; un champ absent vaut null ;
- `==` exige le MÊME type (`1 == true` est faux, `"1" == 1` est faux) ;
- `<`, `<=`, `>`, `>=` ne comparent que deux nombres ou deux chaînes (par point de code, pas par unité UTF-16), sinon faux ;
- `in` est une suite d'égalités ; `and`, `or`, `not` retournent toujours un booléen ;
- vrai/faux d'une valeur : null et faux, `false`, `0` et `""` sont faux, le reste est vrai.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

MAX_LENGTH = 500
MAX_DEPTH = 16
MAX_LIST = 100
_INT_LIMIT = 2**53  # au-delà, un nombre n'est plus exact côté navigateur

KEYWORDS = frozenset({"and", "or", "not", "in", "true", "false", "null", "none"})
_LITERAL_WORDS = {"true": True, "false": False, "null": None, "none": None}

Node = dict[str, Any]


class ExprError(ValueError):
    """Condition invalide : le message cite la position du problème."""


# ── Analyse lexicale ────────────────────────────────────────────────────────────────────────────

_TOKEN_RE = re.compile(
    r"""
    (?P<ws>\s+)
  | (?P<num>\d+(?:\.\d+)?)
  | (?P<str>'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*")
  | (?P<op>==|!=|<=|>=|<|>)
  | (?P<punct>[()\[\],-])
  | (?P<name>[A-Za-z_][A-Za-z0-9_]*)
    """,
    re.VERBOSE,
)
_ESCAPES = {"\\": "\\", "'": "'", '"': '"', "n": "\n", "t": "\t"}


def _unescape(raw: str, pos: int) -> str:
    out, i = [], 1
    while i < len(raw) - 1:
        c = raw[i]
        if c == "\\":
            nxt = raw[i + 1]
            if nxt not in _ESCAPES:
                raise ExprError(f"position {pos + i}: échappement inconnu \\{nxt}")
            out.append(_ESCAPES[nxt])
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _tokenize(src: str) -> list[tuple[str, Any, int]]:
    tokens: list[tuple[str, Any, int]] = []
    pos = 0
    while pos < len(src):
        m = _TOKEN_RE.match(src, pos)
        if not m:
            raise ExprError(f"position {pos}: caractère inattendu {src[pos]!r}")
        kind = m.lastgroup
        text = m.group()
        if kind == "num":
            value: Any = float(text) if "." in text else int(text)
            if isinstance(value, int) and value > _INT_LIMIT:
                raise ExprError(f"position {pos}: entier trop grand (maximum 2^53)")
            tokens.append(("num", value, pos))
        elif kind == "str":
            tokens.append(("str", _unescape(text, pos), pos))
        elif kind == "name":
            low = text.lower()
            tokens.append(("kw", low, pos) if low in KEYWORDS else ("name", text, pos))
        elif kind in ("op", "punct"):
            tokens.append((kind, text, pos))
        pos = m.end()
    tokens.append(("end", None, len(src)))
    return tokens


# ── Analyse syntaxique ──────────────────────────────────────────────────────────────────────────


class _Parser:
    def __init__(self, tokens: list[tuple[str, Any, int]]) -> None:
        self.t, self.i, self.depth = tokens, 0, 0

    # outils
    def peek(self) -> tuple[str, Any, int]:
        return self.t[self.i]

    def next(self) -> tuple[str, Any, int]:
        tok = self.t[self.i]
        self.i += 1
        return tok

    def is_kw(self, word: str) -> bool:
        k, v, _ = self.peek()
        return k == "kw" and v == word

    def is_punct(self, ch: str) -> bool:
        k, v, _ = self.peek()
        return k == "punct" and v == ch

    def fail(self, message: str) -> ExprError:
        k, v, pos = self.peek()
        got = "fin de la condition" if k == "end" else repr(v)
        return ExprError(f"position {pos}: {message} (trouvé : {got})")

    def enter(self) -> None:
        self.depth += 1
        if self.depth > MAX_DEPTH:
            raise ExprError(f"condition trop imbriquée (maximum {MAX_DEPTH} niveaux)")

    # règles
    def parse(self) -> Node:
        node = self.or_()
        if self.peek()[0] != "end":
            raise self.fail("fin de condition attendue")
        return node

    def or_(self) -> Node:
        items = [self.and_()]
        while self.is_kw("or"):
            self.next()
            items.append(self.and_())
        return items[0] if len(items) == 1 else {"t": "or", "items": items}

    def and_(self) -> Node:
        items = [self.not_()]
        while self.is_kw("and"):
            self.next()
            items.append(self.not_())
        return items[0] if len(items) == 1 else {"t": "and", "items": items}

    def not_(self) -> Node:
        if self.is_kw("not"):
            self.next()
            self.enter()
            try:
                return {"t": "not", "x": self.not_()}
            finally:
                self.depth -= 1
        return self.cmp()

    def cmp(self) -> Node:
        left = self.primary()
        k, v, _ = self.peek()
        if k == "op":
            self.next()
            right = self.primary()
            node: Node = {"t": "cmp", "op": v, "l": left, "r": right}
        elif self.is_kw("in") or (self.is_kw("not") and self.t[self.i + 1][:2] == ("kw", "in")):
            negated = self.is_kw("not")
            if negated:
                self.next()
            self.next()  # in
            node = {"t": "in", "neg": negated, "l": left, "r": self.list_()}
        else:
            return left
        k2, v2, _ = self.peek()
        if k2 == "op" or (k2 == "kw" and v2 in ("in",)):
            raise self.fail("les comparaisons ne se chaînent pas, utilisez `and`")
        return node

    def list_(self) -> Node:
        if not self.is_punct("["):
            raise self.fail("une liste littérale `[...]` est attendue après `in`")
        self.next()
        items: list[Node] = []
        if not self.is_punct("]"):
            while True:
                item = self.primary()
                if item["t"] != "lit":
                    raise self.fail("seuls des littéraux sont permis dans une liste")
                items.append(item)
                if len(items) > MAX_LIST:
                    raise ExprError(f"liste trop longue (maximum {MAX_LIST} éléments)")
                if self.is_punct(","):
                    self.next()
                    continue
                break
        if not self.is_punct("]"):
            raise self.fail("`]` attendu")
        self.next()
        return {"t": "list", "items": items}

    def primary(self) -> Node:
        k, v, pos = self.peek()
        if k == "num":
            self.next()
            return {"t": "lit", "v": v}
        if k == "str":
            self.next()
            return {"t": "lit", "v": v}
        if k == "punct" and v == "-":
            self.next()
            k2, v2, _ = self.peek()
            if k2 != "num":
                raise self.fail("un nombre est attendu après `-`")
            self.next()
            return {"t": "lit", "v": -v2}
        if k == "kw" and v in _LITERAL_WORDS:
            self.next()
            return {"t": "lit", "v": _LITERAL_WORDS[v]}
        if k == "name":
            self.next()
            return {"t": "field", "name": v}
        if k == "punct" and v == "(":
            self.next()
            self.enter()
            try:
                node = self.or_()
            finally:
                self.depth -= 1
            if not self.is_punct(")"):
                raise self.fail("`)` attendue")
            self.next()
            return node
        raise self.fail("une valeur est attendue")


def compile_expr(source: str, fieldnames: Iterable[str], *, allow_prefix: str | None = None) -> Node:
    """Chaîne -> arbre JSON. Lève `ExprError` si la syntaxe est fausse ou si un nom n'est pas un champ du DocType.

    `allow_prefix` : les noms qui commencent ainsi sont acceptés sans être des champs (`parent_` dans une ligne de table : le parent
    n'est pas connu quand on lit la définition de la ligne, il l'est quand on enregistre le DocType qui la porte).
    """
    if not isinstance(source, str) or not source.strip():
        raise ExprError("condition vide")
    if len(source) > MAX_LENGTH:
        raise ExprError(f"condition trop longue (maximum {MAX_LENGTH} caractères)")
    ast = _Parser(_tokenize(source)).parse()
    known = set(fieldnames)
    unknown = sorted(n for n in referenced_fields(ast) - known if not (allow_prefix and n.startswith(allow_prefix)))
    if unknown:
        raise ExprError(f"champ(s) inconnu(s) : {', '.join(unknown)}")
    return ast


def referenced_fields(ast: Node) -> set[str]:
    """Les champs que la condition lit : l'interface ne la réévalue que quand l'un d'eux change."""
    t = ast["t"]
    if t == "field":
        return {ast["name"]}
    if t == "cmp":
        return referenced_fields(ast["l"]) | referenced_fields(ast["r"])
    if t == "in":
        return referenced_fields(ast["l"])
    if t in ("and", "or"):
        return set().union(*(referenced_fields(i) for i in ast["items"]))
    if t == "not":
        return referenced_fields(ast["x"])
    return set()


# ── Évaluation (miroir exact de ui/src/lib/expr.ts) ─────────────────────────────────────────────


def _kind(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, (int, float)):
        return "num"
    if isinstance(v, str):
        return "str"
    return "other"


def _equal(a: Any, b: Any) -> bool:
    ka = _kind(a)
    return ka == _kind(b) and ka != "other" and (ka == "null" or a == b)


def _truthy(v: Any) -> bool:
    k = _kind(v)
    if k == "null":
        return False
    if k == "bool":
        return v
    if k == "num":
        return v != 0
    if k == "str":
        return v != ""
    return False


def _ordered(op: str, a: Any, b: Any) -> bool:
    ka, kb = _kind(a), _kind(b)
    if ka != kb or ka not in ("num", "str"):
        return False
    # Python compare les chaînes par point de code : c'est la définition du langage.
    return {"<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b}[op]


def _value(node: Node, values: Mapping[str, Any]) -> Any:
    t = node["t"]
    if t == "lit":
        return node["v"]
    if t == "field":
        return values.get(node["name"])
    return _bool(node, values)


def _bool(node: Node, values: Mapping[str, Any]) -> bool:
    t = node["t"]
    if t in ("lit", "field"):
        return _truthy(_value(node, values))
    if t == "and":
        return all(_bool(i, values) for i in node["items"])
    if t == "or":
        return any(_bool(i, values) for i in node["items"])
    if t == "not":
        return not _bool(node["x"], values)
    if t == "cmp":
        a, b = _value(node["l"], values), _value(node["r"], values)
        op = node["op"]
        if op == "==":
            return _equal(a, b)
        if op == "!=":
            return not _equal(a, b)
        return _ordered(op, a, b)
    if t == "in":
        a = _value(node["l"], values)
        found = any(_equal(a, item["v"]) for item in node["r"]["items"])
        return not found if node["neg"] else found
    raise ValueError(f"nœud inconnu : {t!r}")  # arbre produit par ce module uniquement


def evaluate(ast: Node, values: Mapping[str, Any]) -> bool:
    """Évalue un arbre compilé sur les valeurs d'un document. Retourne toujours un booléen, ne lève jamais."""
    return _bool(ast, values)
