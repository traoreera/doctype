"""Plugin propriétaire du module `stock` (quantités seulement, palier 40 % phase A) : articles, entrepôts, mouvements, journal, soldes.

Comme `sales`, il (1) enregistre les hooks de confiance que ses DocTypes citent, (2) apporte ses DocTypes (`module: stock`, son
propre nom) et (3) déclare à auth les grants que le YAML exige. La logique du stock est dans `ledger.py`.
"""

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from xcore.sdk import AutoDispatchMixin, TrustedBase, action, error, ok

from .ledger import HOOKS, verify_balances

DOCTYPES = Path(__file__).resolve().parent.parent / "doctypes"


class Plugin(AutoDispatchMixin, TrustedBase):
    async def on_load(self) -> None:
        # Les hooks d'abord : un DocType dont un hook manque refuse ses sauvegardes (503).
        hooks = self.get_service("doctype_hooks")
        for name, fn in HOOKS.items():
            hooks.register(name, fn, owner=self.ctx.name)
        registry = self.get_service("doctype_registry")
        registry.load_dir(self.ctx.name, DOCTYPES)
        registry.load_workspaces(self.ctx.name, DOCTYPES.parent / "workspaces")  # APRÈS les DocTypes : les raccourcis les visent
        grants = self.get_service("doctype_grants").for_plugin(self.ctx.name)
        await self.ctx.events.emit("rbac.declare", {"plugin": self.ctx.name, "grants": grants}, source=self.ctx.name)
        self._router = self._buttons()

    def get_router(self) -> APIRouter | None:
        return getattr(self, "_router", None)

    def _buttons(self) -> APIRouter:
        """Un BOUTON serveur (M4) : « Réception rapide » crée un mouvement brouillon avec les droits de l'UTILISATEUR de la requête
        (`doctype_writer.as_request`) — un grant manquant donne le même 403 que l'API, un entrepôt qu'il ne lit pas le même refus."""
        router = APIRouter()
        writers = self.get_service("doctype_writer")

        @router.post("/quick-receipt", status_code=201)
        async def quick_receipt(request: Request, body: dict) -> dict:
            writer = await writers.as_request(request)
            line = {"item": body.get("item"), "qty": body.get("qty"), "target_warehouse": body.get("warehouse")}
            try:
                return await writer.create("Stock Entry", {"purpose": "Réception", "items": [line]})
            except Exception as e:  # noqa: BLE001 — une erreur du moteur garde son code et son statut
                status = getattr(e, "status", None)
                if status is None:
                    raise
                raise HTTPException(status, e.as_detail()) from e

        return router

    async def on_unload(self) -> None:
        self.get_service("doctype_hooks").unregister_owner(self.ctx.name)
        self.get_service("doctype_registry").unregister_owner(self.ctx.name)

    @action("balances.verify")
    async def verify(self, payload: dict) -> dict:
        """Recalcule les soldes d'un tenant depuis le journal et rend les écarts (`ok: true` quand tout concorde). Ne corrige rien.

        Appel d'opérateur (clé du plugin) : le tenant est nommé dans la charge, la lecture est faite en contexte système.
        """
        tenant = (payload or {}).get("tenant")
        if not isinstance(tenant, str) or not tenant:
            return error("`tenant` est obligatoire", "missing_field")
        try:
            reader = self.get_service("doctype_documents").system(tenant, "stock.verify_balances")
        except Exception:  # noqa: BLE001 - tenant invalide : le détail n'a rien à faire dans la réponse
            return error("tenant invalide", "invalid_tenant")

        async def page(doctype: str, offset: int, limit: int) -> dict:
            return await reader.list(doctype, offset=offset, limit=limit, order_by="name", descending=False)

        problems = await verify_balances(page)
        return ok(ok=not problems, problems=problems)
