"""Permissions par rôle et par ligne (process/doctype.md D6).

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

Deux clauses de plus (groupe 2 du programme, `process/programme.md` §2.1), pour un document STOCKÉ (ni unique, ni virtuel, ni table enfant) :

- les **permissions utilisateur** RESSERRENT ce que les rôles donnent (ET) : une restriction « Awa → Entrepôt = Abidjan » (un document
  `User Permission`) limite les Entrepôts qu'Awa voit à Abidjan, et les documents de TOUT type dont un champ Lien vers Entrepôt vaut autre
  chose (un lien vide passe). `applies_to` la borne à un type. `if_owner` compris : la restriction s'ajoute à la ligne qui accorde ;
- le **partage** ÉLARGIT (OU) : un document partagé avec l'utilisateur (`xdt_share`) lui est ouvert en lecture, et en écriture si le
  partage le dit, même sans rôle sur le type et malgré ses restrictions. Ni suppression, ni soumission, ni annulation par un partage.

Le SQL lit restrictions et partages en sous-requêtes (aucun cache : une restriction ajoutée vaut à la requête suivante). Le Python, qui
juge une valeur pas encore en base, reçoit ces faits chargés par l'appelant (`Facts`) et refuse de répondre sans eux.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Mapping

from .context import RequestContext
from .errors import PermissionDenied
from .schema import AUTHENTICATED, DocTypeDef

Action = Literal["read", "write", "create", "delete", "submit", "cancel"]
ACTIONS: tuple[Action, ...] = ("read", "write", "create", "delete", "submit", "cancel")
# Le DocType des restrictions (plugin `system`) et les actions qu'un partage ouvre.
USER_PERMISSION = "User Permission"
SHARED_ACTIONS: frozenset[str] = frozenset({"read", "write"})


@dataclass(frozen=True, slots=True)
class Facts:
    """Ce que la base sait de CET utilisateur pour UN document, chargé par l'appelant pour l'interprète Python : ses restrictions qui
    valent pour ce type (DocType ciblé -> valeurs permises), et si le document lui est partagé au niveau de l'action."""

    restrictions: Mapping[str, frozenset[str]] = field(default_factory=dict)
    shared: bool = False


@dataclass(frozen=True, slots=True)
class Clause:
    """ET de conditions sur un document. Au moins une condition (sinon c'est `Scope.unrestricted`)."""

    owner: str | None  # `owner` du document doit valoir ceci
    match: tuple[str, str] | None  # (champ, valeur) : data[champ] doit valoir la valeur


@dataclass(frozen=True, slots=True)
class Scope:
    action: Action
    clauses: tuple[Clause, ...] = ()  # OU des clauses
    unrestricted: bool = False  # une règle sans condition : tous les documents du tenant (avant les restrictions)
    doctype: str | None = None
    user: str | None = None
    # Les permissions utilisateur qui PEUVENT s'appliquer : (DocType ciblé, champ Lien) ; champ `None` = le nom du document lui-même.
    restrict: tuple[tuple[str, str | None], ...] = ()
    share: bool = False  # les documents partagés avec `user` s'ajoutent (en écriture : les partages qui l'autorisent)

    @classmethod
    def everything(cls, action: Action) -> "Scope":
        """Contexte système, ou lecture interne d'un hook de confiance. Jamais issu d'un utilisateur."""
        return cls(action, unrestricted=True)

    @property
    def granted(self) -> bool:
        """Un RÔLE accorde l'action (sur une partie au moins des documents). Un partage seul ne compte pas : voir `require(single=True)`."""
        return self.unrestricted or bool(self.clauses)

    @property
    def level(self) -> Literal["all", "restricted", "none"]:
        return "all" if self.unrestricted else "restricted" if self.clauses else "none"

    def allows(self, owner: str, data: Mapping[str, object], *, name: str | None = None, facts: Facts | None = None) -> bool:
        """Le même prédicat que le SQL de `Query`, en Python. `facts` est OBLIGATOIRE dès que restrictions ou partage peuvent jouer : sans
        eux la réponse serait fausse, on refuse de la donner (une erreur de programmation, pas un refus d'accès)."""
        if (self.restrict or self.share) and facts is None:
            raise RuntimeError("Scope.allows : restrictions et partages non chargés (Facts)")
        role = self.unrestricted or any(
            (c.owner is None or c.owner == owner)
            and (c.match is None or data.get(c.match[0]) == c.match[1])
            for c in self.clauses
        )
        if role and self.restrict:
            role = within_restrictions(self.restrict, facts.restrictions if facts else {}, data, name)
        return role or (self.share and facts is not None and facts.shared)


def within_restrictions(restrict: tuple[tuple[str, str | None], ...], allowed: Mapping[str, frozenset[str]], data: Mapping[str, object], name: str | None) -> bool:
    """Chaque champ concerné par une restriction de l'utilisateur vaut une valeur permise, ou rien (un lien vide passe ; un nom pas encore
    attribué, à la création, aussi)."""
    for target, fieldname in restrict:
        values = allowed.get(target)
        if values is None:
            continue
        value = name if fieldname is None else data.get(fieldname)
        if value is None or value == "":
            continue
        if value not in values:
            return False
    return True


def stored_kind(defn: DocTypeDef) -> bool:
    """Restrictions et partages ne concernent que les documents STOCKÉS ici, adressables un par un."""
    return defn.source is None and not defn.single and not defn.istable


def scope_for(defn: DocTypeDef, ctx: RequestContext, action: Action) -> Scope:
    if ctx.is_system:
        return Scope.everything(action)
    held, user = ctx.permissions, ctx.user_id
    extra: dict[str, object] = {"doctype": defn.name}
    if user is not None and stored_kind(defn):
        links = tuple((str(f.options), f.fieldname) for f in defn.fields if f.fieldtype == "Link")
        extra.update(user=user, restrict=((defn.name, None), *links), share=action in SHARED_ACTIONS)
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
            return Scope(action, unrestricted=True, **extra)  # type: ignore[arg-type]
        clause = Clause(owner, match)
        if clause not in clauses:
            clauses.append(clause)
    return Scope(action, tuple(clauses), **extra)  # type: ignore[arg-type]


def require(scope: Scope, *, single: bool = False) -> Scope:
    """Aucune règle n'accorde l'action à ses rôles : refus global (403), indépendant du document. `single` : on vise UN document nommé
    (lire, modifier) ; un partage peut alors l'ouvrir sans rôle sur le type, et un document non partagé répond « introuvable » comme ailleurs."""
    if not scope.granted and not (single and scope.share):
        raise PermissionDenied()
    return scope
