"""Un plugin métier apporte ses DocTypes à xcore-doctype (service `doctype_registry`) sur un vrai Xcore."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tests.integration.conftest import boot_host
from tests.support import FIXTURES

SALES, STOCK = FIXTURES / "sales", FIXTURES / "stock"
# `sales` exige `stock` (requires) : l'article, cible des lignes de facture, est un DocType du module stock.
PLUGINS = {"sales": SALES, "stock": STOCK}
GRANTS = [f"sales:{t}:{a}" for t in ("customer", "country", "invoice") for a in ("read", "write", "delete")] + [
    f"stock:item:{a}" for a in ("read", "write", "delete")
]


async def state_of(host, name: str) -> str | None:
    plugins = (await host.ipc("GET", "/status")).json()["plugins"]
    return next((p["state"] for p in plugins if p["name"] == name), None)


async def test_the_sales_plugin_brings_its_doctypes_its_hook_and_its_grants(tmp_path):
    async with boot_host(tmp_path, operator_doctypes=False, link_plugins=PLUGINS) as h:
        t = h.token(permissions=GRANTS)
        # Aucun DocType n'est dans le dossier de l'opérateur : tout vient des plugins `sales` et `stock`.
        meta = (await h.http("GET", "/meta", token=t)).json()
        assert [m["name"] for m in meta] == ["Country", "Customer", "Invoice", "Item"]

        customer = (await h.http("POST", "/doc/Customer", token=t, json={"data": {"customer_name": "Acme"}})).json()
        item = (await h.http("POST", "/doc/Item", token=t, json={"data": {"item_name": "Vis", "uom": "pcs"}})).json()
        lines = [{"item": item["name"], "qty": 3, "rate": 2.5}, {"item": item["name"], "qty": 1, "rate": 10}]
        r = await h.http("POST", "/doc/Invoice", token=t, json={"data": {"customer": customer["name"], "items": lines}})
        assert r.status_code == 201, r.text
        invoice = r.json()
        assert invoice["name"].startswith("FA-") and invoice["name"].endswith("-00001")  # série
        assert invoice["data"]["total"] == 17.5  # le hook du plugin a calculé le total

        # Un seul rbac.declare PAR plugin, émis APRÈS le chargement de ses DocTypes : il contient donc leurs grants.
        (event,) = [e for e in h.rbac_declared if e["plugin"] == "sales"]
        names = {g["name"] for g in event["grants"]}
        assert {"sales:invoice:write", "sales:country:delete", "sales:customer:own"} <= names
        assert not any(n.startswith("stock:") for n in names)  # les grants de l'article sont déclarés par SON plugin
        (stock,) = [e for e in h.rbac_declared if e["plugin"] == "stock"]
        assert {"stock:item:read", "stock:stock_entry:submit", "stock:stock_entry:cancel"} <= {g["name"] for g in stock["grants"]}


async def test_a_plugin_cannot_put_doctypes_in_another_modules_namespace(tmp_path):
    rogue = {"schema_version": 1, "name": "Sneaky", "module": "sales", "fields": [{"fieldname": "x", "fieldtype": "Text"}]}
    main = '''
from pathlib import Path
from xcore.sdk import TrustedBase


class Plugin(TrustedBase):
    async def on_load(self):
        self.get_service("doctype_registry").load_dir(self.ctx.name, Path(__file__).resolve().parent.parent / "doctypes")
'''
    plugin = (main, {"doctypes/sneaky.yaml": yaml.safe_dump(rogue)})
    async with boot_host(tmp_path, operator_doctypes=False, inline_plugins={"rogue": plugin}) as h:
        assert await state_of(h, "rogue") != "ready"  # son on_load a échoué : `module: sales` n'est pas le sien
        assert await state_of(h, "xcore-doctype") == "ready"
        assert (await h.http("GET", "/meta/Sneaky", token=h.token(permissions=GRANTS))).status_code in (403, 404)


async def test_unloading_the_plugin_removes_its_doctypes_but_not_its_documents(tmp_path):
    async with boot_host(tmp_path, operator_doctypes=False, link_plugins=PLUGINS) as h:
        t = h.token(permissions=GRANTS)
        item = (await h.http("POST", "/doc/Item", token=t, json={"data": {"item_name": "Vis", "uom": "pcs"}})).json()

        # L'article appartient à `stock` : décharger `stock` fait partir ses DocTypes (pas ceux de `sales`).
        r = await h.ipc("DELETE", "/stock/unload")
        assert r.status_code == 200, r.text
        assert (await h.http("GET", "/meta/Item", token=t)).status_code == 404  # le DocType a quitté avec son plugin
        assert "Item" not in [m["name"] for m in (await h.http("GET", "/meta", token=t)).json()]

        assert (await h.ipc("POST", "/stock/load")).status_code == 200
        got = await h.http("GET", f"/doc/Item/{item['name']}", token=t)
        assert got.status_code == 200 and got.json()["data"]["item_name"] == "Vis"  # le document était resté en base
