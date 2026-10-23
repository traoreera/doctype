"""L'historique sur un vrai Xcore : route HTTP, action xcore, droits et tenants."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

from tests.integration.test_desk_api import make


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


async def test_the_operator_purges_old_history_through_the_action(host):
    """`doc.history.purge` (clé du plugin) : charge contrôlée, un seul tenant, la date est un instant quel que soit son décalage."""
    t = host.token(sub="alice")
    doc = await make(host, t, customer_name="Ancien")
    purge = lambda payload: host.action("doc.history.purge", payload, token=t)  # noqa: E731

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
