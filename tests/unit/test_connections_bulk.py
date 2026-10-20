"""Connexions d'un document (qui le cite) et actions en lot (modifier, supprimer, soumettre, annuler plusieurs documents d'un coup).

Ce que ces tests défendent, chacun par une mutation qui le casse :
- les connexions ne comptent que les documents que l'appelant PEUT lire, dans les types qu'il peut lire (jamais un total qui révèle ce qu'il ne voit pas) ;
- une action en lot passe par la porte ordinaire de chaque document : un refus (droit, hook, version, lien) sur l'un ne retient pas les autres ;
- le droit d'effectuer l'action est exigé UNE fois d'abord, et ce qui ne se modifie pas en lot (tableaux, secrets, fichiers, champs copiés) est refusé net.
"""

from __future__ import annotations

import pytest

from tests.conftest import make_ctx
from tests.unit.test_properties_h2 import ALICE, GLOBEX, GRANTS, Env as H2Env
from xcore_doctype.errors import DoctypeError, NotFound, PermissionDenied, ValidationFailed


@pytest.fixture
def env(db, tables):
    return H2Env(db, tables)


def by_ref(result) -> dict[tuple[str, str, str | None], int]:
    return {(i["doctype"], i["field"], i["table"]): i["count"] for i in result["items"]}


class TestConnections:
    async def test_counts_the_documents_that_cite_this_one_by_type_and_field(self, env):
        place = await env.make("Place", label="Paris")
        other = await env.make("Place", label="Lyon")
        await env.make("Visit", place=place["name"], stops=[{"place": other["name"]}])
        await env.make("Visit", place=place["name"])
        await env.make("Visit", stops=[{"place": place["name"]}, {"place": place["name"]}])  # deux lignes du MÊME document : un document
        await env.make("Note", ref_type="Place", ref_name=place["name"])
        got = by_ref(await env.svc.connections(ALICE, "Place", place["name"]))
        assert got == {("Visit", "place", None): 2, ("Visit", "place", "stops"): 1, ("Note", "ref_name", None): 1}
        assert by_ref(await env.svc.connections(ALICE, "Place", other["name"])) == {("Visit", "place", "stops"): 1}

    async def test_labels_are_the_ones_the_desk_shows(self, env):
        place = await env.make("Place", label="Paris")
        await env.make("Visit", place=place["name"])
        item = (await env.svc.connections(ALICE, "Place", place["name"]))["items"][0]
        assert (item["doctype"], item["label"], item["field"], item["field_label"], item["table"], item["count"]) == ("Visit", "Visit", "place", "place", None, 1)

    async def test_a_dynamic_link_only_counts_when_it_names_this_doctype(self, env):
        place = await env.make("Place", label="Paris")
        await env.svc.create(ALICE, "Gadget", {}, name=place["name"])  # un Gadget du même nom
        await env.make("Note", ref_type="Gadget", ref_name=place["name"])
        assert by_ref(await env.svc.connections(ALICE, "Place", place["name"])) == {}
        assert by_ref(await env.svc.connections(ALICE, "Gadget", place["name"])) == {("Note", "ref_name", None): 1}

    async def test_a_document_that_cites_itself_is_not_its_own_connection(self, env):
        # Pas de DocType auto-référent dans le jeu ; la règle est dans la requête (`name != name`), vérifiée par test_query_leak.references_to.
        place = await env.make("Place", label="x")
        assert by_ref(await env.svc.connections(ALICE, "Place", place["name"])) == {}

    async def test_only_what_the_caller_can_read_is_counted(self, env):
        place = await env.make("Place", label="Paris")
        await env.make("Visit", place=place["name"])
        await env.make("Note", ref_type="Place", ref_name=place["name"])
        no_notes = make_ctx("acme", "nora", permissions=[g for g in GRANTS if not g.startswith("crm:note")])
        assert by_ref(await env.svc.connections(no_notes, "Place", place["name"])) == {("Visit", "place", None): 1}

    async def test_documents_outside_the_read_scope_are_not_counted(self, env, db, tables):
        from xcore_doctype.document import DocumentService
        from xcore_doctype.hooks import HookRegistry
        from xcore_doctype.registry import DocTypeRegistry
        from xcore_doctype.schema import parse_definition

        reg = DocTypeRegistry()
        reg.register(parse_definition({"schema_version": 1, "name": "Client", "module": "crm", "fields": [{"fieldname": "n", "fieldtype": "Text"}]}))
        reg.register(parse_definition({
            "schema_version": 1, "name": "Deal", "module": "crm", "fields": [{"fieldname": "client", "fieldtype": "Link", "options": "Client"}],
            "permissions": {"read": [{"grant": "crm:deal:read", "if_owner": True}], "write": ["crm:deal:write"], "create": ["crm:deal:write"]},
        }))
        reg.validate_links()
        svc = DocumentService(reg, db, tables, type("L", (), {"info": lambda *a, **k: None, "error": lambda *a, **k: None})(), hooks=HookRegistry())
        grants = ["crm:client:read", "crm:client:write", "crm:deal:read", "crm:deal:write"]
        ada, bob = make_ctx("acme", "ada", permissions=grants), make_ctx("acme", "bob", permissions=grants)
        client = await svc.create(ada, "Client", {"n": "x"})
        await svc.create(ada, "Deal", {"client": client["name"]})
        await svc.create(ada, "Deal", {"client": client["name"]})
        await svc.create(bob, "Deal", {"client": client["name"]})
        assert by_ref(await svc.connections(ada, "Client", client["name"])) == {("Deal", "client", None): 2}
        assert by_ref(await svc.connections(bob, "Client", client["name"])) == {("Deal", "client", None): 1}

    async def test_the_document_must_be_readable_and_belong_to_the_organisation(self, env):
        place = await env.make("Place", label="Paris")
        with pytest.raises(NotFound):
            await env.svc.connections(GLOBEX, "Place", place["name"])
        with pytest.raises(NotFound):
            await env.svc.connections(ALICE, "Place", "absent")
        blind = make_ctx("acme", "bea", permissions=["crm:visit:read"])
        with pytest.raises(PermissionDenied):
            await env.svc.connections(blind, "Place", place["name"])

    async def test_other_organisations_documents_are_never_counted(self, env):
        place = await env.make("Place", label="Paris")
        foreign = await env.make("Place", ctx=GLOBEX, label="Paris")
        await env.make("Visit", ctx=GLOBEX, place=foreign["name"])
        assert by_ref(await env.svc.connections(ALICE, "Place", place["name"])) == {}
        assert by_ref(await env.svc.connections(GLOBEX, "Place", foreign["name"])) == {("Visit", "place", None): 1}


class TestBulk:
    async def _contacts(self, env, n=3):
        return [await env.make("Contact", email=f"c{i}@x.fr", code=i) for i in range(n)]

    async def test_update_sets_the_fields_on_each_document(self, env):
        docs = await self._contacts(env)
        out = await env.svc.bulk(ALICE, "Contact", "update", [d["name"] for d in docs], data={"badge": "VIP"})
        assert (out["done"], out["failed"]) == (3, 0) and all(r["ok"] for r in out["results"])
        for d in docs:
            got = await env.svc.get(ALICE, "Contact", d["name"])
            assert got["data"]["badge"] == "VIP" and got["data"]["email"] == d["data"]["email"] and got["version"] == d["version"] + 1

    async def test_one_failure_does_not_hold_the_others_back(self, env):
        docs = await self._contacts(env)
        await env.svc.update(ALICE, "Contact", docs[1]["name"], {**docs[1]["data"], "badge": "OLD"}, docs[1]["version"])  # set_only_once : figé
        out = await env.svc.bulk(ALICE, "Contact", "update", [docs[0]["name"], docs[1]["name"], "absent", docs[2]["name"]], data={"badge": "NEW"})
        by_name = {r["name"]: r for r in out["results"]}
        assert (out["done"], out["failed"]) == (2, 2)
        assert by_name[docs[0]["name"]]["ok"] and by_name[docs[2]["name"]]["ok"]
        assert by_name[docs[1]["name"]]["code"] == "validation_failed" and by_name[docs[1]["name"]]["details"][0]["type"] == "set_only_once"
        assert by_name["absent"]["code"] == "document_not_found"
        assert (await env.svc.get(ALICE, "Contact", docs[1]["name"]))["data"]["badge"] == "OLD"

    async def test_a_unique_value_cannot_be_given_to_two_documents(self, env):
        docs = await self._contacts(env, 2)
        out = await env.svc.bulk(ALICE, "Contact", "update", [d["name"] for d in docs], data={"email": "same@x.fr"})
        assert (out["done"], out["failed"]) == (1, 1) and out["results"][1]["details"][0]["type"] == "unique"

    async def test_delete_removes_drafts_and_refuses_the_cited(self, env):
        a, b = await env.make("Place", label="A"), await env.make("Place", label="B")
        await env.make("Visit", place=b["name"])
        out = await env.svc.bulk(ALICE, "Place", "delete", [a["name"], b["name"]])
        assert [(r["ok"], r.get("code")) for r in out["results"]] == [(True, None), (False, "document_in_use")]
        with pytest.raises(NotFound):
            await env.svc.get(ALICE, "Place", a["name"])

    async def test_submit_then_cancel(self, env):
        orders = [await env.make("Order", title=f"O{i}", ref=f"R{i}") for i in range(2)]
        names = [o["name"] for o in orders]
        out = await env.svc.bulk(ALICE, "Order", "submit", names)
        assert out["done"] == 2
        assert [(await env.svc.get(ALICE, "Order", n))["docstatus"] for n in names] == [1, 1]
        again = await env.svc.bulk(ALICE, "Order", "submit", names)  # déjà soumis : refusé pour chacun, rien d'autre ne casse
        assert again["failed"] == 2 and {r["code"] for r in again["results"]} == {"invalid_docstatus"}
        assert (await env.svc.bulk(ALICE, "Order", "cancel", names))["done"] == 2

    async def test_the_right_to_do_it_is_asked_once_up_front(self, env):
        docs = await self._contacts(env, 2)
        reader = make_ctx("acme", "rita", permissions=["crm:contact:read"])
        for action, data in (("update", {"badge": "x"}), ("delete", None)):
            with pytest.raises(PermissionDenied):
                await env.svc.bulk(reader, "Contact", action, [d["name"] for d in docs], data=data)
        with pytest.raises(ValidationFailed) as e:  # Contact n'est pas soumettable
            await env.svc.bulk(ALICE, "Contact", "submit", [d["name"] for d in docs])
        assert e.value.code == "not_submittable"

    async def test_what_cannot_be_set_in_bulk_is_refused_before_touching_anything(self, env):
        docs = await self._contacts(env, 1)
        for data in ({"nope": 1}, {}, "x", None):
            with pytest.raises(ValidationFailed):
                await env.svc.bulk(ALICE, "Contact", "update", [docs[0]["name"]], data=data)
        for data in ({"items": []},):  # un tableau (Invoice)
            invoice = await env.make("Company", title="Acme")
            with pytest.raises(ValidationFailed) as e:
                await env.svc.bulk(ALICE, "Invoice", "update", [invoice["name"]], data=data)
            assert e.value.details[0]["type"] == "bulk_field"
        with pytest.raises(ValidationFailed) as e:  # un champ rempli par fetch_from
            await env.svc.bulk(ALICE, "Invoice", "update", ["x"], data={"company_title": "Faux"})
        assert e.value.details[0]["type"] == "bulk_field"

    async def test_bad_requests(self, env):
        docs = await self._contacts(env, 1)
        n = docs[0]["name"]
        for action, names in (("explode", [n]), ("update", []), ("update", "x"), ("update", [n, 3]), ("update", [f"n{i}" for i in range(101)])):
            with pytest.raises(ValidationFailed):
                await env.svc.bulk(ALICE, "Contact", action, names, data={"badge": "x"})
        with pytest.raises(NotFound):
            await env.svc.bulk(ALICE, "Nope", "delete", [n])

    async def test_duplicates_in_the_list_are_processed_once_and_organisations_stay_apart(self, env):
        docs = await self._contacts(env, 1)
        n = docs[0]["name"]
        out = await env.svc.bulk(ALICE, "Contact", "update", [n, n], data={"badge": "x"})
        assert len(out["results"]) == 1
        foreign = await env.svc.bulk(GLOBEX, "Contact", "update", [n], data={"badge": "y"})
        assert foreign["results"][0]["code"] == "document_not_found"
        assert (await env.svc.get(ALICE, "Contact", n))["data"]["badge"] == "x"

    async def test_a_hook_refusal_is_reported_per_document(self, env):
        tasks = [await env.make("Task", title=f"T{i}") for i in range(2)]
        env.refuse["on_trash"] = "pas ce document"
        out = await env.svc.bulk(ALICE, "Task", "delete", [t["name"] for t in tasks])
        assert out["failed"] == 2 and {r["code"] for r in out["results"]} == {"hook_rejected"}
        assert out["results"][0]["message"] == "pas ce document"
        assert (await env.svc.list(ALICE, "Task", limit=10))["total"] == 2
