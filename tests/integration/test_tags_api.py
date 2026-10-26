"""Étiquettes de bout en bout (HTTP et actions IPC) : lire le document pour les voir, l'écrire pour en poser, le filtre `_tag` de la liste et
des agrégats, les comptes de la barre latérale, l'étiquetage en lot."""

from __future__ import annotations

import json

from tests.integration.conftest import boot_host
from tests.integration.test_collaboration_api import READER, WRITER, order, plugins


async def test_tags_over_http(tmp_path):
    async with boot_host(tmp_path, inline_plugins=plugins()) as h:
        ada, rita = h.token(sub="ada", permissions=[*WRITER, "shop:order:delete"]), h.token(sub="rita", permissions=READER)
        o1, o2 = await order(h, ada, "o1"), await order(h, ada, "o2")
        r = await h.http("POST", f"/tags/Order/{o1}", token=ada, json={"add": ["  Urgent ", "Client"]})
        assert r.status_code == 200 and r.json() == {"tags": ["Urgent", "Client"]}
        assert (await h.http("POST", f"/tags/Order/{o2}", token=ada, json={"add": ["client"]})).json() == {"tags": ["Client"]}  # l'écriture en usage
        # Lire suffit pour voir ; il faut écrire pour poser.
        assert (await h.http("GET", f"/tags/Order/{o1}", token=rita)).json() == {"tags": ["Urgent", "Client"], "can_tag": False}
        refused = await h.http("POST", f"/tags/Order/{o1}", token=rita, json={"remove": ["Urgent"]})
        assert refused.status_code == 403
        bad = await h.http("POST", f"/tags/Order/{o1}", token=ada, json={"add": ["a,b"]})
        assert bad.status_code == 422 and bad.json()["detail"]["code"] == "invalid_tag"
        assert (await h.http("POST", f"/tags/Order/{o1}", token=ada, json={"add": ["x"], "extra": 1})).status_code == 422  # corps strict

        listed = await h.http("GET", "/list/Order", token=rita, params={"filters": json.dumps({"_tag": "urgent"})})
        assert [d["name"] for d in listed.json()["items"]] == [o1]
        agg = await h.http("GET", "/aggregate/Order", token=rita, params={"filters": json.dumps({"_tag": ["!=", "Urgent"]})})
        assert agg.json()["rows"][0]["values"] == [1]
        counts = await h.http("GET", "/tag-counts/Order", token=rita, params={"filters": json.dumps({"_tag": "Client"})})
        assert counts.json() == {"items": [{"tag": "Client", "count": 2}, {"tag": "Urgent", "count": 1}]}
        assert (await h.http("GET", "/tag-counts/Order", token=rita, params={"filters": "{"})).status_code == 422
        page = await h.http("POST", "/tags-of/Order", token=rita, json={"names": [o1, o2, "nope"]})
        assert page.json() == {"items": {o1: ["Urgent", "Client"], o2: ["Client"]}}

        many = await h.http("POST", "/tag-many/Order", token=ada, json={"names": [o1, o2], "remove": ["CLIENT"]})
        assert many.json()["done"] == 2
        assert (await h.http("GET", "/tag-counts/Order", token=ada)).json() == {"items": [{"tag": "Urgent", "count": 1}]}
        assert (await h.http("POST", "/tag-many/Order", token=rita, json={"names": [o1], "add": ["x"]})).status_code == 403

        # Le document supprimé emporte ses étiquettes.
        doc = (await h.http("GET", f"/doc/Order/{o1}", token=ada)).json()
        assert (await h.http("DELETE", f"/doc/Order/{o1}", token=ada, params={"version": doc["version"]})).status_code == 204
        assert (await h.http("GET", "/tag-counts/Order", token=ada)).json() == {"items": []}


async def test_the_tag_filter_keeps_out_of_documents_one_cannot_read(tmp_path):
    """`Note` ne se lit que par son propriétaire (`if_owner`) : ni le filtre, ni les comptes, ni la page ne trahissent la note d'un autre."""
    async with boot_host(tmp_path, inline_plugins=plugins()) as h:
        ada, bob = h.token(sub="ada", permissions=WRITER), h.token(sub="bob", permissions=WRITER)
        mine = (await h.http("POST", "/doc/Note", token=ada, json={"data": {"title": "a"}})).json()["name"]
        await h.http("POST", f"/tags/Note/{mine}", token=ada, json={"add": ["Secret"]})
        assert (await h.http("GET", f"/tags/Note/{mine}", token=bob)).status_code == 404
        assert (await h.http("GET", "/list/Note", token=bob, params={"filters": json.dumps({"_tag": "Secret"})})).json()["total"] == 0
        assert (await h.http("GET", "/tag-counts/Note", token=bob)).json() == {"items": []}
        assert (await h.http("POST", "/tags-of/Note", token=bob, json={"names": [mine]})).json() == {"items": {}}


async def test_the_ipc_actions(tmp_path):
    async with boot_host(tmp_path, inline_plugins=plugins()) as h:
        ada = h.token(sub="ada", permissions=WRITER)
        name = await order(h, ada)
        changed = await h.action("doc.tags.change", {"doctype": "Order", "name": name, "add": ["Via IPC"]}, token=ada)
        assert changed["tags"] == ["Via IPC"]
        assert (await h.action("doc.tags.get", {"doctype": "Order", "name": name}, token=ada))["tags"] == ["Via IPC"]
        assert (await h.action("doc.tags.of", {"doctype": "Order", "names": [name]}, token=ada))["items"] == {name: ["Via IPC"]}
        counts = await h.action("doc.tags.counts", {"doctype": "Order", "q": "ipc", "filters": {"title": "o1"}}, token=ada)
        assert counts["items"] == [{"tag": "Via IPC", "count": 1}]
        many = await h.action("doc.tags.many", {"doctype": "Order", "names": [name], "remove": ["via ipc"]}, token=ada)
        assert many["done"] == 1 and (await h.action("doc.tags.get", {"doctype": "Order", "name": name}, token=ada))["tags"] == []
