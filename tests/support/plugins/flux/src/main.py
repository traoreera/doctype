"""Fixture `flux` : un type à circuit d'approbation, sans aucun code métier. Elle n'existe que pour le scénario de navigateur de l'éditeur du
circuit (`ui/e2e/workflow.e2e.mjs`) : `hr` a le même circuit, mais il requiert `iam`, donc le vrai xauth, que l'hôte de test n'a pas."""

from pathlib import Path

from xcore.sdk import AutoDispatchMixin, TrustedBase

DOCTYPES = Path(__file__).resolve().parent.parent / "doctypes"


class Plugin(AutoDispatchMixin, TrustedBase):
    async def on_load(self) -> None:
        me = self.ctx.name
        self.get_service("doctype_registry").load_dir(me, DOCTYPES)
        grants = self.get_service("doctype_grants").for_plugin(me)
        await self.ctx.events.emit("rbac.declare", {"plugin": me, "grants": grants}, source=me)

    async def on_unload(self) -> None:
        self.get_service("doctype_registry").unregister_owner(self.ctx.name)
