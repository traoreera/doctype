"""xflow sur un vrai hôte, à côté du moteur : la couture workflow → flux de bout en bout.

Un DocType `Demande` a un circuit ; sa transition « Approuver » nomme le flux `suivi`. Le flux, déployé par l'API d'xflow, a une
étape qui appelle l'action `doc.save` du moteur pour créer un `Suivi`. On vérifie que :
- l'approbation lance le flux après le commit, et que l'étape écrit AU NOM de l'approbateur (ses droits, redemandés à xauth au
  moment où le flux tourne), dans la bonne organisation ;
- un approbateur dont xauth ne reconnaît plus les droits n'écrit rien : le flux échoue, l'approbation reste faite ;
- une action IPC d'xflow qui nomme une autre organisation que celle de l'appel est refusée.
"""

from __future__ import annotations

import asyncio

import yaml

from tests.integration.conftest import boot_host
from xcore_doctype import platform_plugins_dir

MAIN = '''
from pathlib import Path
from xcore.sdk import AutoDispatchMixin, TrustedBase

BASE = Path(__file__).resolve().parent.parent


class Plugin(AutoDispatchMixin, TrustedBase):
    async def on_load(self):
        self.get_service("doctype_registry").load_dir(self.ctx.name, BASE / "doctypes")

    async def on_unload(self):
        self.get_service("doctype_registry").unregister_owner(self.ctx.name)
'''

DEMANDE = {
    "schema_version": 1, "name": "Demande", "module": "achats", "submittable": True,
    "fields": [{"fieldname": "objet", "fieldtype": "Text", "required": True}],
    "workflow": {
        "states": [{"name": "Brouillon"}, {"name": "Approuvée", "docstatus": "submitted"}],
        "transitions": [{"action": "Approuver", "from": "Brouillon", "to": "Approuvée", "not_owner": True, "flow": "suivi"}],
    },
}
SUIVI = {
    "schema_version": 1, "name": "Suivi", "module": "achats",
    "fields": [{"fieldname": "demande", "fieldtype": "Text"}, {"fieldname": "etape", "fieldtype": "Text"}],
}
FLOW = {
    "name": "suivi",
    "trigger": {"type": "manual"},
    "steps": [{
        "id": "ouvrir", "type": "action", "plugin": "xcore-doctype", "action": "doc.save",
        "payload": {"doctype": "Suivi", "data": {"demande": "{{ trigger.name }}", "etape": "{{ trigger.to }}"}},
    }],
}
READ = ["achats:demande:read", "achats:demande:write", "achats:suivi:read"]
APPROVER = [*READ, "achats:demande:workflow_approuver", "achats:suivi:write"]


def plugins():
    files = {f"doctypes/{d['name'].lower()}.yaml": yaml.safe_dump(d) for d in (DEMANDE, SUIVI)}
    return {"achats": (MAIN, files)}


async def _host(tmp_path, grants):
    return boot_host(
        tmp_path, operator_doctypes=False, inline_plugins=plugins(),
        link_plugins={"xflow": platform_plugins_dir() / "xflow"},
        auth_callers=["xcore-doctype", "xflow"], auth_grants=grants,
    )


async def _suivis(h, token, attempts=150):
    for _ in range(attempts):
        page = (await h.http("GET", "/list/Suivi", token=token)).json()
        if page["total"]:
            return page["items"]
        await asyncio.sleep(0.05)
    return []


async def _approve(h):
    alice = h.token(sub="alice", permissions=[*READ, "xflow:workflows:write", "xflow:runs:read"])
    bob = h.token(sub="bob", permissions=APPROVER)
    deployed = await h.client.post(f"{h.prefix}/xflow/flows", json=FLOW, headers=h.headers(alice, "acme"))
    assert deployed.status_code == 200 and deployed.json().get("status") in ("ok", "success"), deployed.text
    doc = (await h.http("POST", "/doc/Demande", token=alice, json={"data": {"objet": "Écrans"}})).json()
    moved = await h.http("POST", f"/doc/Demande/{doc['name']}/transition", token=bob, json={"action": "Approuver", "version": doc["version"]})
    assert moved.status_code == 200, moved.text
    assert moved.json()["workflow_state"] == "Approuvée"
    return doc, alice, bob


async def test_an_approval_runs_its_flow_and_the_step_writes_in_the_approver_s_name(tmp_path):
    async with await _host(tmp_path, {"bob": APPROVER, "alice": READ}) as h:
        doc, _, bob = await _approve(h)
        [suivi] = await _suivis(h, bob)
        assert suivi["data"] == {"demande": doc["name"], "etape": "Approuvée"}
        assert suivi["owner"] == "bob"  # l'étape a écrit AU NOM de l'approbateur, pas d'xflow ni du déployeur


async def test_an_approver_xauth_no_longer_knows_writes_nothing_and_the_approval_stands(tmp_path):
    async with await _host(tmp_path, {"alice": READ}) as h:  # bob n'est plus reconnu par xauth (compte parti)
        doc, alice, _ = await _approve(h)
        assert await _suivis(h, alice, attempts=40) == []
        state = (await h.http("GET", f"/doc/Demande/{doc['name']}", token=alice)).json()["workflow_state"]
        assert state == "Approuvée"


async def test_an_xflow_action_naming_another_organisation_is_refused(tmp_path):
    async with await _host(tmp_path, {}) as h:
        token = h.token(sub="alice", permissions=["xflow:workflows:read"])
        refused = await h.action("list_workflows", {"tenant_id": "globex"}, token=token, tenant="acme", plugin="xflow")
        assert refused.get("status") == "error" and refused.get("code") == "tenant_mismatch", refused


async def test_config_json_announces_automations_only_when_xflow_is_loaded(tmp_path):
    async with await _host(tmp_path, {}) as h:
        assert (await h.client.get(f"{h.base}/desk/config.json")).json()["automations"] is True
    async with boot_host(tmp_path / "sans", operator_doctypes=False) as h:
        assert (await h.client.get(f"{h.base}/desk/config.json")).json()["automations"] is False


async def test_the_desk_s_editor_cycle_catalogue_deploy_edit_delete(tmp_path):
    """Ce que fait Paramètres › Automatisations : le catalogue des actions (complété par le registre VIVANT d'xcore, le fichier
    d'xflow étant absent), déployer, redéployer sous le même nom (modifier), supprimer. Le catalogue exige une identité."""
    async with await _host(tmp_path, {}) as h:
        writer = h.token(sub="alice", permissions=["xflow:workflows:read", "xflow:workflows:write"])
        url = f"{h.prefix}/xflow"
        assert (await h.client.get(f"{url}/registry")).status_code in (401, 403)
        names = {a["qualified_name"] for a in (await h.client.get(f"{url}/registry", headers=h.headers(writer, "acme"))).json()["actions"]}
        assert {"xcore-doctype.doc.save", "xcore-doctype.doc.submit"} <= names
        flow = {**FLOW, "description": "v1"}
        assert (await h.client.post(f"{url}/flows", json=flow, headers=h.headers(writer, "acme"))).json()["status"] in ("ok", "success")
        assert (await h.client.post(f"{url}/flows", json={**flow, "description": "v2"}, headers=h.headers(writer, "acme"))).json()["status"] in ("ok", "success")
        listed = (await h.client.get(f"{url}/flows", headers=h.headers(writer, "acme"))).json()["workflows"]
        assert [(f["name"], f["description"]) for f in listed] == [("suivi", "v2")]  # un seul flux, modifié
        stored = (await h.client.get(f"{url}/flows/suivi", headers=h.headers(writer, "acme"))).json()["workflow"]
        assert stored["deployed_by"] == "alice" and stored["steps"][0]["action"] == "doc.save"
        ran = await h.client.post(f"{url}/run/suivi", json={}, headers=h.headers(h.token(sub="alice", permissions=["xflow:runs:write"]), "acme"))
        assert ran.status_code == 200, ran.text
        for _ in range(100):  # un flux qui a TOURNÉ : sa suppression répondait 500 (ses exécutions pointent vers ses versions)
            runs = (await h.client.get(f"{url}/executions", headers=h.headers(h.token(sub="alice", permissions=["xflow:runs:read"]), "acme"))).json()["runs"]
            if runs and runs[0]["status"] not in ("pending", "running"):
                break
            await asyncio.sleep(0.05)
        deleted = await h.client.delete(f"{url}/flows/suivi", headers=h.headers(writer, "acme"))
        assert deleted.status_code == 200 and deleted.json()["status"] in ("ok", "success"), deleted.text
        assert (await h.client.get(f"{url}/flows", headers=h.headers(writer, "acme"))).json()["workflows"] == []
