"""Collaboration de bout en bout : commentaires, assignations, pièces jointes d'un document ; tout se juge sur le document."""

from __future__ import annotations

import yaml

from tests.integration.conftest import boot_host

MAIN = '''
from pathlib import Path
from xcore.sdk import TrustedBase, error

BASE = Path(__file__).resolve().parent.parent


class Plugin(TrustedBase):
    async def on_load(self):
        self.get_service("doctype_registry").load_dir(self.ctx.name, BASE / "doctypes")

    async def handle(self, action, payload):
        return error("no actions", "unknown_action")
'''

ORDER = {"schema_version": 1, "name": "Order", "module": "shop", "fields": [{"fieldname": "title", "fieldtype": "Text"}]}
# Un type où l'on ne lit que SES documents (`if_owner`) : le périmètre de lecture du document gouverne tout ce qui l'entoure.
NOTE = {
    "schema_version": 1, "name": "Note", "module": "shop", "fields": [{"fieldname": "title", "fieldtype": "Text"}],
    "permissions": {"read": [{"grant": "shop:note:read", "if_owner": True}], "write": ["shop:note:write"], "create": ["shop:note:write"]},
}
TICKET = {"schema_version": 1, "name": "Ticket", "module": "shop", "submittable": True, "fields": [{"fieldname": "title", "fieldtype": "Text"}]}
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
PDF = b"%PDF-1.4 hello"


def plugins():
    docs = {f"doctypes/{d['name'].lower()}.yaml": yaml.safe_dump(d) for d in (ORDER, NOTE, TICKET)}
    return {"shop": (MAIN, docs)}


WRITER = ["shop:order:read", "shop:order:write", "shop:note:read", "shop:note:write", "shop:ticket:read", "shop:ticket:write", "shop:ticket:submit"]
READER = ["shop:order:read", "shop:note:read", "shop:ticket:read"]


async def order(h, token, title="o1"):
    r = await h.http("POST", "/doc/Order", token=token, json={"data": {"title": title}})
    assert r.status_code == 201, r.text
    return r.json()["name"]


async def test_anybody_who_can_read_a_document_can_comment_and_only_authors_edit(tmp_path):
    async with boot_host(tmp_path, inline_plugins=plugins()) as h:
        ada, rita, bob = h.token(sub="ada", permissions=WRITER), h.token(sub="rita", permissions=READER), h.token(sub="bob", permissions=READER)
        name = await order(h, ada)
        c = await h.http("POST", f"/comments/Order/{name}", token=rita, json={"content": "  Bonjour  "})
        assert c.status_code == 201 and c.json()["content"] == "Bonjour" and c.json()["owner"] == "rita" and c.json()["edited"] is False
        thread = (await h.http("GET", f"/collab/Order/{name}", token=bob)).json()
        assert [x["content"] for x in thread["comments"]] == ["Bonjour"] and thread["can_assign"] is False and thread["me"] == "bob"
        cid = c.json()["id"]
        # Seul l'auteur modifie ; un autre lecteur ne peut ni modifier ni retirer ; qui écrit le document modère.
        assert (await h.http("PUT", f"/comments/{cid}", token=bob, json={"content": "pirate"})).status_code == 403
        assert (await h.http("DELETE", f"/comments/{cid}", token=bob)).status_code == 403
        edited = await h.http("PUT", f"/comments/{cid}", token=rita, json={"content": "Salut"})
        assert edited.status_code == 200 and edited.json()["edited"] is True
        assert (await h.http("GET", f"/collab/Order/{name}", token=ada)).json()["comments"][0]["content"] == "Salut"
        assert (await h.http("DELETE", f"/comments/{cid}", token=ada)).status_code == 204  # le rédacteur du document modère
        assert (await h.http("GET", f"/collab/Order/{name}", token=ada)).json()["comments"] == []


async def test_comment_validation(tmp_path):
    async with boot_host(tmp_path, inline_plugins=plugins()) as h:
        ada = h.token(sub="ada", permissions=WRITER)
        name = await order(h, ada)
        for bad, fragment in (("", "obligatoire"), ("   ", "obligatoire"), ("x" * 4001, "4000"), ("a\x00b", "contrôle")):
            r = await h.http("POST", f"/comments/Order/{name}", token=ada, json={"content": bad})
            assert r.status_code == 422 and fragment in r.text, (bad[:5], r.text)
        assert (await h.http("POST", f"/comments/Order/{name}", token=ada, json={"content": "a\nb\tc"})).status_code == 201  # sauts de ligne et tabulation : permis
        assert (await h.http("POST", f"/comments/Order/{name}", token=ada, json={"content": "x", "extra": 1})).status_code == 422
        assert (await h.http("PUT", "/comments/not-an-id", token=ada, json={"content": "x"})).status_code == 404


async def test_everything_is_judged_on_the_document_so_one_that_is_out_of_scope_does_not_exist(tmp_path):
    async with boot_host(tmp_path, inline_plugins=plugins()) as h:
        ada, bob = h.token(sub="ada", permissions=WRITER), h.token(sub="bob", permissions=["shop:note:read"])
        made = await h.http("POST", "/doc/Note", token=ada, json={"data": {"title": "privé"}})
        name = made.json()["name"]
        await h.http("POST", f"/comments/Note/{name}", token=ada, json={"content": "secret"})
        for method, path, kw in (
            ("GET", f"/collab/Note/{name}", {}),
            ("POST", f"/comments/Note/{name}", {"json": {"content": "x"}}),
            ("POST", f"/assignments/Note/{name}", {"json": {"assignee": "bob"}}),
            ("POST", f"/attachments/Note/{name}", {"files": {"file": ("a.pdf", PDF, "application/pdf")}}),
        ):
            assert (await h.http(method, path, token=bob, **kw)).status_code == 404, (method, path)
        # Même un commentaire dont on a l'identifiant ne se lit, ne se modifie ni ne se retire sans le droit sur son document.
        cid = (await h.http("GET", f"/collab/Note/{name}", token=ada)).json()["comments"][0]["id"]
        assert (await h.http("PUT", f"/comments/{cid}", token=bob, json={"content": "x"})).status_code == 404
        assert (await h.http("DELETE", f"/comments/{cid}", token=bob)).status_code == 404
        assert (await h.http("GET", "/collab/Order/ghost", token=ada)).status_code == 404
        assert (await h.http("GET", "/collab/Nope/x", token=ada)).status_code == 404


async def test_a_tenant_never_sees_another_s_thread(tmp_path):
    async with boot_host(tmp_path, inline_plugins=plugins()) as h:
        ada = h.token(sub="ada", permissions=WRITER)
        name = await order(h, ada)
        await h.http("POST", f"/comments/Order/{name}", token=ada, json={"content": "acme seulement"})
        gus = h.token(sub="gus", tenant="globex", permissions=WRITER)
        assert (await h.http("GET", f"/collab/Order/{name}", token=gus, tenant="globex")).status_code == 404  # le document n'existe pas chez globex
        mine = await h.http("POST", "/doc/Order", token=gus, tenant="globex", json={"data": {"title": "g"}})
        same = mine.json()["name"]
        assert (await h.http("GET", f"/collab/Order/{same}", token=gus, tenant="globex")).json()["comments"] == []


async def test_a_frozen_document_can_still_be_commented(tmp_path):
    async with boot_host(tmp_path, inline_plugins=plugins()) as h:
        ada = h.token(sub="ada", permissions=WRITER)
        made = (await h.http("POST", "/doc/Ticket", token=ada, json={"data": {"title": "t"}})).json()
        done = await h.http("POST", f"/doc/Ticket/{made['name']}/submit", token=ada, json={"version": made["version"]})
        assert done.status_code == 200, done.text
        assert (await h.http("POST", f"/comments/Ticket/{made['name']}", token=h.token(sub="rita", permissions=READER), json={"content": "vu"})).status_code == 201


async def test_assigning_needs_the_right_to_write_and_one_open_assignment_per_person(tmp_path):
    async with boot_host(tmp_path, inline_plugins=plugins()) as h:
        ada, rita = h.token(sub="ada", permissions=WRITER), h.token(sub="rita", permissions=READER)
        name = await order(h, ada)
        assert (await h.http("POST", f"/assignments/Order/{name}", token=rita, json={"assignee": "rita"})).status_code == 403
        ok = await h.http("POST", f"/assignments/Order/{name}", token=ada, json={"assignee": "rita", "description": "À relire", "due_date": "2026-12-24"})
        assert ok.status_code == 201 and ok.json()["status"] == "open" and ok.json()["assigned_by"] == "ada"
        dup = await h.http("POST", f"/assignments/Order/{name}", token=ada, json={"assignee": "rita"})
        assert dup.status_code == 409 and dup.json()["detail"]["code"] == "already_assigned"
        for body, fragment in (({"assignee": ""}, "obligatoire"), ({"assignee": "x", "due_date": "demain"}, "AAAA-MM-JJ"), ({"assignee": "x", "due_date": "2026-02-30"}, "valide"), ({"assignee": "x", "description": "d" * 501}, "500")):
            r = await h.http("POST", f"/assignments/Order/{name}", token=ada, json=body)
            assert r.status_code == 422 and fragment in r.text, (body, r.text)
        # Une assignation terminée n'empêche pas d'en refaire une.
        aid = ok.json()["id"]
        assert (await h.http("PUT", f"/assignments/{aid}", token=rita, json={"status": "done"})).json()["status"] == "done"
        assert (await h.http("POST", f"/assignments/Order/{name}", token=ada, json={"assignee": "rita"})).status_code == 201


async def test_who_may_change_an_assignment(tmp_path):
    async with boot_host(tmp_path, inline_plugins=plugins()) as h:
        ada, rita, bob = h.token(sub="ada", permissions=WRITER), h.token(sub="rita", permissions=READER), h.token(sub="bob", permissions=READER)
        name = await order(h, ada)
        aid = (await h.http("POST", f"/assignments/Order/{name}", token=ada, json={"assignee": "rita"})).json()["id"]
        assert (await h.http("PUT", f"/assignments/{aid}", token=bob, json={"status": "done"})).status_code == 403  # ni l'assigné ni un rédacteur
        assert (await h.http("PUT", f"/assignments/{aid}", token=rita, json={"description": "autre"})).status_code == 403  # l'assigné clôt, il ne réécrit pas
        assert (await h.http("PUT", f"/assignments/{aid}", token=ada, json={"description": "précisé", "due_date": "2027-01-01"})).status_code == 200
        assert (await h.http("PUT", f"/assignments/{aid}", token=rita, json={"status": "cancelled"})).json()["status"] == "cancelled"
        assert (await h.http("PUT", f"/assignments/{aid}", token=ada, json={"status": "open"})).json()["status"] == "open"  # un rédacteur rouvre
        assert (await h.http("PUT", f"/assignments/{aid}", token=ada, json={"status": "weird"})).status_code == 422
        assert (await h.http("PUT", f"/assignments/{aid}", token=ada, json={})).status_code == 422
        assert (await h.http("PUT", f"/assignments/{'0' * 32}", token=ada, json={"status": "done"})).status_code == 404


async def test_my_assignments_hide_documents_i_can_no_longer_read_and_other_people_s_work(tmp_path):
    async with boot_host(tmp_path, inline_plugins=plugins()) as h:
        ada, rita = h.token(sub="ada", permissions=WRITER), h.token(sub="rita", permissions=READER)
        o1, o2 = await order(h, ada, "o1"), await order(h, ada, "o2")
        n1 = (await h.http("POST", "/doc/Note", token=ada, json={"data": {"title": "n"}})).json()["name"]  # `if_owner` : rita ne lira pas la note d'ada
        await h.http("POST", f"/assignments/Order/{o1}", token=ada, json={"assignee": "rita", "due_date": "2026-12-31"})
        await h.http("POST", f"/assignments/Order/{o2}", token=ada, json={"assignee": "rita", "due_date": "2026-11-01"})
        await h.http("POST", f"/assignments/Note/{n1}", token=ada, json={"assignee": "rita"})
        await h.http("POST", f"/assignments/Order/{o1}", token=ada, json={"assignee": "ada"})
        mine = (await h.http("GET", "/my-assignments", token=rita)).json()["items"]
        assert [(i["docname"], i["due_date"]) for i in mine] == [(o2, "2026-11-01"), (o1, "2026-12-31")]  # échéance la plus proche d'abord ; la note est cachée
        assert all(i["assignee"] == "rita" and i["doctype_label"] == "Order" for i in mine)
        first = mine[0]["id"]
        await h.http("PUT", f"/assignments/{first}", token=rita, json={"status": "done"})
        assert len((await h.http("GET", "/my-assignments", token=rita)).json()["items"]) == 1
        assert len((await h.http("GET", "/my-assignments?status=done", token=rita)).json()["items"]) == 1
        assert len((await h.http("GET", "/my-assignments?status=all", token=rita)).json()["items"]) == 2
        assert (await h.http("GET", "/my-assignments?status=bogus", token=rita)).status_code == 422
        assert (await h.http("GET", "/my-assignments?limit=0", token=rita)).status_code == 422


async def test_people_to_assign_to_come_from_the_tenant_and_need_the_right_to_write(tmp_path):
    async with boot_host(tmp_path, inline_plugins=plugins()) as h:
        ada, rita = h.token(sub="ada", permissions=WRITER), h.token(sub="rita", permissions=READER)
        name = await order(h, ada)
        await h.http("POST", f"/comments/Order/{name}", token=rita, json={"content": "hello"})
        await h.http("POST", f"/assignments/Order/{name}", token=ada, json={"assignee": "carol"})
        body = (await h.http("GET", "/people/Order", token=ada)).json()
        assert body["directory"] is False  # pas de xauth dans ce test : les identités vues dans l'organisation
        found = [i["id"] for i in body["items"]]
        assert {"ada", "rita", "carol"} <= set(found)
        assert all(i["email"] is None and i["name"] == i["id"] for i in body["items"])  # sans annuaire, l'identifiant sert de nom
        assert [i["id"] for i in (await h.http("GET", "/people/Order?q=ca", token=ada)).json()["items"]] == ["carol"]
        assert (await h.http("GET", "/people/Order", token=rita)).status_code == 403
        gus = h.token(sub="gus", tenant="globex", permissions=WRITER)
        assert "carol" not in [i["id"] for i in (await h.http("GET", "/people/Order", token=gus, tenant="globex")).json()["items"]]
        assert (await h.http("GET", "/people/Order?q=%25", token=ada)).json()["items"] == []  # un joker n'est pas un joker


async def test_attachments_are_read_like_the_document_and_released_when_removed(tmp_path):
    async with boot_host(tmp_path, inline_plugins=plugins()) as h:
        ada, rita = h.token(sub="ada", permissions=WRITER), h.token(sub="rita", permissions=READER)
        name = await order(h, ada)
        assert (await h.http("POST", f"/attachments/Order/{name}", token=rita, files={"file": ("a.pdf", PDF, "application/pdf")})).status_code == 403
        up = await h.http("POST", f"/attachments/Order/{name}", token=ada, files={"file": ("../devis 1.pdf", PDF, "application/pdf")})
        assert up.status_code == 201, up.text
        att = up.json()
        assert att["filename"] == "devis 1.pdf" and att["size"] == len(PDF) and att["owner"] == "ada"  # le chemin du nom est retiré
        listed = (await h.http("GET", f"/collab/Order/{name}", token=rita)).json()
        assert [a["filename"] for a in listed["attachments"]] == ["devis 1.pdf"] and listed["can_attach"] is False
        # Un lecteur du document obtient un lien signé et télécharge ; qui ne lit pas le document n'en obtient pas.
        link = (await h.http("POST", "/file-links", token=rita, json={"ids": [att["file_id"]]})).json()[att["file_id"]]
        got = await h.http("GET", "/" + link["path"], token=None)
        assert got.status_code == 200 and got.content == PDF and got.headers["content-disposition"].startswith("attachment")
        stranger = h.token(sub="zed", permissions=[])
        assert (await h.http("POST", "/file-links", token=stranger, json={"ids": [att["file_id"]]})).json() == {}
        # Retirer : le rédacteur (ou qui l'a jointe), pas un simple lecteur ; le fichier lui-même disparaît.
        assert (await h.http("DELETE", f"/attachments/{att['id']}", token=rita)).status_code == 403
        assert (await h.http("DELETE", f"/attachments/{att['id']}", token=ada)).status_code == 204
        assert (await h.http("GET", f"/collab/Order/{name}", token=ada)).json()["attachments"] == []
        assert (await h.http("GET", "/" + link["path"], token=None)).status_code == 404


async def test_an_attachment_is_never_an_inline_image_unless_it_really_is_one(tmp_path):
    async with boot_host(tmp_path, inline_plugins=plugins()) as h:
        ada = h.token(sub="ada", permissions=WRITER)
        name = await order(h, ada)
        fake = await h.http("POST", f"/attachments/Order/{name}", token=ada, files={"file": ("evil.html", b"<script>alert(1)</script>", "text/html")})
        link = (await h.http("POST", "/file-links", token=ada, json={"ids": [fake.json()["file_id"]]})).json()[fake.json()["file_id"]]
        served = await h.http("GET", "/" + link["path"], token=None)
        assert served.headers["content-disposition"].startswith("attachment") and served.headers["x-content-type-options"] == "nosniff"
        assert "sandbox" in served.headers["content-security-policy"]
        empty = await h.http("POST", f"/attachments/Order/{name}", token=ada, files={"file": ("e.txt", b"", "text/plain")})
        assert empty.status_code == 422


async def test_deleting_a_document_takes_its_thread_and_files_with_it(tmp_path):
    async with boot_host(tmp_path, inline_plugins=plugins()) as h:
        ada = h.token(sub="ada", permissions=WRITER + ["shop:order:delete"])
        name = await order(h, ada)
        await h.http("POST", f"/comments/Order/{name}", token=ada, json={"content": "x"})
        await h.http("POST", f"/assignments/Order/{name}", token=ada, json={"assignee": "rita"})
        att = (await h.http("POST", f"/attachments/Order/{name}", token=ada, files={"file": ("a.pdf", PDF, "application/pdf")})).json()
        link = (await h.http("POST", "/file-links", token=ada, json={"ids": [att["file_id"]]})).json()[att["file_id"]]
        assert (await h.http("DELETE", f"/doc/Order/{name}", token=ada)).status_code == 204
        assert (await h.http("GET", f"/collab/Order/{name}", token=ada)).status_code == 404  # le document n'existe plus : son fil non plus
        assert (await h.http("GET", "/" + link["path"], token=None)).status_code == 404  # le fichier n'est plus servi
        assert (await h.http("GET", "/my-assignments", token=h.token(sub="rita", permissions=READER))).json()["items"] == []


async def test_the_ipc_actions_apply_the_same_rules(tmp_path):
    async with boot_host(tmp_path, inline_plugins=plugins()) as h:
        ada = h.token(sub="ada", permissions=WRITER)
        name = await order(h, ada)
        c = await h.action("doc.comment.add", {"doctype": "Order", "name": name, "content": "via action"}, token=ada)
        assert c["content"] == "via action"
        a = await h.action("doc.assign", {"doctype": "Order", "name": name, "assignee": "rita"}, token=ada)
        assert a["status"] == "open"
        around = await h.action("doc.collab.get", {"doctype": "Order", "name": name}, token=ada)
        assert len(around["comments"]) == 1 and len(around["assignments"]) == 1
        done = await h.action("doc.assignment.update", {"id": a["id"], "status": "done"}, token=h.token(sub="rita", permissions=READER))
        assert done["status"] == "done"
        mine = await h.action("doc.assignments.mine", {"status": "done"}, token=h.token(sub="rita", permissions=READER))
        assert len(mine["items"]) == 1
