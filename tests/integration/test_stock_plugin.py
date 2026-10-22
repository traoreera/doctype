"""Le module Stock sur un vrai hôte xcore : le plugin `stock` se charge, ses hooks et ses DocTypes suivent, et le parcours HTTP tient.

Pas de mock : un `Xcore` réel, le plugin de test (`tests/support/plugins/stock`) lié tel quel, des jetons de développement. Les règles fines du stock
(concurrence, antidatage…) sont dans tests/unit/test_stock.py ; ici on vérifie le BRANCHEMENT : manifeste, hooks enregistrés sous
leurs noms, grants déclarés, routes de transition, codes d'erreur vus d'un client HTTP.
"""

from __future__ import annotations

from datetime import UTC, datetime

from tests.integration.conftest import boot_host
from tests.support import FIXTURES

STOCK, GEO = FIXTURES / "stock", FIXTURES / "geo"
# `stock` requires `geo` : l'entrepôt situe son pays dans le référentiel partagé, il n'en garde pas de liste propre.
PLUGINS = {"geo": GEO, "stock": STOCK}
KEEPER = [
    "stock:item:read", "stock:item:write", "stock:warehouse:read", "stock:warehouse:write",
    "stock:stock_entry:read", "stock:stock_entry:write", "stock:stock_entry:delete", "stock:stock_entry:submit", "stock:stock_entry:cancel",
    "stock:stock_ledger_entry:read", "stock:bin:read",
]
TODAY = datetime.now(UTC).date().isoformat()


async def make(h, token, doctype, data, tenant="acme"):
    r = await h.http("POST", f"/doc/{doctype}", token=token, json={"data": data}, tenant=tenant)
    assert r.status_code == 201, r.text
    return r.json()


async def world(h, token):
    item = await make(h, token, "Item", {"item_name": "Vis", "uom": "pcs"})
    paris = await make(h, token, "Warehouse", {"warehouse_name": "Paris"})
    lyon = await make(h, token, "Warehouse", {"warehouse_name": "Lyon"})
    return item["name"], paris["name"], lyon["name"]


def entry(purpose, lines, day=None):
    return {"purpose": purpose, "posting_date": day or TODAY, "items": lines}


async def test_the_plugin_loads_with_its_doctypes_hooks_and_grants(tmp_path):
    async with boot_host(tmp_path, operator_doctypes=False, link_plugins=PLUGINS) as h:
        t = h.token(permissions=KEEPER)
        names = [m["name"] for m in (await h.http("GET", "/meta", token=t)).json()]
        # Le DocType de lignes n'est pas listé. `Devise` et `Pays` viennent du référentiel `geo` : ce gestionnaire ne
        # détient AUCUN grant `geo` et les voit quand même, parce que leur lecture est ouverte à tout utilisateur
        # identifié (`@authenticated`) — c'est ce qui rend l'entrepôt capable de citer un pays.
        assert names == ["Bin", "Devise", "Item", "Pays", "Stock Entry", "Stock Ledger Entry", "Warehouse"]
        assert (await h.http("GET", "/meta/Pays", token=t)).json()["rights"] == {
            "read": "all", "write": "none", "create": "none", "delete": "none", "submit": "none", "cancel": "none"}
        # Et `geo` ne déclare à auth que l'écriture : un grant de lecture n'existe pas pour un référentiel.
        (declared_geo,) = [e for e in h.rbac_declared if e["plugin"] == "geo"]
        assert {g["name"] for g in declared_geo["grants"]} == {"geo:pays:write", "geo:pays:delete", "geo:devise:write", "geo:devise:delete"}
        meta = (await h.http("GET", "/meta/Stock Entry", token=t)).json()
        assert meta["submittable"] is True and meta["rights"]["submit"] == "all" and "Stock Entry Line" in meta["children"]
        # Fermés à tout écrivain humain : même ce gestionnaire ne peut pas écrire le journal ni les soldes.
        assert (await h.http("GET", "/meta/Bin", token=t)).json()["rights"] == {
            "read": "all", "write": "none", "create": "none", "delete": "none", "submit": "none", "cancel": "none"}
        (event,) = [e for e in h.rbac_declared if e["plugin"] == "stock"]
        declared = {g["name"] for g in event["grants"]}
        assert {"stock:stock_entry:submit", "stock:stock_entry:cancel", "stock:bin:read", "stock:stock_ledger_entry:read"} <= declared
        assert not any(g.endswith((":submit", ":cancel")) for g in declared if "item" in g or "bin" in g or "warehouse" in g)


async def test_the_http_journey_receipt_issue_transfer_cancel(tmp_path):
    async with boot_host(tmp_path, operator_doctypes=False, link_plugins=PLUGINS) as h:
        t = h.token(permissions=KEEPER)
        vis, paris, lyon = await world(h, t)

        async def post(body, name=None):
            doc = await make(h, t, "Stock Entry", body)
            assert doc["docstatus"] == 0 and doc["name"].startswith("MS-") and doc["data"]["items"][0]["qty"].count(".") == 1
            r = await h.http("POST", f"/doc/Stock Entry/{doc['name']}/submit", token=t, json={"version": doc["version"]})
            return doc, r

        async def balance(wh):
            r = await h.http("GET", f"/list/Bin?filters={{\"warehouse\":\"{wh}\"}}", token=t)
            items = r.json()["items"]
            return items[0]["data"]["qty"] if items else None

        _, r = await post(entry("Réception", [{"item": vis, "qty": "10", "target_warehouse": paris}]))
        assert r.status_code == 200, r.text
        assert (r.json()["docstatus"], r.json()["version"]) == (1, 2)
        assert await balance(paris) == "10.000"

        # Sortie trop grande : refusée avec le détail sur la ligne en cause, rien ne bouge.
        doc, r = await post(entry("Sortie", [{"item": vis, "qty": "11", "source_warehouse": paris}]))
        assert r.status_code == 422, r.text
        body = r.json()["detail"]
        assert body["code"] == "hook_rejected" and "Stock insuffisant" in body["msg"] and body["details"][0]["field"] == "items.0.qty"
        assert await balance(paris) == "10.000"
        assert (await h.http("GET", f"/doc/Stock Entry/{doc['name']}", token=t)).json()["docstatus"] == 0

        out, r = await post(entry("Sortie", [{"item": vis, "qty": "4.5", "source_warehouse": paris}]))
        assert r.status_code == 200 and await balance(paris) == "5.500"

        # Rejouer la soumission : 409, rien de plus dans le journal.
        again = await h.http("POST", f"/doc/Stock Entry/{out['name']}/submit", token=t, json={"version": 2})
        assert (again.status_code, again.json()["detail"]["code"]) == (409, "invalid_docstatus")
        assert await balance(paris) == "5.500"

        # Un document soumis est figé.
        put = await h.http("PUT", f"/doc/Stock Entry/{out['name']}", token=t, json={"version": 2, "data": out["data"]})
        assert (put.status_code, put.json()["detail"]["code"]) == (409, "document_locked")
        assert (await h.http("DELETE", f"/doc/Stock Entry/{out['name']}", token=t)).status_code == 409

        trans, r = await post(entry("Transfert", [{"item": vis, "qty": "2", "source_warehouse": paris, "target_warehouse": lyon}]))
        assert r.status_code == 200 and (await balance(paris), await balance(lyon)) == ("3.500", "2.000")

        # Annuler le transfert écrit les écritures inverses ; l'annuler à nouveau est refusé.
        c = await h.http("POST", f"/doc/Stock Entry/{trans['name']}/cancel", token=t, json={"version": 2})
        assert c.status_code == 200 and c.json()["docstatus"] == 2
        assert (await balance(paris), await balance(lyon)) == ("5.500", "0.000")
        assert (await h.http("POST", f"/doc/Stock Entry/{trans['name']}/cancel", token=t, json={"version": 3})).status_code == 409

        # Amender : une copie en brouillon, avec sa filiation, qu'on corrige puis soumet.
        am = await h.http("POST", f"/doc/Stock Entry/{trans['name']}/amend", token=t)
        assert am.status_code == 201, am.text
        fix = am.json()
        assert (fix["docstatus"], fix["amended_from"], fix["name"]) == (0, trans["name"], f"{trans['name']}-1")
        twice = await h.http("POST", f"/doc/Stock Entry/{trans['name']}/amend", token=t)
        assert (twice.status_code, twice.json()["detail"]["code"]) == (409, "already_amended")

        # Le journal : 1 + 1 + 2 + 2 (le transfert et son inverse) écritures, non modifiables, dans l'ordre.
        led = (await h.http("GET", "/list/Stock Ledger Entry?limit=50", token=t)).json()
        assert led["total"] == 6 and all(e["data"]["voucher"].startswith("MS-") for e in led["items"])
        put = await h.http("PUT", f"/doc/Stock Ledger Entry/{led['items'][0]['name']}", token=t, json={"version": 1, "data": led["items"][0]["data"]})
        assert put.status_code == 403  # fermé à tous

        # L'historique garde qui a soumis et qui a annulé.
        hist = (await h.http("GET", f"/history/Stock Entry/{trans['name']}", token=t)).json()["items"]
        assert [(i["action"], i["user"]) for i in hist] == [("cancel", "alice"), ("submit", "alice"), ("create", "alice")]


async def test_the_actions_of_the_plugin_do_the_same_through_xcore(tmp_path):
    async with boot_host(tmp_path, operator_doctypes=False, link_plugins=PLUGINS) as h:
        t = h.token(permissions=KEEPER)
        vis, paris, _ = await world(h, t)
        saved = (await h.action("doc.save", {"doctype": "Stock Entry", "data": entry("Réception", [{"item": vis, "qty": 3, "target_warehouse": paris}])}, token=t))["doc"]
        sub = await h.action("doc.submit", {"doctype": "Stock Entry", "name": saved["name"], "version": saved["version"]}, token=t)
        assert sub["doc"]["docstatus"] == 1
        replay = await h.action("doc.submit", {"doctype": "Stock Entry", "name": saved["name"], "version": 2}, token=t)
        assert replay["code"] == "invalid_docstatus"
        canc = await h.action("doc.cancel", {"doctype": "Stock Entry", "name": saved["name"], "version": 2}, token=t)
        assert canc["doc"]["docstatus"] == 2
        am = await h.action("doc.amend", {"doctype": "Stock Entry", "name": saved["name"]}, token=t)
        assert am["doc"]["amended_from"] == saved["name"]


async def test_submitting_needs_its_own_grant_and_not_write(tmp_path):
    async with boot_host(tmp_path, operator_doctypes=False, link_plugins=PLUGINS) as h:
        t = h.token(permissions=KEEPER)
        vis, paris, _ = await world(h, t)
        doc = await make(h, t, "Stock Entry", entry("Réception", [{"item": vis, "qty": "1", "target_warehouse": paris}]))
        writer = h.token(sub="wendy", permissions=[g for g in KEEPER if not g.endswith((":submit", ":cancel"))])
        r = await h.http("POST", f"/doc/Stock Entry/{doc['name']}/submit", token=writer, json={"version": 1})
        assert r.status_code == 403
        r = await h.http("POST", f"/doc/Stock Entry/{doc['name']}/submit", token=t, json={"version": 1})
        assert r.status_code == 200
        r = await h.http("POST", f"/doc/Stock Entry/{doc['name']}/cancel", token=writer, json={"version": 2})
        assert r.status_code == 403
        # Sans version : 422, comme pour une modification.
        other = await make(h, t, "Stock Entry", entry("Réception", [{"item": vis, "qty": "1", "target_warehouse": paris}]))
        assert (await h.http("POST", f"/doc/Stock Entry/{other['name']}/submit", token=t, json={})).status_code == 422


async def test_two_tenants_do_not_share_stock(tmp_path):
    async with boot_host(tmp_path, operator_doctypes=False, link_plugins=PLUGINS) as h:
        a, g = h.token(permissions=KEEPER), h.token(sub="gina", tenant="globex", permissions=KEEPER)
        vis, paris, _ = await world(h, a)
        doc = await make(h, a, "Stock Entry", entry("Réception", [{"item": vis, "qty": "5", "target_warehouse": paris}]))
        assert (await h.http("POST", f"/doc/Stock Entry/{doc['name']}/submit", token=g, tenant="globex", json={"version": 1})).status_code == 404
        assert (await h.http("GET", "/list/Bin", token=g, tenant="globex")).json()["total"] == 0
        assert (await h.http("POST", f"/doc/Stock Entry/{doc['name']}/submit", token=a, json={"version": 1})).status_code == 200
        assert (await h.http("GET", "/list/Bin", token=g, tenant="globex")).json()["total"] == 0
        assert (await h.http("GET", "/list/Bin", token=a)).json()["total"] == 1


async def test_the_workspace_is_served_filtered_by_the_callers_rights(tmp_path):
    async with boot_host(tmp_path, operator_doctypes=False, link_plugins=PLUGINS) as h:
        full = (await h.http("GET", "/workspaces", token=h.token(permissions=KEEPER))).json()
        # Deux espaces : celui du stock (order 20) et le référentiel de `geo` (order 80), que tout utilisateur identifié lit.
        assert [w["name"] for w in full] == ["stock", "geo"] and full[0]["icon"] == "warehouse"
        blocks = full[0]["blocks"]
        # Le module s'ouvre sur un tableau de bord : des chiffres, des graphiques, les derniers mouvements, PUIS les accès.
        assert [b["type"] for b in blocks[:12]] == ["header", "number_card", "number_card", "number_card", "number_card", "chart", "chart", "chart", "chart", "quick_list", "header", "shortcut"]
        shortcuts = [(b["doctype"], b["create"]) for b in blocks if b["type"] == "shortcut"]
        assert shortcuts == [("Stock Entry", True), ("Bin", False), ("Stock Ledger Entry", False), ("Item", True), ("Warehouse", True)]
        # « + » où l'on peut écrire ; jamais sur les soldes ni le journal (fermés à toute saisie : leur `new` n'est d'ailleurs pas demandé).
        # Un magasinier qui ne lit que les soldes ne voit que ce qui parle des soldes : plus de mouvements, d'articles, d'entrepôts, ni de « Référentiel ».
        only_bins = (await h.http("GET", "/workspaces", token=h.token(permissions=["stock:bin:read"]))).json()
        seen = [(b["type"], b.get("doctype") or b["text"]) for b in only_bins[0]["blocks"]]
        assert seen == [("header", "Vue d'ensemble"), ("chart", "Bin"), ("chart", "Bin"), ("header", "Opérations"), ("shortcut", "Bin")]
        # Sans aucun grant : plus rien du stock, mais le référentiel demeure — c'est le prix EXACT de `@authenticated`,
        # et il est voulu (un pays se lit par tous, sinon aucun autre module ne pourrait en citer un) ; ses raccourcis
        # n'offrent pas « + », faute du droit d'écrire dans `geo`.
        bare = (await h.http("GET", "/workspaces", token=h.token(permissions=[]))).json()
        assert [w["name"] for w in bare] == ["geo"]
        assert [(b["doctype"], b["create"]) for b in bare[0]["blocks"] if b["type"] == "shortcut"] == [("Pays", False), ("Devise", False)]
        assert (await h.http("GET", "/workspaces", token=None)).status_code == 401
        done = await h.action("meta.workspaces", {}, token=h.token(permissions=KEEPER))
        assert [w["name"] for w in done["items"]] == ["stock", "geo"]


async def test_the_verify_action_recomputes_the_balances_from_the_ledger(tmp_path):
    """La commande de recalcul (doc/palier40.md §4.5) : appel d'opérateur par la route IPC d'xcore, lecture en contexte système."""
    import sqlite3

    async with boot_host(tmp_path, operator_doctypes=False, link_plugins=PLUGINS) as h:
        t = h.token(permissions=KEEPER)
        vis, paris, _ = await world(h, t)
        doc = await make(h, t, "Stock Entry", entry("Réception", [{"item": vis, "qty": "10", "target_warehouse": paris}]))
        assert (await h.http("POST", f"/doc/Stock Entry/{doc['name']}/submit", token=t, json={"version": 1})).status_code == 200

        ok = await h.action("balances.verify", {"tenant": "acme"}, token=t, plugin="stock")
        assert (ok["status"], ok["ok"], ok["problems"]) == ("ok", True, [])
        assert (await h.action("balances.verify", {"tenant": "globex"}, token=t, plugin="stock"))["problems"] == []  # un autre tenant : rien à lui reprocher

        # Un solde qui dérive (on l'écrit en SQL, hors de tout contrôle) : constaté, nommé, jamais corrigé en silence.
        db = sqlite3.connect(h.root / "t.db")
        db.execute("UPDATE xdt_document SET data = json_set(data, '$.qty', '9.000') WHERE doctype = 'Bin'")
        db.commit()
        db.close()
        bad = await h.action("balances.verify", {"tenant": "acme"}, token=t, plugin="stock")
        assert bad["ok"] is False
        assert bad["problems"] == [{"item": vis, "warehouse": paris, "ledger": "10.000", "bin": "9.000"}]
        again = await h.action("balances.verify", {"tenant": "acme"}, token=t, plugin="stock")
        assert again["problems"] == bad["problems"]  # rien n'a été réparé

        assert (await h.action("balances.verify", {}, token=t, plugin="stock"))["code"] == "missing_field"
        assert (await h.action("balances.verify", {"tenant": "../x"}, token=t, plugin="stock"))["code"] == "invalid_tenant"


# ── Le critère de sortie sur un vrai hôte et un vrai PostgreSQL : des soumissions HTTP simultanées ─────────────

import asyncio  # noqa: E402
import os  # noqa: E402

import pytest  # noqa: E402

PG_URL = os.environ.get("XCORE_DOCTYPE_TEST_PG_URL")


@pytest.mark.postgres
@pytest.mark.skipif(not PG_URL, reason="XCORE_DOCTYPE_TEST_PG_URL non défini")
async def test_simultaneous_http_issues_never_overdraw_on_postgresql(tmp_path):
    import asyncpg

    from xcore_doctype.db.tables import build_system_tables

    conn = await asyncpg.connect(PG_URL.replace("postgresql+asyncpg", "postgresql"))
    for name in build_system_tables().metadata.tables:
        await conn.execute(f"DROP TABLE IF EXISTS public.{name}")
    try:
        async with boot_host(tmp_path, operator_doctypes=False, link_plugins=PLUGINS, db_type="postgresql+aio", db_url=PG_URL) as h:
            t = h.token(permissions=KEEPER)
            vis, paris, _ = await world(h, t)
            first = await make(h, t, "Stock Entry", entry("Réception", [{"item": vis, "qty": "10", "target_warehouse": paris}]))
            assert (await h.http("POST", f"/doc/Stock Entry/{first['name']}/submit", token=t, json={"version": 1})).status_code == 200
            drafts = [await make(h, t, "Stock Entry", entry("Sortie", [{"item": vis, "qty": "3", "source_warehouse": paris}])) for _ in range(8)]
            replies = await asyncio.gather(
                *(h.http("POST", f"/doc/Stock Entry/{d['name']}/submit", token=t, json={"version": 1}) for d in drafts)
            )
            codes = sorted(r.status_code for r in replies)
            assert codes == [200] * 3 + [422] * 5, codes  # 10 // 3 = 3 sorties, jamais une de plus
            bins = (await h.http("GET", "/list/Bin", token=t)).json()["items"]
            assert [b["data"]["qty"] for b in bins] == ["1.000"]
    finally:
        for name in build_system_tables().metadata.tables:
            await conn.execute(f"DROP TABLE IF EXISTS public.{name}")
        await conn.close()
