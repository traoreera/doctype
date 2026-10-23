"""Collaboration : joindre, retirer, assigner et modérer demandent de pouvoir ÉCRIRE LE DOCUMENT, pas seulement le type.

Un rédacteur `if_owner` (il lit tout, il n'écrit que ses documents) ne joint ni ne retire rien sur le document d'un autre, ne l'assigne pas, ne modère pas
ses commentaires, et la fiche ne le lui propose pas (`can_attach` / `can_assign`). Ce qui reste à lui : retirer SA pièce jointe, modifier SON commentaire,
clore SA tâche (règles de l'auteur et de l'assigné, inchangées).
"""

from __future__ import annotations

import pytest

from tests.conftest import make_ctx
from tests.unit.test_files import MemoryStore, _Log
from xcore_doctype import files as F
from xcore_doctype.collaboration import CollaborationService
from xcore_doctype.document import DocumentService
from xcore_doctype.errors import PermissionDenied
from xcore_doctype.file_service import FileService
from xcore_doctype.registry import DocTypeRegistry
from xcore_doctype.schema import parse_definition

NOTE = {
    "schema_version": 1, "name": "Note", "module": "crm",
    "fields": [{"fieldname": "title", "fieldtype": "Text"}],
    "permissions": {
        "read": ["crm:note:read"],
        "create": ["crm:note:write", "crm:note:own"],
        "write": ["crm:note:write", {"grant": "crm:note:own", "if_owner": True}],
    },
}
HANA = make_ctx("acme", "hana", permissions=["crm:note:read", "crm:note:write"])
OLGA = make_ctx("acme", "olga", permissions=["crm:note:read", "crm:note:own"])  # lit toutes les notes, n'écrit que les siennes


@pytest.fixture
def env(db, tables):
    reg = DocTypeRegistry()
    reg.register(parse_definition(NOTE))
    docs = DocumentService(reg, db, tables, _Log())
    store = MemoryStore()
    files = FileService(docs, store, F.UrlSigner("s" * 32))
    docs.files = files
    return type("Env", (), {"docs": docs, "collab": CollaborationService(docs, files), "store": store})


async def note(env, ctx=HANA, title="n"):
    return (await env.docs.create(ctx, "Note", {"title": title}))["name"]


async def attach(env, ctx, name, filename="a.txt"):
    return await env.collab.attach(ctx, "Note", name, content=b"x", filename=filename, content_type="text/plain")


async def test_the_thread_offers_attaching_and_assigning_only_on_documents_one_may_write(env):
    theirs, mine = await note(env), await note(env, OLGA)
    seen = await env.collab.get(OLGA, "Note", theirs)
    assert (seen["can_attach"], seen["can_assign"]) == (False, False)
    own = await env.collab.get(OLGA, "Note", mine)
    assert (own["can_attach"], own["can_assign"]) == (True, True)


async def test_attaching_to_and_detaching_from_another_s_document_is_refused(env):
    theirs = await note(env)
    with pytest.raises(PermissionDenied):
        await attach(env, OLGA, theirs)
    att = await attach(env, HANA, theirs)
    with pytest.raises(PermissionDenied):
        await env.collab.detach(OLGA, att["id"])
    assert [a["id"] for a in (await env.collab.get(HANA, "Note", theirs))["attachments"]] == [att["id"]] and len(env.store.blobs) == 1
    # Sur son propre document, rien ne change : elle joint et retire.
    mine = await note(env, OLGA)
    hers = await attach(env, OLGA, mine)
    await env.collab.detach(OLGA, hers["id"])


async def test_the_author_of_an_attachment_still_removes_it(env):
    theirs = await note(env)
    async with env.docs._query(OLGA) as q:  # jointe par elle, quand elle en avait le droit
        await q.add_file("e" * 32, doctype="Note", fieldname=F.ATTACHMENT_FIELD, filename="sienne.txt", content_type="text/plain", size=1,
                         namespace="n", blob_id="b", stored_name="s")
        hers = await q.add_attachment("Note", theirs, "e" * 32)
    await env.collab.detach(OLGA, hers["id"])
    assert (await env.collab.get(HANA, "Note", theirs))["attachments"] == []


async def test_assigning_another_s_document_is_refused(env):
    theirs, mine = await note(env), await note(env, OLGA)
    with pytest.raises(PermissionDenied):
        await env.collab.assign(OLGA, "Note", theirs, assignee="hana")
    assert (await env.collab.assign_many(OLGA, "Note", [mine, theirs], assignee="hana"))["done"] == 1
    assert (await env.collab.get(HANA, "Note", theirs))["assignments"] == []


async def test_moderating_and_managing_another_s_document_needs_to_write_it(env):
    theirs = await note(env)
    comment = await env.collab.add_comment(HANA, "Note", theirs, "à revoir")  # commenter : lire suffit, à tous
    mine = await env.collab.add_comment(OLGA, "Note", theirs, "vu")
    with pytest.raises(PermissionDenied):
        await env.collab.delete_comment(OLGA, comment["id"])  # modérer le fil d'un document qu'elle n'écrit pas : non
    await env.collab.delete_comment(OLGA, mine["id"])  # le sien : oui
    task = await env.collab.assign(HANA, "Note", theirs, assignee="sam", description="relire")
    with pytest.raises(PermissionDenied):
        await env.collab.update_assignment(OLGA, task["id"], status="done")  # ni l'assignée ni une rédactrice de CE document
    with pytest.raises(PermissionDenied):
        await env.collab.update_assignment(OLGA, task["id"], description="autre chose")
    sam = make_ctx("acme", "sam", permissions=["crm:note:read"])
    assert (await env.collab.update_assignment(sam, task["id"], status="done"))["status"] == "done"  # l'assigné clôt la sienne
