"""Permissions par rôle et par ligne (doc/doctype.md D6).

Un `Scope` est le prédicat « quels documents cet utilisateur peut-il viser pour cette action », compilé
UNE fois depuis les règles du DocType et le contexte. Il a deux interprètes :

- `Query` le traduit en SQL (lecture, mise à jour, suppression) : jamais de filtrage après coup ;
- `Scope.allows` l'évalue en Python, pour la valeur ÉCRITE (création, déplacement) qui n'est pas encore en base.

Les deux doivent rester équivalents : `tests/unit/test_permissions_matrix.py` les compare à un oracle
écrit indépendamment, sur SQLite et PostgreSQL.

Sémantique : les lignes dont l'utilisateur détient le `grant` (dans `principal["permissions"]`) et qui accordent
l'action s'additionnent (OU) ; dans une ligne, `if_owner` et `match` se cumulent (ET). Une ligne dont une condition ne
peut pas être évaluée (utilisateur sans l'attribut de `match`, `if_owner` sans identité) n'accorde rien : elle ne
devient jamais « tout ». Les noms de RÔLE ne comptent pas : chez auth ils sont libres et propres à chaque tenant ;
la composition rôle -> grants est faite par auth, c'est le grant qui est le contrat stable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Mapping

from .context import RequestContext
from .errors import PermissionDenied
from .schema import AUTHENTICATED, DocTypeDef

Action = Literal["read", "write", "create", "delete", "submit", "cancel"]
ACTIONS: tuple[Action, ...] = ("read", "write", "create", "delete", "submit", "cancel")


@dataclass(frozen=True, slots=True)
class Clause:
    """ET de conditions sur un document. Au moins une condition (sinon c'est `Scope.unrestricted`)."""

    owner: str | None  # `owner` du document doit valoir ceci
    match: tuple[str, str] | None  # (champ, valeur) : data[champ] doit valoir la valeur


@dataclass(frozen=True, slots=True)
class Scope:
    action: Action
    clauses: tuple[Clause, ...] = ()  # OU des clauses
    unrestricted: bool = False  # une règle sans condition : tous les documents du tenant

    @classmethod
    def everything(cls, action: Action) -> "Scope":
        """Contexte système, ou lecture interne d'un hook de confiance. Jamais issu d'un utilisateur."""
        return cls(action, unrestricted=True)

    @property
    def granted(self) -> bool:
        return self.unrestricted or bool(self.clauses)

    @property
    def level(self) -> Literal["all", "restricted", "none"]:
        return "all" if self.unrestricted else "restricted" if self.clauses else "none"

    def allows(self, owner: str, data: Mapping[str, object]) -> bool:
        if self.unrestricted:
            return True
        return any(
            (c.owner is None or c.owner == owner)
            and (c.match is None or data.get(c.match[0]) == c.match[1])
            for c in self.clauses
        )


def scope_for(defn: DocTypeDef, ctx: RequestContext, action: Action) -> Scope:
    if ctx.is_system:
        return Scope.everything(action)
    held, user = ctx.permissions, ctx.user_id
    clauses: list[Clause] = []
    for rule in defn.rules_for(action):
        if rule.grant != AUTHENTICATED and rule.grant not in held:
            continue
        if rule.grant == AUTHENTICATED and user is None:
            continue
        owner = None
        if rule.if_owner:
            if user is None:
                continue
            owner = user
        match = None
        if rule.match is not None:
            value = ctx.user_attr(rule.match.principal_attr)
            if not isinstance(value, str) or not value:
                continue  # attribut absent ou non textuel : jamais d'égalité avec « rien »
            match = (rule.match.field, value)
        if owner is None and match is None:
            return Scope(action, unrestricted=True)
        clause = Clause(owner, match)
        if clause not in clauses:
            clauses.append(clause)
    return Scope(action, tuple(clauses))


def require(scope: Scope) -> Scope:
    """Aucune règle n'accorde l'action à ses rôles : refus global (403), indépendant du document."""
    if not scope.granted:
        raise PermissionDenied()
    return scope
