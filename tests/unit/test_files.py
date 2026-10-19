"""Champs Image et Attach : contenu vérifié, droits de lecture par document, liens signés, tenants étanches, fichiers orphelins libérés."""

from __future__ import annotations

import struct
import zlib

import pytest

from tests.conftest import make_ctx
from xcore_doctype import files as F
from xcore_doctype.document import DocumentService
from xcore_doctype.errors import InvalidDefinition, NotFound, PermissionDenied, ValidationFailed
from xcore_doctype.file_service import FileService
from xcore_doctype.registry import DocTypeRegistry
from xcore_doctype.schema import parse_definition


def png(extra: bytes = b"") -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00")) + chunk(b"IEND", b"") + extra


JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 20
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 20
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'

EMPLOYEE = {
    "schema_version": 1,
    "name": "Employee",
    "module": "hr",
    "title_field": "full_name",
    "image_field": "photo",
    "fields": [
        {"fieldname": "full_name", "fieldtype": "Text", "required": True},
        {"fieldname": "photo", "fieldtype": "Image", "max_mb": 1},
        {"fieldname": "contract", "fieldtype": "Attach"},
    ],
    "permissions": {"read": ["hr:employee:read", {"grant": "hr:employee:own", "if_owner": True}], "create": ["hr:employee:write"], "write": ["hr:employee:write"]},
}
HR = make_ctx("acme", "hana", permissions=["hr:employee:read", "hr:employee:write", "hr:employee:delete"])
READER = make_ctx("acme", "rita", permissions=["hr:employee:read"])
OWN_ONLY = make_ctx("acme", "olga", permissions=["hr:employee:own", "hr:employee:write"])
STRANGER = make_ctx("acme", "sam", permissions=[])
OTHER_TENANT = make_ctx("globex", "hana", permissions=["hr:employee:read", "hr:employee:write"])


class _Log:
    def info(self, *a, **k): ...
    def error(self, *a, **k): ...


class MemoryStore:
    def __init__(self):
        self.blobs: dict[tuple[str, str], bytes] = {}

    async def save(self, content, filename, namespace):
        blob_id = f"b{len(self.blobs)}"
        self.blobs[(namespace, blob_id)] = content
        return type("B", (), {"file_id": blob_id, "stored_name": blob_id, "size_bytes": len(content)})()

    async def read(self, blob_id, namespace, stored_name=None):
        return self.blobs.get((namespace, stored_name or blob_id))

    async def delete(self, blob_id, namespace, stored_name=None):
        return self.blobs.pop((namespace, stored_name or blob_id), None) is not None


@pytest.fixture
def env(db, tables):
    reg = DocTypeRegistry()
    reg.register(parse_definition(EMPLOYEE))
    docs = DocumentService(reg, db, tables, _Log())
    store, clock = MemoryStore(), [1_000_000.0]
    signer = F.UrlSigner("s" * 32, clock=lambda: clock[0])
    svc = FileService(docs, store, signer)
    docs.files = svc
    return type("Env", (), {"docs": docs, "files": svc, "store": store, "clock": clock, "signer": signer})


async def up(env, ctx=HR, field="photo", content=None, name="moi.png", mime="image/png"):
    return await env.files.upload(ctx, "Employee", field, content=content if content is not None else png(), filename=name, content_type=mime)


class TestRules:
    def test_images_are_recognised_by_content_not_by_name(self):
        assert F.sniff_image(png()) == ("image/png", ".png")
        assert F.sniff_image(JPEG)[0] == "image/jpeg" and F.sniff_image(WEBP)[0] == "image/webp"
        assert F.sniff_image(b"GIF89a....")[0] == "image/gif"
        assert F.sniff_image(SVG) is None  # SVG porte du script
        assert F.sniff_image(b"RIFF\x00\x00\x00\x00WAVEfmt ") is None  # un autre conteneur RIFF
        assert F.sniff_image(b"") is None

    @pytest.mark.parametrize(
        "raw, expected",
        [("photo.png", "photo.png"), ("../../etc/passwd", "passwd"), ("C:\\Users\\x\\cv.pdf", "cv.pdf"), ("a\x00b\nc.txt", "abc.txt"),
         ("...", "fichier"), ("", "fichier"), (None, "fichier"), ("x" * 400 + ".png", ("x" * 400 + ".png")[:200])],
    )
    def test_filenames_are_cleaned(self, raw, expected):
        assert F.clean_filename(raw) == expected

    def test_content_disposition_cannot_be_broken_out_of(self):
        header = F.content_disposition('a"; filename="evil.html\r\nX: y', inline=False)
        assert "\r" not in header and "\n" not in header
        assert header.startswith("attachment; filename=\"a__ filename__evil.html__X_ y\"")
        assert "filename*=UTF-8''" in header
        assert F.content_disposition("é.png", inline=True).startswith("inline;")

    def test_checks(self):
        assert F.check_upload(kind="Image", content=png(), filename="a.exe", declared_type="application/x-msdownload", limit_mb=1) == ("image/png", "a.exe")
        with pytest.raises(F.FileTypeRefused):
            F.check_upload(kind="Image", content=SVG, filename="a.png", declared_type="image/png", limit_mb=1)  # le nom et l'en-tête mentent
        with pytest.raises(ValidationFailed, match="vide"):
            F.check_upload(kind="Attach", content=b"", filename="a", declared_type=None, limit_mb=1)
        with pytest.raises(F.FileTooLarge):
            F.check_upload(kind="Attach", content=b"x" * (1024 * 1024 + 1), filename="a", declared_type=None, limit_mb=1)
        assert F.check_upload(kind="Attach", content=b"x" * 1024 * 1024, filename="a", declared_type=None, limit_mb=1)[0] == "application/octet-stream"
        assert F.check_upload(kind="Attach", content=b"x", filename="a", declared_type="Text/HTML; charset=utf-8", limit_mb=1)[0] == "text/html"
        assert F.check_upload(kind="Attach", content=b"x", filename="a", declared_type="pas un type\r\n", limit_mb=1)[0] == "application/octet-stream"

    def test_signed_urls(self):
        now = [100.0]
        s = F.UrlSigner("k" * 32, ttl=300, clock=lambda: now[0])
        exp, sig = s.sign("acme", "a" * 32)
        assert s.verify("acme", "a" * 32, exp, sig)
        assert not s.verify("globex", "a" * 32, exp, sig)  # un autre tenant
        assert not s.verify("acme", "b" * 32, exp, sig)  # un autre fichier
        assert not s.verify("acme", "a" * 32, exp + 1, sig)  # une échéance rallongée
        assert not s.verify("acme", "a" * 32, exp, "0" * 64)
        now[0] += 301
        assert not s.verify("acme", "a" * 32, exp, sig)  # expirée
        assert not F.UrlSigner("autre" * 8, clock=lambda: 100.0).verify("acme", "a" * 32, exp, sig)  # un autre secret

    async def test_the_local_store_cannot_leave_its_directory(self, tmp_path):
        store = F.LocalBlobStore(tmp_path / "files")
        blob = await store.save(b"abc", "../../evil.txt", "../../outside/ns")
        assert await store.read(blob.file_id, "../../outside/ns", blob.stored_name) == b"abc"
        written = [p for p in tmp_path.rglob("*") if p.is_file()]
        assert len(written) == 1 and (tmp_path / "files") in written[0].parents
        assert await store.delete(blob.file_id, "../../outside/ns", blob.stored_name) is True
        assert await store.delete(blob.file_id, "../../outside/ns", blob.stored_name) is False
        assert await store.read("x", "ns", "../../../../etc/passwd") is None


class TestDefinition:
    def test_image_field_must_be_an_image(self):
        with pytest.raises(InvalidDefinition, match="il faut une Image"):
            parse_definition({**EMPLOYEE, "image_field": "contract"})
        with pytest.raises(InvalidDefinition, match="champ inconnu"):
            parse_definition({**EMPLOYEE, "image_field": "ghost"})

    @pytest.mark.parametrize(
        "field, fragment",
        [({"fieldname": "x", "fieldtype": "Image", "default": "a" * 32}, "pas de valeur par défaut"), ({"fieldname": "x", "fieldtype": "Text", "max_mb": 3}, "max_mb"),
         ({"fieldname": "x", "fieldtype": "Image", "max_mb": 51}, "max_mb"), ({"fieldname": "x", "fieldtype": "Attach", "options": ["a"]}, "options")],
    )
    def test_bad_file_fields(self, field, fragment):
        with pytest.raises(InvalidDefinition, match=fragment):
            parse_definition({**EMPLOYEE, "fields": [field]})

    def test_a_child_table_has_no_file_fields(self):
        with pytest.raises(InvalidDefinition, match="fichiers ne sont pas pris en charge"):
            parse_definition({"schema_version": 1, "name": "Row", "istable": True, "fields": [{"fieldname": "pic", "fieldtype": "Image"}]})

    def test_a_file_cannot_be_filtered(self):
        from xcore_doctype.builder import CompiledDocType

        c = CompiledDocType.compile(parse_definition(EMPLOYEE))
        with pytest.raises(ValidationFailed):
            c.coerce_filter("photo", "a" * 32)
        with pytest.raises(ValidationFailed):
            c.coerce_filter("photo", ["!=", "a" * 32])


class TestUpload:
    async def test_an_image_is_stored_and_described(self, env):
        r = await up(env)
        assert F.FILE_ID_RE.match(r["id"]) and (r["filename"], r["content_type"], r["size"]) == ("moi.png", "image/png", len(png()))
        assert list(env.store.blobs) == [("xcore-doctype/acme/Employee", "b0")]  # le tenant et le type dans l'espace de noms

    async def test_what_enters_is_checked(self, env):
        with pytest.raises(F.FileTypeRefused):
            await up(env, content=SVG, name="x.png")
        with pytest.raises(F.FileTooLarge):
            await up(env, content=png(b"x" * (1024 * 1024 + 1)))  # photo : 1 Mo au plus
        assert env.store.blobs == {}  # rien n'est resté dans le magasin

    async def test_an_attachment_keeps_any_content_but_gets_a_safe_type(self, env):
        r = await up(env, field="contract", content=b"%PDF-1.7 ...", name="contrat.pdf", mime="application/pdf")
        assert r["content_type"] == "application/pdf"

    async def test_only_a_file_field_takes_a_file(self, env):
        with pytest.raises(ValidationFailed, match="n'est pas un champ Image ou Attach"):
            await up(env, field="full_name")
        with pytest.raises(ValidationFailed):
            await up(env, field="ghost")
        with pytest.raises(NotFound):
            await env.files.upload(HR, "Nope", "photo", content=png(), filename="a.png", content_type=None)

    async def test_it_takes_the_right_to_create_or_write(self, env):
        with pytest.raises(PermissionDenied):
            await up(env, ctx=READER)
        with pytest.raises(PermissionDenied):
            await up(env, ctx=STRANGER)
        assert (await up(env, ctx=OWN_ONLY))["id"]


class TestCitingAFile:
    async def test_a_document_cites_its_file(self, env):
        photo = (await up(env))["id"]
        doc = await env.docs.create(HR, "Employee", {"full_name": "Hana", "photo": photo})
        assert doc["data"]["photo"] == photo

    @pytest.mark.parametrize("value", ["pas-un-id", "A" * 32, "a" * 31, "a" * 33, 12, True])
    async def test_a_malformed_id_is_refused(self, env, value):
        with pytest.raises(ValidationFailed):
            await env.docs.create(HR, "Employee", {"full_name": "H", "photo": value})

    async def test_an_unknown_file_is_refused(self, env):
        with pytest.raises(ValidationFailed) as e:
            await env.docs.create(HR, "Employee", {"full_name": "H", "photo": "a" * 32})
        assert e.value.details[0]["type"] == "file_not_found"

    async def test_a_file_cannot_be_cited_by_another_field_or_type(self, env):
        contract = (await up(env, field="contract", content=b"x", name="c.pdf"))["id"]
        with pytest.raises(ValidationFailed) as e:
            await env.docs.create(HR, "Employee", {"full_name": "H", "photo": contract})  # le fichier d'un autre champ
        assert e.value.details[0]["field"] == "photo"

    async def test_another_tenants_file_does_not_exist_here(self, env):
        theirs = (await env.files.upload(OTHER_TENANT, "Employee", "photo", content=png(), filename="a.png", content_type=None))["id"]
        with pytest.raises(ValidationFailed):
            await env.docs.create(HR, "Employee", {"full_name": "H", "photo": theirs})

    async def test_an_unchanged_file_is_not_rechecked(self, env):
        photo = (await up(env))["id"]
        doc = await env.docs.create(HR, "Employee", {"full_name": "H", "photo": photo})
        updated = await env.docs.update(HR, "Employee", doc["name"], {"full_name": "H2", "photo": photo}, doc["version"])
        assert updated["data"]["photo"] == photo


class TestReading:
    async def test_links_follow_the_right_to_read_the_document(self, env):
        photo = (await up(env))["id"]
        doc = await env.docs.create(HR, "Employee", {"full_name": "H", "photo": photo})
        assert doc
        ok = await env.files.links(READER, [photo])  # peut lire le document : peut voir sa photo
        assert set(ok) == {photo} and ok[photo]["filename"] == "moi.png"
        assert await env.files.links(STRANGER, [photo]) == {}  # ne peut pas le lire : le fichier n'existe pas pour lui
        assert await env.files.links(OTHER_TENANT, [photo]) == {}  # un autre tenant : jamais

    async def test_an_uploaded_file_is_private_to_its_uploader_until_a_document_cites_it(self, env):
        photo = (await up(env))["id"]
        assert set(await env.files.links(HR, [photo])) == {photo}
        assert await env.files.links(READER, [photo]) == {}  # rien ne le cite encore : seul le dépositaire le voit

    async def test_the_owner_only_scope_applies_to_files(self, env):
        mine = (await up(env, ctx=OWN_ONLY))["id"]
        await env.docs.create(OWN_ONLY, "Employee", {"full_name": "Olga", "photo": mine})
        theirs = (await up(env))["id"]
        await env.docs.create(HR, "Employee", {"full_name": "Hana", "photo": theirs})
        got = await env.files.links(OWN_ONLY, [mine, theirs])
        assert set(got) == {mine}  # le fichier du document d'un autre n'est pas pour elle

    async def test_bad_ids_are_refused_not_looked_up(self, env):
        for ids in (["x"], ["A" * 32], [1], "abc", ["a" * 32] * 101):
            with pytest.raises(ValidationFailed):
                await env.files.links(HR, ids)
        assert await env.files.links(HR, []) == {}

    async def test_a_link_is_signed_and_serves_the_content(self, env):
        photo = (await up(env))["id"]
        link = (await env.files.links(HR, [photo]))[photo]
        path, query = link["path"].split("?")
        assert path == f"files/acme/{photo}"
        params = dict(p.split("=") for p in query.split("&"))
        content, mime, name, inline = await env.files.serve("acme", photo, int(params["exp"]), params["sig"])
        assert (content, mime, name, inline) == (png(), "image/png", "moi.png", True)

    async def test_serving_checks_the_signature_the_tenant_the_expiry(self, env):
        photo = (await up(env))["id"]
        link = (await env.files.links(HR, [photo]))[photo]
        params = dict(p.split("=") for p in link["path"].split("?")[1].split("&"))
        exp, sig = int(params["exp"]), params["sig"]
        with pytest.raises(NotFound):
            await env.files.serve("globex", photo, exp, sig)
        with pytest.raises(NotFound):
            await env.files.serve("acme", photo, exp, "0" * 64)
        with pytest.raises(NotFound):
            await env.files.serve("acme", "../x", exp, sig)
        env.clock[0] += 400
        with pytest.raises(NotFound):
            await env.files.serve("acme", photo, exp, sig)

    async def test_an_attachment_is_never_served_inline(self, env):
        c = (await up(env, field="contract", content=b"<html><script>1</script></html>", name="x.html", mime="text/html"))["id"]
        link = (await env.files.links(HR, [c]))[c]
        params = dict(p.split("=") for p in link["path"].split("?")[1].split("&"))
        _, mime, _, inline = await env.files.serve("acme", c, int(params["exp"]), params["sig"])
        assert inline is False and mime == "text/html"  # servi en téléchargement avec nosniff et une CSP vide (api/files.py)


class TestRelease:
    async def test_deleting_a_document_deletes_its_files(self, env):
        photo = (await up(env))["id"]
        doc = await env.docs.create(HR, "Employee", {"full_name": "H", "photo": photo})
        assert len(env.store.blobs) == 1
        await env.docs.delete(HR, "Employee", doc["name"])
        assert env.store.blobs == {}
        assert await env.files.links(HR, [photo]) == {}

    async def test_replacing_a_file_deletes_the_old_one(self, env):
        old = (await up(env))["id"]
        doc = await env.docs.create(HR, "Employee", {"full_name": "H", "photo": old})
        new = (await up(env))["id"]
        await env.docs.update(HR, "Employee", doc["name"], {"full_name": "H", "photo": new}, doc["version"])
        assert len(env.store.blobs) == 1
        assert set(await env.files.links(HR, [old, new])) == {new}

    async def test_removing_a_file_deletes_it(self, env):
        photo = (await up(env))["id"]
        doc = await env.docs.create(HR, "Employee", {"full_name": "H", "photo": photo})
        await env.docs.update(HR, "Employee", doc["name"], {"full_name": "H"}, doc["version"])
        assert env.store.blobs == {}

    async def test_a_file_still_cited_elsewhere_survives(self, env):
        photo = (await up(env))["id"]
        a = await env.docs.create(HR, "Employee", {"full_name": "A", "photo": photo})
        await env.docs.create(HR, "Employee", {"full_name": "B", "photo": photo})
        await env.docs.delete(HR, "Employee", a["name"])
        assert len(env.store.blobs) == 1  # B le cite encore

    async def test_a_failing_release_does_not_undo_the_delete(self, env):
        photo = (await up(env))["id"]
        doc = await env.docs.create(HR, "Employee", {"full_name": "H", "photo": photo})

        async def boom(*a, **k):
            raise RuntimeError("stockage indisponible")

        env.store.delete = boom
        await env.docs.delete(HR, "Employee", doc["name"])  # ne lève pas : un fichier orphelin vaut mieux qu'un document restant
        with pytest.raises(NotFound):
            await env.docs.get(HR, "Employee", doc["name"])
