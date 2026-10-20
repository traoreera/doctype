"""Plugin `system` : les réglages globaux de l'application. Livré avec xcore-doctype (`xcore-doctype install` le dépose à côté), aucun code métier :
il apporte `doctypes/system_settings.yaml` et déclare à auth les grants que ce YAML exige. Un plugin qui veut AJOUTER un réglage global n'a pas
besoin de lui : il déclare un DocType `single: true, settings_group: general` dans ses propres `doctypes/`.
"""

from pathlib import Path

from xcore.sdk import AutoDispatchMixin, TrustedBase

DOCTYPES = Path(__file__).resolve().parent.parent / "doctypes"


class Plugin(AutoDispatchMixin, TrustedBase):
    async def on_load(self) -> None:
        self.get_service("doctype_registry").load_dir(self.ctx.name, DOCTYPES)
        grants = self.get_service("doctype_grants").for_plugin(self.ctx.name)
        await self.ctx.events.emit("rbac.declare", {"plugin": self.ctx.name, "grants": grants}, source=self.ctx.name)

    async def on_unload(self) -> None:
        self.get_service("doctype_registry").unregister_owner(self.ctx.name)
