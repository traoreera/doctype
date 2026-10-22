"""Règles de notification (C4) de bout en bout : un administrateur écrit une règle par l'API, une soumission sonne la cloche par `xauth.notify`.

Le plugin `system` livré (ses trois YAML) est posé tel quel ; l'hôte de test imite xauth (`notifications.jsonl`) et son annuaire.
"""

from __future__ import annotations

import asyncio

from tests.integration.conftest import boot_host
from tests.integration.test_h2_api import ADA, GRANTS, MEMBERS, RITA, make, plugins, sent
from xcore_doctype import system_plugin_dir

RULES = ["system:notification_rule:read", "system:notification_rule:write", "system:notification_rule:delete"]


def with_system():
    out = plugins()
    folder = system_plugin_dir()
    out["system"] = (
        (folder / "src" / "main.py").read_text(encoding="utf-8"),
        {f"{d}/{p.name}": p.read_text(encoding="utf-8") for d in ("doctypes", "workspaces") for p in sorted((folder / d).glob("*.yaml"))},
    )
    return out


async def rang(h, count: int) -> list[dict]:
    """L'évaluation part en tâche de fond après le commit : on attend qu'elle ait écrit (2 s au plus)."""
    for _ in range(100):
        notes = [n for n in sent(h) if n.get("type") == "RULE"]
        if len(notes) >= count:
            return notes
        await asyncio.sleep(0.02)
    return [n for n in sent(h) if n.get("type") == "RULE"]


async def test_a_submit_rule_written_through_the_api_rings_the_named_member(tmp_path):
    async with boot_host(tmp_path, inline_plugins=with_system(), auth_members=MEMBERS) as h:
        admin = h.token(sub=ADA, permissions=GRANTS + RULES)
        rule = {"rule_name": "Commande soumise", "document_type": "Order", "event": "Soumission", "subject": "{{ doc.title }} soumise par {{ actor }}",
                "recipients": [{"email": "rita@acme.test"}]}
        r = await h.http("POST", "/doc/Notification Rule", token=admin, json={"data": rule})
        assert r.status_code == 201, r.text
        order = await make(h, admin, "Order", {"title": "CMD-7"})
        assert await rang(h, 1) == []  # une création ne déclenche pas une règle de soumission
        r = await h.http("POST", f"/doc/Order/{order['name']}/submit", token=admin, json={"version": order["version"]})
        assert r.status_code == 200, r.text
        [one] = await rang(h, 1)
        assert one["user_ids"] == [RITA] and one["tenant_id"] == "acme"
        assert one["title"] == "CMD-7 soumise par ada@acme.test" and one["link"] == f"#/app/Order/{order['name']}"


async def test_an_incoherent_rule_is_refused_with_its_fields_and_a_rule_needs_its_grant(tmp_path):
    async with boot_host(tmp_path, inline_plugins=with_system(), auth_members=MEMBERS) as h:
        admin = h.token(sub=ADA, permissions=GRANTS + RULES)
        r = await h.http("POST", "/doc/Notification Rule", token=admin, json={"data": {
            "rule_name": "x", "document_type": "Place", "event": "Soumission", "subject": "{{ doc.nope }}", "notify_owner": True}})
        assert r.status_code == 422 and r.json()["detail"]["code"] == "invalid_rule", r.text
        assert {d["field"] for d in r.json()["detail"]["details"]} == {"event", "subject"}
        member = h.token(sub=RITA, permissions=GRANTS)  # tous les droits du métier, aucun sur les règles
        r = await h.http("POST", "/doc/Notification Rule", token=member, json={"data": {
            "rule_name": "x", "document_type": "Place", "event": "Création", "subject": "s", "notify_owner": True}})
        assert r.status_code == 403


async def test_the_administration_workspace_shows_the_rules_to_who_may_read_them_only(tmp_path):
    async with boot_host(tmp_path, inline_plugins=with_system(), auth_members=MEMBERS) as h:
        admin, member = h.token(sub=ADA, permissions=GRANTS + RULES), h.token(sub=RITA, permissions=GRANTS)
        mine = {w["name"]: w for w in (await h.http("GET", "/workspaces", token=admin)).json()}
        assert mine["system"]["label"] == "Administration"
        assert [b["doctype"] for b in mine["system"]["blocks"] if b["type"] == "shortcut"] == ["Notification Rule"]
        assert "system" not in {w["name"] for w in (await h.http("GET", "/workspaces", token=member)).json()}
