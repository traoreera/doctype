"""`doctype_channels` sur un vrai hôte : un plugin de confiance déclare un canal (le faux WhatsApp d'un module), et ce que la cloche
reçoit — ici une assignation faite par l'API HTTP — lui arrive avec le document, en tâche de fond, sans rien changer à la réponse."""

from __future__ import annotations

import asyncio

import yaml

from tests.integration.conftest import boot_host

MAIN = '''
from pathlib import Path
from xcore.sdk import TrustedBase, error

BASE = Path(__file__).resolve().parent.parent


class Plugin(TrustedBase):
    async def on_load(self):
        self.received = []
        self.get_service("doctype_registry").load_dir(self.ctx.name, BASE / "doctypes")
        self.get_service("doctype_channels").register(self.ctx.name, "whatsapp", self.send, label="WhatsApp", listen_all=True)

    async def on_unload(self):
        self.get_service("doctype_channels").unregister_owner(self.ctx.name)
        self.get_service("doctype_registry").unregister_owner(self.ctx.name)

    async def send(self, notification):
        if notification.doctype == "Order":  # le filtre du module : ce qui ne le concerne pas ne part pas
            self.received.append(notification)

    async def handle(self, action, payload):
        return error("no actions", "unknown_action")
'''

ORDER = {"schema_version": 1, "name": "Order", "module": "shop", "fields": [{"fieldname": "title", "fieldtype": "Text"}]}
WRITER = ["shop:order:read", "shop:order:write"]


async def test_a_plugin_channel_receives_what_rings_the_bell(tmp_path):
    plugins = {"shop": (MAIN, {"doctypes/order.yaml": yaml.safe_dump(ORDER)})}
    async with boot_host(tmp_path, inline_plugins=plugins) as h:
        shop = h.xcore.plugins._loader.get("shop")._instance
        ada = h.token(sub="ada", permissions=WRITER)
        created = await h.http("POST", "/doc/Order", token=ada, json={"data": {"title": "o1"}})
        name = created.json()["name"]
        r = await h.http("POST", f"/assignments/Order/{name}", token=ada, json={"assignee": "rita", "description": "À relire"})
        assert r.status_code == 201, r.text
        for _ in range(100):
            if shop.received:
                break
            await asyncio.sleep(0.02)
        [n] = shop.received
        assert (n.kind, n.doctype, n.name, n.user_ids, n.message) == ("ASSIGNMENT", "Order", name, ("rita",), "À relire")
