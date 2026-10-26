"""Partage et permissions utilisateur de bout en bout (HTTP et IPC) sur un hôte réel : la route, le service, le SQL."""

from __future__ import annotations

from tests.integration.conftest import boot_host
from tests.integration.test_collaboration_api import READER, WRITER, order, plugins


async def test_sharing_over_http(tmp_path):
    async with boot_host(tmp_path, inline_plugins=plugins()) as h:
        ada, sam = h.token(sub="ada", permissions=WRITER), h.token(sub="sam", permissions=[])
        name = await order(h, ada)
        assert (await h.http("GET", f"/doc/Order/{name}", token=sam)).status_code == 404  # rien : introuvable
        r = await h.http("POST", f"/shares/Order/{name}", token=ada, json={"member": "sam", "write": True})
        assert r.status_code == 200 and r.json() == {"member": "sam", "write": True}
        assert (await h.http("GET", f"/doc/Order/{name}", token=sam)).status_code == 200
        meta = (await h.http("GET", "/meta/Order", token=sam)).json()
        assert (meta["rights"]["read"], meta["rights"]["write"]) == ("restricted", "restricted")
        doc = (await h.http("GET", f"/doc/Order/{name}", token=sam)).json()
        assert (await h.http("PUT", f"/doc/Order/{name}", token=sam, json={"version": doc["version"], "data": {"title": "relu"}})).status_code == 200
        assert (await h.http("GET", "/list/Order", token=sam)).status_code == 403
        mine = (await h.http("GET", "/shared-with-me", token=sam)).json()["items"]
        assert [(i["doctype"], i["name"], i["write"]) for i in mine] == [("Order", name, True)]
        listed = (await h.http("GET", f"/shares/Order/{name}", token=ada)).json()
        assert listed["can_share"] is True and [i["member"] for i in listed["items"]] == ["sam"]
        # Un partage en écriture laisse modifier, pas repartager : il faut un RÔLE d'écriture (sinon de proche en proche, hors de toute règle).
        assert (await h.http("POST", f"/shares/Order/{name}", token=sam, json={"member": "rita"})).status_code == 403
        reader = h.token(sub="rita", permissions=READER)
        assert (await h.http("POST", f"/shares/Order/{name}", token=reader, json={"member": "bob"})).status_code == 403
        assert (await h.http("POST", f"/shares/Order/{name}", token=ada, json={"member": "sam", "extra": 1})).status_code == 422
        assert (await h.http("DELETE", f"/shares/Order/{name}", token=ada, params={"member": "sam"})).status_code == 204
        assert (await h.http("GET", f"/doc/Order/{name}", token=sam)).status_code == 404


async def test_sharing_through_ipc(tmp_path):
    async with boot_host(tmp_path, inline_plugins=plugins()) as h:
        ada, sam = h.token(sub="ada", permissions=WRITER), h.token(sub="sam", permissions=[])
        name = await order(h, ada)
        assert (await h.action("doc.shares.add", {"doctype": "Order", "name": name, "member": "sam"}, token=ada))["write"] is False
        assert [i["member"] for i in (await h.action("doc.shares.get", {"doctype": "Order", "name": name}, token=ada))["items"]] == ["sam"]
        assert [i["name"] for i in (await h.action("doc.shares.mine", {}, token=sam))["items"]] == [name]
        await h.action("doc.shares.remove", {"doctype": "Order", "name": name, "member": "sam"}, token=sam)  # il renonce au sien
        assert (await h.action("doc.shares.mine", {}, token=sam))["items"] == []
