"""La corbeille sur un vrai Xcore : routes HTTP, actions xcore, purge d'opérateur. Les règles fines sont dans tests/unit/test_trash.py."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tests.integration.conftest import boot_host
from tests.integration.test_desk_api import make
from tests.integration.test_history import OPERATOR

OPEN_PURGE = [{"resource": "ops.trash.purge", "actions": ["execute"], "effect": "allow"}]


async def test_delete_restore_and_purge_over_http_and_ipc(host):
    t = host.token(sub="alice")
    doc = await make(host, t, customer_name="Acme")
    assert (await host.http("DELETE", f"/doc/Customer/{doc['name']}", token=t)).status_code == 204

    listed = await host.http("GET", "/trash/Customer", token=t)
    assert listed.status_code == 200, listed.text
    [item] = listed.json()["items"]
    assert (item["name"], item["deleted_by"]) == (doc["name"], "alice")
    assert (await host.action("doc.trash.list", {"doctype": "Customer"}, token=t))["items"][0]["id"] == item["id"]

    back = await host.http("POST", f"/trash/Customer/{item['id']}/restore", token=t)
    assert back.status_code == 200, back.text
    assert (await host.http("GET", f"/doc/Customer/{doc['name']}", token=t)).json()["data"]["customer_name"] == "Acme"
    again = await host.http("POST", f"/trash/Customer/{item['id']}/restore", token=t)
    assert again.status_code == 404 and again.json()["detail"]["code"] == "trash_not_found"

    await host.http("DELETE", f"/doc/Customer/{doc['name']}", token=t)
    [item] = (await host.http("GET", "/trash/Customer", token=t)).json()["items"]
    stranger = host.token(sub="sam", permissions=[])
    assert (await host.http("GET", "/trash/Customer", token=stranger)).status_code == 403
    assert (await host.http("DELETE", f"/trash/Customer/{item['id']}", token=t)).status_code == 204
    assert (await host.http("GET", "/trash/Customer", token=t)).json()["total"] == 0
    assert (await host.action("doc.trash.restore", {"doctype": "Customer", "id": item["id"]}, token=t))["code"] == "trash_not_found"


async def test_the_operator_empties_the_trash_by_age(tmp_path):
    soon = (datetime.now(UTC) + timedelta(minutes=1)).isoformat()
    async with boot_host(tmp_path / "shipped") as host:  # le manifeste livré ne l'ouvre pas
        assert (await host.action("ops.trash.purge", {"tenant": "acme", "before": soon}, token=host.token(sub="ops", permissions=[OPERATOR])))["code"] == "permission_denied"

    async with boot_host(tmp_path / "opened", plugin_permissions=OPEN_PURGE) as host:
        t = host.token(sub="alice")
        doc = await make(host, t, customer_name="Ancien")
        await host.http("DELETE", f"/doc/Customer/{doc['name']}", token=t)
        purge = lambda payload, token: host.action("ops.trash.purge", payload, token=token)  # noqa: E731
        assert (await purge({"tenant": "acme", "before": soon}, t))["code"] == "forbidden"  # un utilisateur, même avec tous ses grants
        operator = host.token(sub="ops", permissions=[OPERATOR])
        assert (await purge({"tenant": "acme"}, operator))["code"] == "missing_field"
        assert (await purge({"tenant": "acme", "before": soon, "doctype": "Nope"}, operator))["code"] == "doctype_not_found"
        assert (await purge({"tenant": "acme", "before": (datetime.now(UTC) - timedelta(hours=1)).isoformat()}, operator))["purged"] == 0
        assert (await purge({"tenant": "acme", "before": soon}, operator))["purged"] == 1
        assert (await host.http("GET", "/trash/Customer", token=t)).json()["total"] == 0
