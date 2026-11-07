"""Le catalogue de l'éditeur d'automatisations pour UNE organisation : les nœuds que les plugins déclarent (`flownodes.FlowCatalog`), plus
ceux que l'organisation a décrits à l'écran (rangés en base, `flownodes.KIND_FLOW_NODE`).

Un nœud d'organisation sert à appeler une action IPC qu'aucun plugin n'a décrite : on lui donne un libellé, des champs, des sorties, et
l'éditeur le montre comme les autres. Ce n'est que de la DESCRIPTION : xflow exécute l'étape comme n'importe quelle action
`{plugin, action, payload}`, au nom de l'utilisateur du flux, et le plugin visé garde ses contrôles (`allowed_callers`, permissions).

Quand un plugin déclare la même action, sa description l'emporte dans la palette (c'est lui qui connaît son action) ; celle de
l'organisation reste listée, marquée `shadowed`, pour qu'on puisse la retirer.
"""

from __future__ import annotations

from typing import Any

from .context import RequestContext
from .document import DocumentService
from .errors import DoctypeError, NotFound, ValidationFailed
from .flownodes import DECLARE_GRANTS, KIND_FLOW_NODE, MAX_ORG_NODES, InvalidDefinition, org_target, parse_org_node


class FlowNodes:
    def __init__(self, documents: DocumentService) -> None:
        self._docs = documents

    @staticmethod
    def may_declare(ctx: RequestContext) -> bool:
        return not ctx.is_system and bool(ctx.permissions & DECLARE_GRANTS)

    async def _stored(self, ctx: RequestContext) -> list[dict[str, Any]]:
        async with self._docs._query(ctx) as q:
            rows = await q.overrides(KIND_FLOW_NODE)
        out = []
        for row in rows:
            try:  # un nœud enregistré qu'une version plus stricte refuserait ne casse pas l'éditeur : il est ignoré
                out.append(parse_org_node(row.get("config") or {}).model_dump(mode="json", exclude_none=True))
            except InvalidDefinition:
                continue
        return out

    async def catalog(self, ctx: RequestContext) -> dict[str, list[dict[str, Any]]]:
        """Ce que l'éditeur propose à l'appelant : les nœuds des plugins, puis ceux de son organisation qu'aucun plugin ne décrit déjà."""
        ctx.require_identity()
        served = self._docs.flow_catalog.as_dict()
        known = {(n["plugin"], n["action"]) for n in served["nodes"]}
        for n in served["nodes"]:
            n["source"] = "plugin"
        own = [{**n, "source": "organisation"} for n in await self._stored(ctx) if (n["plugin"], n["action"]) not in known]
        return {"nodes": served["nodes"] + own, "events": served["events"]}

    async def list(self, ctx: RequestContext) -> dict[str, Any]:
        """Les nœuds de l'organisation, pour l'écran qui les édite, et si l'appelant peut les modifier."""
        ctx.require_identity()
        known = {(n["plugin"], n["action"]) for n in self._docs.flow_catalog.as_dict()["nodes"]}
        nodes = [{**n, "shadowed": (n["plugin"], n["action"]) in known} for n in await self._stored(ctx)]
        return {"nodes": nodes, "can_edit": self.may_declare(ctx)}

    def _require_declare(self, ctx: RequestContext) -> None:
        ctx.require_identity()
        if not self.may_declare(ctx):
            raise DoctypeError("flow_node_reserved", "décrire un nœud d'automatisation demande le droit de déployer des automatisations", status=403)

    async def save(self, ctx: RequestContext, raw: Any, *, replacing: tuple[str, str] | None = None) -> dict[str, Any]:
        """Créer ou remplacer un nœud. `replacing` : la clé (plugin, action) qu'il avait, quand on la change en l'éditant."""
        self._require_declare(ctx)
        try:
            node = parse_org_node(raw)
        except InvalidDefinition as e:
            raise ValidationFailed(str(e), code="invalid_flow_node") from None
        config = node.model_dump(mode="json", exclude_none=True)
        target = org_target(node.plugin, node.action)
        async with self._docs._query(ctx) as q:
            existing = {r["target"] for r in await q.overrides(KIND_FLOW_NODE)}
            old = org_target(*replacing) if replacing else None
            if target not in existing and old not in existing and len(existing) >= MAX_ORG_NODES:
                raise ValidationFailed(f"au plus {MAX_ORG_NODES} nœuds par organisation", code="too_many_flow_nodes")
            if old and old != target:
                if target in existing:
                    raise ValidationFailed(f"un nœud décrit déjà {node.plugin} › {node.action}", code="flow_node_exists")
                await q.delete_override(KIND_FLOW_NODE, old)
            await q.put_override(KIND_FLOW_NODE, target, owner=ctx.owner_id, config=config)
        return config

    async def delete(self, ctx: RequestContext, plugin: str, action: str) -> dict[str, Any]:
        self._require_declare(ctx)
        async with self._docs._query(ctx) as q:
            if not await q.delete_override(KIND_FLOW_NODE, org_target(plugin, action)):
                raise NotFound("flow_node_not_found", "aucun nœud de l'organisation pour cette action")
        return {"deleted": True}
