"""Hooks de bout en bout sur un vrai Xcore : un plugin de confiance (`sales`) et un plugin SANDBOXÉ (`sales-rules`,
sous-processus xcore). Critères 6 et 8 de process/doctype.md §14 bis.8, plus les propriétés de sécurité des hooks.

DocType `Invoice` (voir conftest.INVOICE) :
    validate: sales.check_amount            (confiance : refuse un montant négatif)
    before_save: sales.stamp, action:sales-rules.check_credit   (confiance puis sandboxé : refuse > 10000)
    after_save: sales.notify                (confiance : notification)
"""

from __future__ import annotations

import time

import pytest

from tests.integration.conftest import boot_host, probe

pytestmark = pytest.mark.asyncio

INVOICE = {"customer": "ACME", "amount": 50}


@pytest.fixture
async def hh(tmp_path):
    async with boot_host(tmp_path, hook_plugins=True) as h:
        yield h


def sales(h):
    return h.xcore.plugins._loader.get("sales")._instance


async def post(h, data, token=None, doctype="Invoice", tenant="acme"):
    return await h.http("POST", f"/doc/{doctype}", token=token or h.token(tenant=tenant), tenant=tenant, json={"data": data})


async def stored(h, doctype="Invoice"):
    return (await h.http("GET", f"/list/{doctype}", token=h.token())).json()


# ── Le pipeline complet ─────────────────────────────────────────────────────────────────────────


async def test_full_pipeline_with_a_real_sandboxed_plugin(hh):
    r = await post(hh, INVOICE)
    assert r.status_code == 201, r.text
    doc = r.json()
    # before_save de confiance (stamp) ET hook sandboxé (note) ont modifié le document, qui a été revalidé.
    assert doc["data"] == {"customer": "ACME", "amount": 50, "note": "checked-by-sandbox", "stamp": "acme:alice"}
    assert sales(hh).calls == [("validate", doc["name"]), ("after_save", doc["name"], 50)]
    assert (await stored(hh))["total"] == 1


async def test_update_runs_the_hooks_again_with_the_old_value(hh):
    doc = (await post(hh, INVOICE)).json()
    put = await hh.http("PUT", f"/doc/Invoice/{doc['name']}", token=hh.token(), json={"version": 1, "data": {"customer": "ACME", "amount": 70}})
    assert put.status_code == 200 and put.json()["version"] == 2 and put.json()["data"]["note"] == "checked-by-sandbox"
    assert [c[0] for c in sales(hh).calls] == ["validate", "after_save", "validate", "after_save"]


async def test_same_pipeline_over_xcore_actions(hh):
    t = hh.token()
    res = await hh.action("doc.save", {"doctype": "Invoice", "data": {"customer": "ACME", "amount": -5}}, token=t)
    assert (res["status"], res["code"], res["msg"]) == ("error", "hook_rejected", "Montant négatif")
    ok = await hh.action("doc.save", {"doctype": "Invoice", "data": INVOICE}, token=t)
    assert ok["doc"]["data"]["note"] == "checked-by-sandbox"


# ── Critère 6 ───────────────────────────────────────────────────────────────────────────────────


async def test_c6_validate_that_raises_cancels_the_save(hh):
    r = await post(hh, {"customer": "ACME", "amount": -1})
    assert r.status_code == 422
    assert r.json()["detail"] == {"code": "hook_rejected", "msg": "Montant négatif", "details": {"field": "amount"}}
    assert (await stored(hh))["total"] == 0
    assert all(c[0] != "after_save" for c in sales(hh).calls)  # pas de notification pour une sauvegarde annulée


async def test_c6_after_save_that_raises_does_not_undo_the_save(hh):
    r = await post(hh, {"customer": "notify-fails", "amount": 1})
    assert r.status_code == 201
    assert (await stored(hh))["total"] == 1


async def test_a_crashing_trusted_hook_aborts_the_save_without_leaking(hh):
    r = await post(hh, {"customer": "boom", "amount": 1})
    assert r.status_code == 500 and r.json()["detail"]["code"] == "hook_error" and "hunter2" not in r.text
    assert (await stored(hh))["total"] == 0


# ── Hooks sandboxés (D4, D21) ───────────────────────────────────────────────────────────────────


async def test_sandboxed_rejection_is_422_with_the_plugins_message(hh):
    r = await post(hh, {"customer": "ACME", "amount": 20_000})
    assert r.status_code == 422
    assert r.json()["detail"] == {"code": "hook_rejected", "msg": "Crédit dépassé",
                                  "details": {"hook": "action:sales-rules.check_credit", "code": "credit_exceeded"}}
    assert (await stored(hh))["total"] == 0


async def test_sandboxed_hook_receives_the_requests_user_and_tenant_not_ambient_state(tmp_path):
    """Une route HTTP ne pose ni tenant ni principal dans les ContextVar : ils sont passés explicitement."""
    async with boot_host(tmp_path, hook_plugins=True, extra_doctypes=(probe("tenant"),)) as h:
        token = h.token(sub="carol", tenant="globex", roles=["Sales Manager"])
        r = await post(h, {"blob": "x"}, token=token, doctype="Probe Tenant", tenant="globex")
        assert r.status_code == 201 and r.json()["data"]["note"] == "user=carol roles=Sales Manager"
        audit = (await h.ipc("GET", "/audit", params={"plugin": "sales-rules"})).json()["calls"]
        mine = [c for c in audit if c.get("action") == "tenant"]
        assert mine and all(c["tenant_id"] == "globex" and c["caller"] == "xcore-doctype" for c in mine), audit


async def test_sandboxed_output_cannot_inject_system_fields(tmp_path):
    async with boot_host(tmp_path, hook_plugins=True, extra_doctypes=(probe("evil"),)) as h:
        r = await post(h, {"blob": "x"}, doctype="Probe Evil")
        assert r.status_code == 502 and r.json()["detail"]["code"] == "hook_invalid_result"
        assert (await stored(h, "Probe Evil"))["total"] == 0


async def test_sandboxed_crash_is_503_and_does_not_expose_its_message(tmp_path):
    async with boot_host(tmp_path, hook_plugins=True, extra_doctypes=(probe("crash"),)) as h:
        r = await post(h, {"blob": "x"}, doctype="Probe Crash")
        assert r.status_code == 503 and r.json()["detail"]["code"] == "hook_unavailable"
        assert "hunter2" not in r.text and "boom" not in r.text
        # Le plugin sandboxé survit à son propre crash de handler.
        assert (await post(h, INVOICE)).status_code == 201


async def test_missing_sandboxed_plugin_is_503(tmp_path):
    ghost = {**probe("check"), "name": "Ghost", "hooks": {"before_save": ["action:no-such-plugin.check"]}}
    async with boot_host(tmp_path, hook_plugins=True, extra_doctypes=(ghost,)) as h:
        r = await post(h, {"blob": "x"}, doctype="Ghost")
        assert r.status_code == 503 and r.json()["detail"]["code"] == "hook_unavailable"


async def test_slow_sandboxed_hook_times_out_within_our_budget(tmp_path):
    async with boot_host(tmp_path, hook_plugins=True, extra_doctypes=(probe("slow"),), doctype_config={"hook_timeout_seconds": 1}) as h:
        start = time.monotonic()
        r = await post(h, {"blob": "x"}, doctype="Probe Slow")
        assert r.status_code == 504 and r.json()["detail"]["code"] == "hook_timeout"
        assert time.monotonic() - start < 2.5  # 1 s de budget, pas les 3 s du timeout du plugin ni les 30 s de son sommeil
        assert (await stored(h, "Probe Slow"))["total"] == 0


# ── Critère 8 : document trop gros ──────────────────────────────────────────────────────────────


async def test_c8_oversized_document_fails_immediately_and_leaves_the_worker_alone(tmp_path):
    async with boot_host(tmp_path, hook_plugins=True) as h:
        start = time.monotonic()
        r = await post(h, {"customer": "ACME", "note": "x" * 70_000})
        elapsed = time.monotonic() - start
        assert r.status_code == 413 and r.json()["detail"]["code"] == "hook_payload_too_large"
        assert elapsed < 1.0, f"erreur attendue tout de suite, pas après le timeout ({elapsed:.1f}s)"
        # Aucun appel n'est parti : le plugin sandboxé n'a pas été recyclé et répond tout de suite.
        assert (await post(h, INVOICE)).status_code == 201


async def test_c8_why_the_cap_exists_xcore_drops_lines_over_64kib_without_answering(hh):
    """Caractérise xcore : une ligne IPC > 64 Kio est ignorée par le worker, qui ne répond pas du tout.

    Sans notre borne, l'appelant attendrait le timeout du plugin (10 s ici) puis xcore recyclerait le sous-processus.
    """
    import asyncio

    call = hh.xcore.plugins.call("sales-rules", "size", {"blob": "x" * 70_000}, caller="xcore-doctype", tenant_id="acme")
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(call, timeout=3)  # un document de 5 Ko répond en quelques ms
    quick = await hh.xcore.plugins.call("sales-rules", "size", {"blob": "x" * 5_000}, caller="xcore-doctype", tenant_id="acme")
    assert quick["status"] == "ok"


async def test_c8_documents_up_to_the_cap_go_through_the_real_worker(hh):
    r = await post(hh, {"customer": "ACME", "note": "x" * 59_000})
    assert r.status_code == 201 and r.json()["data"]["note"] == "checked-by-sandbox"
    over = await post(hh, {"customer": "ACME", "note": "x" * 59_900})
    assert over.status_code == 413


async def test_c8_the_configurable_ceiling_is_itself_safe_for_the_real_worker(tmp_path):
    """Le maximum acceptable pour `hook_max_payload_bytes` laisse de la place à l'enveloppe IPC."""
    async with boot_host(tmp_path, hook_plugins=True, doctype_config={"hook_max_payload_bytes": 63_488}) as h:
        r = await post(h, {"customer": "ACME", "note": "x" * 63_000})
        assert r.status_code == 201, r.text


async def test_c8_oversize_check_counts_escaped_characters(hh):
    r = await post(hh, {"customer": "ACME", "note": "é" * 12_000})  # 24 Ko en UTF-8 mais ~72 Ko sur la ligne IPC
    assert r.status_code == 413


# ── Échec fermé quand un hook disparaît (D16) ───────────────────────────────────────────────────


async def test_reloading_xcore_doctype_makes_hooked_doctypes_refuse_writes_until_the_plugin_reregisters(hh):
    assert (await post(hh, INVOICE)).status_code == 201

    assert (await hh.ipc("POST", "/xcore-doctype/reload")).status_code == 200
    r = await post(hh, INVOICE)
    assert r.status_code == 503 and r.json()["detail"]["code"] == "hook_unavailable"
    assert "sales." not in r.text  # noms internes dans le journal, pas dans la réponse
    # Les lectures et les DocTypes sans hook ne sont pas affectés.
    assert (await stored(hh))["total"] == 1
    assert (await post(hh, {"customer_name": "Sans hook"}, doctype="Customer")).status_code == 201

    # Le plugin dépendant se réenregistre auprès du NOUVEAU registre.
    assert (await hh.ipc("POST", "/sales/reload")).status_code == 200
    assert (await post(hh, INVOICE)).status_code == 201


async def test_unloading_the_hook_plugin_refuses_writes_and_keeps_data_intact(hh):
    doc = (await post(hh, INVOICE)).json()
    assert (await hh.ipc("DELETE", "/sales/unload")).status_code == 200
    r = await hh.http("PUT", f"/doc/Invoice/{doc['name']}", token=hh.token(), json={"version": 1, "data": {"customer": "ACME", "amount": 1}})
    assert r.status_code == 503
    assert (await hh.http("GET", f"/doc/Invoice/{doc['name']}", token=hh.token())).json() == doc


# ── Événement et exposition ─────────────────────────────────────────────────────────────────────


async def test_eventbus_receives_a_notification_without_the_documents_content(hh):
    received = []

    async def on_saved(event):
        received.append((event.name, dict(event.data)))

    hh.xcore.events.subscribe("doctype.Invoice.after_save", on_saved)
    doc = (await post(hh, {"customer": "TOP SECRET CLIENT", "amount": 9})).json()
    assert received == [("doctype.Invoice.after_save",
                         {"doctype": "Invoice", "name": doc["name"], "tenant_id": "acme", "version": 1, "user": "alice", "created": True})]
    assert "SECRET" not in repr(received)


async def test_meta_never_exposes_hook_references(hh):
    meta = (await hh.http("GET", "/meta/Invoice", token=hh.token())).json()
    assert "hooks" not in meta and "permissions" not in meta
    assert "sales.check_amount" not in repr(meta) and "sales-rules" not in repr(meta)


# ── Déclaration des grants : source unique = le YAML des DocTypes ───────────────────────────────

AUTH_RESERVED = {"admin", "rbac", "auth", "tenants", "user", "license", "plugins"}


def xauth_accepts(plugin: str, name: str) -> bool:
    """Recopié d'xauth (services/rbac/grants.py, `_is_valid_grant`) : ce que sa réconciliation conserve."""
    import re

    return (
        plugin not in AUTH_RESERVED
        and bool(name)
        and bool(re.match(r"^[a-z0-9_]+:[a-z0-9_]+(:[a-z0-9_]+)*$", name))
        and name.split(":", 1)[0] == plugin
    )


async def test_the_owner_plugin_declares_the_grants_derived_from_its_doctypes(hh):
    """`sales` ne retape rien : il passe à `rbac.declare` la liste de `doctype_grants` (Country + Customer + Invoice)."""
    (event,) = hh.rbac_declared
    assert event["plugin"] == "sales"
    names = [g["name"] for g in event["grants"]]
    assert names == [
        "sales:country:delete", "sales:country:read", "sales:country:write",
        "sales:customer:delete", "sales:customer:own", "sales:customer:read", "sales:customer:write",
        "sales:invoice:delete", "sales:invoice:read", "sales:invoice:write",
    ]
    assert all(xauth_accepts("sales", n) for n in names)
    # Un grant délégable porte en plus `default_roles` : auth le verse aussi au rôle du propriétaire de l'organisation, sans
    # quoi celui qui crée son organisation n'aurait aucun droit sur ses propres documents (revue d'auth du 26 oct.).
    base = {"name", "description", "group", "tenant_grantable"}
    assert all(set(g) == (base | {"default_roles"} if g["tenant_grantable"] else base) for g in event["grants"])
    assert all(g["default_roles"] == ["tenant_admin"] for g in event["grants"] if g["tenant_grantable"])
    by_name = {g["name"]: g for g in event["grants"]}
    assert by_name["sales:customer:read"]["description"] == "Consulter Client"  # libellé du DocType (label: Client)
    assert by_name["sales:invoice:write"]["group"] == "sales-invoice"


async def test_the_declared_grants_are_exactly_the_ones_the_engine_checks(hh):
    """Cohérence bout en bout : un principal qui détient les grants déclarés pour Invoice obtient les droits correspondants."""
    declared = {g["name"] for g in hh.rbac_declared[0]["grants"] if g["name"].startswith("sales:invoice:")}
    reader = hh.token(sub="r", permissions=[n for n in declared if n.endswith(":read")])
    assert (await hh.http("GET", "/list/Invoice", token=reader)).status_code == 200
    assert (await post(hh, INVOICE, token=reader)).status_code == 403  # lecture seule
    writer_reader = hh.token(sub="w", permissions=sorted(declared - {"sales:invoice:delete"}))
    assert (await post(hh, INVOICE, token=writer_reader)).status_code == 201


async def test_reloading_the_owner_plugin_redeclares_its_grants(hh):
    assert (await hh.ipc("POST", "/sales/reload")).status_code == 200
    assert len(hh.rbac_declared) == 2 and hh.rbac_declared[0] == hh.rbac_declared[1]


async def test_the_catalog_follows_a_reload_of_xcore_doctype(hh):
    """Après un reload d'xcore-doctype, le propriétaire doit se recharger pour obtenir le NOUVEAU catalogue."""
    assert (await hh.ipc("POST", "/xcore-doctype/reload")).status_code == 200
    assert (await hh.ipc("POST", "/sales/reload")).status_code == 200
    assert len(hh.rbac_declared) == 2 and hh.rbac_declared[1]["grants"] == hh.rbac_declared[0]["grants"]
