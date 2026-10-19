"""Un plugin de test qui déclare des grants DÉLÉGABLES (ceux d'un module métier), pour que les rôles et les fiches de membres aient de quoi attribuer."""

from __future__ import annotations

import yaml

MAIN = '''
from pathlib import Path
from xcore.sdk import TrustedBase, error

BASE = Path(__file__).resolve().parent.parent


class Plugin(TrustedBase):
    async def on_load(self):
        self.get_service("doctype_registry").load_dir(self.ctx.name, BASE / "doctypes")
        grants = [{**g, "tenant_grantable": True} for g in self.get_service("doctype_grants").for_plugin(self.ctx.name)]
        await self.ctx.events.emit("rbac.declare", {"plugin": self.ctx.name, "grants": grants}, source=self.ctx.name)

    async def on_unload(self):
        self.get_service("doctype_registry").unregister_owner(self.ctx.name)

    async def handle(self, action, payload):
        return error("no actions", "unknown_action")
'''
ORDER = {"schema_version": 1, "name": "Order", "label": "Commande", "module": "shop", "fields": [{"fieldname": "title", "fieldtype": "Text"}]}
MANIFEST = yaml.safe_dump({"name": "shop", "version": "0.1.0", "framework_version": ">=2.9", "execution_mode": "trusted", "entry_point": "src/main.py", "requires": ["xcore-doctype", "auth"]})
GRANTS_PLUGIN = (MAIN, {"doctypes/order.yaml": yaml.safe_dump(ORDER), "plugin.yaml": MANIFEST})
