"""Palier 70 % (H2/I) de bout en bout, par l'API : nom saisi, renommer, connexions, actions en lot, notifications.

Les notifications passent par `xauth.notify` : l'hôte de test pose un faux xauth qui l'enregistre (`app/auth/notifications.jsonl`), comme il imite l'annuaire.
"""

from __future__ import annotations

import json

import yaml

from tests.integration.conftest import boot_host
from tests.integration.test_collaboration_api import MAIN

PLACE = {"schema_version": 1, "name": "Place", "module": "shop", "allow_rename": True, "fields": [{"fieldname": "label", "fieldtype": "Text"}]}
VISIT = {
    "schema_version": 1, "name": "Visit", "module": "shop",
    "fields": [{"fieldname": "place", "fieldtype": "Link", "options": "Place"}, {"fieldname": "note", "fieldtype": "Text"}],
}
TAG = {"schema_version": 1, "name": "Tag", "module": "shop", "naming": "prompt", "fields": [{"fieldname": "color", "fieldtype": "Text"}]}
ORDER = {"schema_version": 1, "name": "Order", "module": "shop", "submittable": True, "fields": [{"fieldname": "title", "fieldtype": "Text"}]}
GRANTS = [f"shop:{n}:{a}" for n in ("place", "visit", "tag", "order") for a in ("read", "write", "delete")] + ["shop:order:submit", "shop:order:cancel"]
READER = ["shop:place:read", "shop:visit:read", "shop:order:read"]

MEMBERS = {"acme": [{"id": "11111111-aaaa", "email": "ada@acme.test"}, {"id": "22222222-bbbb", "email": "rita@acme.test"}, {"id": "33333333-cccc", "email": "carl@acme.test"}]}
ADA, RITA, CARL = "11111111-aaaa", "22222222-bbbb", "33333333-cccc"


def plugins():
    return {"shop": (MAIN, {f"doctypes/{d['name'].lower()}.yaml": yaml.safe_dump(d) for d in (PLACE, VISIT, TAG, ORDER)})}


def sent(h) -> list[dict]:
    path = h.root / "app" / "auth" / "notifications.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


async def make(h, token, doctype, data, **extra):
    r = await h.http("POST", f"/doc/{doctype}", token=token, json={"data": data, **extra})
    assert r.status_code == 201, r.text
    return r.json()


async def test_a_prompt_named_doctype_takes_its_name_from_the_request(tmp_path):
    async with boot_host(tmp_path, inline_plugins=plugins()) as h:
        ada = h.token(sub="ada", permissions=GRANTS)
        assert (await make(h, ada, "Tag", {"color": "red"}, name="urgent"))["name"] == "urgent"
        missing = await h.http("POST", "/doc/Tag", token=ada, json={"data": {}})
        assert missing.status_code == 422 and missing.json()["detail"]["details"][0]["field"] == "name"
        bad = await h.http("POST", "/doc/Tag", token=ada, json={"data": {}, "name": "a/b"})
        assert bad.status_code == 422 and bad.json()["detail"]["details"][0]["type"] == "invalid_name"
        dup = await h.http("POST", "/doc/Tag", token=ada, json={"data": {}, "name": "urgent"})
        assert dup.status_code == 409
        refused = await h.http("POST", "/doc/Place", token=ada, json={"data": {}, "name": "mine"})  # un nommage automatique n'accepte pas de nom
        assert refused.status_code == 422 and refused.json()["detail"]["code"] == "name_not_allowed"
        via_action = await h.action("doc.save", {"doctype": "Tag", "data": {}, "new_name": "from-action"}, token=ada)
        assert via_action["doc"]["name"] == "from-action"


async def test_renaming_follows_every_link_and_is_all_or_nothing(tmp_path):
    async with boot_host(tmp_path, inline_plugins=plugins()) as h:
        ada = h.token(sub="ada", permissions=GRANTS)
        place = (await make(h, ada, "Place", {"label": "Paris"}))["name"]
        other = (await make(h, ada, "Place", {"label": "Lyon"}))["name"]
        visit = (await make(h, ada, "Visit", {"place": place}))["name"]
        done = await h.http("POST", f"/doc/Place/{place}/rename", token=ada, json={"new_name": "Paris 8e"})
        assert done.status_code == 200 and done.json()["name"] == "Paris 8e"
        assert (await h.http("GET", f"/doc/Visit/{visit}", token=ada)).json()["data"]["place"] == "Paris 8e"
        assert (await h.http("GET", f"/doc/Place/{place}", token=ada)).status_code == 404
        taken = await h.http("POST", "/doc/Place/Paris 8e/rename", token=ada, json={"new_name": other})
        assert taken.status_code == 409
        assert (await h.http("GET", f"/doc/Visit/{visit}", token=ada)).json()["data"]["place"] == "Paris 8e"  # rien n'a bougé
        history = (await h.http("GET", "/history/Place/Paris 8e", token=ada)).json()["items"]
        assert history[0]["action"] == "rename" and history[0]["changes"] == {"name": {"old": place, "new": "Paris 8e"}}
        reader = h.token(sub="rita", permissions=READER)
        assert (await h.http("POST", f"/doc/Place/Paris 8e/rename", token=reader, json={"new_name": "x"})).status_code == 403
        via_action = await h.action("doc.rename", {"doctype": "Place", "name": "Paris 8e", "new_name": "Paris 9e"}, token=ada)
        assert via_action["doc"]["name"] == "Paris 9e"
        assert (await h.http("GET", f"/doc/Visit/{visit}", token=ada)).json()["data"]["place"] == "Paris 9e"


async def test_connections_and_bulk_actions(tmp_path):
    async with boot_host(tmp_path, inline_plugins=plugins()) as h:
        ada, rita = h.token(sub="ada", permissions=GRANTS), h.token(sub="rita", permissions=READER)
        place = (await make(h, ada, "Place", {"label": "Paris"}))["name"]
        visits = [(await make(h, ada, "Visit", {"place": place}))["name"] for _ in range(3)]
        conn = (await h.http("GET", f"/connections/Place/{place}", token=rita)).json()["items"]
        assert [(c["doctype"], c["field"], c["count"]) for c in conn] == [("Visit", "place", 3)]
        assert (await h.http("GET", "/connections/Place/absent", token=rita)).status_code == 404

        done = await h.http("POST", "/bulk/Visit", token=ada, json={"action": "update", "names": visits, "data": {"note": "à revoir"}})
        assert done.status_code == 200 and done.json()["done"] == 3
        assert {(await h.http("GET", f"/doc/Visit/{v}", token=ada)).json()["data"]["note"] for v in visits} == {"à revoir"}
        denied = await h.http("POST", "/bulk/Visit", token=rita, json={"action": "delete", "names": visits})
        assert denied.status_code == 403
        wrong = await h.http("POST", "/bulk/Visit", token=ada, json={"action": "explode", "names": visits})
        assert wrong.status_code == 422 and wrong.json()["detail"]["code"] == "bulk_action"
        # Un lien existe : le lieu ne se supprime pas, ses visites, si.
        out = (await h.http("POST", "/bulk/Place", token=ada, json={"action": "delete", "names": [place]})).json()
        assert out["failed"] == 1 and out["results"][0]["code"] == "document_in_use"
        out = (await h.http("POST", "/bulk/Visit", token=ada, json={"action": "delete", "names": visits})).json()
        assert out["done"] == 3
        via_action = await h.action("doc.bulk", {"doctype": "Place", "action": "delete", "names": [place]}, token=ada)
        assert via_action["done"] == 1
        order = (await make(h, ada, "Order", {"title": "O"}))["name"]
        submitted = await h.action("doc.bulk", {"doctype": "Order", "action": "submit", "names": [order]}, token=ada)
        assert submitted["done"] == 1


async def test_assigning_and_mentioning_notify_the_people_concerned(tmp_path):
    async with boot_host(tmp_path, inline_plugins=plugins(), auth_members=MEMBERS) as h:
        ada, rita = h.token(sub=ADA, permissions=GRANTS), h.token(sub=RITA, permissions=READER)
        place = (await make(h, ada, "Place", {"label": "Paris"}))["name"]
        r = await h.http("POST", f"/assignments/Place/{place}", token=ada, json={"assignee": "rita@acme.test", "description": "à vérifier"})
        assert r.status_code == 201
        [one] = sent(h)
        assert one["user_ids"] == [RITA] and one["tenant_id"] == "acme" and one["type"] == "ASSIGNMENT"
        assert one["title"] == f"ada@acme.test vous a assigné Place {place}" and one["message"] == "à vérifier" and one["link"] == f"#/app/Place/{place}"
        # Se l'assigner à soi-même ne prévient personne.
        await h.http("POST", f"/assignments/Place/{place}", token=ada, json={"assignee": ADA})
        assert len(sent(h)) == 1
        # Une mention : le membre cité, jamais l'auteur ni un inconnu ; le texte du commentaire ne voyage pas.
        c = await h.http("POST", f"/comments/Place/{place}", token=rita, json={"content": "voir avec @carl@acme.test et @ada@acme.test, pas @inconnu@ailleurs.test ni rita@acme.test"})
        assert c.status_code == 201
        notes = sent(h)
        assert len(notes) == 2 and notes[1]["user_ids"] == [ADA, CARL]  # l'ordre de l'annuaire ; ni Rita (l'auteur) ni l'inconnu
        assert notes[1]["type"] == "MENTION" and "voir avec" not in json.dumps(notes[1]) and notes[1]["title"].startswith("rita@acme.test vous a mentionné")
        # Un commentaire sans mention ne notifie personne.
        await h.http("POST", f"/comments/Place/{place}", token=rita, json={"content": "ok"})
        assert len(sent(h)) == 2


async def test_assigning_many_documents_sends_one_notification(tmp_path):
    async with boot_host(tmp_path, inline_plugins=plugins(), auth_members=MEMBERS) as h:
        ada = h.token(sub=ADA, permissions=GRANTS)
        places = [(await make(h, ada, "Place", {"label": f"P{i}"}))["name"] for i in range(3)]
        await h.http("POST", f"/assignments/Place/{places[0]}", token=ada, json={"assignee": RITA})  # déjà assigné à Rita : un refus pour lui seul
        before = len(sent(h))
        out = await h.http("POST", "/assign-many/Place", token=ada, json={"names": [*places, "absent"], "assignee": "rita@acme.test"})
        assert out.status_code == 200
        body = out.json()
        assert (body["done"], body["failed"]) == (2, 2)
        assert {r["name"]: r.get("code") for r in body["results"] if not r["ok"]} == {places[0]: "already_assigned", "absent": "document_not_found"}
        notes = sent(h)[before:]
        assert len(notes) == 1 and notes[0]["title"].endswith("vous a assigné 2 documents (Place)") and notes[0]["link"] == "#/tasks"
        unknown = await h.http("POST", "/assign-many/Place", token=ada, json={"names": places, "assignee": "inconnu@ailleurs.test"})
        assert unknown.status_code == 422 and unknown.json()["detail"]["code"] == "unknown_user"
        reader = h.token(sub=RITA, permissions=READER)
        assert (await h.http("POST", "/assign-many/Place", token=reader, json={"names": places, "assignee": CARL})).status_code == 403
        via_action = await h.action("doc.assign.many", {"doctype": "Place", "names": places[1:2], "assignee": CARL}, token=ada)
        assert via_action["done"] == 1


async def test_a_notification_that_cannot_be_sent_never_blocks_the_work(tmp_path):
    # xauth n'a pas ce plugin dans ses `allowed_callers` : l'annuaire ET la notification sont refusés ; l'assignation a lieu quand même.
    async with boot_host(tmp_path, inline_plugins=plugins(), auth_members=MEMBERS, auth_callers=["quelquun-d-autre"]) as h:
        ada = h.token(sub="ada", permissions=GRANTS)
        place = (await make(h, ada, "Place", {"label": "Paris"}))["name"]
        assert (await h.http("POST", f"/assignments/Place/{place}", token=ada, json={"assignee": "rita"})).status_code == 201
        assert (await h.http("POST", f"/comments/Place/{place}", token=ada, json={"content": "@rita@acme.test"})).status_code == 201
        assert sent(h) == []


async def test_notifications_can_be_switched_off(tmp_path):
    async with boot_host(tmp_path, inline_plugins=plugins(), auth_members=MEMBERS, doctype_config={"notifications": {"provider": "none"}}) as h:
        ada = h.token(sub=ADA, permissions=GRANTS)
        place = (await make(h, ada, "Place", {"label": "Paris"}))["name"]
        assert (await h.http("POST", f"/assignments/Place/{place}", token=ada, json={"assignee": RITA})).status_code == 201
        assert sent(h) == []


async def test_mention_suggestions_need_only_the_right_to_read_the_document(tmp_path):
    async with boot_host(tmp_path, inline_plugins=plugins(), auth_members=MEMBERS) as h:
        ada, rita = h.token(sub=ADA, permissions=GRANTS), h.token(sub=RITA, permissions=READER)
        place = (await make(h, ada, "Place", {"label": "Paris"}))["name"]
        # Un lecteur (il ne peut pas assigner) peut citer : les membres avec une adresse, sans l'auteur, la saisie filtre par adresse, sans casse.
        body = (await h.http("GET", f"/mentionable/Place/{place}", token=rita)).json()
        assert body["directory"] is True and [p["email"] for p in body["items"]] == ["ada@acme.test", "carl@acme.test"]
        assert [p["email"] for p in (await h.http("GET", f"/mentionable/Place/{place}?q=CAR", token=rita)).json()["items"]] == ["carl@acme.test"]
        assert [p["email"] for p in (await h.http("GET", f"/mentionable/Place/{place}?q=zzz", token=rita)).json()["items"]] == []
        # Le document doit être lisible : un autre organisation ou un document inconnu répondent « introuvable », sans rien révéler de l'annuaire.
        assert (await h.http("GET", "/mentionable/Place/absent", token=rita)).status_code == 404
        stranger = h.token(sub="gus", tenant="globex", permissions=GRANTS)
        assert (await h.http("GET", f"/mentionable/Place/{place}", token=stranger, tenant="globex")).status_code == 404
        blind = h.token(sub=CARL, permissions=["shop:visit:read"])
        # Sans droit de lire le type : un partage pourrait ouvrir CE document, donc « introuvable » comme ailleurs (permissions.py).
        assert (await h.http("GET", f"/mentionable/Place/{place}", token=blind)).status_code == 404
        via_action = await h.action("doc.mentionable", {"doctype": "Place", "name": place, "q": "ad"}, token=rita)
        assert [p["email"] for p in via_action["items"]] == ["ada@acme.test"]


async def test_without_a_directory_there_is_nobody_to_mention(tmp_path):
    async with boot_host(tmp_path, inline_plugins=plugins(), auth_callers=["quelquun-d-autre"], auth_members=MEMBERS) as h:
        ada = h.token(sub="ada", permissions=GRANTS)
        place = (await make(h, ada, "Place", {"label": "Paris"}))["name"]
        assert (await h.http("GET", f"/mentionable/Place/{place}", token=ada)).json() == {"items": [], "directory": False}


async def test_csv_import_by_http_and_ipc(tmp_path):
    async with boot_host(tmp_path, inline_plugins=plugins(), auth_members=MEMBERS) as h:
        ada, rita = h.token(sub=ADA, permissions=GRANTS), h.token(sub=RITA, permissions=READER)
        r = await h.http("POST", "/import/Place", token=ada, json={"content": "﻿label\nParis\nLyon\n"})
        assert r.status_code == 200 and r.json()["done"] == 2, r.text
        names = [x["name"] for x in r.json()["results"]]
        upd = await h.http("POST", "/import/Place", token=ada, json={"content": f"name;label\n{names[0]};Paris 1er\nabsent;x\n", "mode": "update"})
        assert [x["ok"] for x in upd.json()["results"]] == [True, False]
        assert (await h.http("GET", f"/doc/Place/{names[0]}", token=ada)).json()["data"]["label"] == "Paris 1er"
        assert (await h.http("POST", "/import/Place", token=rita, json={"content": "label\nX\n"})).status_code == 403
        bad = await h.http("POST", "/import/Place", token=ada, json={"content": "label;oops\nX;1\n"})
        assert bad.status_code == 422 and bad.json()["detail"]["code"] == "csv_columns"
        assert (await h.http("POST", "/import/Place", token=ada, json={"content": "label\nX\n", "extra": 1})).status_code == 422
        via_action = await h.action("doc.import", {"doctype": "Tag", "content": "name,color\nurgent,red\n"}, token=ada)
        assert via_action["results"] == [{"row": 2, "ok": True, "name": "urgent"}]
