"""Hooks (process/doctype.md D2, D4, D16, D21).

Deux familles, avec des garanties différentes :

- **Hooks de confiance** : coroutines enregistrées par nom dans un `HookRegistry` (D16, jamais importées depuis un
  chemin d'un fichier YAML). `validate` et `before_save` s'exécutent dans la transaction d'écriture et sont appelés
  DIRECTEMENT par le service (l'EventBus avale les erreurs et ne peut rien annuler) ; `after_save` s'exécute après le commit.
  `before_insert` (création seulement, après `before_save`) aussi. `on_submit`, `on_cancel`, `before_cancel` et `on_update_after_submit`
  (documents soumettables) et `on_trash` (avant une suppression) s'exécutent dans la transaction, avec en plus le droit d'écrire
  d'autres documents DANS cette transaction (`HookEvent.tx`) : ce que le hook écrit est validé et annulé avec le reste.
  `after_insert` et `after_delete` s'exécutent après le commit, comme `after_save` : une erreur est journalisée et ne défait rien.
- **Hooks `action:`** (D4, D21) : une action d'un autre plugin, typiquement sandboxé. Appelée AVANT d'ouvrir la
  transaction, avec le tenant et le principal de la requête passés explicitement (jamais les ContextVar ambiantes,
  qui ne sont pas posées pour une route HTTP). Le plugin sandboxé ne touche jamais la base.

Contrat d'une action de hook : reçoit
`{"event", "doctype", "name", "doc", "old", "user", "roles"}` et répond `{"status": "ok", "doc": {...}}` (le `doc`
est facultatif : absent = inchangé) ou `{"status": "error", "code": ..., "msg": ...}` pour refuser (422).
"""

from __future__ import annotations

import asyncio
import inspect
import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Awaitable, Callable, Literal, Mapping, Protocol

from .context import RequestContext
from .errors import ConfigurationError, DoctypeError, HookRegistrationError
from .schema import ACTION_HOOK_RE, TRUSTED_HOOK_RE

HookName = Literal[
    "validate", "before_save", "after_save", "on_submit", "on_cancel",
    "before_insert", "after_insert", "on_trash", "after_delete", "before_cancel", "on_update_after_submit",
]

# Codes qu'xcore renvoie quand l'APPEL échoue, par opposition à un refus voulu par le hook.
_INFRA_CODES = frozenset(
    {
        "permission_denied",
        "action_permission_denied",
        "ipc_denied",
        "not_found",
        "not_ready",
        "unknown_action",
        "rate_limit_exceeded",
        "all_retries_failed",
        "handler_error",
        "json_error",
        "filesystem_denied",
        "disk_quota",
    }
)
_MAX_REJECTION_MSG = 500


# ── Registre de hooks de confiance ──────────────────────────────────────────────────────────────


class HookRegistry:
    """Nom -> coroutine. Porté par l'instance du plugin (D8) : un reload d'xcore-doctype le vide.

    Un plugin qui dépend d'xcore-doctype l'obtient par `self.get_service("doctype_hooks")` dans son `on_load`
    (`requires: [xcore-doctype]`), enregistre ses hooks et les retire dans `on_unload`. Après un reload d'xcore-doctype,
    les noms manquants font REFUSER les sauvegardes concernées (503) au lieu d'être ignorés : voir `DocumentService`.
    """

    def __init__(self) -> None:
        self._hooks: dict[str, tuple[Callable[["HookEvent"], Awaitable[None]], str | None]] = {}

    def register(self, name: str, fn: Callable[["HookEvent"], Awaitable[None]], *, owner: str | None = None) -> None:
        if not isinstance(name, str) or not TRUSTED_HOOK_RE.match(name):
            raise HookRegistrationError(f"nom de hook invalide : {name!r} (attendu : identifiants séparés par des points)")
        if not inspect.iscoroutinefunction(fn):
            raise HookRegistrationError(f"{name}: un hook doit être une coroutine (async def), pas un appel bloquant")
        if name in self._hooks:
            raise HookRegistrationError(f"{name}: déjà enregistré par {self._hooks[name][1] or 'un autre plugin'}")
        self._hooks[name] = (fn, owner)

    def unregister(self, name: str) -> None:
        self._hooks.pop(name, None)

    def unregister_owner(self, owner: str) -> int:
        gone = [n for n, (_, o) in self._hooks.items() if o == owner]
        for n in gone:
            del self._hooks[n]
        return len(gone)

    def get(self, name: str) -> Callable[["HookEvent"], Awaitable[None]] | None:
        entry = self._hooks.get(name)
        return entry[0] if entry else None

    def names(self) -> list[str]:
        return sorted(self._hooks)

    def clear(self) -> None:
        self._hooks.clear()


class HookTransaction(Protocol):
    """Les écritures qu'un hook `on_submit` / `on_cancel` peut faire, dans la transaction du document qu'il traite.

    C'est du code de CONFIANCE (D16) : aucune permission n'est contrôlée, la lecture n'est pas filtrée, et les champs Link
    sont vérifiés sur l'existence seule (la cible n'a pas à être lisible par l'utilisateur qui soumet). Chaque écriture
    est en revanche validée comme n'importe quelle autre (types stricts, champs obligatoires, liens, numérotation), historisée
    au nom de l'utilisateur, et défaite avec la transaction si le hook, ou un hook suivant, refuse.

    Le DocType écrit garde ses règles : ses hooks de confiance `validate`, `before_save` (et `before_insert` à la création) passent
    dans la transaction — un refus défait tout —, `after_insert` / `after_save` après le commit. Ses hooks `action:` (sandboxés, hors
    transaction) ne le peuvent pas : un tel type se refuse (`hook_not_supported`). Un hook manquant refuse l'écriture (503). Des hooks qui
    s'écrivent en chaîne s'arrêtent au-delà de `MAX_TX_DEPTH` niveaux (`hook_recursion`).
    """

    @property
    def today(self) -> date:
        """La date du jour dans le fuseau de l'organisation (`doctype.timezone`), la même que celle des séries de numérotation."""

    async def get(self, doctype: str, name: str, *, lock: bool = False) -> dict[str, Any] | None:
        """Le document (même tenant), ou `None`. `lock=True` le verrouille jusqu'à la fin de la transaction."""

    async def find(
        self, doctype: str, filters: Mapping[str, Any] | None = None, *, order_by: str = "name", descending: bool = False,
        limit: int = 500, lock: bool = False,
    ) -> list[dict[str, Any]]:
        """Les documents de ce type (même tenant) qui répondent aux filtres — les MÊMES filtres typés que la liste (`{"voucher_no": x}`,
        `{"posting_date": [">=", "2026-01-01"]}`, `docstatus`, `_tag`) —, lus DANS la transaction : un hook voit ce qu'il vient d'écrire.
        Au plus 500 (`MAX_FIND`), dans l'ordre de `order_by` puis du nom. `lock=True` les verrouille jusqu'à la fin de la transaction
        (PostgreSQL ; SQLite n'a pas de verrou de ligne et compte sur la première écriture). C'est ainsi qu'une annulation retrouve SES
        écritures. Un type virtuel ne se cherche pas ici (`not_in_transaction`)."""

    async def sum(self, doctype: str, field: str, filters: Mapping[str, Any] | None = None) -> Decimal:
        """La somme EXACTE d'un champ Decimal ou Currency sur les documents qui répondent aux filtres, à l'échelle du champ (0 si aucun) :
        le solde d'un compte, le reste dû d'une facture, la valeur d'un stock. Faite en `decimal.Decimal`, jamais par le SUM du SQL
        (un double sous SQLite). Un autre genre de champ est refusé (`not_summable`)."""

    async def create(self, doctype: str, data: Mapping[str, Any], *, name: str | None = None) -> dict[str, Any]:
        """Crée un document en brouillon. `name` : un nom choisi par le hook (un nom déjà pris échoue en 409 `already_exists`)."""

    async def ensure(self, doctype: str, name: str, data: Mapping[str, Any]) -> bool:
        """Crée le document s'il n'existe pas, atomiquement (vrai si c'est cet appel qui l'a créé). Ne modifie jamais l'existant."""

    async def update(self, doctype: str, name: str, data: Mapping[str, Any], version: int) -> dict[str, Any]:
        """Remplace le contenu d'un document, avec le contrôle de version habituel (409 si le document a changé). Un document SOUMIS ne laisse
        changer que ses champs `allow_on_submit` (un autre champ changé : 500 `hook_invalid_write`) et son `on_update_after_submit` tourne ;
        annulé, il refuse (409 `document_locked`). C'est ainsi qu'un paiement soumis met à jour le reste dû de sa facture."""


@dataclass(slots=True)
class HookEvent:
    """Ce que reçoit un hook de confiance.

    - `validate` : `data` et `old` sont en lecture seule ; lever `DocumentValidationError` pour annuler.
    - `before_save` : `data` est modifiable en place (revalidé ensuite par le schéma et les permissions).
    - `before_insert` : comme `before_save`, mais à la CRÉATION seulement et après lui ; `data` est modifiable en place.
    - `after_save` : appelé après le commit ; une erreur est journalisée et ne défait rien. `after_insert` : de même, à la création seulement, juste avant lui.
    - `on_submit` / `on_cancel` / `before_cancel` : dans la transaction du changement d'état, `data` est le document (figé, en lecture seule) et
      `tx` donne le droit d'écrire d'autres documents ; lever `DocumentValidationError` annule tout, état compris. (`before_cancel` passe avant
      `on_cancel` ; l'état est déjà posé, un refus le défait.)
    - `on_update_after_submit` : un document SOUMIS dont un champ `allow_on_submit` vient d'être modifié ; dans la transaction, `old` est l'ancien contenu.
    - `on_trash` : dans la transaction, juste avant la suppression du document (`data` = ce qu'il contient, `tx` disponible) ; un refus l'empêche.
      `after_delete` : après le commit, `data` est ce que le document contenait.
    `old` vaut `None` à la création. `name` vaut `None` pendant `validate`, `before_save` et `before_insert` d'une CRÉATION dans une série de
    numérotation ou nommée par un champ (`naming: {field: …}`) : le nom est attribué en dernier, juste avant l'insertion (pour la série, afin de ne
    tenir le compteur que le temps du commit, naming.py). `after_save` et `after_insert` reçoivent toujours le nom définitif.
    """

    event: HookName
    ctx: RequestContext
    doctype: str
    name: str | None
    data: Mapping[str, Any]
    old: Mapping[str, Any] | None
    _get: Callable[[str, str], Awaitable[dict[str, Any] | None]]
    tx: HookTransaction | None = None  # seulement pour `on_submit`, `on_cancel`, `before_cancel`, `on_update_after_submit` et `on_trash`

    async def get(self, doctype: str, name: str) -> dict[str, Any] | None:
        """Lit un document du même tenant, SANS filtre de permission : le code d'un hook de confiance en répond."""
        return await self._get(doctype, name)


# ── Réglages ────────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class HookSettings:
    # Le worker sandbox ignore sans répondre une ligne IPC > 64 KiB (limite du StreamReader) puis
    # xcore recycle le sous-processus au timeout. La ligne contient aussi l'enveloppe JSON : on garde de la marge.
    timeout_seconds: float = 5.0
    max_payload_bytes: int = 60_000

    @classmethod
    def from_plugin_config(cls, config: Mapping[str, Any]) -> "HookSettings":
        block = dict((config or {}).get("doctype") or {})
        out: dict[str, Any] = {}
        if "hook_timeout_seconds" in block:
            v = block["hook_timeout_seconds"]
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 < v <= 300:
                raise ConfigurationError("doctype.hook_timeout_seconds doit être un nombre entre 0 et 300")
            out["timeout_seconds"] = float(v)
        if "hook_max_payload_bytes" in block:
            v = block["hook_max_payload_bytes"]
            if isinstance(v, bool) or not isinstance(v, int) or not 1_024 <= v <= 63_488:
                raise ConfigurationError("doctype.hook_max_payload_bytes doit être un entier entre 1024 et 63488")
            out["max_payload_bytes"] = v
        return cls(**out)


# ── Hooks `action:` ─────────────────────────────────────────────────────────────────────────────

Caller = Callable[..., Awaitable[dict]]


class ActionHooks:
    def __init__(self, caller: Caller | None, plugin_name: str, settings: HookSettings, logger: Any) -> None:
        self._caller, self._name, self._settings, self._log = caller, plugin_name, settings, logger

    @staticmethod
    def split(ref: str) -> tuple[str, str]:
        m = ACTION_HOOK_RE.match(ref)
        if not m:
            raise ValueError(f"référence d'action invalide : {ref!r}")
        return m.group(1), m.group(2)

    async def run(
        self,
        ref: str,
        *,
        event: str,
        ctx: RequestContext,
        doctype: str,
        name: str | None,
        data: Mapping[str, Any],
        old: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        """Appelle l'action et retourne les données (éventuellement modifiées). Le service les revalide."""
        plugin, action = self.split(ref)
        payload = {
            "event": event,
            "doctype": doctype,
            "name": name,
            "doc": dict(data),
            "old": dict(old) if old is not None else None,
            "user": ctx.user_id,
            "roles": sorted(ctx.roles),
        }
        # Même sérialisation que le canal IPC (ASCII échappé) : c'est la taille réelle de la ligne envoyée.
        size = len(json.dumps(payload).encode())
        if size > self._settings.max_payload_bytes:
            raise DoctypeError(
                "hook_payload_too_large",
                f"document trop volumineux pour le hook {ref} ({size} octets, maximum {self._settings.max_payload_bytes})",
                status=413,
            )
        if self._caller is None:
            raise DoctypeError("hook_unavailable", "appel de plugin indisponible", status=503)

        try:
            result = await asyncio.wait_for(
                self._caller(plugin, action, payload, caller=self._name, tenant_id=ctx.tenant_id, principal=ctx.principal),
                timeout=self._settings.timeout_seconds,
            )
        except TimeoutError:
            raise DoctypeError("hook_timeout", f"le hook {ref} n'a pas répondu à temps", status=504) from None
        except Exception as e:  # noqa: BLE001 - toute panne d'appel est une indisponibilité, jamais un détail à exposer
            self._log.error("action hook call failed", hook=ref, error=repr(e))
            raise DoctypeError("hook_unavailable", f"le hook {ref} est indisponible", status=503) from None
        return self._interpret(ref, result, data)

    def _interpret(self, ref: str, result: Any, current: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(result, dict):
            self._log.error("action hook returned a non-object", hook=ref, type=type(result).__name__)
            raise DoctypeError("hook_invalid_result", f"réponse invalide du hook {ref}", status=502)
        status = result.get("status")
        if status == "ok":
            doc = result.get("doc", None)
            if doc is None:
                return dict(current)
            if not isinstance(doc, dict):
                raise DoctypeError("hook_invalid_result", f"le hook {ref} a renvoyé un `doc` qui n'est pas un objet", status=502)
            return doc
        if status == "error":
            code = result.get("code")
            if code == "timeout":
                raise DoctypeError("hook_timeout", f"le hook {ref} n'a pas répondu à temps", status=504)
            if code in _INFRA_CODES:
                # Permission, allowed_callers, plugin absent, crash du handler : un problème de déploiement.
                self._log.error("action hook infrastructure error", hook=ref, code=code, plugin_message=str(result.get("msg"))[:300])
                raise DoctypeError("hook_unavailable", f"le hook {ref} est indisponible", status=503)
            msg = result.get("msg")
            msg = msg if isinstance(msg, str) and msg else "refusé"
            raise DoctypeError(
                "hook_rejected",
                msg[:_MAX_REJECTION_MSG],
                status=422,
                details={"hook": ref, **({"code": code} if isinstance(code, str) else {})},
            )
        raise DoctypeError("hook_invalid_result", f"réponse invalide du hook {ref}", status=502)
