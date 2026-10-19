"""La forêt d'un type arborescent (phase C), de bout en bout : `/tree`, l'action IPC, la boucle et le type non arborescent."""

from __future__ import annotations

from tests.integration.conftest import boot_host

GRANTS = ["tree:node:read", "tree:node:write", "tree:node:delete"]

NODE = {
    "schema_version": 1,
    "name": "Node",
    "label": "Nœud",
    "module": "tree",
    "title_field": "title",
    "fields": [
        {"fieldname": "title", "fieldtype": "Text", "required": True},
        {"fieldname": "parent_node", "fieldtype": "Link", "options": "Node"},
    ],
    "views": {"tree": {"parent": "parent_node"}},
}


async def make(h, token, title, parent=None):
    data: dict = {"title": title}
    if parent is not None:
        data["parent_node"] = parent
    r = await h.http("POST", "/doc/Node", token=token, json={"data": data})
    assert r.status_code == 201, r.text
    return r.json()["name"]


async def test_the_forest_groups_each_document_under_its_parent(tmp_path):
    async with boot_host(tmp_path, extra_doctypes=(NODE,)) as h:
        t = h.token(permissions=GRANTS)
        root = await make(h, t, "Racine")
        child = await make(h, t, "Enfant", root)
        grand = await make(h, t, "Petit-enfant", child)
        other = await make(h, t, "Autre racine")

        r = await h.http("GET", "/tree/Node", token=t)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["parent"] == "parent_node" and body["total"] == 4 and body["truncated"] is False
        roots = {n["name"]: n for n in body["items"]}
        assert set(roots) == {root, other}
        node = roots[root]
        assert node["title"] == "Racine"
        [kid] = node["children"]
        assert kid["name"] == child and kid["children"][0]["name"] == grand
        # L'action IPC rend exactement la même forêt que la route.
        result = await h.action("doc.tree", {"doctype": "Node"}, token=t)
        assert result["items"] == body["items"] and result["parent"] == body["parent"] and result["total"] == body["total"]


async def test_a_cycle_is_rendered_as_roots_not_an_infinite_loop(tmp_path):
    async with boot_host(tmp_path, extra_doctypes=(NODE,)) as h:
        t = h.token(permissions=GRANTS)
        a = await make(h, t, "A")
        b = await make(h, t, "B", a)
        # `a` devient l'enfant de `b` : b -> a -> b. Un Link se vérifie à l'écriture, pas l'acyclicité de l'arbre.
        got = (await h.http("GET", f"/doc/Node/{a}", token=t)).json()
        r = await h.http("PUT", f"/doc/Node/{a}", token=t, json={"version": got["version"], "data": {"title": "A", "parent_node": b}})
        assert r.status_code == 200, r.text
        body = (await h.http("GET", "/tree/Node", token=t)).json()
        # Les deux sortent en racines : on ne boucle pas, et rien ne disparaît sans le dire.
        assert {n["name"] for n in body["items"]} == {a, b}


async def test_a_submitted_document_still_appears_in_the_forest(tmp_path):
    async with boot_host(tmp_path, extra_doctypes=(NODE,)) as h:
        t = h.token(permissions=GRANTS)
        root = await make(h, t, "Racine")
        assert (await h.http("GET", "/tree/Node", token=t)).json()["items"][0]["name"] == root


async def test_a_non_tree_doctype_is_refused(tmp_path):
    async with boot_host(tmp_path, extra_doctypes=(NODE,)) as h:
        t = h.token()  # les grants par défaut couvrent Customer (pas la peine de tester le refus de droit ici)
        r = await h.http("GET", "/tree/Customer", token=t)
        assert r.status_code == 422 and r.json()["detail"]["code"] == "validation_failed"


async def test_reading_the_tree_needs_the_read_grant_and_is_tenant_scoped(tmp_path):
    async with boot_host(tmp_path, extra_doctypes=(NODE,)) as h:
        t = h.token(permissions=GRANTS)
        await make(h, t, "Secret")
        blind = h.token(sub="b", permissions=["tree:node:write"])
        assert (await h.http("GET", "/tree/Node", token=blind)).status_code == 403
        globex = h.token(sub="g", tenant="globex", permissions=GRANTS)
        body = (await h.http("GET", "/tree/Node", token=globex, tenant="globex")).json()
        assert body["items"] == [] and body["total"] == 0
