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
