"""L'historique sur un vrai Xcore : route HTTP, action xcore, droits et tenants."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import yaml

from tests.integration.conftest import boot_host
from tests.integration.test_desk_api import make

# Ce que l'opérateur ajoute à SA copie du manifeste pour ouvrir la purge (le manifeste livré ne l'ouvre pas).
OPEN_PURGE = [{"resource": "ops.history.purge", "actions": ["execute"], "effect": "allow"}]
OPERATOR = "doctype:cross_tenant"  # `doctype.cross_tenant_grant` (défaut du manifeste)
# Un plugin de confiance qui relaie une requête vers la purge, avec le principal de la requête (`call_plugin`).
RELAY = """
from xcore.sdk import AutoDispatchMixin, TrustedBase, action


class Plugin(AutoDispatchMixin, TrustedBase):
    @action("relay.purge")
    async def relay(self, payload: dict) -> dict:
        return await self.call_plugin("xcore-doctype", "ops.history.purge", payload)
"""
RELAY_MANIFEST = yaml.safe_dump({
    "name": "relay", "version": "0.1.0", "framework_version": ">=2.9", "execution_mode": "trusted", "entry_point": "src/main.py",
    "requires": ["xcore-doctype"], "permissions": [{"resource": "relay.*", "actions": ["execute"], "effect": "allow"}],
})


async def test_history_over_http_follows_the_life_of_a_document(host):
    t = host.token(sub="alice")
    doc = await make(host, t, customer_name="Acme", credit_limit=10)
    upd = await host.http("PUT", f"/doc/Customer/{doc['name']}", token=t, json={"version": 1, "data": {"customer_name": "Acme", "type": "Particulier", "credit_limit": 25}})
    assert upd.status_code == 200

    r = await host.http("GET", f"/history/Customer/{doc['name']}", token=t)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 2 and (body["limit"], body["offset"]) == (20, 0)
    newest, oldest = body["items"]
    assert (newest["action"], newest["user"], newest["version"]) == ("update", "alice", 2)
    assert newest["changes"] == {"credit_limit": {"old": 10, "new": 25}}
    assert oldest["action"] == "create" and oldest["changes"]["customer_name"] == {"old": None, "new": "Acme"}

    paged = (await host.http("GET", f"/history/Customer/{doc['name']}", token=t, params={"limit": 1, "offset": 1})).json()
    assert [i["action"] for i in paged["items"]] == ["create"] and paged["total"] == 2

    same = await host.action("doc.history", {"doctype": "Customer", "name": doc["name"]}, token=t)
    assert same["items"] == body["items"]  # l'action xcore donne la même réponse


async def test_history_answers_like_the_document_does(host):
    manager = host.token(sub="boss")
    doc = await make(host, manager, customer_name="Secret")

    # Sans droit de lecture : 403. Hors de son périmètre (`if_owner`) : 404, comme le document lui-même.
    assert (await host.http("GET", f"/history/Customer/{doc['name']}", token=host.token(sub="x", permissions=[]))).status_code == 403
    mine = host.token(sub="alice", permissions=["sales:customer:own"])
    assert (await host.http("GET", f"/history/Customer/{doc['name']}", token=mine)).status_code == 404
    assert (await host.http("GET", f"/doc/Customer/{doc['name']}", token=mine)).status_code == 404

    other_tenant = host.token(sub="boss", tenant="globex")
    r = await host.http("GET", f"/history/Customer/{doc['name']}", token=other_tenant, tenant="globex")
    assert r.status_code == 404 and r.json()["detail"]["code"] == "document_not_found"
    assert (await host.http("GET", "/history/Customer/nope", token=manager)).status_code == 404
    assert (await host.http("GET", f"/history/Customer/{doc['name']}", token=manager, params={"limit": 0})).status_code == 422


async def test_a_deleted_document_keeps_its_history_for_those_who_could_read_it(host):
    t = host.token(sub="alice")
    doc = await make(host, t, customer_name="Éphémère")
    assert (await host.http("DELETE", f"/doc/Customer/{doc['name']}", token=t)).status_code == 204
    assert (await host.http("GET", f"/doc/Customer/{doc['name']}", token=t)).status_code == 404
    body = (await host.http("GET", f"/history/Customer/{doc['name']}", token=t)).json()
    assert [i["action"] for i in body["items"]] == ["delete", "create"]
    assert body["items"][0]["changes"]["customer_name"] == {"old": "Éphémère", "new": None}


async def test_the_operator_purges_old_history_through_the_action(tmp_path):
    """`ops.history.purge` (clé du plugin ET grant transverse) : charge contrôlée, un seul tenant, la date est un instant quel que soit son décalage."""
    async with boot_host(tmp_path, plugin_permissions=OPEN_PURGE) as host:
        t = host.token(sub="alice")
        doc = await make(host, t, customer_name="Ancien")
        operator = host.token(sub="ops", permissions=[OPERATOR])
        purge = lambda payload: host.action("ops.history.purge", payload, token=operator)  # noqa: E731

        assert (await purge({"before": "2026-01-01T00:00:00+00:00"}))["code"] == "missing_field"  # sans tenant
        assert (await purge({"tenant": "acme"}))["code"] == "missing_field"  # sans date
        assert (await purge({"tenant": "acme", "before": "hier"}))["code"] == "invalid_payload"
        assert (await purge({"tenant": "acme", "before": "2026-01-01", "doctype": ""}))["code"] == "invalid_payload"

        an_hour_ago_east = (datetime.now(UTC) - timedelta(hours=1)).astimezone(timezone(timedelta(hours=5))).isoformat()
        assert (await purge({"tenant": "acme", "before": an_hour_ago_east}))["deleted"] == 0  # l'entrée est plus récente que cet instant
        soon = (datetime.now(UTC) + timedelta(minutes=1)).isoformat()
        assert (await purge({"tenant": "globex", "before": soon}))["deleted"] == 0  # un autre tenant : rien à lui
        assert (await purge({"tenant": "acme", "before": soon, "doctype": "Customer"}))["deleted"] == 1
        body = (await host.http("GET", f"/history/Customer/{doc['name']}", token=t)).json()
        assert body["total"] == 0 and body["items"] == []


async def test_the_purge_is_refused_without_the_right(tmp_path):
    """Hors de `doc.*` (que `allowed_callers: ["*"]` ouvre à tout plugin) : le manifeste livré ne l'ouvre pas, et ouverte, elle exige
    le grant transverse sur le principal, que l'appel vienne de la route IPC ou d'un plugin qui relaie la requête d'un utilisateur."""
    soon = {"tenant": "acme", "before": (datetime.now(UTC) + timedelta(minutes=1)).isoformat()}
    async with boot_host(tmp_path / "shipped") as host:
        operator = host.token(sub="ops", permissions=[OPERATOR])
        await make(host, host.token(sub="alice"), customer_name="Gardé")
        assert (await host.action("ops.history.purge", soon, token=operator))["code"] == "permission_denied"
        assert (await host.action("doc.history.purge", soon, token=operator))["status"] == "error"  # l'ancien nom n'existe plus

    inline = {"relay": (RELAY, {"plugin.yaml": RELAY_MANIFEST})}
    async with boot_host(tmp_path / "opened", plugin_permissions=OPEN_PURGE, inline_plugins=inline) as host:
        alice = host.token(sub="alice")  # tous les grants de ses DocTypes, pas le grant transverse
        doc = await make(host, alice, customer_name="Gardé")
        assert (await host.action("ops.history.purge", soon, token=None))["code"] == "forbidden"  # la clé du plugin seule ne suffit pas
        assert (await host.action("ops.history.purge", soon, token=alice))["code"] == "forbidden"
        assert (await host.action("relay.purge", soon, token=alice, plugin="relay"))["code"] == "forbidden"  # un plugin qui relaie
        assert (await host.http("GET", f"/history/Customer/{doc['name']}", token=alice)).json()["total"] == 1  # rien n'a été purgé
        operator = host.token(sub="ops", permissions=[OPERATOR])
        assert (await host.action("relay.purge", soon, token=operator, plugin="relay"))["deleted"] == 1  # l'opérateur, lui, passe
