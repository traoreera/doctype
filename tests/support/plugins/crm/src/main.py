"""Plugin propriétaire du module `crm` : l'exemple de celui qui RÉUTILISE le référentiel d'un autre plugin.

Il ne contient aucun code de plus que `geo` : citer les DocTypes d'un autre module ne demande rien au moteur, seulement
`requires: [geo]` dans le manifeste (xcore charge `geo` d'abord, donc ses DocTypes sont là quand les Link de `crm` sont
vérifiés). Tout le reste est dans `doctypes/lead.yaml` : le champ Link vers `Pays`, et les valeurs que `fetch_from`
recopie du pays à chaque écriture.
"""

from pathlib import Path

from xcore.sdk import AutoDispatchMixin, TrustedBase

DOCTYPES = Path(__file__).resolve().parent.parent / "doctypes"


class Plugin(AutoDispatchMixin, TrustedBase):
    async def on_load(self) -> None:
        registry = self.get_service("doctype_registry")
        registry.load_dir(self.ctx.name, DOCTYPES)
        registry.load_workspaces(self.ctx.name, DOCTYPES.parent / "workspaces")  # APRÈS les DocTypes : les blocs les visent
        grants = self.get_service("doctype_grants").for_plugin(self.ctx.name)
        await self.ctx.events.emit("rbac.declare", {"plugin": self.ctx.name, "grants": grants}, source=self.ctx.name)

    async def on_unload(self) -> None:
        self.get_service("doctype_registry").unregister_owner(self.ctx.name)
