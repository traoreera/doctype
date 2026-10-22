"""L'écran « Fichiers » (`file_library.py`) : la liste se juge sur le document CITANT (jamais de fuite, pas même dans le total), et détacher passe par
l'écriture ordinaire (mise à jour du document pour un champ, `collaboration.detach` pour une pièce jointe) avec tous ses refus."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from tests.conftest import make_ctx
from tests.unit.test_files import MemoryStore, _Log, png
from tests.unit.test_virtual_doctypes import MemorySource
from xcore_doctype import files as F
from xcore_doctype.collaboration import CollaborationService
from xcore_doctype.document import DocumentService
from xcore_doctype.errors import DoctypeError, DocumentLocked, DocumentValidationError, NotFound, PermissionDenied, ValidationFailed, VersionConflict
from xcore_doctype.file_library import FileLibrary
from xcore_doctype.file_service import FileService
from xcore_doctype.hooks import HookRegistry
from xcore_doctype.query import StoredDoc
from xcore_doctype.registry import DocTypeRegistry
from xcore_doctype.schema import parse_definition
from xcore_doctype.sources import DocumentSources

EMPLOYEE = {
    "schema_version": 1,
    "name": "Employee",
    "label": "Salarié",
    "module": "hr",
    "title_field": "full_name",
    "fields": [
        {"fieldname": "full_name", "fieldtype": "Text", "required": True},
        {"fieldname": "photo", "label": "Photo", "fieldtype": "Image"},
        {"fieldname": "contract", "label": "Contrat", "fieldtype": "Attach"},
    ],
    "hooks": {"validate": ["hr.keep_contract"]},
    "permissions": {
        "read": ["hr:employee:read", {"grant": "hr:employee:own", "if_owner": True}],
        "create": ["hr:employee:write", "hr:employee:own"],
        "write": ["hr:employee:write", {"grant": "hr:employee:own", "if_owner": True}],
    },
}
# Un type que les lecteurs de salariés ne lisent pas : ses fichiers n'existent pas pour eux.
PAYSLIP = {"schema_version": 1, "name": "Payslip", "label": "Bulletin", "module": "hr", "fields": [{"fieldname": "pdf", "fieldtype": "Attach"}]}
# Un champ obligatoire, un champ figé une fois posé : on ne peut pas les vider.
CARD = {
    "schema_version": 1, "name": "Card", "module": "hr",
    "fields": [{"fieldname": "picture", "fieldtype": "Image", "required": True}, {"fieldname": "scan", "fieldtype": "Attach", "set_only_once": True}],
}
EXPENSE = {"schema_version": 1, "name": "Expense", "module": "hr", "submittable": True, "fields": [{"fieldname": "receipt", "fieldtype": "Attach"}]}
MEMBER = {"schema_version": 1, "name": "Member", "module": "hr", "source": "memory", "fields": [{"fieldname": "email", "fieldtype": "Text"}]}

EVERY = ["hr:employee:read", "hr:employee:write", "hr:payslip:read", "hr:payslip:write", "hr:card:read", "hr:card:write",
         "hr:expense:read", "hr:expense:write", "hr:expense:submit", "hr:member:read", "hr:member:write"]
HR = make_ctx("acme", "hana", permissions=EVERY)
READER = make_ctx("acme", "rita", permissions=["hr:employee:read", "hr:member:read"])
OWN = make_ctx("acme", "olga", permissions=["hr:employee:own"])
STRANGER = make_ctx("acme", "sam", permissions=[])
OTHER_TENANT = make_ctx("globex", "hana", permissions=EVERY)
NOW = datetime(2026, 10, 22, 9, 0, tzinfo=UTC)


@pytest.fixture
def env(db, tables):
    reg = DocTypeRegistry()
    for d in (EMPLOYEE, PAYSLIP, CARD, EXPENSE, MEMBER):
        reg.register(parse_definition(d))
    hooks = HookRegistry()

    async def keep_contract(event):
        if event.data.get("full_name") == "Verrouillé" and (event.old or {}).get("contract") and not event.data.get("contract"):
            raise DocumentValidationError("Le contrat d'un salarié verrouillé reste joint")

    hooks.register("hr.keep_contract", keep_contract)
    source, sources = MemorySource(), DocumentSources()
    sources.register("test", "memory", source)
    docs = DocumentService(reg, db, tables, _Log(), hooks=hooks, sources=sources)
    store = MemoryStore()
    files = FileService(docs, store, F.UrlSigner("s" * 32))
    docs.files = files
    collab = CollaborationService(docs, files)
    lib = FileLibrary(docs, collab)
    return type("Env", (), {"docs": docs, "files": files, "collab": collab, "lib": lib, "store": store, "source": source})


async def up(env, ctx=HR, doctype="Employee", field="photo", name="photo.png", content=None):
    meta = await env.files.upload(ctx, doctype, field, content=content if content is not None else png(), filename=name, content_type=None)
    return meta["id"]


async def employee(env, ctx=HR, full_name="Hana", **files):
    return await env.docs.create(ctx, "Employee", {"full_name": full_name, **files})


def names(page):
    return [(i["doctype"], i["docname"], i["field"], i["filename"]) for i in page["items"]]


class TestListing:
    async def test_what_documents_cite_with_where_and_who(self, env):
        photo = await up(env)
        doc = await employee(env, photo=photo)
        await up(env, name="jamais-cité.png")  # déposé, jamais enregistré dans un document : rien ne le cite
        att = await env.collab.attach(HR, "Employee", doc["name"], content=b"%PDF", filename="avenant.pdf", content_type="application/pdf")
        got = await env.lib.list(HR)
        assert got["total"] == 2 and got["unavailable"] == []
        by_field = {i["field"]: i for i in got["items"]}
        assert set(by_field) == {"photo", "_attachment"}
        p = by_field["photo"]
        assert (p["file_id"], p["filename"], p["content_type"], p["owner"], p["doctype"], p["doctype_label"], p["docname"], p["field_label"]) == (
            photo, "photo.png", "image/png", "hana", "Employee", "Salarié", doc["name"], "Photo"
        )
        assert p["size"] == len(png()) and p["version"] == doc["version"] and p["attachment"] is None and p["can_detach"] is True
        assert p["creation"].endswith("Z")
        a = by_field["_attachment"]
        assert (a["file_id"], a["attachment"], a["field_label"], a["content_type"]) == (att["file_id"], att["id"], None, "application/pdf")
        assert got["doctypes"] == [{"name": "Employee", "label": "Salarié", "count": 2}]

    async def test_a_file_of_a_document_the_caller_cannot_read_does_not_exist_not_even_in_the_total(self, env):
        mine = await employee(env, OWN, full_name="Olga", photo=await up(env, OWN))
        theirs = await employee(env, photo=await up(env))
        await env.collab.attach(HR, "Employee", theirs["name"], content=b"x", filename="sien.txt", content_type="text/plain")
        await env.docs.create(HR, "Payslip", {"pdf": await up(env, doctype="Payslip", field="pdf", name="bulletin.pdf", content=b"%PDF")})
        assert len((await env.lib.list(HR))["items"]) == 4
        # Le seul document qu'elle lit (`if_owner`) : son fichier, et rien d'autre, ni dans le total ni dans les comptes.
        got = await env.lib.list(OWN)
        assert [(i["docname"], i["field"]) for i in got["items"]] == [(mine["name"], "photo")] and got["total"] == 1
        assert got["doctypes"] == [{"name": "Employee", "label": "Salarié", "count": 1}]
        # Il lit les salariés, pas les bulletins : le fichier du bulletin n'existe pas pour lui.
        reader = await env.lib.list(READER)
        assert {i["doctype"] for i in reader["items"]} == {"Employee"} and reader["total"] == 3
        assert all(i["can_detach"] is False for i in reader["items"])  # lire n'est pas écrire
        for nobody in (STRANGER, OTHER_TENANT):
            assert await env.lib.list(nobody) == {"items": [], "total": 0, "limit": 20, "offset": 0, "doctypes": [], "unavailable": []}

    async def test_a_file_two_documents_cite_is_listed_once_per_document(self, env):
        photo = await up(env)
        a = await employee(env, full_name="A", photo=photo)
        b = await employee(env, full_name="B", photo=photo)
        got = await env.lib.list(HR)
        assert sorted(i["docname"] for i in got["items"]) == sorted([a["name"], b["name"]]) and got["total"] == 2

    async def test_filter_by_doctype_and_search_in_the_name(self, env):
        await employee(env, photo=await up(env, name="Portrait_Hana.PNG"))
        await employee(env, full_name="Lina", photo=await up(env, name="lina.png"))
        await env.docs.create(HR, "Payslip", {"pdf": await up(env, doctype="Payslip", field="pdf", name="bulletin-portrait.pdf", content=b"%PDF")})
        assert {i["filename"] for i in (await env.lib.list(HR, q="PORTRAIT"))["items"]} == {"Portrait_Hana.PNG", "bulletin-portrait.pdf"}
        only = await env.lib.list(HR, doctype="Payslip")
        assert names(only) == [("Payslip", only["items"][0]["docname"], "pdf", "bulletin-portrait.pdf")] and only["total"] == 1
        # Les comptes par type suivent la recherche, pas le filtre de type (de quoi proposer les autres types).
        assert {d["name"]: d["count"] for d in only["doctypes"]} == {"Employee": 2, "Payslip": 1}
        both = await env.lib.list(HR, doctype="Employee", q="portrait")
        assert [i["filename"] for i in both["items"]] == ["Portrait_Hana.PNG"] and both["total"] == 1
        # Les jokers de LIKE sont des caractères comme les autres : `_` ne remplace pas une lettre, `%` ne prend pas tout.
        assert [i["filename"] for i in (await env.lib.list(HR, q="t_h"))["items"]] == ["Portrait_Hana.PNG"]
        assert (await env.lib.list(HR, q="%"))["total"] == 0

    async def test_pagination_and_order(self, env):
        for i in range(5):
            await employee(env, full_name=f"E{i}", photo=await up(env, name=f"f{i}.png", content=png(b"x" * i)))
        first = await env.lib.list(HR, limit=2, offset=0, order_by="filename", descending=False)
        second = await env.lib.list(HR, limit=2, offset=2, order_by="filename", descending=False)
        last = await env.lib.list(HR, limit=2, offset=4, order_by="filename", descending=False)
        assert [i["filename"] for i in first["items"] + second["items"] + last["items"]] == [f"f{i}.png" for i in range(5)]
        assert first["total"] == second["total"] == last["total"] == 5 and (await env.lib.list(HR, offset=5))["items"] == []
        assert [i["filename"] for i in (await env.lib.list(HR, order_by="size", limit=1))["items"]] == ["f4.png"]  # le plus gros d'abord

    @pytest.mark.parametrize("kw", [{"limit": 0}, {"limit": 201}, {"limit": True}, {"offset": -1}, {"order_by": "owner"}, {"q": 3}, {"q": "x" * 101}])
    async def test_bad_requests_are_refused(self, env, kw):
        with pytest.raises(ValidationFailed):
            await env.lib.list(HR, **kw)

    async def test_the_doctype_filter_answers_like_the_doctype_s_own_list(self, env):
        with pytest.raises(NotFound):
            await env.lib.list(HR, doctype="Nope")
        with pytest.raises(PermissionDenied):
            await env.lib.list(READER, doctype="Payslip")  # il ne peut pas lire ce type : comme sa liste, 403

    async def test_the_attachments_of_a_virtual_doctype_follow_its_source(self, env):
        env.source.put("acme", StoredDoc("Member", "m-1", {"email": "a@x.fr"}, "hana", NOW, NOW, 1))
        env.source.put("acme", StoredDoc("Member", "m-2", {"email": "b@x.fr"}, "hana", NOW, NOW, 1))
        att = await env.collab.attach(HR, "Member", "m-1", content=b"x", filename="cv.txt", content_type="text/plain")
        got = await env.lib.list(READER)
        assert [(i["doctype"], i["docname"], i["attachment"], i["can_detach"]) for i in got["items"]] == [("Member", "m-1", att["id"], False)]
        assert (await env.lib.list(HR))["items"][0]["can_detach"] is True
        env.source.docs["acme"].pop("m-1")  # la source ne le connaît plus : sa pièce jointe n'a plus de document lisible
        assert (await env.lib.list(READER))["total"] == 0

    async def test_a_failing_source_is_named_never_silently_dropped(self, env):
        env.source.put("acme", StoredDoc("Member", "m-1", {"email": "a@x.fr"}, "hana", NOW, NOW, 1))
        await env.collab.attach(HR, "Member", "m-1", content=b"x", filename="cv.txt", content_type="text/plain")
        await employee(env, photo=await up(env))
        calls = len(env.source.calls)
        env.source.boom = RuntimeError("xauth est arrêté")
        got = await env.lib.list(READER)
        assert got["unavailable"] == ["Member"] and [i["doctype"] for i in got["items"]] == ["Employee"]
        assert len(env.source.calls) == calls + 1  # demandée UNE fois, parce qu'elle a des pièces jointes

    async def test_a_virtual_doctype_without_attachments_is_not_asked(self, env):
        await employee(env, photo=await up(env))
        await env.lib.list(READER)
        assert env.source.calls == []


class TestDetachingAField:
    async def test_it_is_an_ordinary_update_versioned_historized_and_the_file_released(self, env):
        photo = await up(env)
        doc = await employee(env, photo=photo, contract=await up(env, field="contract", name="c.pdf", content=b"%PDF"))
        assert len(env.store.blobs) == 2
        row = next(i for i in (await env.lib.list(HR))["items"] if i["field"] == "photo")
        out = await env.lib.detach(HR, doctype="Employee", name=doc["name"], field="photo", file_id=photo, version=row["version"])
        assert out == {"detached": "field", "version": doc["version"] + 1}
        after = await env.docs.get(HR, "Employee", doc["name"])
        assert "photo" not in after["data"] and after["data"]["contract"] and after["data"]["full_name"] == "Hana"  # le reste est intact
        history = await env.docs.history(HR, "Employee", doc["name"])
        assert history["items"][0]["action"] == "update" and history["items"][0]["changes"]["photo"]["old"] == photo
        assert len(env.store.blobs) == 1 and await env.files.links(HR, [photo]) == {}  # plus cité : libéré (blob et ligne)
        assert [i["field"] for i in (await env.lib.list(HR))["items"]] == ["contract"]

    async def test_a_value_the_definition_no_longer_knows_does_not_block_it(self, env):
        """Une valeur laissée par une extension retirée (D26) : le Desk ne la renvoie plus, détacher non plus (le schéma strict la refuserait)."""
        photo = await up(env)
        async with env.docs._query(HR) as q:
            stored = await q.insert("Employee", "e-1", {"full_name": "Hana", "photo": photo, "payroll_badge": "B-7"})
        await env.lib.detach(HR, doctype="Employee", name="e-1", field="photo", file_id=photo, version=stored.version)
        assert (await env.docs.get(HR, "Employee", "e-1"))["data"] == {"full_name": "Hana"}

    async def test_a_stale_version_is_a_conflict(self, env):
        photo = await up(env)
        doc = await employee(env, photo=photo)
        await env.docs.update(HR, "Employee", doc["name"], {"full_name": "Hana B.", "photo": photo}, doc["version"])  # modifié ailleurs
        with pytest.raises(VersionConflict):
            await env.lib.detach(HR, doctype="Employee", name=doc["name"], field="photo", file_id=photo, version=doc["version"])
        assert (await env.docs.get(HR, "Employee", doc["name"]))["data"]["photo"] == photo

    async def test_a_document_that_no_longer_cites_the_file_is_not_emptied(self, env):
        old, new = await up(env), await up(env, name="nouvelle.png")
        doc = await employee(env, photo=old)
        doc = await env.docs.update(HR, "Employee", doc["name"], {"full_name": "Hana", "photo": new}, doc["version"])
        with pytest.raises(DoctypeError) as e:
            await env.lib.detach(HR, doctype="Employee", name=doc["name"], field="photo", file_id=old, version=doc["version"])
        assert (e.value.code, e.value.status) == ("file_not_cited", 409)
        assert (await env.docs.get(HR, "Employee", doc["name"]))["data"]["photo"] == new  # le nouveau fichier n'a pas été retiré

    async def test_the_rights_are_those_of_writing_the_document(self, env):
        photo = await up(env)
        doc = await employee(env, photo=photo)
        target = dict(doctype="Employee", name=doc["name"], field="photo", file_id=photo, version=doc["version"])
        with pytest.raises(PermissionDenied):
            await env.lib.detach(READER, **target)  # il le lit, il ne l'écrit pas
        for blind in (OWN, STRANGER, OTHER_TENANT):
            with pytest.raises((NotFound, PermissionDenied)):
                await env.lib.detach(blind, **target)
        with pytest.raises(NotFound):
            await env.lib.detach(OWN, **target)  # hors de son périmètre : introuvable, pas « interdit »
        assert (await env.docs.get(HR, "Employee", doc["name"]))["data"]["photo"] == photo

    async def test_the_hooks_run_and_may_refuse(self, env):
        contract = await up(env, field="contract", name="c.pdf", content=b"%PDF")
        doc = await employee(env, full_name="Verrouillé", contract=contract)
        with pytest.raises(DoctypeError) as e:
            await env.lib.detach(HR, doctype="Employee", name=doc["name"], field="contract", file_id=contract, version=doc["version"])
        assert (e.value.code, e.value.status) == ("hook_rejected", 422)
        assert (await env.docs.get(HR, "Employee", doc["name"]))["data"]["contract"] == contract and len(env.store.blobs) == 1

    async def test_what_the_definition_forbids_is_refused_and_not_offered(self, env):
        picture, scan = await up(env, doctype="Card", field="picture"), await up(env, doctype="Card", field="scan", name="s.pdf", content=b"%PDF")
        card = await env.docs.create(HR, "Card", {"picture": picture, "scan": scan})
        rows = {i["field"]: i for i in (await env.lib.list(HR, doctype="Card"))["items"]}
        assert rows["picture"]["can_detach"] is False and rows["scan"]["can_detach"] is False
        with pytest.raises(ValidationFailed):  # obligatoire
            await env.lib.detach(HR, doctype="Card", name=card["name"], field="picture", file_id=picture, version=card["version"])
        with pytest.raises(ValidationFailed):  # figé une fois posé
            await env.lib.detach(HR, doctype="Card", name=card["name"], field="scan", file_id=scan, version=card["version"])

    async def test_a_submitted_document_is_frozen(self, env):
        receipt = await up(env, doctype="Expense", field="receipt", name="r.pdf", content=b"%PDF")
        doc = await env.docs.create(HR, "Expense", {"receipt": receipt})
        doc = await env.docs.submit(HR, "Expense", doc["name"], doc["version"])
        row = (await env.lib.list(HR, doctype="Expense"))["items"][0]
        assert row["docstatus"] == 1 and row["can_detach"] is False
        with pytest.raises(DocumentLocked):
            await env.lib.detach(HR, doctype="Expense", name=doc["name"], field="receipt", file_id=receipt, version=doc["version"])

    @pytest.mark.parametrize(
        "kw",
        [
            {"doctype": "Employee", "name": "x", "field": "photo", "file_id": "a" * 32},  # sans version
            {"doctype": "Employee", "name": "x", "field": "full_name", "file_id": "a" * 32, "version": 1},  # pas un champ fichier
            {"doctype": "Employee", "name": "x", "field": "photo", "file_id": 3, "version": 1},
            {"attachment": "a" * 32, "doctype": "Employee"},  # les deux formes mêlées
            {},
        ],
    )
    async def test_the_target_is_checked(self, env, kw):
        with pytest.raises(ValidationFailed):
            await env.lib.detach(HR, **kw)


class TestDetachingAnAttachment:
    async def test_it_goes_through_collaboration_detach_and_releases_the_file(self, env):
        doc = await employee(env)
        att = await env.collab.attach(HR, "Employee", doc["name"], content=b"x", filename="a.txt", content_type="text/plain")
        with pytest.raises(PermissionDenied):
            await env.lib.detach(READER, attachment=att["id"])  # ni l'auteur, ni un rédacteur du document
        assert len(env.store.blobs) == 1
        assert await env.lib.detach(HR, attachment=att["id"]) == {"detached": "attachment", "version": None}
        assert env.store.blobs == {} and (await env.lib.list(HR))["total"] == 0
        assert (await env.docs.get(HR, "Employee", doc["name"]))["version"] == doc["version"]  # le document lui-même n'a pas bougé
        with pytest.raises(NotFound):
            await env.lib.detach(HR, attachment=att["id"])

    async def test_its_author_may_detach_it_even_without_the_right_to_write(self, env):
        doc = await employee(env, OWN, full_name="Olga")
        att = await env.collab.attach(OWN, "Employee", doc["name"], content=b"x", filename="a.txt", content_type="text/plain")
        olga_reads = make_ctx("acme", "olga", permissions=["hr:employee:read"])  # elle a perdu le droit d'écrire, pas celui de lire
        row = (await env.lib.list(olga_reads))["items"][0]
        assert row["can_detach"] is True
        await env.lib.detach(olga_reads, attachment=att["id"])
        assert (await env.lib.list(OWN))["total"] == 0

    async def test_the_author_shown_and_allowed_is_who_attached_it(self, env):
        """« Déposé par » d'une pièce jointe = qui l'a JOINTE, la personne même que `collaboration.detach` laisse la retirer (pas celle qui a téléversé
        le contenu, si ce n'est pas la même)."""
        doc = await employee(env)
        olga_reads = make_ctx("acme", "olga", permissions=["hr:employee:read"])
        async with env.docs._query(HR) as q:  # le fichier téléversé par hana…
            await q.add_file("f" * 32, doctype="Employee", fieldname=F.ATTACHMENT_FIELD, filename="note.txt", content_type="text/plain", size=1,
                             namespace="n", blob_id="b", stored_name="s")
        async with env.docs._query(olga_reads) as q:  # … joint par olga
            att = await q.add_attachment("Employee", doc["name"], "f" * 32)
        row = (await env.lib.list(olga_reads))["items"][0]
        assert (row["owner"], row["can_detach"]) == ("olga", True)
        assert (await env.lib.list(READER))["items"][0]["can_detach"] is False
        await env.lib.detach(olga_reads, attachment=att["id"])
        assert (await env.lib.list(HR))["total"] == 0

    async def test_an_attachment_of_a_document_one_cannot_read_does_not_exist(self, env):
        other = await env.collab.attach(HR, "Employee", (await employee(env))["name"], content=b"y", filename="b.txt", content_type="text/plain")
        with pytest.raises(NotFound):
            await env.lib.detach(OWN, attachment=other["id"])
        assert (await env.lib.list(HR))["total"] == 1
