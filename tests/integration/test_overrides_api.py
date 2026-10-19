"""Habillages (overrides) de bout en bout : routes `/override/...`, action IPC, permissions, validation, retour au YAML."""

from __future__ import annotations

import yaml

from tests.integration.conftest import boot_host

SHOP_MAIN = '''
from pathlib import Path
from xcore.sdk import TrustedBase, error

BASE = Path(__file__).resolve().parent.parent


class Plugin(TrustedBase):
    async def on_load(self):
        reg = self.get_service("doctype_registry")
        reg.load_dir(self.ctx.name, BASE / "doctypes")
        reg.load_workspaces(self.ctx.name, BASE / "workspaces")

    async def handle(self, action, payload):
        return error("no actions", "unknown_action")
'''

ORDER = {
    "schema_version": 1,
    "name": "Order",
    "label": "Commande",
    "module": "shop",
    "fields": [
        {"fieldname": "title", "fieldtype": "Text", "required": True},
        {"fieldname": "note", "fieldtype": "Text"},
    ],
}
SPACE = {
    "schema_version": 1,
    "name": "shop",
    "label": "Boutique",
    "blocks": [{"type": "shortcut", "doctype": "Order", "new": True}],
}
GRANTS = ["shop:order:read", "shop:order:write"]


def shop_plugin() -> dict[str, tuple[str, dict[str, str]]]:
    return {
        "shop": (
            SHOP_MAIN,
            {"doctypes/order.yaml": yaml.safe_dump(ORDER), "workspaces/shop.yaml": yaml.safe_dump(SPACE)},
        )
    }


FORM_BODY = {"layout": [{"section": "Haut", "columns": [["title"]]}], "fields": {"note": {"label": "Remarque"}}}
WS_BODY = {"label": "Caisse", "blocks": [{"type": "shortcut", "doctype": "Order", "new": False}]}


async def test_a_form_override_replaces_the_yaml_layout_and_can_be_removed(tmp_path):
    async with boot_host(tmp_path, inline_plugins=shop_plugin()) as h:
        t = h.token(permissions=GRANTS)
        assert (await h.http("GET", "/override/form/Order", token=t)).json()["config"] == {}
        r = await h.http("PUT", "/override/form/Order", token=t, json={"config": FORM_BODY})
        assert r.status_code == 200, r.text
        meta = (await h.http("GET", "/meta/Order", token=t)).json()
        assert meta["layout"][0]["section"] == "Haut"
        assert next(f for f in meta["fields"] if f["fieldname"] == "note")["label"] == "Remarque"
        # L'action IPC voit le même habillage que la route.
        result = await h.action("doc.override.form.get", {"doctype": "Order"}, token=t)
        assert result["config"]["layout"][0]["section"] == "Haut"
        assert (await h.http("DELETE", "/override/form/Order", token=t)).status_code == 204
        assert (await h.http("GET", "/meta/Order", token=t)).json()["layout"][0]["section"] is None


async def test_a_workspace_override_replaces_the_plugin_page(tmp_path):
    async with boot_host(tmp_path, inline_plugins=shop_plugin()) as h:
        t = h.token(permissions=GRANTS)
        assert [w["label"] for w in (await h.http("GET", "/workspaces", token=t)).json()] == ["Boutique"]
        r = await h.http("PUT", "/override/workspace/shop", token=t, json={"config": WS_BODY})
        assert r.status_code == 200, r.text
        assert (await h.http("GET", "/workspaces", token=t)).json()[0]["label"] == "Caisse"
        assert (await h.http("DELETE", "/override/workspace/shop", token=t)).status_code == 204
        assert (await h.http("GET", "/workspaces", token=t)).json()[0]["label"] == "Boutique"


async def test_writing_an_override_needs_write_rights(tmp_path):
    async with boot_host(tmp_path, inline_plugins=shop_plugin()) as h:
        reader = h.token(sub="bob", permissions=["shop:order:read"])
        assert (await h.http("PUT", "/override/form/Order", token=reader, json={"config": FORM_BODY})).status_code == 403
        assert (await h.http("PUT", "/override/workspace/shop", token=reader, json={"config": WS_BODY})).status_code == 403


async def test_an_invalid_config_is_refused(tmp_path):
    async with boot_host(tmp_path, inline_plugins=shop_plugin()) as h:
        t = h.token(permissions=GRANTS)
        r = await h.http("PUT", "/override/form/Order", token=t, json={"config": {"layout": [{"columns": [["nope"]]}]}})
        assert r.status_code == 422 and r.json()["detail"]["code"] == "validation_failed"
        r = await h.http("PUT", "/override/workspace/shop", token=t, json={"config": {"blocks": [{"type": "shortcut", "doctype": "Nope"}]}})
        assert r.status_code == 422 and r.json()["detail"]["code"] == "validation_failed"


async def test_an_unknown_workspace_is_not_a_thing(tmp_path):
    async with boot_host(tmp_path, inline_plugins=shop_plugin()) as h:
        t = h.token(permissions=GRANTS)
        r = await h.http("PUT", "/override/workspace/nope", token=t, json={"config": WS_BODY})
        assert r.status_code == 404


async def test_a_form_override_is_per_tenant(tmp_path):
    async with boot_host(tmp_path, inline_plugins=shop_plugin()) as h:
        acme = h.token(permissions=GRANTS)
        await h.http("PUT", "/override/form/Order", token=acme, json={"config": FORM_BODY})
        globex = h.token(sub="bob", tenant="globex", permissions=GRANTS)
        assert (await h.http("GET", "/override/form/Order", token=globex, tenant="globex")).json()["config"] == {}
        assert (await h.http("GET", "/meta/Order", token=globex, tenant="globex")).json()["layout"][0]["section"] is None


BOARD = {"blocks": [{"type": "header", "text": "Suivi"}, {"type": "number_card", "label": "Commandes", "cols": 6}, {"type": "chart", "label": "Par mois", "group_by": "creation", "bucket": "month", "kind": "bar", "cols": 6}]}


async def test_a_dashboard_override_replaces_the_generated_one_and_can_be_removed(tmp_path):
    async with boot_host(tmp_path, inline_plugins=shop_plugin()) as h:
        t = h.token(permissions=GRANTS)
        generated = (await h.http("GET", "/meta/Order", token=t)).json()["views"]["dashboard"]
        r = await h.http("PUT", "/override/dashboard/Order", token=t, json={"config": BOARD})
        assert r.status_code == 200, r.text
        views = (await h.http("GET", "/meta/Order", token=t)).json()["views"]
        # Le type cible reste implicite dans ce qui est enregistré, et chaque carte le cite dans ce que reçoit le Desk.
        assert [b["type"] for b in views["dashboard"]] == ["header", "number_card", "chart"]
        assert all(b["doctype"] == "Order" for b in views["dashboard"] if b["type"] != "header")
        assert "doctype" not in r.json()["config"]["blocks"][1]
        # L'action IPC applique la même validation que la route.
        again = await h.action("doc.override.dashboard.save", {"doctype": "Order", "config": BOARD}, token=t)
        assert again["config"] == r.json()["config"]
        assert (await h.http("DELETE", "/override/dashboard/Order", token=t)).status_code == 204
        assert (await h.http("GET", "/meta/Order", token=t)).json()["views"]["dashboard"] == generated


async def test_a_dashboard_override_is_validated_like_the_yaml(tmp_path):
    async with boot_host(tmp_path, inline_plugins=shop_plugin()) as h:
        t = h.token(permissions=GRANTS)
        bad = [
            ({"blocks": []}, "1 à 100"),
            ({"blocks": [{"type": "chart", "label": "x", "group_by": "note"}]}, "regroupement impossible"),
            ({"blocks": [{"type": "number_card", "label": "x", "doctype": "Order"}]}, "n'a pas de sens ici"),  # le type est implicite
            ({"blocks": [{"type": "shortcut", "doctype": "Order"}]}, "invalide"),  # pas un bloc de tableau de bord
            ({"blocks": [{"type": "number_card", "label": "x", "function": "sum", "field": "note"}]}, "seul un Number"),
            ({"blocks": [{"type": "header", "text": "x"}], "extra": 1}, "clés inconnues"),
        ]
        for config, fragment in bad:
            r = await h.http("PUT", "/override/dashboard/Order", token=t, json={"config": config})
            assert r.status_code == 422 and r.json()["detail"]["code"] == "validation_failed", (config, r.text)
            assert fragment in r.text, (fragment, r.text)
        # Rien n'a été enregistré : le tableau généré est toujours celui qu'on reçoit.
        assert (await h.http("GET", "/meta/Order", token=t)).json()["views"]["dashboard"][0]["label"] == "Commande"


async def test_a_dashboard_override_needs_write_rights_and_stays_in_its_tenant(tmp_path):
    async with boot_host(tmp_path, inline_plugins=shop_plugin()) as h:
        reader = h.token(sub="bob", permissions=["shop:order:read"])
        assert (await h.http("PUT", "/override/dashboard/Order", token=reader, json={"config": BOARD})).status_code == 403
        assert (await h.http("DELETE", "/override/dashboard/Order", token=reader)).status_code == 403
        acme = h.token(permissions=GRANTS)
        assert (await h.http("PUT", "/override/dashboard/Order", token=acme, json={"config": BOARD})).status_code == 200
        globex = h.token(sub="bob", tenant="globex", permissions=GRANTS)
        views = (await h.http("GET", "/meta/Order", token=globex, tenant="globex")).json()["views"]["dashboard"]
        assert [b["type"] for b in views] != ["header", "number_card", "chart"]
        assert (await h.http("PUT", "/override/dashboard/Nope", token=acme, json={"config": BOARD})).status_code == 404


async def test_a_workspace_saved_from_what_the_desk_received_is_accepted(tmp_path):
    # Le Desk renvoie ce qu'il a reçu, débarrassé des champs de lecture (`create`, `icon`, `single`) : un raccourci demande `new`.
    async with boot_host(tmp_path, inline_plugins=shop_plugin()) as h:
        t = h.token(permissions=GRANTS)
        served = (await h.http("GET", "/workspaces", token=t)).json()[0]["blocks"]
        assert served[0]["create"] is True and "new" not in served[0]  # ce que le serveur dit en lecture…
        r = await h.http("PUT", "/override/workspace/shop", token=t, json={"config": {"label": "Boutique", "blocks": [{"type": "shortcut", "doctype": "Order", "label": "Commandes", "new": True}]}})
        assert r.status_code == 200, r.text
        # … et ce qu'il refuse en écriture, pour mémoire du pourquoi de `declare()` côté Desk.
        r = await h.http("PUT", "/override/workspace/shop", token=t, json={"config": {"label": "Boutique", "blocks": [served[0]]}})
        assert r.status_code == 422
