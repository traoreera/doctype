"""Spike d'intégration xcore : critères d'acceptation de process/doctype.md §14 bis.8, sur un vrai `Xcore`.

Chaque test cite son critère. Les écarts avec la doc sont documentés dans les tests eux-mêmes.
"""

from __future__ import annotations

import gc
import weakref

import pytest

from tests.integration.conftest import DEFAULT_GRANTS, boot_host

CUSTOMER = {"customer_name": "Acme SA", "type": "Entreprise", "company_id": "FR-123", "credit_limit": 5000}


async def create(host, token=None, tenant="acme", **data):
    token = token or host.token(tenant=tenant)
    r = await host.http("POST", "/doc/Customer", token=token, tenant=tenant, json={"data": {**CUSTOMER, **data}})
    assert r.status_code == 201, r.text
    return r.json()


async def plugin_state(host, name="xcore-doctype") -> str | None:
    plugins = (await host.ipc("GET", "/status")).json()["plugins"]
    return next((p["state"] for p in plugins if p["name"] == name), None)


# ── Critère 1 : chargement et signature ─────────────────────────────────────────────────────────


EXPECTED_ROUTES = sorted(
    f"{op} /app/xcore-doctype{path}"
    for op, path in [
        ("GET", "/meta"),
        ("GET", "/meta/{doctype}"),
        ("GET", "/workspaces"),
        ("GET", "/list/{doctype}"),
        ("GET", "/aggregate/{doctype}"),
        ("GET", "/search"),
        ("GET", "/search/{doctype}"),
        ("GET", "/history/{doctype}/{name}"),
        ("GET", "/mappings/{doctype}/{name}"),
        ("GET", "/map/{doctype}/{source}/{name}"),
        ("POST", "/upload/{doctype}/{fieldname}"),
        ("POST", "/file-links"),
        ("GET", "/preferences"),
        ("GET", "/collab/{doctype}/{name}"),
        ("POST", "/comments/{doctype}/{name}"),
        ("PUT", "/comments/{comment_id}"),
        ("DELETE", "/comments/{comment_id}"),
        ("POST", "/assignments/{doctype}/{name}"),
        ("PUT", "/assignments/{assignment_id}"),
        ("GET", "/my-assignments"),
        ("GET", "/users"),
        ("GET", "/people/{doctype}"),
        ("GET", "/mentionable/{doctype}/{name}"),
        ("POST", "/assign-many/{doctype}"),
        ("GET", "/shares/{doctype}/{name}"),
        ("POST", "/shares/{doctype}/{name}"),
        ("DELETE", "/shares/{doctype}/{name}"),
        ("GET", "/shared-with-me"),
        ("GET", "/tags/{doctype}/{name}"),
        ("POST", "/tags/{doctype}/{name}"),
        ("POST", "/tags-of/{doctype}"),
        ("GET", "/tag-counts/{doctype}"),
        ("GET", "/tags-used/{doctype}"),
        ("POST", "/tag-many/{doctype}"),
        ("POST", "/attachments/{doctype}/{name}"),
        ("DELETE", "/attachments/{attachment_id}"),
        ("GET", "/file-library"),
        ("POST", "/file-library/detach"),
        ("GET", "/files/{tenant}/{file_id}"),  # PUBLIQUE : la signature de l'URL fait foi (voir api/files.py)
        ("GET", "/tree/{doctype}"),
        ("GET", "/connections/{doctype}/{name}"),
        ("GET", "/views/{doctype}"),
        ("POST", "/views/{doctype}/{name}"),
        ("DELETE", "/views/{doctype}/{name}"),
        ("GET", "/override/form/{doctype}"),
        ("PUT", "/override/form/{doctype}"),
        ("DELETE", "/override/form/{doctype}"),
        ("GET", "/override/workflow/{doctype}"),
        ("PUT", "/override/workflow/{doctype}"),
        ("DELETE", "/override/workflow/{doctype}"),
        ("PUT", "/override/dashboard/{doctype}"),
        ("DELETE", "/override/dashboard/{doctype}"),
        ("PUT", "/override/workspace/{name}"),
        ("DELETE", "/override/workspace/{name}"),
        ("POST", "/doc/{doctype}"),
        ("GET", "/doc/{doctype}/{name}"),
        ("PUT", "/doc/{doctype}/{name}"),
        ("POST", "/doc/{doctype}/{name}/submit"),
        ("POST", "/doc/{doctype}/{name}/transition"),  # workflow : la transition remplace /submit sur un type qui en a un
        ("GET", "/transitions/{doctype}/{name}"),
        ("POST", "/doc/{doctype}/{name}/cancel"),
        ("POST", "/doc/{doctype}/{name}/amend"),
        ("POST", "/doc/{doctype}/{name}/rename"),
        ("POST", "/bulk/{doctype}"),
        ("POST", "/import/{doctype}"),
        ("GET", "/devtools/doctype/{doctype}"),
        ("POST", "/devtools/check"),
        ("DELETE", "/doc/{doctype}/{name}"),
    ]
)


async def test_c1_plugin_loads_from_a_plugin_folder(host):
    assert await plugin_state(host) == "ready"
    assert host.plugin_routes() == EXPECTED_ROUTES


async def test_c1_strict_trusted_rejects_an_unsigned_plugin(tmp_path):
    """Caractérise l'écart qui motive D19.

    Un wheel ne peut pas fournir `plugin.sig` (calculé avec la clé du déploiement) : avec
    `strict_trusted` (défaut d'xcore), le plugin n'est pas chargé. Il faut la liste blanche
    d'entry points (D19) ou la commande `sign`. Si ce test échoue un jour parce que le plugin
    se charge, xcore a changé : relire D19.
    """
    async with boot_host(tmp_path, strict_trusted=True) as h:
        assert await plugin_state(h) != "ready"
        r = await h.http("GET", "/meta", token=h.token())
        assert r.status_code == 404  # routes jamais montées


# ── Critère 2 : action doc.get, et deny_all sans bloc `permissions` ─────────────────────────────


async def test_c2_action_doc_get_succeeds(host):
    doc = await create(host)
    res = await host.action("doc.get", {"doctype": "Customer", "name": doc["name"]}, token=host.token())
    assert res == {"status": "ok", "doc": doc}


async def test_c2_without_permissions_block_every_action_is_denied(tmp_path):
    async with boot_host(tmp_path, permissions=False) as h:
        t = h.token()
        for name, payload in (
            ("doc.get", {"doctype": "Customer", "name": "x"}),
            ("doc.list", {"doctype": "Customer"}),
            ("meta.list", {}),
        ):
            res = await h.action(name, payload, token=t)
            assert (res["status"], res["code"]) == ("error", "permission_denied"), name


async def test_c2_http_routes_bypass_the_manifest_permissions(tmp_path):
    """Fait d'xcore à ne pas oublier : les routes du plugin ne passent pas par PermissionEngine.

    Même avec `deny_all`, l'API HTTP répond. Toute restriction en HTTP doit donc vivre dans le
    service Document (process/doctype.md §10), jamais dans le manifeste.
    """
    async with boot_host(tmp_path, permissions=False) as h:
        r = await h.http("GET", "/meta", token=h.token())
        assert r.status_code == 200 and r.json()[0]["name"] == "Customer"


async def test_c2_unknown_action_is_reported_not_crashed(host):
    res = await host.action("doc.drop_everything", {}, token=host.token())
    assert (res["status"], res["code"]) == ("error", "unknown_action")


# ── Critère 3 : HTTP et action donnent le même résultat ─────────────────────────────────────────


async def test_c3_same_result_over_http_and_action(host):
    t = host.token()
    doc = await create(host, t)
    http_get = (await host.http("GET", f"/doc/Customer/{doc['name']}", token=t)).json()
    act_get = (await host.action("doc.get", {"doctype": "Customer", "name": doc["name"]}, token=t))["doc"]
    assert http_get == act_get == doc

    http_list = (await host.http("GET", "/list/Customer", token=t, params={"filters": '{"type": "Entreprise"}'})).json()
    act_list = await host.action("doc.list", {"doctype": "Customer", "filters": {"type": "Entreprise"}}, token=t)
    assert http_list == {k: v for k, v in act_list.items() if k != "status"}


async def test_c3_same_errors_over_http_and_action(host):
    t = host.token()
    http = await host.http("GET", "/doc/Customer/nope", token=t)
    act = await host.action("doc.get", {"doctype": "Customer", "name": "nope"}, token=t)
    assert http.status_code == 404 and http.json()["detail"]["code"] == act["code"] == "document_not_found"

    http = await host.http("POST", "/doc/Customer", token=t, json={"data": {"credit_limit": "x"}})
    act = await host.action("doc.save", {"doctype": "Customer", "data": {"credit_limit": "x"}}, token=t)
    assert http.status_code == 422 and http.json()["detail"]["code"] == act["code"] == "validation_failed"


async def test_c3_full_write_cycle_over_actions(host):
    t = host.token()
    created = (await host.action("doc.save", {"doctype": "Customer", "data": CUSTOMER}, token=t))["doc"]
    updated = (
        await host.action(
            "doc.save",
            {"doctype": "Customer", "name": created["name"], "version": 1, "data": {"customer_name": "Renamed"}},
            token=t,
        )
    )["doc"]
    assert updated["version"] == 2 and updated["data"]["customer_name"] == "Renamed"

    stale = await host.action(
        "doc.save", {"doctype": "Customer", "name": created["name"], "version": 1, "data": {"customer_name": "x"}}, token=t
    )
    assert stale["code"] == "version_conflict"
    missing_version = await host.action(
        "doc.save", {"doctype": "Customer", "name": created["name"], "data": {"customer_name": "x"}}, token=t
    )
    assert missing_version["code"] == "version_required"

    assert (await host.action("doc.delete", {"doctype": "Customer", "name": created["name"]}, token=t))["status"] == "ok"
    assert (await host.action("doc.get", {"doctype": "Customer", "name": created["name"]}, token=t))["code"] == "document_not_found"


async def test_c3_http_conflict_is_409(host):
    t = host.token()
    doc = await create(host, t)
    ok = await host.http("PUT", f"/doc/Customer/{doc['name']}", token=t, json={"version": 1, "data": {"customer_name": "a"}})
    stale = await host.http("PUT", f"/doc/Customer/{doc['name']}", token=t, json={"version": 1, "data": {"customer_name": "b"}})
    assert (ok.status_code, stale.status_code) == (200, 409)


# ── Critère 4 : tenant lié à l'identité ─────────────────────────────────────────────────────────


async def test_c4_header_must_match_the_claim(host):
    t = host.token(tenant="acme")
    http = await host.http("GET", "/meta", token=t, tenant="globex")
    act = await host.action("meta.list", {}, token=t, tenant="globex")
    assert http.status_code == 403 and http.json()["detail"]["code"] == "tenant_mismatch"
    assert act["code"] == "tenant_mismatch"


async def test_c4_cross_tenant_grant_allows_and_acts_in_the_header_tenant(host):
    admin = host.token(sub="root", tenant="acme", permissions=[*DEFAULT_GRANTS, "doctype:cross_tenant"])
    doc = await create(host, admin, tenant="globex")  # écrit chez globex bien que le claim soit acme
    assert (await host.http("GET", f"/doc/Customer/{doc['name']}", token=host.token(sub="bob", tenant="globex"), tenant="globex")).status_code == 200
    # Et ce document n'existe pas chez acme.
    assert (await host.http("GET", f"/doc/Customer/{doc['name']}", token=host.token(tenant="acme"), tenant="acme")).status_code == 404
    # Même chose par une action.
    res = await host.action("doc.get", {"doctype": "Customer", "name": doc["name"]}, token=admin, tenant="globex")
    assert res["doc"] == doc


async def test_c4_missing_header_means_default_tenant_which_the_claim_must_cover(host):
    t = host.token(tenant="acme")
    r = await host.http("GET", "/meta", token=t, tenant=None)
    assert r.status_code == 403 and r.json()["detail"]["code"] == "tenant_mismatch"
    assert (await host.http("GET", "/meta", token=host.token(tenant="default"), tenant=None)).status_code == 200


async def test_c4_token_without_tenant_claim_refused(host):
    t = host.token(tenant=None)
    assert (await host.http("GET", "/meta", token=t)).json()["detail"]["code"] == "tenant_claim_missing"
    assert (await host.action("meta.list", {}, token=t))["code"] == "tenant_claim_missing"


@pytest.mark.parametrize("bad", ["public", "information_schema", "pg_catalog", "a b", "x" * 64])
async def test_c4_reserved_or_invalid_tenant_rejected_by_xcore_before_us(host, bad):
    r = await host.http("GET", "/meta", token=host.token(tenant=bad), tenant=bad)
    assert r.status_code == 400


async def test_c4_data_is_invisible_across_tenants_end_to_end(host):
    doc = await create(host, tenant="acme")
    other = host.token(sub="bob", tenant="globex")
    assert (await host.http("GET", f"/doc/Customer/{doc['name']}", token=other, tenant="globex")).status_code == 404
    assert (await host.http("GET", "/list/Customer", token=other, tenant="globex")).json()["total"] == 0
    assert (await host.http("DELETE", f"/doc/Customer/{doc['name']}", token=other, tenant="globex")).status_code == 404
    assert (await host.http("PUT", f"/doc/Customer/{doc['name']}", token=other, tenant="globex", json={"version": 1, "data": {"customer_name": "x"}})).status_code == 404
    assert (await host.http("GET", f"/doc/Customer/{doc['name']}", token=host.token(), tenant="acme")).json() == doc


async def test_c4_binding_disabled_makes_the_header_authoritative(tmp_path):
    """Rappel de ce que `enforce_tenant_binding: false` abandonne : n'importe quel porteur de jeton choisit son tenant."""
    async with boot_host(tmp_path, binding=False) as h:
        t = h.token(tenant="acme")
        assert (await h.http("GET", "/meta", token=t, tenant="globex")).status_code == 200


async def test_c4_forgetting_xcore_setup_fails_closed(tmp_path):
    """`Xcore.setup(app)` oublié (c'était le cas de main.py) : refus explicite plutôt que tenant « default » silencieux."""
    from contextlib import asynccontextmanager

    import httpx
    from fastapi import FastAPI
    from xcore import Xcore

    async with boot_host(tmp_path) as ok_host:
        config_path = ok_host.root / "integration.yaml"
    # Hôte sans middleware : on rejoue le démarrage sans appeler `setup`.
    xcore = Xcore(config_path=str(config_path))

    @asynccontextmanager
    async def lifespan(app):
        await xcore.boot(app)
        yield
        await xcore.shutdown()

    app = FastAPI(lifespan=lifespan)  # pas de xcore.setup(app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            from tests.integration.conftest import DEV_SECRET
            from tests.support import mint_token

            r = await c.get(
                "/app/xcore-doctype/meta",
                headers={"Authorization": "Bearer " + mint_token(DEV_SECRET, {"sub": "a", "tenant_id": "acme"}), "X-Tenant-ID": "acme"},
            )
    assert r.status_code == 500 and r.json()["detail"]["code"] == "tenant_middleware_missing"


# ── Critère 5 : appel sans principal ────────────────────────────────────────────────────────────


async def test_c5_no_token_refused_on_both_paths(host):
    http = await host.http("GET", "/meta", token=None)
    act = await host.action("doc.list", {"doctype": "Customer"}, token=None)
    assert http.status_code == 401
    assert (act["status"], act["code"]) == ("error", "unauthenticated")


@pytest.mark.parametrize("make", ["forged", "tampered", "garbage"])
async def test_c5_invalid_token_refused(host, make):
    good = host.token()
    token = {
        "forged": "e30.deadbeef",
        "tampered": good[:-4] + ("0000" if not good.endswith("0000") else "1111"),
        "garbage": "not-a-token",
    }[make]
    assert (await host.http("GET", "/meta", token=token)).status_code == 401
    assert (await host.action("meta.list", {}, token=token))["code"] == "unauthenticated"


async def test_c5_expired_token_refused(host):
    from tests.integration.conftest import DEV_SECRET
    from tests.support import mint_token

    expired = mint_token(DEV_SECRET, {"sub": "a", "tenant_id": "acme"}, ttl=-10)
    assert (await host.http("GET", "/meta", token=expired)).status_code == 401


async def test_c5_every_route_requires_identity(host):
    """Une route ajoutée plus tard sans paramètre `ctx` reste couverte par la dépendance de routeur.

    Les routes viennent d'OpenAPI (`plugin_routes`) : `app.router.routes` ne les expose pas avec ce FastAPI, et une
    première version de ce test, qui l'énumérait, ne vérifiait AUCUNE route. Le garde-fou ci-dessous l'empêche.
    """
    routes = host.plugin_routes()
    assert routes == EXPECTED_ROUTES, "la liste est vide ou périmée : ce test ne vérifierait rien"
    for entry in routes:
        method, path = entry.split(" ", 1)
        concrete = path.replace("{doctype}", "Customer").replace("{name}", "x").replace("{fieldname}", "photo").replace("{tenant}", "acme").replace("{file_id}", "a" * 32)
        r = await host.client.request(method, concrete, json={"data": {}, "version": 1})
        if "/files/" in path:
            # La seule route publique de l'API : elle n'exige pas de jeton mais une URL SIGNÉE ; sans signature elle ne sert rien.
            assert r.status_code == 422, f"{entry} sert quelque chose sans signature ({r.status_code})"
            forged = await host.client.get(concrete, params={"exp": 9999999999, "sig": "0" * 64})
            assert forged.status_code == 404, f"{entry} accepte une signature fausse ({forged.status_code})"
            continue
        assert r.status_code == 401, f"{entry} accessible sans identité ({r.status_code})"


# ── Critère 7 : reload ──────────────────────────────────────────────────────────────────────────


async def test_c7_reload_remounts_routes_and_leaves_no_residual_state(host):
    t = host.token()
    doc = await create(host, t)
    routes_before = host.plugin_routes()
    assert routes_before == EXPECTED_ROUTES  # garde-fou : la comparaison après reload ne doit pas être vide

    handler = host.xcore.plugins._loader.get("xcore-doctype")
    old = handler._instance
    old_registry, old_router = old.registry, old.get_router()
    assert old_registry.names() == ["Country", "Customer"]
    old_ref = weakref.ref(old)
    del old, handler

    r = await host.ipc("POST", "/xcore-doctype/reload")
    assert r.status_code == 200, r.text

    # Routes remontées, ni perdues ni dupliquées.
    assert host.plugin_routes() == routes_before
    # Les données persistent et l'API répond avec la nouvelle instance.
    assert (await host.http("GET", f"/doc/Customer/{doc['name']}", token=t)).json() == doc
    # Aucun état résiduel : le registre de l'ancienne instance a été rendu (D8).
    assert old_registry.names() == []
    # L'ancienne instance est libérée (pas retenue par une tâche ou un callback).
    for _ in range(5):
        gc.collect()
    assert old_ref() is None, "l'ancienne instance du plugin est encore référencée après reload"
    assert old_router is not host.xcore.plugins._loader.get("xcore-doctype")._instance.get_router()


async def test_c7_repeated_reloads_do_not_accumulate_routes(host):
    baseline = host.plugin_routes()
    assert baseline == EXPECTED_ROUTES
    for _ in range(3):
        assert (await host.ipc("POST", "/xcore-doctype/reload")).status_code == 200
    assert host.plugin_routes() == baseline
    assert (await host.http("GET", "/meta", token=host.token())).status_code == 200


async def test_c7_unload_removes_routes(host):
    assert host.plugin_routes() == EXPECTED_ROUTES
    assert (await host.ipc("DELETE", "/xcore-doctype/unload")).status_code == 200
    assert host.plugin_routes() == []
    assert (await host.http("GET", "/meta", token=host.token())).status_code == 404


# ── Critère 10 : adaptateur synchrone ───────────────────────────────────────────────────────────


async def test_c10_sync_database_adapter_prevents_loading(tmp_path):
    async with boot_host(tmp_path, db_type="sqlite") as h:
        assert await plugin_state(h) != "ready"
        assert (await h.http("GET", "/meta", token=h.token())).status_code == 404


async def test_c10_other_plugins_still_load_when_ours_fails(tmp_path):
    async with boot_host(tmp_path, db_type="sqlite") as h:
        assert await plugin_state(h, "auth") == "ready"


# ── Durcissement trouvé pendant le spike ────────────────────────────────────────────────────────


async def test_unexpected_errors_never_leak_internals(host):
    """Une erreur inattendue (base, bug) ne doit révéler ni SQL, ni paramètres, ni message interne.

    Constat initial : sur PostgreSQL, xcore renvoyait `str(e)` à l'appelant IPC, soit la requête
    complète et ses paramètres liés (tenant compris).
    """
    import httpx

    plugin = host.xcore.plugins._loader.get("xcore-doctype")._instance
    secret = "SELECT * FROM secret WHERE password='hunter2'"

    async def boom(*args, **kwargs):
        raise RuntimeError(secret)

    plugin.documents.get = boom  # l'instance du service est partagée par les routes et les actions
    t = host.token()

    res = await host.action("doc.get", {"doctype": "Customer", "name": "x"}, token=t)
    assert (res["status"], res["code"], res["msg"]) == ("error", "internal_error", "Erreur interne")
    assert "hunter2" not in repr(res)

    transport = httpx.ASGITransport(app=host.app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get(host.base + "/doc/Customer/x", headers=host.headers(t, "acme"))
    assert r.status_code == 500 and "hunter2" not in r.text


async def test_nan_in_raw_json_is_rejected_and_cannot_poison_the_tenant(host):
    """`json.loads` accepte NaN/Infinity. Stocké, un NaN faisait échouer la sérialisation de toute réponse
    contenant le document : un client pouvait rendre durablement `list` et `get` inutilisables pour son tenant."""
    t = host.token()
    for literal in ("NaN", "Infinity", "-Infinity"):
        r = await host.client.post(
            host.base + "/doc/Customer",
            headers={**host.headers(t, "acme"), "content-type": "application/json"},
            content='{"data": {"customer_name": "x", "credit_limit": %s}}' % literal,
        )
        assert r.status_code == 422, (literal, r.status_code)
    assert (await host.http("GET", "/list/Customer", token=t)).json()["total"] == 0
