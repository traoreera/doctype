"""Permissions de bout en bout : les grants viennent du jeton (comme chez xauth), HTTP et actions donnent le même résultat.

Règles de `app/sales/doctypes/customer.yaml` (par GRANT, jamais par nom de rôle) :
- `sales:customer:read` lit ; `sales:customer:write` modifie et crée ; `sales:customer:delete` supprime ;
- `sales:customer:own` lit, modifie et crée ses PROPRES clients (`if_owner`), sans suppression.
Auth compose les rôles à partir de ces grants, par tenant : le moteur ne voit que `principal["permissions"]`.
"""

from __future__ import annotations

from tests.integration.conftest import CUSTOMER_MANAGER, DEFAULT_GRANTS, boot_host


def manager(h, sub="manager"):
    return h.token(sub=sub, permissions=CUSTOMER_MANAGER)


def own_only(h, sub):
    return h.token(sub=sub, permissions=["sales:customer:own"])


def reader(h, sub="reader"):
    return h.token(sub=sub, permissions=["sales:customer:read"])


async def make(h, token, name="Acme"):
    r = await h.http("POST", "/doc/Customer", token=token, json={"data": {"customer_name": name}})
    assert r.status_code == 201, r.text
    return r.json()


async def test_if_owner_user_only_sees_and_changes_their_own_documents(host):
    alice, bob = own_only(host, "alice"), own_only(host, "bob")
    mine, theirs = await make(host, alice, "A1"), await make(host, bob, "B1")

    listed = (await host.http("GET", "/list/Customer", token=alice)).json()
    assert [d["name"] for d in listed["items"]] == [mine["name"]] and listed["total"] == 1

    assert (await host.http("GET", f"/doc/Customer/{theirs['name']}", token=alice)).status_code == 404  # pas 403
    put = await host.http("PUT", f"/doc/Customer/{theirs['name']}", token=alice, json={"version": 1, "data": {"customer_name": "x"}})
    assert put.status_code == 404
    own = await host.http("PUT", f"/doc/Customer/{mine['name']}", token=alice, json={"version": 1, "data": {"customer_name": "A1b"}})
    assert own.status_code == 200 and own.json()["version"] == 2
    # `sales:customer:own` n'accorde pas la suppression, même de ses propres documents : refus global.
    assert (await host.http("DELETE", f"/doc/Customer/{mine['name']}", token=alice)).status_code == 403


async def test_grants_add_up_across_rules(host):
    """`own` limite à ses documents ; ajouter `read` (un autre grant, une autre règle) ouvre la lecture de tous."""
    owner = own_only(host, "bob")
    doc = await make(host, owner, "B1")
    alice_alone = own_only(host, "alice")
    assert (await host.http("GET", f"/doc/Customer/{doc['name']}", token=alice_alone)).status_code == 404

    alice_more = host.token(sub="alice", permissions=["sales:customer:own", "sales:customer:read"])
    assert (await host.http("GET", f"/doc/Customer/{doc['name']}", token=alice_more)).status_code == 200
    # Lire n'autorise pas à écrire : seul `own` écrit, et seulement sur ses propres documents.
    put = await host.http("PUT", f"/doc/Customer/{doc['name']}", token=alice_more, json={"version": 1, "data": {"customer_name": "x"}})
    assert put.status_code == 403


async def test_forbidden_and_not_found_are_distinguishable_only_when_it_is_safe(host):
    """403 : aucune règle n'accorde l'action. 404 : le document est hors du périmètre de lecture."""
    doc = await make(host, manager(host))
    nobody = host.token(sub="nobody", permissions=[])
    r = await host.http("GET", f"/doc/Customer/{doc['name']}", token=nobody)
    assert r.status_code == 403 and r.json()["detail"] == {"code": "permission_denied", "msg": "Accès refusé"}
    # Un nom inexistant et un document hors périmètre répondent pareil.
    other = own_only(host, "alice")
    assert (await host.http("GET", f"/doc/Customer/{doc['name']}", token=other)).status_code == 404
    assert (await host.http("GET", "/doc/Customer/does-not-exist", token=other)).status_code == 404


async def test_reader_sees_everything_but_cannot_write(host):
    m = manager(host)
    a, b = await make(host, m, "A"), await make(host, m, "B")
    r = reader(host)
    listed = (await host.http("GET", "/list/Customer", token=r)).json()
    assert listed["total"] == 2 and {d["name"] for d in listed["items"]} == {a["name"], b["name"]}
    assert (await host.http("PUT", f"/doc/Customer/{a['name']}", token=r, json={"version": 1, "data": {"customer_name": "x"}})).status_code == 403
    assert (await host.http("POST", "/doc/Customer", token=r, json={"data": {"customer_name": "x"}})).status_code == 403
    assert (await host.http("DELETE", f"/doc/Customer/{a['name']}", token=r)).status_code == 403


async def test_write_without_read_is_forbidden(host):
    """Un utilisateur qui n'a que `write` ne peut ni lire ni (donc) modifier : c'est le groupe de grants d'auth qui les assemble."""
    doc = await make(host, manager(host))
    write_only = host.token(sub="w", permissions=["sales:customer:write"])
    r = await host.http("PUT", f"/doc/Customer/{doc['name']}", token=write_only, json={"version": 1, "data": {"customer_name": "x"}})
    assert r.status_code == 403


async def test_grants_of_other_plugins_and_platform_admin_open_nothing(host):
    """Un principal xauth porte les grants de tous les plugins, `admin:*` compris : seuls les grants cités comptent."""
    await make(host, manager(host))
    noise = host.token(sub="u", permissions=["xsales:customer:read", "stock:item:write", "user:list", "admin:*"])
    assert (await host.http("GET", "/list/Customer", token=noise)).status_code == 403


async def test_role_names_do_not_grant_anything(host):
    """Les noms de rôle sont libres et propres à chaque tenant chez auth : ils ne sont pas le contrat."""
    await make(host, manager(host))
    named = host.token(sub="u", permissions=[], roles=["Sales Manager", "sales:customer:read", "admin"])
    assert (await host.http("GET", "/list/Customer", token=named)).status_code == 403


async def test_http_and_actions_agree_for_a_restricted_user(host):
    m, alice, bob = manager(host), own_only(host, "alice"), own_only(host, "bob")
    mine, theirs = await make(host, alice, "A1"), await make(host, bob, "B1")
    await make(host, m, "M1")

    http = (await host.http("GET", "/list/Customer", token=alice)).json()
    act = await host.action("doc.list", {"doctype": "Customer"}, token=alice)
    assert http == {k: v for k, v in act.items() if k != "status"} and http["total"] == 1

    assert (await host.action("doc.get", {"doctype": "Customer", "name": theirs["name"]}, token=alice))["code"] == "document_not_found"
    assert (await host.action("doc.get", {"doctype": "Customer", "name": mine["name"]}, token=alice))["doc"]["name"] == mine["name"]
    denied = await host.action("doc.delete", {"doctype": "Customer", "name": mine["name"]}, token=alice)
    assert (denied["status"], denied["code"]) == ("error", "permission_denied")


async def test_meta_lists_only_accessible_doctypes_and_hides_the_rules(host):
    assert [m["name"] for m in (await host.http("GET", "/meta", token=reader(host))).json()] == ["Customer"]
    assert (await host.http("GET", "/meta", token=host.token(sub="n", permissions=[]))).json() == []

    meta = (await host.http("GET", "/meta/Customer", token=own_only(host, "alice"))).json()
    assert meta["rights"] == {"read": "restricted", "write": "restricted", "create": "restricted", "delete": "none", "submit": "none", "cancel": "none"}
    assert "permissions" not in meta and "sales:customer" not in repr(meta)  # ni les règles ni les noms de grants

    assert (await host.http("GET", "/meta/Customer", token=reader(host))).json()["rights"] == {
        "read": "all", "write": "none", "create": "none", "delete": "none", "submit": "none", "cancel": "none"}
    # Customer ne se soumet pas : `submit` et `cancel` restent fermés même au gestionnaire.
    rights = (await host.http("GET", "/meta/Customer", token=manager(host))).json()["rights"]
    assert {a: rights[a] for a in ("read", "write", "create", "delete")} == dict.fromkeys(("read", "write", "create", "delete"), "all")
    assert (rights["submit"], rights["cancel"]) == ("none", "none")
    assert (await host.http("GET", "/meta/Customer", token=host.token(sub="n", permissions=[]))).status_code == 403


async def test_tenant_and_grants_are_independent_checks(host):
    """Les grants d'un tenant n'ouvrent rien dans un autre : le tenant est un contrôle séparé."""
    doc = await make(host, manager(host))
    other = host.token(sub="manager", tenant="globex", permissions=CUSTOMER_MANAGER)
    assert (await host.http("GET", f"/doc/Customer/{doc['name']}", token=other, tenant="globex")).status_code == 404


async def test_a_doctype_without_permission_rules_is_denied_and_the_operator_is_warned(tmp_path, caplog):
    open_doctype = {"schema_version": 1, "name": "Unprotected", "fields": [{"fieldname": "a", "fieldtype": "Text"}]}
    async with boot_host(tmp_path, extra_doctypes=(open_doctype,)) as h:
        r = await h.http("GET", "/list/Unprotected", token=h.token(permissions=[*DEFAULT_GRANTS, "admin:*"]))
        assert r.status_code == 403
        assert "Unprotected" not in [m["name"] for m in (await h.http("GET", "/meta", token=h.token())).json()]
    assert "doctype without permission rules" in caplog.text


async def test_the_grants_a_doctype_needs_are_logged_for_the_owner_plugin(tmp_path, caplog):
    """Le plugin propriétaire doit les déclarer dans auth (`rbac.declare`) : l'opérateur les retrouve dans le journal."""
    async with boot_host(tmp_path):
        pass
    loaded = [r for r in caplog.records if r.getMessage() == "xcore-doctype loaded"]
    assert loaded, "le plugin n'a pas journalisé son chargement"
    assert loaded[0].xcore_ctx["grants"] == [
        "sales:country:delete", "sales:country:read", "sales:country:write",  # Country : cible du Link Customer.country
        "sales:customer:delete", "sales:customer:own", "sales:customer:read", "sales:customer:write",
    ]
