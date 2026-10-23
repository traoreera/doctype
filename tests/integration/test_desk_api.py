"""L'API que consomme le Desk, de bout en bout sur un vrai Xcore : métadonnées, filtres à opérateurs, recherche, conditions."""

from __future__ import annotations

import json

from tests.integration.conftest import CUSTOMER_MANAGER, DEFAULT_GRANTS
from xcore_doctype.expr import evaluate


async def make(h, token, **data):
    base = {"customer_name": "Acme", "type": "Particulier"}
    r = await h.http("POST", "/doc/Customer", token=token, json={"data": {**base, **data}})
    assert r.status_code == 201, r.text
    return r.json()


async def test_meta_gives_the_desk_everything_it_needs(host):
    meta = (await host.http("GET", "/meta/Customer", token=host.token())).json()
    assert meta["title_field"] == "customer_name" and meta["label"] == "Client"
    assert meta["list_columns"] == ["customer_name", "type", "credit_limit", "country"]
    assert [s["section"] for s in meta["layout"]] == ["Général", "Crédit"]
    fields = {f["fieldname"]: f for f in meta["fields"]}
    assert fields["customer_name"]["description"] == "Raison sociale ou nom complet"
    # Les conditions arrivent en arbres JSON, prêts à être évalués par le navigateur.
    assert isinstance(fields["company_id"]["depends_on"], dict) and isinstance(fields["approver"]["mandatory_depends_on"], dict)
    assert evaluate(fields["company_id"]["depends_on"], {"type": "Entreprise"}) is True
    assert evaluate(fields["approver"]["mandatory_depends_on"], {"credit_limit": 20000}) is True
    assert json.loads(json.dumps(meta)) == meta
    assert meta["rights"] == {"read": "all", "write": "all", "create": "all", "delete": "all", "submit": "none", "cancel": "none"}


async def test_filters_with_operators_over_http(host):
    t = host.token()
    for name, limit, typ in (("Alpha", 100, "Entreprise"), ("Bravo", 7, "Particulier"), ("Charlie", 500, "Entreprise")):
        await make(host, t, customer_name=name, credit_limit=limit, type=typ,
                   **({"company_id": "X"} if typ == "Entreprise" else {}))

    async def ask(flt, **extra):
        r = await host.http("GET", "/list/Customer", token=t, params={"filters": json.dumps(flt), "order_by": "customer_name", "order": "asc", **extra})
        assert r.status_code == 200, r.text
        return [d["data"]["customer_name"] for d in r.json()["items"]], r.json()["total"]

    assert await ask({"credit_limit": [">=", 100]}) == (["Alpha", "Charlie"], 2)
    assert await ask({"customer_name": ["like", "AR"]}) == (["Charlie"], 1)
    assert await ask({"type": ["!=", "Entreprise"]}) == (["Bravo"], 1)
    assert await ask({"credit_limit": ["in", [7, 500]]}) == (["Bravo", "Charlie"], 2)
    assert await ask({"type": "Entreprise", "credit_limit": ["<", 200]}) == (["Alpha"], 1)
    assert await ask({"credit_limit": [">=", 0]}, limit=2, offset=2) == (["Charlie"], 3)  # le total ignore la page


async def test_bad_filters_are_422_with_the_reason(host):
    t = host.token()
    for flt in ({"customer_name": [">", "a"]}, {"credit_limit": ["like", "1"]}, {"nope": 1}, {"credit_limit": [">", "abc"]}, {"type": ["in", "x"]}):
        r = await host.http("GET", "/list/Customer", token=t, params={"filters": json.dumps(flt)})
        assert r.status_code == 422 and r.json()["detail"]["code"] == "validation_failed", flt


async def test_search_for_a_link_field(host):
    t = host.token()
    for n in ("Paris SA", "Parme SARL", "Berlin GmbH"):
        await make(host, t, customer_name=n)
    r = await host.http("GET", "/search/Customer", token=t, params={"q": "par"})
    assert r.status_code == 200
    assert [i["title"] for i in r.json()["items"]] == ["Paris SA", "Parme SARL"]  # trié par titre ; « Berlin » n'y est pas
    assert all(set(i) == {"name", "title"} for i in r.json()["items"])
    same = await host.action("doc.search", {"doctype": "Customer", "q": "par"}, token=t)
    assert [i["title"] for i in same["items"]] == [i["title"] for i in r.json()["items"]]  # l'action donne la même réponse
    limited = (await host.http("GET", "/search/Customer", token=t, params={"q": "", "limit": 2})).json()["items"]
    assert [i["title"] for i in limited] == ["Berlin GmbH", "Paris SA"]


async def test_search_is_filtered_by_permissions_and_tenant(host):
    manager = host.token(sub="m")
    mine = host.token(sub="alice", permissions=["sales:customer:own"])
    await make(host, manager, customer_name="Secret SA")
    await make(host, mine, customer_name="Mon client")
    assert [i["title"] for i in (await host.http("GET", "/search/Customer", token=mine, params={"q": ""})).json()["items"]] == ["Mon client"]
    blind = host.token(sub="b", permissions=[])
    assert (await host.http("GET", "/search/Customer", token=blind, params={"q": ""})).status_code == 403
    other = host.token(sub="x", tenant="globex")
    assert (await host.http("GET", "/search/Customer", token=other, tenant="globex", params={"q": ""})).json()["items"] == []
    assert (await host.http("GET", "/search/Customer", token=None, params={"q": ""})).status_code == 401


async def test_search_parameters_are_validated(host):
    t = host.token()
    for params in ({"limit": 0}, {"limit": 51}, {"q": "x" * 101}, {"docstatus": "x"}, {"docstatus": "3"}, {"docstatus": ""}):
        assert (await host.http("GET", "/search/Customer", token=t, params=params)).status_code == 422, params
    assert (await host.http("GET", "/search/Nope", token=t)).status_code == 404


async def test_search_can_offer_only_some_states(host):
    """`docstatus=0,1` (ce qu'envoie le Desk pour un lien `not_cancelled`) : un client n'est jamais soumis, ses documents sont des brouillons."""
    t = host.token()
    await make(host, t, customer_name="Paris SA")
    titles = lambda r: [i["title"] for i in r.json()["items"]]  # noqa: E731
    assert titles(await host.http("GET", "/search/Customer", token=t, params={"q": "par", "docstatus": "0,1"})) == ["Paris SA"]
    assert titles(await host.http("GET", "/search/Customer", token=t, params={"q": "par", "docstatus": "1"})) == []
    same = await host.action("doc.search", {"doctype": "Customer", "q": "par", "docstatus": [1, 2]}, token=t)
    assert same["items"] == []


async def test_global_search_spans_doctypes(host):
    t = host.token()
    await make(host, t, customer_name="Paris SA")
    r = await host.http("GET", "/search", token=t, params={"q": "par"})
    assert r.status_code == 200
    items = r.json()["items"]
    assert any(i["doctype"] == "Customer" and i["title"] == "Paris SA" for i in items)
    # Chaque résultat porte de quoi ouvrir le document ET nommer son type.
    assert all(set(i) == {"doctype", "label", "name", "title"} for i in items)
    # Moins de deux caractères ne cherche rien (la saisie d'un caractère ramènerait tout le premier type venu).
    assert (await host.http("GET", "/search", token=t, params={"q": "p"})).json()["items"] == []
    # L'action IPC donne la même réponse que la route HTTP.
    same = await host.action("doc.global_search", {"q": "par"}, token=t)
    assert same["items"] == items
    for params in ({"limit": 0}, {"limit": 51}, {"q": "x" * 101}):
        assert (await host.http("GET", "/search", token=t, params=params)).status_code == 422, params


async def test_global_search_is_filtered_by_permissions_and_tenant(host):
    manager = host.token(sub="m")
    await make(host, manager, customer_name="Secret SA")
    # Aucun type lisible : la recherche globale ne rend rien (elle n'énumère pas ce qu'on ne peut pas voir), pas 403.
    blind = host.token(sub="b", permissions=[])
    assert (await host.http("GET", "/search", token=blind, params={"q": "sec"})).json()["items"] == []
    # Un autre tenant ne voit pas le document.
    other = host.token(sub="x", tenant="globex")
    assert (await host.http("GET", "/search", token=other, tenant="globex", params={"q": "sec"})).json()["items"] == []
    assert (await host.http("GET", "/search", token=None, params={"q": "sec"})).status_code == 401


async def test_conditions_are_enforced_by_the_server_over_http(host):
    t = host.token()
    # company_id est obligatoire seulement pour une entreprise
    r = await host.http("POST", "/doc/Customer", token=t, json={"data": {"customer_name": "X", "type": "Entreprise"}})
    assert r.status_code == 422
    assert r.json()["detail"]["details"] == [{"field": "company_id", "msg": "Champ obligatoire", "type": "missing"}]
    ok = await make(host, t, customer_name="Particulier sans n°", type="Particulier")
    assert "company_id" not in ok["data"]
    # approver devient obligatoire au-delà de 10000
    r = await host.http("POST", "/doc/Customer", token=t, json={"data": {"customer_name": "Gros", "credit_limit": 50000}})
    assert [d["field"] for d in r.json()["detail"]["details"]] == ["approver"]
    # un compte bloqué verrouille son plafond
    blocked = await make(host, t, customer_name="Bloqué", credit_limit=100, blocked=True)
    r = await host.http("PUT", f"/doc/Customer/{blocked['name']}", token=t, json={"version": 1, "data": {"customer_name": "Bloqué", "credit_limit": 999, "blocked": True}})
    assert r.status_code == 422 and r.json()["detail"]["details"][0]["type"] == "read_only"
    same = await host.http("PUT", f"/doc/Customer/{blocked['name']}", token=t, json={"version": 1, "data": {"customer_name": "Renommé", "credit_limit": 100, "blocked": True}})
    assert same.status_code == 200


# ── Agrégats (cartes, graphiques, rapports) ──────────────────────────────────────────────────


async def test_aggregate_over_http_and_the_same_answer_from_the_action(host):
    t = host.token()
    for name, limit, typ in (("Alpha", 100, "Entreprise"), ("Bravo", 7, "Particulier"), ("Charlie", 500, "Entreprise")):
        await make(host, t, customer_name=name, credit_limit=limit, type=typ, **({"company_id": "X"} if typ == "Entreprise" else {}))

    r = await host.http("GET", "/aggregate/Customer", token=t, params={"group_by": "type", "metrics": "count,sum:credit_limit"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert [(x["key"], x["values"]) for x in body["rows"]] == [("Entreprise", [2, 600]), ("Particulier", [1, 7])]
    assert body["group"]["fieldtype"] == "Select" and [m["function"] for m in body["metrics"]] == ["count", "sum"]
    # Les mêmes filtres que la liste, et la même réponse par l'action xcore.
    flt = {"credit_limit": [">=", 100]}
    http = (await host.http("GET", "/aggregate/Customer", token=t, params={"filters": json.dumps(flt)})).json()
    act = await host.action("doc.aggregate", {"doctype": "Customer", "filters": flt}, token=t)
    assert http["rows"] == [{"key": None, "values": [2]}] and act["rows"] == http["rows"]


async def test_aggregate_refusals_over_http(host):
    t = host.token()
    for params in ({"group_by": "customer_name"}, {"metrics": "sum:customer_name"}, {"metrics": "avg"}, {"bucket": "month"}, {"limit": 0}):
        r = await host.http("GET", "/aggregate/Customer", token=t, params=params)
        assert r.status_code == 422 and r.json()["detail"]["code"] == "validation_failed", params
    assert (await host.http("GET", "/aggregate/Customer", token=t, params={"filters": "{pas du json"})).status_code == 422
    assert (await host.http("GET", "/aggregate/Nope", token=t)).status_code == 404
    assert (await host.http("GET", "/aggregate/Customer", token=host.token(sub="b", permissions=[]))).status_code == 403
    assert (await host.http("GET", "/aggregate/Customer", token=None)).status_code == 401


async def test_an_aggregate_counts_only_what_the_caller_may_read_in_their_tenant(host):
    manager = host.token(sub="m")
    mine = host.token(sub="alice", permissions=["sales:customer:own"])
    await make(host, manager, customer_name="Secret SA")
    await make(host, mine, customer_name="Mon client")
    count = lambda tok, tenant="acme": host.http("GET", "/aggregate/Customer", token=tok, tenant=tenant)  # noqa: E731
    assert (await count(manager)).json()["rows"][0]["values"] == [2]
    assert (await count(mine)).json()["rows"][0]["values"] == [1]  # le chiffre de sa liste, pas celui de l'organisation
    other = host.token(sub="x", tenant="globex")
    assert (await count(other, "globex")).json()["rows"][0]["values"] == [0]


async def test_meta_announces_the_views_and_a_ready_made_dashboard(host):
    meta = (await host.http("GET", "/meta/Customer", token=host.token())).json()
    views = meta["views"]
    assert views["available"] == ["list", "report", "dashboard"] and views["default"] == "list"
    assert views["dashboard"][0] == {"type": "number_card", "label": "Client", "doctype": "Customer", "function": "count", "cols": 6, "color": "blue"}  # le libellé du type ; les cartes se partagent la rangée
    # Tout ce que le tableau de bord généré demande, le serveur sait le calculer.
    for block in views["dashboard"]:
        params = {"group_by": block["group_by"]} if block["type"] == "chart" else {}
        if block.get("bucket"):
            params["bucket"] = block["bucket"]
        if block.get("periods"):
            params["periods"] = block["periods"]
        if block.get("field"):
            params["metrics"] = f"{block['function']}:{block['field']}"
        r = await host.http("GET", "/aggregate/Customer", token=host.token(), params=params)
        assert r.status_code == 200, (block, r.text)


# ── Vues sauvegardées (rapports de liste) ────────────────────────────────────────────────────


async def test_saved_views_round_trip_over_http_and_the_action(host):
    t = host.token()
    config = {"columns": ["customer_name", "credit_limit"], "filters": {"type": "Entreprise"},
              "order_by": "credit_limit", "descending": False}
    r = await host.http("POST", "/views/Customer/entreprises", token=t,
                        json={"label": "Entreprises", "kind": "list", "config": config, "is_default": True})
    assert r.status_code == 200, r.text
    saved = r.json()
    assert saved["label"] == "Entreprises" and saved["is_default"] is True and saved["config"]["order_by"] == "credit_limit"

    listing = (await host.http("GET", "/views/Customer", token=t)).json()["items"]
    assert [v["name"] for v in listing] == ["entreprises"]
    # L'action IPC donne exactement la même réponse que la route HTTP.
    same = await host.action("doc.views.list", {"doctype": "Customer"}, token=t)
    assert same["items"] == listing

    # Une seconde vue par défaut désarme la première : une seule par défaut.
    await host.http("POST", "/views/Customer/tous", token=t, json={"label": "Tous", "is_default": True})
    listing = (await host.http("GET", "/views/Customer", token=t)).json()["items"]
    assert {v["name"]: v["is_default"] for v in listing} == {"entreprises": False, "tous": True}

    # Renommage : l'ancien nom disparaît, le nouveau garde la configuration.
    r = await host.http("POST", "/views/Customer/clients-entreprise", token=t,
                        json={"label": "Clients entreprise", "config": config, "is_default": True, "previous": "entreprises"})
    assert r.status_code == 200
    assert [v["name"] for v in (await host.http("GET", "/views/Customer", token=t)).json()["items"]] == ["clients-entreprise", "tous"]

    assert (await host.http("DELETE", "/views/Customer/tous", token=t)).status_code == 204
    assert [v["name"] for v in (await host.http("GET", "/views/Customer", token=t)).json()["items"]] == ["clients-entreprise"]
    assert (await host.http("DELETE", "/views/Customer/tous", token=t)).status_code == 404  # déjà supprimée


async def test_saved_views_are_private_and_validated(host):
    alice = host.token(sub="alice")
    bob = host.token(sub="bob")
    await host.http("POST", "/views/Customer/mienne", token=alice, json={"label": "Mienne", "config": {"columns": ["customer_name"]}})
    assert (await host.http("GET", "/views/Customer", token=bob)).json()["items"] == []  # privée à son propriétaire
    assert (await host.http("GET", "/views/Customer", token=alice)).json()["items"][0]["name"] == "mienne"
    # Un autre tenant ne voit rien.
    other = host.token(sub="x", tenant="globex")
    assert (await host.http("GET", "/views/Customer", token=other, tenant="globex")).json()["items"] == []
    # Pas d'accès, pas de jeton.
    blind = host.token(sub="b", permissions=[])
    assert (await host.http("GET", "/views/Customer", token=blind)).status_code == 403
    assert (await host.http("GET", "/views/Customer", token=None)).status_code == 401

    # Validations : rien d'inconnu n'est stocké.
    bad = [
        {"label": "x", "kind": "kanban"},  # vue non déclarée pour ce type
        {"label": "x", "config": {"columns": ["nope"]}},  # colonne inconnue
        {"label": "x", "config": {"columns": ["customer_name"], "order_by": "nope"}},  # tri impossible
        {"label": "x", "config": {"bogus": 1}},  # clé de configuration inconnue
        {"label": "", "config": {}},  # label vide
    ]
    for body in bad:
        r = await host.http("POST", "/views/Customer/vue", token=alice, json=body)
        assert r.status_code == 422 and r.json()["detail"]["code"] == "validation_failed", body
    # Une vue d'un autre utilisateur est « introuvable » à la suppression.
    assert (await host.http("DELETE", "/views/Customer/mienne", token=bob)).status_code == 404
    assert (await host.http("DELETE", "/views/Nope/vue", token=alice)).status_code == 404
