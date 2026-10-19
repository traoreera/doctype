"""Dispositions personnalisées de bout en bout : la sienne ou celle de l'organisation, espace fabriqué, blocs cachés conservés, couleurs libres."""

from __future__ import annotations

import yaml

from tests.integration.conftest import boot_host

MAIN = '''
from pathlib import Path
from xcore.sdk import TrustedBase, error

BASE = Path(__file__).resolve().parent.parent


class Plugin(TrustedBase):
    async def on_load(self):
        reg = self.get_service("doctype_registry")
        reg.load_dir(self.ctx.name, BASE / "doctypes")
        if (BASE / "workspaces").is_dir():
            reg.load_workspaces(self.ctx.name, BASE / "workspaces")

    async def handle(self, action, payload):
        return error("no actions", "unknown_action")
'''


def dt(name, module, label=None):
    return {"schema_version": 1, "name": name, "label": label or name, "module": module, "fields": [{"fieldname": "title", "fieldtype": "Text"}, {"fieldname": "qty", "fieldtype": "Number"}, {"fieldname": "kind", "fieldtype": "Select", "options": ["a", "b"]}]}


SHOP_WS = {
    "schema_version": 1,
    "name": "shop",
    "label": "Boutique",
    "blocks": [
        {"type": "header", "text": "Ventes"},
        {"type": "shortcut", "doctype": "Order"},
        {"type": "shortcut", "doctype": "Secret", "label": "Confidentiel"},
        {"type": "header", "text": "Autre"},
        {"type": "number_card", "label": "Commandes", "doctype": "Order"},
    ],
}


def plugins():
    return {
        "shop": (MAIN, {"doctypes/order.yaml": yaml.safe_dump(dt("Order", "shop", "Commande")), "doctypes/secret.yaml": yaml.safe_dump(dt("Secret", "shop")),
                        "workspaces/shop.yaml": yaml.safe_dump(SHOP_WS)}),
        "labs": (MAIN, {"doctypes/widget.yaml": yaml.safe_dump(dt("Widget", "labs"))}),  # aucun espace déclaré : un espace fabriqué
    }


ADMIN = ["shop:order:read", "shop:order:write", "shop:secret:read", "shop:secret:write", "labs:widget:read", "labs:widget:write"]
EDITOR = ["shop:order:read", "shop:order:write"]  # ne voit pas « Secret »
READER = ["shop:order:read"]


def kinds(workspace):
    return [(b["type"], b.get("doctype") or b.get("text")) for b in workspace["blocks"]]


async def workspace(h, token, name="shop", tenant=None):
    spaces = (await h.http("GET", "/workspaces", token=token, **({"tenant": tenant} if tenant else {}))).json()
    return next((w for w in spaces if w["name"] == name), None)


def body(*blocks, label="Boutique"):
    return {"config": {"label": label, "blocks": list(blocks)}}


MINE = body({"type": "header", "text": "Ma vue"}, {"type": "shortcut", "doctype": "Order", "new": True})


async def test_my_layout_is_mine_alone_and_goes_before_the_organisation_s(tmp_path):
    async with boot_host(tmp_path, inline_plugins=plugins()) as h:
        bob, ann = h.token(sub="bob", permissions=EDITOR), h.token(sub="ann", permissions=EDITOR)
        r = await h.http("PUT", "/override/workspace/shop", params={"scope": "user"}, token=bob, json=MINE)
        assert r.status_code == 200 and r.json()["layer"] == "user", r.text
        assert kinds(await workspace(h, bob)) == [("header", "Ma vue"), ("shortcut", "Order")] and (await workspace(h, bob))["layer"] == "user"
        # Ann, du même tenant, ne voit rien de tout cela : la disposition du plugin.
        seen = await workspace(h, ann)
        assert seen["layer"] is None and ("header", "Ma vue") not in kinds(seen)
        # L'organisation enregistre la sienne : Ann la reçoit, Bob garde la sienne (elle passe avant).
        org = await h.http("PUT", "/override/workspace/shop", params={"scope": "org"}, token=bob, json=body({"type": "shortcut", "doctype": "Order", "label": "Org"}))
        assert org.status_code == 200
        assert (await workspace(h, ann))["layer"] == "org" and (await workspace(h, bob))["layer"] == "user"
        # Retirer la sienne : Bob retrouve celle de l'organisation.
        assert (await h.http("DELETE", "/override/workspace/shop", params={"scope": "user"}, token=bob)).status_code == 204
        assert (await workspace(h, bob))["layer"] == "org"
        # La disposition d'un tenant ne sort pas de son tenant.
        other = h.token(sub="bob", tenant="globex", permissions=EDITOR)
        assert (await workspace(h, other, tenant="globex"))["layer"] is None


async def test_a_personal_layout_needs_only_to_read_what_it_shows_the_organisation_s_needs_to_write_it(tmp_path):
    async with boot_host(tmp_path, inline_plugins=plugins()) as h:
        reader = h.token(sub="rita", permissions=READER)
        assert (await h.http("PUT", "/override/workspace/shop", params={"scope": "user"}, token=reader, json=MINE)).status_code == 200
        assert (await h.http("PUT", "/override/workspace/shop", params={"scope": "org"}, token=reader, json=MINE)).status_code == 403
        assert (await h.http("PUT", "/override/workspace/shop", token=reader, json=MINE)).status_code == 403  # sans `scope` : l'organisation (comme avant)
        # Ni la sienne ni celle d'un autre ne peut viser ce qu'on ne lit pas.
        unreadable = body({"type": "shortcut", "doctype": "Secret"})
        assert (await h.http("PUT", "/override/workspace/shop", params={"scope": "user"}, token=reader, json=unreadable)).status_code == 403
        assert (await h.http("DELETE", "/override/workspace/shop", params={"scope": "org"}, token=reader)).status_code == 403
        assert (await h.http("PUT", "/override/workspace/shop", params={"scope": "everyone"}, token=reader, json=MINE)).status_code == 422


async def test_an_editor_can_share_what_they_can_write_and_a_reader_cannot(tmp_path):
    async with boot_host(tmp_path, inline_plugins=plugins()) as h:
        assert (await workspace(h, h.token(sub="eve", permissions=EDITOR)))["shareable"] is True
        assert (await workspace(h, h.token(sub="rita", permissions=READER)))["shareable"] is False


async def test_what_the_user_cannot_see_survives_their_save(tmp_path):
    # « Confidentiel » est caché à l'éditeur : enregistrer SA disposition de l'organisation ne l'efface pas pour ceux qui le voient.
    async with boot_host(tmp_path, inline_plugins=plugins()) as h:
        editor, admin = h.token(sub="eve", permissions=EDITOR), h.token(sub="ada", permissions=ADMIN)
        assert ("shortcut", "Secret") not in kinds(await workspace(h, editor))
        saved = await h.http("PUT", "/override/workspace/shop", params={"scope": "org"}, token=editor, json=body(
            {"type": "number_card", "label": "Commandes", "doctype": "Order"},  # l'éditeur remonte la carte, et retire son titre
            {"type": "shortcut", "doctype": "Order"},
        ))
        assert saved.status_code == 200, saved.text
        full = kinds(await workspace(h, admin))
        assert ("shortcut", "Secret") in full  # toujours là
        assert full.index(("shortcut", "Secret")) == full.index(("shortcut", "Order")) + 1
        # Et il garde son libellé, intact (l'éditeur ne l'a jamais contrôlé).
        stored = next(b for b in (await workspace(h, admin))["blocks"] if b.get("doctype") == "Secret")
        assert stored["label"] == "Confidentiel"
        assert ("shortcut", "Secret") not in kinds(await workspace(h, editor))


async def test_hidden_blocks_stay_next_to_their_neighbour(tmp_path):
    async with boot_host(tmp_path, inline_plugins=plugins()) as h:
        editor, admin = h.token(sub="eve", permissions=EDITOR), h.token(sub="ada", permissions=ADMIN)
        # L'éditeur ajoute une carte après « Order » : « Confidentiel » reste collé à « Order » qui le précédait, pas rejeté à la fin.
        await h.http("PUT", "/override/workspace/shop", params={"scope": "org"}, token=editor, json=body(
            {"type": "header", "text": "Ventes"},
            {"type": "shortcut", "doctype": "Order"},
            {"type": "number_card", "label": "Commandes", "doctype": "Order"},
        ))
        full = kinds(await workspace(h, admin))
        i = full.index(("shortcut", "Order"))
        assert full[i + 1] == ("shortcut", "Secret")


async def test_a_module_without_a_workspace_file_can_be_customised_too(tmp_path):
    async with boot_host(tmp_path, inline_plugins=plugins()) as h:
        admin = h.token(sub="ada", permissions=ADMIN)
        made = await workspace(h, admin, "labs")
        assert made is not None and made["layer"] is None
        mine = body({"type": "number_card", "label": "Mes gadgets", "doctype": "Widget", "color": "#d6452a"}, label="Labs")
        assert (await h.http("PUT", "/override/workspace/labs", params={"scope": "user"}, token=admin, json=mine)).status_code == 200
        got = await workspace(h, admin, "labs")
        assert got["layer"] == "user" and got["blocks"][0]["label"] == "Mes gadgets"
        assert (await h.http("PUT", "/override/workspace/labs", params={"scope": "org"}, token=admin, json=mine)).status_code == 200
        assert (await h.http("DELETE", "/override/workspace/labs", params={"scope": "user"}, token=admin)).status_code == 204
        assert (await workspace(h, admin, "labs"))["layer"] == "org"
        assert (await h.http("DELETE", "/override/workspace/labs", params={"scope": "org"}, token=admin)).status_code == 204
        assert (await workspace(h, admin, "labs"))["layer"] is None  # l'espace fabriqué reprend la main
        # Un nom qui n'est ni un espace ni un module n'existe pas ; un module qu'on ne lit pas non plus.
        assert (await h.http("PUT", "/override/workspace/ghost", params={"scope": "user"}, token=admin, json=mine)).status_code == 404
        assert (await h.http("PUT", "/override/workspace/labs", params={"scope": "user"}, token=h.token(sub="x", permissions=READER), json=mine)).status_code == 404


async def test_a_free_colour_is_accepted_as_rrggbb_and_nothing_else(tmp_path):
    async with boot_host(tmp_path, inline_plugins=plugins()) as h:
        t = h.token(sub="ada", permissions=ADMIN)
        card = {"type": "number_card", "label": "N", "doctype": "Order", "color": "#d6452a"}
        chart = {"type": "chart", "label": "C", "doctype": "Order", "kind": "bar", "group_by": "kind", "color": "#2a78d6"}
        r = await h.http("PUT", "/override/workspace/shop", params={"scope": "user"}, token=t, json=body(card, chart))
        assert r.status_code == 200, r.text
        got = (await workspace(h, t))["blocks"]
        assert got[0]["color"] == "#d6452a" and got[1]["color"] == "#2a78d6"
        for bad in ("red;}", "#abc", "url(x)", "#12345g"):
            r = await h.http("PUT", "/override/workspace/shop", params={"scope": "user"}, token=t, json=body({**card, "color": bad}))
            assert r.status_code == 422, bad
        donut = {**chart, "kind": "donut"}
        r = await h.http("PUT", "/override/workspace/shop", params={"scope": "user"}, token=t, json=body(donut))
        assert r.status_code == 422 and "color" in r.text  # un camembert garde la palette


async def test_a_dashboard_can_be_mine_or_the_organisation_s(tmp_path):
    async with boot_host(tmp_path, inline_plugins=plugins()) as h:
        bob, ann = h.token(sub="bob", permissions=EDITOR), h.token(sub="ann", permissions=READER)
        meta = lambda t: h.http("GET", "/meta/Order", token=t)  # noqa: E731
        generated = (await meta(ann)).json()["views"]
        assert "dashboard_layer" not in generated and generated["dashboard_shareable"] is False
        blocks = {"blocks": [{"type": "number_card", "label": "À moi", "function": "count", "color": "#d6452a"}]}
        assert (await h.http("PUT", "/override/dashboard/Order", params={"scope": "user"}, token=ann, json={"config": blocks})).status_code == 200  # lecteur : le sien
        assert (await h.http("PUT", "/override/dashboard/Order", params={"scope": "org"}, token=ann, json={"config": blocks})).status_code == 403
        mine = (await meta(ann)).json()["views"]
        assert mine["dashboard_layer"] == "user" and mine["dashboard"][0]["label"] == "À moi"
        assert "dashboard_layer" not in (await meta(bob)).json()["views"]  # celui de Bob n'est pas touché
        org = {"blocks": [{"type": "number_card", "label": "Pour tous", "function": "count"}]}
        assert (await h.http("PUT", "/override/dashboard/Order", params={"scope": "org"}, token=bob, json={"config": org})).status_code == 200
        assert (await meta(bob)).json()["views"]["dashboard"][0]["label"] == "Pour tous"
        assert (await meta(bob)).json()["views"]["dashboard_layer"] == "org" and (await meta(bob)).json()["views"]["dashboard_shareable"] is True
        assert (await meta(ann)).json()["views"]["dashboard"][0]["label"] == "À moi"  # le sien passe avant
        assert (await h.http("DELETE", "/override/dashboard/Order", params={"scope": "user"}, token=ann)).status_code == 204
        assert (await meta(ann)).json()["views"]["dashboard"][0]["label"] == "Pour tous"
