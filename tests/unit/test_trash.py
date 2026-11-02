"""La corbeille (`process/programme.md` groupe 2, élément 7) : supprimer met de côté, restaurer remet en place.

Le choix : une table à part (`xdt_trash`) écrite dans la transaction de la suppression, pas une colonne « supprimé » sur xdt_document —
aucune lecture, aucun lien, aucune valeur unique du moteur n'a à le savoir (§0 du programme). Restaurer est une CRÉATION ordinaire.

Ce que ces tests défendent, chacun par une mutation qui le casse :
- un document supprimé est à la corbeille, tel qu'il était (secrets masqués à l'affichage), et ses valeurs uniques sont libres ;
- restaurer le rend sous son nom, à son PROPRIÉTAIRE, dans son état de workflow, secrets compris, avec son HISTORIQUE d'avant ;
- restaurer est refusé proprement quand le monde a changé : nom repris (409), lien vers un document supprimé depuis (422), valeur
  unique reprise (422), une suppression plus récente du même nom (409) ; une restauration ne passe qu'une fois ;
- on ne voit et ne restaure que ce qu'on pouvait LIRE (`if_owner`), il faut le droit de CRÉER, et de SUPPRIMER pour purger ;
- les fichiers des champs restent jusqu'à la purge, puis partent ;
- la purge d'opérateur ne vide que ce qui est plus vieux que la date.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tests.conftest import make_ctx
from tests.unit.test_print_service import MemoryStore, png
from xcore_doctype import files as F
from xcore_doctype.document import DocumentService, SystemReader
from xcore_doctype.errors import AlreadyExists, DoctypeError, NotFound, PermissionDenied, ValidationFailed
from xcore_doctype.file_service import FileService
from xcore_doctype.registry import DocTypeRegistry
from xcore_doctype.schema import parse_definition
from xcore_doctype.sealing import Sealer
from xcore_doctype.context import RequestContext

CUSTOMER = {
    "schema_version": 1, "name": "Customer", "module": "crm", "title_field": "customer_name", "naming": "prompt",
    "fields": [
        {"fieldname": "customer_name", "fieldtype": "Text", "required": True},
        {"fieldname": "code", "fieldtype": "Text", "unique": True},
        {"fieldname": "api_key", "fieldtype": "Password"},
        {"fieldname": "logo", "fieldtype": "Image"},
    ],
    "permissions": {
        "read": [{"grant": "crm:customer:read"}, {"grant": "crm:customer:own", "if_owner": True}],
        "write": ["crm:customer:write", {"grant": "crm:customer:own", "if_owner": True}],
        "create": ["crm:customer:write", {"grant": "crm:customer:own", "if_owner": True}],
        "delete": ["crm:customer:delete", {"grant": "crm:customer:own", "if_owner": True}],
    },
}
ORDER = {
    "schema_version": 1, "name": "Request", "module": "crm",
    "fields": [{"fieldname": "customer", "fieldtype": "Link", "options": "Customer"}, {"fieldname": "note", "fieldtype": "Text"}],
    "workflow": {
        "states": [{"name": "Brouillon"}, {"name": "À valider"}, {"name": "Validée"}],
        "transitions": [{"action": "Envoyer", "from": "Brouillon", "to": "À valider"}, {"action": "Valider", "from": "À valider", "to": "Validée"}],
    },
}
ALL = [f"crm:{t}:{a}" for t in ("customer", "request") for a in ("read", "write", "delete", "workflow_envoyer", "workflow_valider")]
ALICE = make_ctx("acme", "alice", permissions=ALL)
BOB = make_ctx("acme", "bob", permissions=ALL)
OWNER_ONLY = make_ctx("acme", "olga", permissions=["crm:customer:own"])  # ne voit que SES clients
READER = make_ctx("acme", "rita", permissions=["crm:customer:read"])


class Log:
    def info(self, *a, **k): ...
    def warning(self, *a, **k): ...
    def error(self, *a, **k): ...


@pytest.fixture
def svc(db, tables):
    reg = DocTypeRegistry()
    for d in (CUSTOMER, ORDER):
        reg.register(parse_definition(d))
    reg.validate_links()
    docs = DocumentService(reg, db, tables, Log(), sealer=Sealer("k" * 32))
    store = MemoryStore()
    docs.files = FileService(docs, store, F.UrlSigner("s" * 32))
    docs.store = store  # pour les tests : voir ce qui reste
    return docs


async def customer(svc, ctx=ALICE, name="durand", **data):
    return await svc.create(ctx, "Customer", {"customer_name": "Durand SA", "code": "C1", **data}, name=name)


async def trashed(svc, ctx=ALICE, doctype="Customer"):
    return (await svc.trash(ctx, doctype))["items"]


async def test_a_deleted_document_is_in_the_trash_as_it_was(svc):
    await customer(svc, api_key="s3cret")
    await svc.delete(BOB, "Customer", "durand")
    [item] = await trashed(svc)
    assert (item["name"], item["title"], item["owner"], item["deleted_by"]) == ("durand", "Durand SA", "alice", "bob")
    assert item["data"]["api_key"] == "********"  # un secret ne sort pas plus de la corbeille que du document
    with pytest.raises(NotFound):
        await svc.get(ALICE, "Customer", "durand")
    await customer(svc, name="other")  # sa valeur unique « C1 » est libre
    assert (await svc.list(ALICE, "Customer"))["total"] == 1


async def test_restoring_gives_it_back_to_its_owner_with_its_history_and_secrets(svc):
    doc = await customer(svc, api_key="s3cret")
    await svc.update(ALICE, "Customer", "durand", {**doc["data"], "api_key": "********", "customer_name": "Durand"}, doc["version"])
    await svc.delete(BOB, "Customer", "durand")
    [item] = await trashed(svc)
    back = await svc.restore(BOB, "Customer", item["id"])
    assert (back["name"], back["owner"], back["data"]["customer_name"]) == ("durand", "alice", "Durand")
    reader = SystemReader(svc, RequestContext.system("acme", "test"))
    assert await reader.secret("Customer", "durand", "api_key") == "s3cret"  # rendu chiffré tel quel, jamais re-scellé
    actions = [e["action"] for e in (await svc.history(ALICE, "Customer", "durand"))["items"]]
    assert actions == ["restore", "delete", "update", "create"]  # l'historique d'avant la suppression revient
    assert await trashed(svc) == []
    with pytest.raises(DoctypeError) as e:
        await customer(svc, name="x2", code="C1")  # la valeur unique est de nouveau prise
    assert e.value.status == 422


async def test_a_restore_passes_once(svc):
    await customer(svc)
    await svc.delete(ALICE, "Customer", "durand")
    [item] = await trashed(svc)
    await svc.restore(ALICE, "Customer", item["id"])
    with pytest.raises(NotFound):
        await svc.restore(ALICE, "Customer", item["id"])


async def test_a_restore_refuses_what_changed_since(svc):
    await customer(svc)
    request = await svc.create(ALICE, "Request", {"customer": "durand", "note": "n"})
    await svc.delete(ALICE, "Request", request["name"])
    await svc.delete(ALICE, "Customer", "durand")  # la cible du lien part ensuite
    [req] = await trashed(svc, doctype="Request")
    with pytest.raises(DoctypeError) as e:
        await svc.restore(ALICE, "Request", req["id"])
    assert e.value.code == "validation_failed" and e.value.details[0]["type"] == "link_not_found"

    [cust] = await trashed(svc)
    await customer(svc, name="durand", code="C9")  # le nom est repris
    with pytest.raises(AlreadyExists):
        await svc.restore(ALICE, "Customer", cust["id"])
    await svc.delete(ALICE, "Customer", "durand")
    await customer(svc, name="martin", code="C1")  # la valeur unique est reprise
    newer, older = await trashed(svc)
    assert newer["data"]["code"] == "C9"
    with pytest.raises(DoctypeError) as e:
        await svc.restore(ALICE, "Customer", older["id"])  # une suppression plus récente du même nom passe d'abord
    assert e.value.code == "trash_superseded"
    await svc.purge_trashed(ALICE, "Customer", newer["id"])
    with pytest.raises(ValidationFailed) as e:
        await svc.restore(ALICE, "Customer", older["id"])
    assert e.value.details[0]["type"] == "unique"
    assert len(await trashed(svc)) == 1  # tout refus laisse le document à la corbeille


async def test_the_workflow_state_comes_back(svc):
    req = await svc.create(ALICE, "Request", {"note": "n"})
    req = await svc.workflow_transition(ALICE, "Request", req["name"], "Envoyer", req["version"])
    await svc.delete(ALICE, "Request", req["name"])
    [item] = await trashed(svc, doctype="Request")
    assert (await svc.restore(ALICE, "Request", item["id"]))["workflow_state"] == "À valider"


async def test_one_sees_and_restores_only_what_one_could_read(svc):
    await customer(svc)  # le client d'Alice
    await customer(svc, OWNER_ONLY, name="mine", code="C2")
    await svc.delete(ALICE, "Customer", "durand")
    await svc.delete(OWNER_ONLY, "Customer", "mine")
    assert [i["name"] for i in await trashed(svc, OWNER_ONLY)] == ["mine"]
    alices = next(i for i in await trashed(svc) if i["name"] == "durand")
    with pytest.raises(NotFound):
        await svc.restore(OWNER_ONLY, "Customer", alices["id"])  # comme absent
    with pytest.raises(NotFound):
        await svc.purge_trashed(OWNER_ONLY, "Customer", alices["id"])
    mine = (await trashed(svc, OWNER_ONLY))[0]
    assert (await svc.restore(OWNER_ONLY, "Customer", mine["id"]))["owner"] == "olga"


async def test_restoring_needs_create_and_purging_needs_delete(svc):
    await customer(svc)
    await svc.delete(ALICE, "Customer", "durand")
    [item] = await trashed(svc, READER)  # un lecteur la voit
    with pytest.raises(PermissionDenied):
        await svc.restore(READER, "Customer", item["id"])
    with pytest.raises(PermissionDenied):
        await svc.purge_trashed(READER, "Customer", item["id"])
    assert len(await trashed(svc)) == 1


async def test_field_files_stay_until_the_purge(svc):
    up = await svc.files.upload(ALICE, "Customer", "logo", content=png(), filename="logo.png", content_type="image/png")
    await customer(svc, logo=up["id"])
    await svc.delete(ALICE, "Customer", "durand")
    assert len(svc.store.blobs) == 1  # gardé : une restauration le retrouve
    [item] = await trashed(svc)
    assert (await svc.restore(ALICE, "Customer", item["id"]))["data"]["logo"] == up["id"]
    await svc.delete(ALICE, "Customer", "durand")
    [item] = await trashed(svc)
    await svc.purge_trashed(ALICE, "Customer", item["id"])
    assert svc.store.blobs == {} and await trashed(svc) == []


async def test_the_operator_purge_empties_only_what_is_older(svc):
    await customer(svc)
    await svc.delete(ALICE, "Customer", "durand")
    assert await svc.purge_trash("acme", datetime.now(UTC) - timedelta(days=1)) == 0
    assert await svc.purge_trash("globex", datetime.now(UTC) + timedelta(days=1)) == 0  # un autre tenant : rien
    assert await svc.purge_trash("acme", datetime.now(UTC) + timedelta(days=1)) == 1
    assert await trashed(svc) == []


async def test_a_deletion_that_fails_leaves_nothing_in_the_trash(svc):
    await customer(svc)
    await svc.create(ALICE, "Request", {"customer": "durand"})
    with pytest.raises(DoctypeError) as e:
        await svc.delete(ALICE, "Customer", "durand")  # encore cité
    assert e.value.code == "document_in_use"
    assert await trashed(svc) == []


async def test_reading_everything_does_not_restore_or_purge_someone_else_s(svc):
    """Lire tous les clients mais ne créer et supprimer que LES SIENS : il voit le client d'Alice à la corbeille, il ne peut ni le
    restaurer (ce serait créer un document qui n'est pas à lui) ni le purger."""
    mixed = make_ctx("acme", "max", permissions=["crm:customer:read", "crm:customer:own"])
    await customer(svc)
    await svc.delete(ALICE, "Customer", "durand")
    [item] = await trashed(svc, mixed)
    with pytest.raises(PermissionDenied):
        await svc.restore(mixed, "Customer", item["id"])
    with pytest.raises(PermissionDenied):
        await svc.purge_trashed(mixed, "Customer", item["id"])
    assert len(await trashed(svc)) == 1
