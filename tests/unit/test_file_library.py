"""L'écran « Fichiers » (`file_library.py`) : la liste se juge sur le document CITANT (jamais de fuite, pas même dans le total), et détacher passe par
l'écriture ordinaire (mise à jour du document pour un champ, `collaboration.detach` pour une pièce jointe) avec tous ses refus."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import event

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
from xcore_doctype.permissions import Scope
from xcore_doctype.schema import parse_definition
from xcore_doctype.sealing import MASK, PREFIX, Sealer
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
# Un type que les lecteurs de salariés ne lisent pas : ses fichiers n'existent pas pour eux. Sans historique (`track_changes: false`).
PAYSLIP = {"schema_version": 1, "name": "Payslip", "label": "Bulletin", "module": "hr", "track_changes": False, "fields": [{"fieldname": "pdf", "fieldtype": "Attach"}]}
# Un champ obligatoire, un champ figé une fois posé, un champ en lecture seule : on ne peut pas les vider.
CARD = {
    "schema_version": 1, "name": "Card", "module": "hr",
    "fields": [{"fieldname": "picture", "fieldtype": "Image", "required": True}, {"fieldname": "scan", "fieldtype": "Attach", "set_only_once": True},
               {"fieldname": "stamp", "fieldtype": "Attach", "read_only": True}],
}
EXPENSE = {"schema_version": 1, "name": "Expense", "module": "hr", "submittable": True, "fields": [{"fieldname": "receipt", "fieldtype": "Attach"}]}
MEMBER = {"schema_version": 1, "name": "Member", "module": "hr", "source": "memory", "fields": [{"fieldname": "email", "fieldtype": "Text"}]}
# Soumis, il laisse encore changer son justificatif (`allow_on_submit`) ; il porte un secret, que toute lecture rend masqué.
VOUCHER = {
    "schema_version": 1, "name": "Voucher", "module": "hr", "submittable": True,
    "fields": [{"fieldname": "proof", "fieldtype": "Attach", "allow_on_submit": True}, {"fieldname": "api_key", "fieldtype": "Password"}],
}
# Des secrets figés (posé une fois, lecture seule) à côté d'une photo qu'on peut retirer.
DEVICE = {
    "schema_version": 1, "name": "Device", "module": "hr",
    "fields": [{"fieldname": "photo", "fieldtype": "Image"}, {"fieldname": "pin", "fieldtype": "Password", "set_only_once": True},
               {"fieldname": "seal", "fieldtype": "Password", "read_only": True}],
}
# Des champs que leurs CONDITIONS rendent obligatoires, figés, ou au contraire facultatifs.
CLAIM = {
    "schema_version": 1, "name": "Claim", "module": "hr",
    "fields": [
        {"fieldname": "kind", "fieldtype": "Select", "options": ["Simple", "Justifiée"]},
        {"fieldname": "proof", "fieldtype": "Attach", "mandatory_depends_on": "kind == 'Justifiée'"},
        {"fieldname": "status", "fieldtype": "Select", "options": ["Ouverte", "Close"]},
        {"fieldname": "scan", "fieldtype": "Attach", "read_only_depends_on": "status == 'Close'"},
        {"fieldname": "abroad", "fieldtype": "Boolean"},
        {"fieldname": "visa", "fieldtype": "Attach", "required": True, "depends_on": "abroad"},
    ],
}

EVERY = ["hr:employee:read", "hr:employee:write", "hr:payslip:read", "hr:payslip:write", "hr:card:read", "hr:card:write",
         "hr:expense:read", "hr:expense:write", "hr:expense:submit", "hr:member:read", "hr:member:write",
         "hr:voucher:read", "hr:voucher:write", "hr:voucher:submit", "hr:device:read", "hr:device:write", "hr:claim:read", "hr:claim:write"]
HR = make_ctx("acme", "hana", permissions=EVERY)
READER = make_ctx("acme", "rita", permissions=["hr:employee:read", "hr:member:read"])
OWN = make_ctx("acme", "olga", permissions=["hr:employee:own"])
OLGA = make_ctx("acme", "olga", permissions=["hr:employee:read", "hr:employee:own"])  # lit tous les salariés, n'écrit que les siens
STRANGER = make_ctx("acme", "sam", permissions=[])
OTHER_TENANT = make_ctx("globex", "hana", permissions=EVERY)
NOW = datetime(2026, 10, 22, 9, 0, tzinfo=UTC)


@pytest.fixture
def env(db, tables):
    reg = DocTypeRegistry()
    for d in (EMPLOYEE, PAYSLIP, CARD, EXPENSE, MEMBER, VOUCHER, DEVICE, CLAIM):
        reg.register(parse_definition(d))
    hooks = HookRegistry()

    async def keep_contract(event):
        if event.data.get("full_name") == "Verrouillé" and (event.old or {}).get("contract") and not event.data.get("contract"):
            raise DocumentValidationError("Le contrat d'un salarié verrouillé reste joint")

    hooks.register("hr.keep_contract", keep_contract)
    source, sources = MemorySource(), DocumentSources()
    sources.register("test", "memory", source)
    docs = DocumentService(reg, db, tables, _Log(), hooks=hooks, sources=sources, sealer=Sealer("k" * 32))
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


async def raw(env, doctype, name):
    """Le document tel qu'il est ENREGISTRÉ (secrets chiffrés compris), sans passer par ce que l'API en montre."""
    async with env.docs._query(HR) as q:
        return (await q.get(doctype, name, scope=Scope.everything("read"))).data


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

    @pytest.mark.parametrize(
        "kw", [{"limit": 0}, {"limit": 201}, {"limit": True}, {"offset": -1}, {"order_by": "owner"}, {"q": 3}, {"q": "x" * 101}, {"doctype": ["Employee"]}, {"doctype": 3}]
    )
    async def test_bad_requests_are_refused(self, env, kw):
        with pytest.raises(ValidationFailed):
            await env.lib.list(HR, **kw)

    async def test_the_doctype_filter_answers_like_the_doctype_s_own_list(self, env):
        with pytest.raises(NotFound):
            await env.lib.list(HR, doctype="Nope")
        with pytest.raises(PermissionDenied):
            await env.lib.list(READER, doctype="Payslip")  # il ne peut pas lire ce type : comme sa liste, 403

    async def test_can_detach_follows_the_write_scope_of_each_document(self, env):
        """Lire tous les salariés, n'écrire que les siens (`if_owner`) : « Détacher » n'est offert que sur ses documents, jamais par simple droit sur le type."""
        theirs = await employee(env, photo=await up(env))
        mine = await employee(env, OLGA, full_name="Olga", photo=await up(env, OLGA))
        rows = {i["docname"]: i["can_detach"] for i in (await env.lib.list(OLGA))["items"]}
        assert rows == {theirs["name"]: False, mine["name"]: True}

    async def test_an_attachment_is_offered_to_any_writer_of_its_document_not_only_its_author(self, env):
        doc = await employee(env)
        await env.collab.attach(HR, "Employee", doc["name"], content=b"x", filename="a.txt", content_type="text/plain")
        other_writer = make_ctx("acme", "wes", permissions=["hr:employee:read", "hr:employee:write"])
        row = (await env.lib.list(other_writer))["items"][0]
        assert (row["owner"], row["can_detach"]) == ("hana", True)
        await env.lib.detach(other_writer, attachment=row["attachment"])
        assert (await env.lib.list(HR))["total"] == 0

    async def test_every_row_says_whether_its_document_keeps_a_history(self, env):
        """Le Desk ne promet « historisé » que si le type garde un historique (`track_changes`)."""
        await employee(env, photo=await up(env))
        await env.docs.create(HR, "Payslip", {"pdf": await up(env, doctype="Payslip", field="pdf", name="b.pdf", content=b"%PDF")})
        assert {i["doctype"]: i["track_changes"] for i in (await env.lib.list(HR))["items"]} == {"Employee": True, "Payslip": False}

    @pytest.mark.parametrize("descending", [True, False])
    async def test_ties_are_broken_by_file_then_document_in_both_directions(self, env, descending):
        """Même taille partout : l'ordre ne dépend que du départage (fichier, puis type, document, champ), jamais de l'ordre de lecture de la base, sans quoi
        deux pages pourraient se recouvrir. Le fichier du champ est lu avant la pièce jointe (l'union les donne dans cet ordre) : le départage les inverse."""
        meta = dict(content_type="image/png", size=7, namespace="n", blob_id="b", stored_name="s")
        async with env.docs._query(HR) as q:
            await q.add_file("f" * 32, doctype="Employee", fieldname="photo", filename="f.png", **meta)
            await q.add_file("a" * 32, doctype="Employee", fieldname=F.ATTACHMENT_FIELD, filename="a.png", **meta)
            await q.insert("Employee", "z-2", {"full_name": "Z", "photo": "f" * 32})
            await q.insert("Employee", "z-1", {"full_name": "Y", "photo": "f" * 32})
            await q.add_attachment("Employee", "z-2", "a" * 32)
        got = await env.lib.list(HR, order_by="size", descending=descending)
        assert [(i["file_id"][0], i["docname"], i["field"]) for i in got["items"]] == [("a", "z-2", "_attachment"), ("f", "z-1", "photo"), ("f", "z-2", "photo")]
        pages = [i["docname"] + i["field"] for o in (0, 1, 2) for i in (await env.lib.list(HR, order_by="size", descending=descending, limit=1, offset=o))["items"]]
        assert pages == [i["docname"] + i["field"] for i in got["items"]]

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
        assert got["unavailable"] == [{"name": "Member", "label": "Member"}] and [i["doctype"] for i in got["items"]] == ["Employee"]
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
        stamp = await up(env, doctype="Card", field="stamp", name="t.pdf", content=b"%PDF")
        card = await env.docs.create(HR, "Card", {"picture": picture, "scan": scan, "stamp": stamp})
        rows = {i["field"]: i for i in (await env.lib.list(HR, doctype="Card"))["items"]}
        assert {f: r["can_detach"] for f, r in rows.items()} == {"picture": False, "scan": False, "stamp": False}
        for field, fid, refusal in (("picture", picture, "missing"), ("scan", scan, "set_only_once"), ("stamp", stamp, "read_only")):
            with pytest.raises(ValidationFailed) as e:  # obligatoire, figé une fois posé, en lecture seule
                await env.lib.detach(HR, doctype="Card", name=card["name"], field=field, file_id=fid, version=card["version"])
            assert e.value.details[0]["type"] == refusal

    async def test_a_rule_of_the_organisation_counts_like_one_of_the_definition(self, env):
        """C4.3 × C4.2 : vider une photo est une modification ; si l'organisation a rendu obligatoire un AUTRE champ que ce document n'a pas
        encore, le serveur refusera la modification : l'écran ne la propose donc pas."""
        env.docs._registry.register(parse_definition({
            "schema_version": 1, "name": "Badge", "module": "hr",
            "fields": [{"fieldname": "label", "fieldtype": "Text"}, {"fieldname": "photo", "fieldtype": "Image"}],
            "permissions": {"read": ["hr:employee:read"], "write": ["hr:employee:write"], "create": ["hr:employee:write"]},
        }))
        photo = await up(env, doctype="Badge")
        badge = await env.docs.create(HR, "Badge", {"photo": photo})
        assert [i["can_detach"] for i in (await env.lib.list(HR, doctype="Badge"))["items"]] == [True]
        await env.docs.save_form_override(HR, "Badge", {"fields": {"label": {"mandatory": True}}})
        assert [i["can_detach"] for i in (await env.lib.list(HR, doctype="Badge"))["items"]] == [False]
        with pytest.raises(ValidationFailed) as e:
            await env.lib.detach(HR, doctype="Badge", name=badge["name"], field="photo", file_id=photo, version=badge["version"])
        assert e.value.details[0] == {"field": "label", "msg": "Champ obligatoire", "type": "missing"}

    async def test_a_secret_beside_it_goes_back_masked_and_stays_what_it_was(self, env):
        """Un document qui porte un secret : toute lecture le rend `********`. Le renvoyer ainsi veut dire « inchangé », même quand le secret est figé (posé
        une fois, lecture seule) ou le document soumis (seul le justificatif `allow_on_submit` change) : la liste l'offre, le serveur l'accepte."""
        proof = await up(env, doctype="Voucher", field="proof", name="p.pdf", content=b"%PDF")
        voucher = await env.docs.create(HR, "Voucher", {"proof": proof, "api_key": "s3cret"})
        voucher = await env.docs.submit(HR, "Voucher", voucher["name"], voucher["version"])
        photo = await up(env, doctype="Device", field="photo")
        device = await env.docs.create(HR, "Device", {"photo": photo, "pin": "1234", "seal": "scellé"})
        before = {n: await raw(env, t, n) for t, n in (("Voucher", voucher["name"]), ("Device", device["name"]))}
        rows = {i["doctype"]: i for i in (await env.lib.list(HR))["items"] if i["doctype"] in ("Voucher", "Device")}
        assert rows["Voucher"]["can_detach"] is True and rows["Device"]["can_detach"] is True
        await env.lib.detach(HR, doctype="Voucher", name=voucher["name"], field="proof", file_id=proof, version=rows["Voucher"]["version"])
        await env.lib.detach(HR, doctype="Device", name=device["name"], field="photo", file_id=photo, version=rows["Device"]["version"])
        after_v, after_d = await raw(env, "Voucher", voucher["name"]), await raw(env, "Device", device["name"])
        assert "proof" not in after_v and after_v["api_key"] == before[voucher["name"]]["api_key"]  # le même secret chiffré : rien n'a été rechiffré
        assert "photo" not in after_d and (after_d["pin"], after_d["seal"]) == (before[device["name"]]["pin"], before[device["name"]]["seal"])

    async def test_what_goes_back_is_the_stored_document_not_its_masked_view(self, env):
        """Une valeur au préfixe des secrets dans un champ texte (écrite avant qu'on la refuse) est masquée à la lecture ; détacher la photo renvoie la
        valeur ENREGISTRÉE, pas `********`, qui l'aurait écrasée sans bruit."""
        photo = await up(env)
        async with env.docs._query(HR) as q:
            stored = await q.insert("Employee", "e-1", {"full_name": f"{PREFIX}Hana", "photo": photo})
        assert (await env.docs.get(HR, "Employee", "e-1"))["data"]["full_name"] == MASK
        await env.lib.detach(HR, doctype="Employee", name="e-1", field="photo", file_id=photo, version=stored.version)
        assert await raw(env, "Employee", "e-1") == {"full_name": f"{PREFIX}Hana"}

    async def test_what_conditions_forbid_is_not_offered_and_what_they_allow_is(self, env):
        """`can_detach` évalue les conditions comme le serveur : obligatoire (`mandatory_depends_on`) ou figé (`read_only_depends_on`) par une condition vraie,
        non offert et refusé ; `required` d'un champ que `depends_on` cache, offert et accepté."""
        proof, scan, visa = [await up(env, doctype="Claim", field=f, name=f"{f}.pdf", content=b"%PDF") for f in ("proof", "scan", "visa")]
        claim = await env.docs.create(HR, "Claim", {"kind": "Justifiée", "proof": proof, "status": "Close", "scan": scan, "abroad": False, "visa": visa})
        rows = {i["field"]: i["can_detach"] for i in (await env.lib.list(HR, doctype="Claim"))["items"]}
        assert rows == {"proof": False, "scan": False, "visa": True}
        for field, fid, refusal in (("proof", proof, "missing"), ("scan", scan, "read_only")):
            with pytest.raises(ValidationFailed) as e:
                await env.lib.detach(HR, doctype="Claim", name=claim["name"], field=field, file_id=fid, version=claim["version"])
            assert e.value.details[0]["type"] == refusal
        await env.lib.detach(HR, doctype="Claim", name=claim["name"], field="visa", file_id=visa, version=claim["version"])
        # Les mêmes champs, conditions fausses : offerts, et le serveur les accepte.
        open_claim = await env.docs.create(HR, "Claim", {"kind": "Simple", "proof": proof, "status": "Ouverte", "scan": scan})
        rows = {i["field"]: i["can_detach"] for i in (await env.lib.list(HR, doctype="Claim"))["items"] if i["docname"] == open_claim["name"]}
        assert rows == {"proof": True, "scan": True}
        done = await env.lib.detach(HR, doctype="Claim", name=open_claim["name"], field="scan", file_id=scan, version=open_claim["version"])
        await env.lib.detach(HR, doctype="Claim", name=open_claim["name"], field="proof", file_id=proof, version=done["version"])

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

    async def test_a_writer_of_their_own_documents_only_cannot_detach_another_s_attachment(self, env):
        """`if_owner` en écriture : la pièce jointe du document d'un autre n'est ni offerte ni retirée (ni par cet écran, ni par la collaboration), même
        par qui lit tout ; la sienne, jointe au document d'un autre, reste à lui (« qui l'a jointe la retire »)."""
        theirs = await employee(env)
        att = await env.collab.attach(HR, "Employee", theirs["name"], content=b"%PDF", filename="contrat.pdf", content_type="application/pdf")
        row = (await env.lib.list(OLGA))["items"][0]
        assert row["can_detach"] is False
        with pytest.raises(PermissionDenied):
            await env.lib.detach(OLGA, attachment=att["id"])
        assert (await env.lib.list(HR))["total"] == 1 and len(env.store.blobs) == 1  # ni retirée, ni son fichier supprimé
        async with env.docs._query(OLGA) as q:  # jointe par elle (quand elle en avait le droit)
            await q.add_file("e" * 32, doctype="Employee", fieldname=F.ATTACHMENT_FIELD, filename="sienne.txt", content_type="text/plain", size=1,
                             namespace="n", blob_id="b", stored_name="s")
            hers = await q.add_attachment("Employee", theirs["name"], "e" * 32)
        assert {i["filename"]: i["can_detach"] for i in (await env.lib.list(OLGA))["items"]} == {"contrat.pdf": False, "sienne.txt": True}
        await env.lib.detach(OLGA, attachment=hers["id"])

    async def test_an_attachment_of_a_document_one_cannot_read_does_not_exist(self, env):
        other = await env.collab.attach(HR, "Employee", (await employee(env))["name"], content=b"y", filename="b.txt", content_type="text/plain")
        with pytest.raises(NotFound):
            await env.lib.detach(OWN, attachment=other["id"])
        assert (await env.lib.list(HR))["total"] == 1


class TestManyDoctypes:
    """L'union de `Query.cited_files` : une partie par place qui a des fichiers dans l'organisation (pas une par champ et par type lisibles), et jamais plus
    de termes par instruction que SQLite n'en accepte (500)."""

    @staticmethod
    def library(db, tables, n: int):
        reg = DocTypeRegistry()
        for i in range(n):
            reg.register(parse_definition({"schema_version": 1, "name": f"A{i}", "module": "m", "fields": [{"fieldname": "scan", "fieldtype": "Attach"}]}))
        docs = DocumentService(reg, db, tables, _Log())
        files = FileService(docs, MemoryStore(), F.UrlSigner("s" * 32))
        docs.files = files
        return docs, FileLibrary(docs, CollaborationService(docs, files))

    @staticmethod
    def statements(db):
        seen: list[str] = []

        def capture(conn, cursor, statement, *args):
            seen.append(statement)

        event.listen(db.engine.sync_engine, "before_cursor_execute", capture)
        return seen, lambda: event.remove(db.engine.sync_engine, "before_cursor_execute", capture)

    async def test_only_the_places_that_hold_files_are_queried(self, db, tables):
        """251 types lisibles à un champ Attach : sans fichiers, rien à unir ; avec des fichiers dans UN type, deux parties (son champ, ses pièces jointes)."""
        docs, lib = self.library(db, tables, 251)
        reader = make_ctx("acme", "rita", permissions=[f"m:a{i}:{a}" for i in range(251) for a in ("read", "write")])
        seen, stop = self.statements(db)
        try:
            assert (await lib.list(reader))["total"] == 0
            meta = dict(content_type="application/pdf", size=4, namespace="n", blob_id="b", stored_name="s")
            async with docs._query(reader) as q:
                await q.add_file("c" * 32, doctype="A7", fieldname="scan", filename="scan.pdf", **meta)
                await q.add_file("d" * 32, doctype="A7", fieldname=F.ATTACHMENT_FIELD, filename="joint.pdf", **meta)
                await q.insert("A7", "a-7", {"scan": "c" * 32})
                await q.add_attachment("A7", "a-7", "d" * 32)
            seen.clear()
            got = await lib.list(reader)
        finally:
            stop()
        assert got["total"] == 2 and {i["filename"] for i in got["items"]} == {"scan.pdf", "joint.pdf"}
        assert max(s.count("UNION ALL") for s in seen) == 1, "une partie par champ et par type lisibles, fichiers ou pas"

    async def test_more_places_with_files_than_one_statement_may_unite(self, db, tables):
        """260 types qui ont chacun un fichier de champ et une pièce jointe : 520 parties, découpées en sous-requêtes (SQLite en refuse plus de 500 d'un coup)."""
        n = 260
        docs, lib = self.library(db, tables, n)
        reader = make_ctx("acme", "rita", permissions=[f"m:a{i}:read" for i in range(n)])
        meta = dict(content_type="application/pdf", size=4, namespace="n", blob_id="b", stored_name="s")
        async with docs._query(reader) as q:
            for i in range(n):
                field_file, joined_file = f"{i:032x}", f"{i + 1000:032x}"
                await q.add_file(field_file, doctype=f"A{i}", fieldname="scan", filename=f"scan-{i}.pdf", **meta)
                await q.add_file(joined_file, doctype=f"A{i}", fieldname=F.ATTACHMENT_FIELD, filename=f"joint-{i}.pdf", **meta)
                await q.insert(f"A{i}", f"d-{i}", {"scan": field_file})
                await q.add_attachment(f"A{i}", f"d-{i}", joined_file)
        got = await lib.list(reader, order_by="filename", descending=False, limit=3)
        assert got["total"] == 2 * n and len(got["doctypes"]) == n
        assert [i["filename"] for i in got["items"]] == ["joint-0.pdf", "joint-1.pdf", "joint-10.pdf"]
