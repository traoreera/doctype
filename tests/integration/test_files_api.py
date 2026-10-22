"""Téléversement, liens signés et contenu, de bout en bout sur un vrai hôte xcore (multipart, en-têtes de sécurité, route publique sans jeton)."""

from __future__ import annotations

from tests.integration.conftest import boot_host
from tests.unit.test_files import EMPLOYEE, SVG, png

HR = ["hr:employee:read", "hr:employee:write", "hr:employee:delete"]


async def upload(h, token, field="photo", content=None, name="moi.png", mime="image/png", **kw):
    return await h.http("POST", f"/upload/Employee/{field}", token=token, files={"file": (name, content if content is not None else png(), mime)}, **kw)


async def test_upload_link_and_signed_download(tmp_path):
    async with boot_host(tmp_path, extra_doctypes=(EMPLOYEE,), operator_doctypes=False) as h:
        t = h.token(permissions=HR)
        r = await upload(h, t)
        assert r.status_code == 201, r.text
        meta = r.json()
        assert set(meta) == {"id", "filename", "content_type", "size"} and meta["content_type"] == "image/png"

        doc = await h.http("POST", "/doc/Employee", token=t, json={"data": {"full_name": "Hana", "photo": meta["id"]}})
        assert doc.status_code == 201, doc.text

        links = await h.http("POST", "/file-links", token=t, json={"ids": [meta["id"]]})
        link = links.json()[meta["id"]]
        # Le contenu se sert SANS jeton : la signature de l'URL fait foi.
        got = await h.http("GET", "/" + link["path"], token=None, tenant=None)
        assert got.status_code == 200 and got.content == png()
        assert got.headers["content-type"] == "image/png"
        assert got.headers["x-content-type-options"] == "nosniff"
        assert got.headers["content-security-policy"] == "default-src 'none'; sandbox"
        assert got.headers["content-disposition"].startswith("inline;")

        tampered = link["path"].replace("sig=", "sig=0")[:-1]
        assert (await h.http("GET", "/" + tampered, token=None, tenant=None)).status_code in (404, 422)
        other = link["path"].replace("/acme/", "/globex/")
        assert (await h.http("GET", "/" + other, token=None, tenant=None)).status_code == 404


async def test_what_is_refused(tmp_path):
    async with boot_host(tmp_path, extra_doctypes=(EMPLOYEE,), operator_doctypes=False) as h:
        t = h.token(permissions=HR)
        assert (await upload(h, t, content=SVG, name="x.png")).status_code == 415  # le contenu n'est pas une image reconnue
        assert (await upload(h, t, content=b"")).status_code == 422
        assert (await upload(h, t, content=png(b"x" * (1024 * 1024 + 1)))).status_code == 413
        assert (await upload(h, t, field="full_name")).status_code == 422
        assert (await upload(h, h.token(permissions=["hr:employee:read"]))).status_code == 403
        assert (await upload(h, None)).status_code == 401
        assert (await h.http("POST", "/file-links", token=None, json={"ids": []})).status_code == 401


async def test_an_attachment_is_downloaded_never_displayed(tmp_path):
    async with boot_host(tmp_path, extra_doctypes=(EMPLOYEE,), operator_doctypes=False) as h:
        t = h.token(permissions=HR)
        meta = (await upload(h, t, field="contract", content=b"<html><script>alert(1)</script>", name="a\"b.html", mime="text/html")).json()
        await h.http("POST", "/doc/Employee", token=t, json={"data": {"full_name": "H", "contract": meta["id"]}})
        link = (await h.http("POST", "/file-links", token=t, json={"ids": [meta["id"]]})).json()[meta["id"]]
        got = await h.http("GET", "/" + link["path"], token=None, tenant=None)
        assert got.headers["content-disposition"].startswith("attachment;")
        disposition = got.headers["content-disposition"]
        assert "\r" not in disposition and "\n" not in disposition
        assert disposition.count('"') == 2  # le nom d'origine contenait un guillemet : il n'a pas pu fermer la valeur entre guillemets
        assert got.headers["x-content-type-options"] == "nosniff"


async def test_the_links_of_a_file_you_cannot_read_are_absent_not_forbidden(tmp_path):
    async with boot_host(tmp_path, extra_doctypes=(EMPLOYEE,), operator_doctypes=False) as h:
        t = h.token(permissions=HR)
        meta = (await upload(h, t)).json()
        await h.http("POST", "/doc/Employee", token=t, json={"data": {"full_name": "H", "photo": meta["id"]}})
        blind = h.token(sub="x", permissions=[])
        assert (await h.http("POST", "/file-links", token=blind, json={"ids": [meta["id"]]})).json() == {}
        foreign = h.token(sub="g", tenant="globex", permissions=HR)
        assert (await h.http("POST", "/file-links", token=foreign, tenant="globex", json={"ids": [meta["id"]]})).json() == {}
        assert (await h.http("POST", "/file-links", token=t, json={"ids": ["nope"]})).status_code == 422


async def test_the_files_screen_lists_what_one_may_read_and_detaches_by_ordinary_writes(tmp_path):
    """`/file-library` : une identité exigée, la liste jugée sur le document citant, détacher = modifier le document (version, droits) ou retirer la pièce jointe."""
    async with boot_host(tmp_path, extra_doctypes=(EMPLOYEE,), operator_doctypes=False) as h:
        t, reader = h.token(permissions=HR), h.token(sub="rita", permissions=["hr:employee:read"])
        photo = (await upload(h, t)).json()["id"]
        doc = (await h.http("POST", "/doc/Employee", token=t, json={"data": {"full_name": "Hana", "photo": photo}})).json()
        att = (await h.http("POST", f"/attachments/Employee/{doc['name']}", token=t, files={"file": ("avenant.pdf", b"%PDF", "application/pdf")})).json()

        assert (await h.http("GET", "/file-library", token=None)).status_code == 401
        listed = await h.http("GET", "/file-library", token=reader, params={"q": "AVENANT"})
        assert listed.status_code == 200, listed.text
        body = listed.json()
        assert [(i["filename"], i["docname"], i["attachment"], i["can_detach"]) for i in body["items"]] == [("avenant.pdf", doc["name"], att["id"], False)]
        assert body["total"] == 1 and body["doctypes"] == [{"name": "Employee", "label": "Employee", "count": 1}]
        assert (await h.http("GET", "/file-library", token=h.token(sub="zed", permissions=[]))).json()["total"] == 0
        assert (await h.http("GET", "/file-library", token=t, params={"doctype": "Nope"})).status_code == 404
        assert (await h.http("GET", "/file-library", token=t, params={"limit": 0})).status_code == 422
        assert (await h.http("GET", "/file-library", token=t, params={"order": "sideways"})).status_code == 422

        target = {"doctype": "Employee", "name": doc["name"], "field": "photo", "file_id": photo, "version": doc["version"]}
        assert (await h.http("POST", "/file-library/detach", token=reader, json=target)).status_code == 403  # lire n'est pas écrire
        assert (await h.http("POST", "/file-library/detach", token=t, json={**target, "version": doc["version"] + 1})).status_code == 409
        assert (await h.http("POST", "/file-library/detach", token=t, json={**target, "extra": 1})).status_code == 422
        done = await h.http("POST", "/file-library/detach", token=t, json=target)
        assert done.status_code == 200 and done.json() == {"detached": "field", "version": doc["version"] + 1}, done.text
        assert "photo" not in (await h.http("GET", f"/doc/Employee/{doc['name']}", token=t)).json()["data"]

        assert (await h.http("POST", "/file-library/detach", token=reader, json={"attachment": att["id"]})).status_code == 403
        assert (await h.http("POST", "/file-library/detach", token=t, json={"attachment": att["id"]})).json() == {"detached": "attachment", "version": None}
        assert (await h.http("GET", "/file-library", token=t)).json()["total"] == 0


async def test_the_files_screen_ipc_actions_apply_the_same_rules(tmp_path):
    """`doc.files.list` / `doc.files.detach` : les mêmes règles que les routes (lecture du document citant, détacher = l'écriture ordinaire)."""
    async with boot_host(tmp_path, extra_doctypes=(EMPLOYEE,), operator_doctypes=False) as h:
        t, reader = h.token(permissions=HR), h.token(sub="rita", permissions=["hr:employee:read"])
        photo = (await upload(h, t)).json()["id"]
        doc = (await h.http("POST", "/doc/Employee", token=t, json={"data": {"full_name": "Hana", "photo": photo}})).json()
        listed = await h.action("doc.files.list", {"doctype": "Employee"}, token=reader)
        assert [(i["file_id"], i["field"], i["can_detach"]) for i in listed["items"]] == [(photo, "photo", False)] and listed["total"] == 1
        assert (await h.action("doc.files.list", {}, token=h.token(sub="zed", permissions=[])))["total"] == 0
        target = {"doctype": "Employee", "name": doc["name"], "field": "photo", "file_id": photo, "version": doc["version"]}
        assert (await h.action("doc.files.detach", target, token=reader))["status"] == "error"  # lire n'est pas écrire
        assert (await h.action("doc.files.detach", {**target, "extra": 1}, token=t))["code"] == "detach_target"
        done = await h.action("doc.files.detach", target, token=t)
        assert (done["detached"], done["version"]) == ("field", doc["version"] + 1)
        assert (await h.action("doc.files.list", {}, token=t))["total"] == 0
