"""Plugin propriétaire du module `geo` : un RÉFÉRENTIEL, c'est-à-dire des DocTypes faits pour que d'autres plugins les citent.

Il n'apporte que du YAML (aucun hook, aucune action) : c'est le plugin le plus simple qui existe sur xcore-doctype. Tout
l'intérêt est dans les deux DocTypes qu'il publie, dont la LECTURE est ouverte à tout utilisateur identifié
(`permissions: {read: ["@authenticated"]}`) : un plugin qui les cite par un champ Link n'oblige donc pas ses propres
utilisateurs à détenir un grant du module `geo`. L'ÉCRITURE, elle, reste à `geo` (grants déduits : `geo:pays:write`…).
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
