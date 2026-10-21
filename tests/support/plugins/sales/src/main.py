"""Plugin propriétaire du module `sales` : l'exemple de ce qu'un plugin métier fait de xcore-doctype.

1. il apporte ses DocTypes (`../doctypes/*.yaml`), dont le `module` est son propre nom : xcore-doctype refuse les autres ;
2. il enregistre les hooks de confiance que ces DocTypes citent par leur nom ;
3. il déclare à auth les grants que ses DocTypes exigent, tirés du YAML (source unique : rien n'est retapé ici) ;
4. il livre une extension JavaScript du Desk (`../desk/index.js`) : un panneau et une action sur la fiche Client, une page, une commande.
"""

from pathlib import Path

from xcore.sdk import TrustedBase, error

DOCTYPES = Path(__file__).resolve().parent.parent / "doctypes"


class Plugin(TrustedBase):
    async def on_load(self) -> None:
        # Les hooks d'abord : un DocType dont un hook manque refuse ses sauvegardes (503), il vaut mieux qu'ils existent à l'arrivée.
        self.get_service("doctype_hooks").register("sales.invoice_totals", self.invoice_totals, owner=self.ctx.name)
        registry = self.get_service("doctype_registry")
        registry.load_dir(self.ctx.name, DOCTYPES)
        registry.load_workspaces(self.ctx.name, DOCTYPES.parent / "workspaces")  # APRÈS les DocTypes : les raccourcis les visent
        # 4. son extension du Desk (D28) : servie seulement si l'opérateur l'autorise (`doctype.desk.extensions`).
        registry.load_desk(self.ctx.name, DOCTYPES.parent / "desk")
        # Un SEUL `rbac.declare` pour tout le plugin, APRÈS le chargement des DocTypes : auth désactive les grants
        # qu'il ne retrouve plus dans la liste reçue.
        grants = self.get_service("doctype_grants").for_plugin(self.ctx.name)
        await self.ctx.events.emit("rbac.declare", {"plugin": self.ctx.name, "grants": grants}, source=self.ctx.name)

    async def on_unload(self) -> None:
        self.get_service("doctype_hooks").unregister_owner(self.ctx.name)
        self.get_service("doctype_registry").unregister_owner(self.ctx.name)

    async def invoice_totals(self, event) -> None:
        """`before_save` de `Invoice` : le total est la somme des lignes. Ce que le client envoie pour `total` est écrasé."""
        rows = event.data.get("items") or []
        event.data["total"] = round(sum(row["qty"] * row["rate"] for row in rows), 2)

    async def handle(self, action, payload):
        return error("no actions", "unknown_action")
