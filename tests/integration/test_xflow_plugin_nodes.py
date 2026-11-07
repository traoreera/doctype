"""Un plugin apporte SES nœuds et SES déclencheurs à l'éditeur d'automatisations (section `xflow:` de son manifeste, flownodes.py).

Bout à bout sur un vrai hôte, avec le plugin de test `mailer` :
- le catalogue (`GET /flow-catalog`) sert le nœud « Envoyer un e-mail » et l'événement « E-mail reçu » déclarés dans son manifeste ;
- un flux déclenché par l'événement d'un AUTRE plugin (`mailer.received`) crée un client avec une valeur de cet événement, puis appelle
  l'action IPC du plugin (`mail.send`) avec le RÉSULTAT de l'étape précédente (le nom du client créé) : les trois choses à prouver.
"""

from __future__ import annotations

import asyncio
import json
import shutil

from tests.integration.conftest import boot_host
from tests.support import FIXTURES
from xcore_doctype import platform_plugins_dir

CUSTOMER = [f"sales:customer:{a}" for a in ("read", "write")]
XFLOW = ["xflow:workflows:read", "xflow:workflows:write", "xflow:runs:read", "xflow:runs:write"]
FLOW = {
    "name": "accueil_courrier",
    "trigger": {"type": "event", "event_name": "mailer.received"},
    "entry_step": "creer",
    "steps": [
        {"id": "creer", "type": "action", "plugin": "xcore-doctype", "action": "doc.save", "on_success": "repondre",
         "payload": {"doctype": "Customer", "data": {"customer_name": "{{ trigger.subject }}", "type": "Particulier"}}},
        {"id": "repondre", "type": "action", "plugin": "mailer", "action": "mail.send",
         "payload": {"to": "{{ trigger.sender }}", "subject": "Bienvenue", "body": "Votre fiche : {{ steps.creer.result.doc.name }}"}},
    ],
}


async def test_a_plugin_node_and_event_drive_a_flow_that_reuses_a_previous_result(tmp_path):
    mailer = tmp_path / "mailer"
    shutil.copytree(FIXTURES / "mailer", mailer, ignore=shutil.ignore_patterns("__pycache__", "outbox.jsonl"))
    async with boot_host(
        tmp_path / "h", link_plugins={"xflow": platform_plugins_dir() / "xflow", "mailer": mailer},
        auth_callers=["xcore-doctype", "xflow"], auth_grants={"alice": CUSTOMER + XFLOW},
    ) as h:
        alice = h.token(sub="alice", permissions=CUSTOMER + XFLOW)
        catalog = (await h.http("GET", "/flow-catalog", token=alice)).json()
        [node] = catalog["nodes"]
        assert (node["plugin"], node["action"], node["label"]) == ("mailer", "mail.send", "Envoyer un e-mail")
        assert [f["name"] for f in node["fields"]] == ["to", "subject", "body"] and node["outputs"][0]["path"] == "message_id"
        [event] = catalog["events"]
        assert (event["name"], [f["name"] for f in event["fields"]]) == ("mailer.received", ["sender", "subject"])

        deployed = await h.client.post(f"{h.prefix}/xflow/flows", json=FLOW, headers=h.headers(alice, "acme"))
        assert deployed.json().get("status") in ("ok", "success"), deployed.text
        received = await h.client.post(f"{h.prefix}/mailer/receive", json={"sender": "kone@example.com", "subject": "Koné SARL"},
                                       headers=h.headers(alice, "acme"))
        assert received.status_code == 200, received.text

        outbox = mailer / "outbox.jsonl"
        for _ in range(200):
            if outbox.exists() and outbox.read_text().strip():
                break
            await asyncio.sleep(0.05)
        assert outbox.exists(), "l'étape du plugin n'a rien envoyé"
        [mail] = [json.loads(line) for line in outbox.read_text().splitlines()]
        customers = (await h.http("GET", "/list/Customer", token=alice)).json()["items"]
        [customer] = [c for c in customers if c["data"]["customer_name"] == "Koné SARL"]  # la valeur de l'ÉVÉNEMENT du plugin
        assert customer["owner"] == "alice"  # un run d'événement agit au nom de qui a déployé le flux
        assert mail["to"] == "kone@example.com" and mail["body"] == f"Votre fiche : {customer['name']}"  # le RÉSULTAT de l'étape 1


async def test_a_faulty_section_fails_the_plugin_s_load():
    from xcore_doctype.flownodes import FlowCatalog
    from xcore_doctype.errors import InvalidDefinition
    import pytest

    catalog = FlowCatalog()
    for bad, words in (
        ({"events": [{"name": "autre.recu", "label": "x"}]}, "doit commencer par « mailer. »"),
        ({"nodes": [{"action": "a", "label": "x"}, {"action": "a", "label": "y"}]}, "deux fois"),
        ({"nodes": [{"action": "a", "label": "x", "fields": [{"name": "c", "label": "C", "type": "select"}]}]}, "sans options"),
        ({"nodes": [{"action": "a", "label": "x", "inconnu": 1}]}, "inconnu"),
    ):
        with pytest.raises(InvalidDefinition, match=words):
            catalog.declare("mailer", bad)
    assert catalog.as_dict() == {"nodes": [], "events": []}


ORG_NODE = {
    "plugin": "mailer", "action": "mail.log", "label": "Noter dans le journal", "icon": "book",
    "fields": [{"name": "note", "label": "Note", "type": "longtext", "required": True}],
    "outputs": [{"path": "entry", "label": "Numéro de la note"}],
}


async def test_an_organisation_describes_a_node_in_the_desk_and_a_flow_runs_it(tmp_path):
    """Un nœud décrit À L'ÉCRAN (flow_nodes_service.py) : rangé pour CETTE organisation seulement, réservé à qui peut déployer, effacé
    par un plugin qui décrit la même action, et exécuté par xflow comme n'importe quelle action du plugin."""
    mailer = tmp_path / "mailer"
    shutil.copytree(FIXTURES / "mailer", mailer, ignore=shutil.ignore_patterns("__pycache__", "outbox.jsonl"))
    async with boot_host(
        tmp_path / "h", link_plugins={"xflow": platform_plugins_dir() / "xflow", "mailer": mailer},
        auth_callers=["xcore-doctype", "xflow"], auth_grants={"alice": CUSTOMER + XFLOW},
    ) as h:
        alice = h.token(sub="alice", permissions=CUSTOMER + XFLOW)
        bob = h.token(sub="bob", permissions=CUSTOMER)  # lit, ne déploie pas
        other = h.token(sub="carol", tenant="globex", permissions=CUSTOMER + XFLOW)

        refused = await h.http("PUT", "/flow-nodes", token=bob, json={"node": ORG_NODE})
        assert refused.status_code == 403 and refused.json()["detail"]["code"] == "flow_node_reserved"
        bad = await h.http("PUT", "/flow-nodes", token=alice, json={"node": {**ORG_NODE, "fields": [{"name": "Note", "label": "x"}]}})
        assert bad.status_code == 422 and bad.json()["detail"]["code"] == "invalid_flow_node"

        saved = await h.http("PUT", "/flow-nodes", token=alice, json={"node": ORG_NODE})
        assert saved.status_code == 200, saved.text
        listed = (await h.http("GET", "/flow-nodes", token=bob)).json()
        assert listed["can_edit"] is False and [n["action"] for n in listed["nodes"]] == ["mail.log"]
        assert listed["nodes"][0]["shadowed"] is False
        catalog = (await h.http("GET", "/flow-catalog", token=bob)).json()
        assert [(n["action"], n["source"]) for n in catalog["nodes"]] == [("mail.send", "plugin"), ("mail.log", "organisation")]
        # Une autre organisation ne le voit pas : il est rangé par tenant.
        assert [n["action"] for n in (await h.http("GET", "/flow-catalog", token=other, tenant="globex")).json()["nodes"]] == ["mail.send"]

        # Le décrire pour une action que le plugin décrit déjà : la description du plugin l'emporte dans la palette.
        await h.http("PUT", "/flow-nodes", token=alice, json={"node": {**ORG_NODE, "action": "mail.send", "label": "Autre"}})
        assert [n["source"] for n in (await h.http("GET", "/flow-catalog", token=alice)).json()["nodes"]] == ["plugin", "organisation"]
        shadow = {n["action"]: n["shadowed"] for n in (await h.http("GET", "/flow-nodes", token=alice)).json()["nodes"]}
        assert shadow == {"mail.log": False, "mail.send": True}
        assert (await h.http("DELETE", "/flow-nodes/mailer/mail.send", token=alice)).status_code == 200
        assert (await h.http("DELETE", "/flow-nodes/mailer/mail.send", token=alice)).status_code == 404

        # Un flux qui appelle le nœud de l'organisation : xflow l'exécute, et l'étape reçoit la valeur saisie.
        flow = {"name": "journal", "trigger": {"type": "manual"},
                "steps": [{"id": "noter", "type": "action", "plugin": "mailer", "action": "mail.log", "payload": {"note": "Bonjour"}}]}
        deployed = await h.client.post(f"{h.prefix}/xflow/flows", json=flow, headers=h.headers(alice, "acme"))
        assert deployed.json().get("status") in ("ok", "success"), deployed.text
        started = await h.client.post(f"{h.prefix}/xflow/run/journal", json={}, headers=h.headers(alice, "acme"))
        assert started.status_code < 300, started.text
        outbox = mailer / "outbox.jsonl"
        for _ in range(200):
            if outbox.exists() and outbox.read_text().strip():
                break
            await asyncio.sleep(0.05)
        assert [json.loads(line).get("note") for line in outbox.read_text().splitlines()] == ["Bonjour"]
