"""L'automatisation d'une transition : le workflow d'un DocType reste ICI, ce qui doit SUIVRE une décision part dans xflow.

Deux moteurs, une couture. Le workflow du moteur garde l'état du document — états, droits par transition, `not_owner`, gel,
boutons de la fiche — parce que tout cela vit dans la même transaction que le document. xflow (plugin de plateforme,
`platform/xflow`) orchestre la suite : appeler d'autres plugins, attendre, réessayer, prévenir. Une transition du YAML
nomme le flux qui la suit :

    workflow:
      transitions:
        - {action: approve, from: Soumis, to: Approuvé, flow: commande_fournisseur}

Après le COMMIT de la transition (jamais dedans : un flux est lent, faillible, et ne défait pas une approbation), le moteur demande à
xflow `trigger` le flux nommé, dans l'organisation de la transition et AU NOM de qui l'a faite (le principal de la requête ; xflow
ne garde que son identifiant et redemande ses droits à xauth à chaque exécution, donc un flux n'agit jamais avec plus de droits que
son déclencheur n'en a au moment où il tourne). Ce qui part : le document (type, nom, version, docstatus), l'action, l'état de
départ et d'arrivée, et l'utilisateur ; jamais le contenu — une étape qui en a besoin le relit par `doc.get`, avec ces droits-là.

La politique est celle des notifications : un flux est un EFFET DE BORD. Le lancement est une tâche de fond bornée
(`START_TIMEOUT`), et xflow absent, un flux pas déployé dans cette organisation ou un refus sont journalisés, jamais remontés : la
transition, elle, est faite. Le flux se déploie par organisation chez xflow (son API `/flows`), c'est pourquoi le moteur ne peut pas
vérifier au chargement qu'il existe : il vérifie seulement son NOM (`schema.FLOW_NAME`).
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Mapping

DEFAULT_PLUGIN = "xflow"
DEFAULT_ACTION = "trigger"
START_TIMEOUT = 10.0  # secondes pour qu'xflow accepte le run (il l'exécute ensuite de son côté, en file)

# (plugin, action, charge utile, tenant, principal) -> réponse d'xflow
Call = Callable[[str, str, dict[str, Any], str, Mapping[str, Any] | None], Awaitable[Any]]


class NoFlows:
    """Pas d'xflow (`doctype.flows: false`, ou hôte sans appel de plugin) : une transition qui nomme un flux est seulement faite."""

    def start(self, ctx: Any, flow: str, payload: dict[str, Any]) -> None:
        return None

    async def settled(self) -> None:
        return None

    async def close(self) -> None:
        return None


class FlowLauncher:
    def __init__(self, call: Call, logger: Any = None, *, plugin: str = DEFAULT_PLUGIN, action: str = DEFAULT_ACTION,
                 timeout: float = START_TIMEOUT) -> None:
        self._call, self._log = call, logger
        self._plugin, self._action, self._timeout = plugin, action, timeout
        self._pending: set[asyncio.Task[None]] = set()
        self._closed = False

    def start(self, ctx: Any, flow: str, payload: dict[str, Any]) -> None:
        """Après le commit. Ne bloque jamais la requête : le lancement part en tâche de fond."""
        if self._closed:
            return
        principal = dict(ctx.principal) if isinstance(getattr(ctx, "principal", None), Mapping) else None
        task = asyncio.get_running_loop().create_task(self._start(ctx.tenant_id, flow, payload, principal))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def _start(self, tenant_id: str, flow: str, payload: dict[str, Any], principal: dict[str, Any] | None) -> None:
        body = {"tenant_id": tenant_id, "workflow_name": flow, "payload": payload}
        try:
            answer = await asyncio.wait_for(self._call(self._plugin, self._action, body, tenant_id, principal), self._timeout)
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            self._warn("flow not started: timed out", flow=flow, doctype=payload.get("doctype"))
            return
        except Exception as e:  # noqa: BLE001 - un flux ne défait pas la transition
            self._warn("flow not started", flow=flow, doctype=payload.get("doctype"), error=repr(e))
            return
        # xflow répond l'état du RUN (`pending` : en file) ; seul `error` est un refus.
        if isinstance(answer, Mapping) and answer.get("status") == "error":
            # `not_found` : le flux n'est pas déployé dans cette organisation ; `ipc_denied` : xflow ne nomme pas le moteur.
            self._warn("flow refused", flow=flow, doctype=payload.get("doctype"), flow_code=answer.get("code"),
                       answer=str(answer.get("msg") or answer.get("message") or "")[:300])

    async def settled(self) -> None:
        while self._pending:
            await asyncio.gather(*list(self._pending), return_exceptions=True)

    async def close(self) -> None:
        self._closed = True
        for task in list(self._pending):
            task.cancel()
        await asyncio.gather(*list(self._pending), return_exceptions=True)

    def _warn(self, message: str, **fields: Any) -> None:
        if self._log is None:
            return
        try:
            self._log.warning(message, **fields)  # pas de champ `plugin` ni `msg` : le logger d'xcore les réserve
        except Exception:  # noqa: BLE001
            pass
