"""Un plugin métier apporte ses DocTypes à xcore-doctype (service `doctype_registry`) sur un vrai Xcore."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tests.integration.conftest import boot_host
from tests.support import FIXTURES

SALES, STOCK, GEO, CRM = FIXTURES / "sales", FIXTURES / "stock", FIXTURES / "geo", FIXTURES / "crm"
# La chaîne des dépendances : `sales` exige `stock` (l'article, cible des lignes de facture, est un DocType du module
# stock), qui exige `geo` (le pays de l'entrepôt vient du référentiel partagé).
PLUGINS = {"geo": GEO, "sales": SALES, "stock": STOCK}
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
        # `Devise` et `Pays` appartiennent à `geo` : ce jeton n'a aucun grant `geo` et les lit quand même (`@authenticated`).
        assert [m["name"] for m in meta] == ["Country", "Customer", "Devise", "Invoice", "Item", "Pays"]

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


async def test_a_plugin_reuses_another_plugins_referential(tmp_path):
    """`crm` cite les pays de `geo` : un champ Link et deux `fetch_from` vers les DocTypes d'un autre plugin.

    Le point du test est le DROIT : le jeton du commercial ne porte QUE des grants `crm`, et il lit pourtant le
    référentiel (sa lecture est ouverte à tout utilisateur identifié), sans jamais pouvoir y écrire. Les règles fines
    sont dans tests/unit/test_geo.py ; ici on vérifie que les deux plugins se chargent et s'articulent sur un vrai hôte.
    """
    async with boot_host(tmp_path, operator_doctypes=False, link_plugins={"geo": GEO, "crm": CRM}) as h:
        assert (await state_of(h, "geo"), await state_of(h, "crm")) == ("ready", "ready")
        seller = h.token(permissions=["crm:lead:read", "crm:lead:write"])
        librarian = h.token(permissions=["geo:pays:write", "geo:pays:delete", "geo:devise:write"])

        assert (await h.http("POST", "/doc/Devise", token=librarian, json={"data": {"code": "XOF", "nom": "Franc CFA", "decimales": 0}})).status_code == 201
        pays = {"code": "CI", "nom": "Côte d'Ivoire", "devise": "XOF", "indicatif": "+225", "langue": "fr"}
        assert (await h.http("POST", "/doc/Pays", token=librarian, json={"data": pays})).json()["name"] == "CI"
        # Le commercial ne peut pas écrire dans le référentiel…
        assert (await h.http("POST", "/doc/Pays", token=seller, json={"data": {"code": "ML", "nom": "Mali"}})).status_code == 403
        # … mais il le lit, et son autocomplétion fonctionne.
        assert [i["name"] for i in (await h.http("GET", "/search/Pays?q=Ivoire", token=seller)).json()["items"]] == ["CI"]

        r = await h.http("POST", "/doc/Lead", token=seller, json={"data": {"lead_name": "Soco", "pays": "CI", "indicatif": "+1"}})
        assert r.status_code == 201, r.text
        # Ce que `crm` a demandé au pays est recopié par le serveur ; ce que le client avait envoyé est écrasé.
        assert (r.json()["data"]["indicatif"], r.json()["data"]["langue"]) == ("+225", "fr")
        # Et le pays cité ne peut plus partir, même pour celui qui tient le référentiel et ne voit aucun prospect.
        gone = await h.http("DELETE", "/doc/Pays/CI", token=librarian)
        assert (gone.status_code, gone.json()["detail"]["code"]) == (409, "document_in_use")

        spaces = (await h.http("GET", "/workspaces", token=seller)).json()
        assert [w["name"] for w in spaces] == ["crm", "geo"]  # order 40 puis 80


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


CRM_MAIN = '''
from pathlib import Path
from xcore.sdk import TrustedBase, error

HERE = Path(__file__).resolve().parent.parent


class Plugin(TrustedBase):
    async def on_load(self):
        self.get_service("doctype_hooks").register("crm.tag_item", self.tag_item, owner=self.ctx.name)
        self.get_service("doctype_registry").load_extensions(self.ctx.name, HERE / "extends")

    async def on_unload(self):
        self.get_service("doctype_hooks").unregister_owner(self.ctx.name)
        self.get_service("doctype_registry").unregister_owner(self.ctx.name)

    async def tag_item(self, event):
        event.data["crm_tag"] = "suivi"

    async def handle(self, action, payload):
        return error("no actions", "unknown_action")
'''


async def test_a_plugin_extends_another_plugins_doctype_with_a_field_and_a_hook(tmp_path):
    """D26 : `crm` ajoute un champ et un hook `before_save` à l'article de `stock`, sans toucher au YAML de `stock`."""
    extension = {"schema_version": 1, "doctype": "Item", "label": "Relation client",
                 "fields": [{"fieldname": "crm_tag", "fieldtype": "Text", "label": "Suivi"}], "hooks": {"before_save": ["crm.tag_item"]}}
    manifest = {"name": "crm", "version": "0.1.0", "framework_version": ">=2.9", "execution_mode": "trusted",
                "entry_point": "src/main.py", "requires": ["xcore-doctype", "stock"]}
    crm = (CRM_MAIN, {"plugin.yaml": yaml.safe_dump(manifest), "extends/item.yaml": yaml.safe_dump(extension, allow_unicode=True)})
    async with boot_host(tmp_path, operator_doctypes=False, link_plugins={"geo": GEO, "stock": STOCK}, inline_plugins={"crm": crm}) as h:
        assert await state_of(h, "crm") == "ready"
        t = h.token(permissions=GRANTS)
        meta = (await h.http("GET", "/meta/Item", token=t)).json()
        assert meta["fields"][-1]["fieldname"] == "crm_tag"
        assert meta["extended_by"] == [{"plugin": "crm", "label": "Relation client", "fields": ["crm_tag"]}]
        r = await h.http("POST", "/doc/Item", token=t, json={"data": {"item_name": "Vis", "uom": "pcs"}})
        assert r.status_code == 201, r.text
        assert r.json()["data"]["crm_tag"] == "suivi"  # le hook de crm a tourné sur un DocType de stock

        assert (await h.ipc("DELETE", "/crm/unload")).status_code == 200
        meta = (await h.http("GET", "/meta/Item", token=t)).json()
        assert "crm_tag" not in [f["fieldname"] for f in meta["fields"]] and "extended_by" not in meta
        r = await h.http("POST", "/doc/Item", token=t, json={"data": {"item_name": "Clou", "uom": "pcs"}})
        assert r.status_code == 201, r.text  # ni champ ni hook : le DocType de stock redevient lui-même
        assert "crm_tag" not in r.json()["data"]
