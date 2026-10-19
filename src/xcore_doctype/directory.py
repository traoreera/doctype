"""L'annuaire des utilisateurs d'une organisation : de qui parle-t-on quand un document est « assigné à » quelqu'un ?

Avec **xauth** (le système d'authentification par défaut), l'identité d'un utilisateur est son identifiant (`sub` du jeton : un UUID, qui ne se lit
pas) et xauth connaît ses membres : l'action `xauth.users.list.tenant` rend, pour une organisation, `{id, email}`. Ce module la demande (par l'appel
de plugin d'xcore, `ctx.caller`) pour :

- **proposer** des personnes à assigner (les membres de l'organisation, rien d'autre) ;
- **vérifier** qu'une personne assignée est bien membre de CETTE organisation ;
- **afficher** un nom lisible (l'e-mail) là où un document porte un identifiant (auteur d'un commentaire, assigné, propriétaire…).

Sans xauth (un hôte de test, ou un plugin d'authentification qui n'expose pas cette action) l'annuaire est « indisponible » : on
retombe sur les identités que l'organisation a déjà fait apparaître (`Query.people`), l'identifiant s'affiche tel quel et n'importe quel identifiant
peut être assigné. L'annuaire est mis en cache quelques secondes par organisation (une frappe dans un champ de recherche ne doit pas faire un appel
de plugin), y compris son absence (un xauth arrêté ne doit pas ralentir chaque requête).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol

DEFAULT_PLUGIN = "auth"  # le `name:` du plugin d'authentification d'orchestra-backend (son dossier est `app/auth`)
DEFAULT_ACTION = "xauth.users.list.tenant"
DEFAULT_TTL = 60.0
UNAVAILABLE_TTL = 15.0  # une absence se retente plus vite qu'un annuaire qu'on a : xauth peut démarrer après nous


@dataclass(frozen=True, slots=True)
class Person:
    """Un membre de l'organisation. `email` peut manquer (annuaire d'observation) : on affiche alors l'identifiant."""

    id: str
    email: str | None = None

    @property
    def label(self) -> str:
        return self.email or self.id

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "email": self.email, "name": self.label}

    def matches(self, prefix: str) -> bool:
        p = prefix.strip().lower()
        return not p or p in self.id.lower() or p in (self.email or "").lower()


class Directory(Protocol):
    async def members(self, tenant_id: str) -> list[Person] | None:
        """Les membres de l'organisation, ou `None` si aucun annuaire ne répond (on retombe sur les identités observées)."""


class NoDirectory:
    """`doctype.directory.provider: none` : jamais d'annuaire (identités observées seulement)."""

    async def members(self, tenant_id: str) -> list[Person] | None:
        return None


Call = Callable[[str, str, dict[str, Any], str], Awaitable[Any]]


class XauthDirectory:
    """Les membres d'une organisation d'après xauth, par un appel de plugin. Un échec (plugin absent, arrêté, lent, réponse inattendue) est
    « pas d'annuaire », jamais une erreur pour l'utilisateur."""

    def __init__(
        self, call: Call, *, plugin: str = DEFAULT_PLUGIN, action: str = DEFAULT_ACTION, ttl: float = DEFAULT_TTL, logger: Any = None, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._call, self._plugin, self._action, self._ttl, self._log, self._clock = call, plugin, action, ttl, logger, clock
        self._cache: dict[str, tuple[float, list[Person] | None]] = {}

    async def members(self, tenant_id: str) -> list[Person] | None:
        now = self._clock()
        hit = self._cache.get(tenant_id)
        if hit is not None and hit[0] > now:
            return hit[1]
        people = await self._fetch(tenant_id)
        self._cache[tenant_id] = (now + (self._ttl if people is not None else min(self._ttl, UNAVAILABLE_TTL)), people)
        return people

    async def _fetch(self, tenant_id: str) -> list[Person] | None:
        try:
            result = await self._call(self._plugin, self._action, {"tenant_id": tenant_id}, tenant_id)
        except Exception as e:  # noqa: BLE001 - xauth absent, arrêté ou injoignable : on retombe sur les identités observées
            self._warn("user directory unavailable", error=repr(e))
            return None
        users = result.get("users") if isinstance(result, dict) and result.get("status", "ok") == "ok" else None
        if not isinstance(users, list):
            # Le refus le plus probable : `allowed_callers` de xauth ne nomme pas ce plugin (le message d'xcore dit quoi ajouter).
            said = result.get("msg") if isinstance(result, dict) else None
            self._warn("user directory answered something unexpected", directory_action=self._action, answer=str(said)[:300] if said else None)
            return None
        people: dict[str, Person] = {}
        for u in users:
            if isinstance(u, dict) and isinstance(u.get("id"), str) and u["id"]:
                email = u.get("email")
                people.setdefault(u["id"], Person(u["id"], email if isinstance(email, str) and email else None))
        return sorted(people.values(), key=lambda p: p.label.lower())

    def _warn(self, message: str, **fields: Any) -> None:
        if self._log is None:
            return
        try:
            self._log.warning(message, directory_plugin=self._plugin, **fields)  # pas de champ `plugin` ni `msg` : le logger d'xcore les réserve
        except Exception:  # noqa: BLE001 - une ligne de journal ne doit jamais faire échouer une requête
            pass
