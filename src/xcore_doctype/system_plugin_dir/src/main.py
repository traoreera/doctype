"""Plugin `system` : les réglages globaux de l'application. Livré avec xcore-doctype (`xcore-doctype install` le dépose à côté), aucun code métier :
il apporte `doctypes/` (les réglages, `System Settings`, et les règles de notification de l'organisation, `Notification Rule`, que le moteur
évalue : notification_rules.py), l'espace « Administration » (`workspaces/`) et déclare à auth les grants que ces YAML exigent. Un plugin qui veut AJOUTER un réglage global n'a pas
besoin de lui : il déclare un DocType `single: true, settings_group: general` dans ses propres `doctypes/`.
"""

from pathlib import Path

from xcore.sdk import AutoDispatchMixin, TrustedBase

DOCTYPES = Path(__file__).resolve().parent.parent / "doctypes"


class Plugin(AutoDispatchMixin, TrustedBase):
    async def on_load(self) -> None:
        registry = self.get_service("doctype_registry")
        registry.load_dir(self.ctx.name, DOCTYPES)
        if (DOCTYPES.parent / "workspaces").is_dir():
            registry.load_workspaces(self.ctx.name, DOCTYPES.parent / "workspaces")  # APRÈS les DocTypes : les blocs les visent
        grants = self.get_service("doctype_grants").for_plugin(self.ctx.name)
        await self.ctx.events.emit("rbac.declare", {"plugin": self.ctx.name, "grants": grants}, source=self.ctx.name)

    async def on_unload(self) -> None:
        self.get_service("doctype_registry").unregister_owner(self.ctx.name)
