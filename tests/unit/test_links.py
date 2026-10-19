"""Champs Link complets (doc/doctype.md §7.2) : la cible existe et est lisible à l'écriture, et reste là tant qu'on la cite."""

from __future__ import annotations

import asyncio

import pytest

from tests.conftest import make_ctx
from xcore_doctype.document import DocumentService
from xcore_doctype.errors import DocumentInUse, InvalidDefinition, ValidationFailed
from xcore_doctype.query import Query
from xcore_doctype.registry import DocTypeRegistry
from xcore_doctype.schema import parse_definition

COUNTRY = {
    "schema_version": 1,
    "name": "Country",
    "label": "Pays",
    "module": "sales",
    "title_field": "country_name",
    "fields": [{"fieldname": "country_name", "fieldtype": "Text", "required": True}],
}
CLIENT = {
    "schema_version": 1,
    "name": "Client",
    "label": "Client",
    "module": "sales",
    "fields": [
        {"fieldname": "client_name", "fieldtype": "Text", "required": True},
        {"fieldname": "country", "fieldtype": "Link", "options": "Country", "label": "Pays"},
        {"fieldname": "billing_country", "fieldtype": "Link", "options": "Country"},
        {"fieldname": "parent", "fieldtype": "Link", "options": "Client", "label": "Maison mère"},
    ],
}
ALL = ["sales:country:read", "sales:country:write", "sales:country:delete",
       "sales:client:read", "sales:client:write", "sales:client:delete"]


class Log:
    def info(self, *a, **k): ...
    def error(self, *a, **k): ...


@pytest.fixture
def registry():
    reg = DocTypeRegistry()
    reg.register(parse_definition(COUNTRY))
    reg.register(parse_definition(CLIENT))
    return reg


@pytest.fixture
def svc(db, tables, registry):
    return DocumentService(registry, db, tables, Log())


def ctx(tenant="acme", user="alice", perms=ALL):
    return make_ctx(tenant, user, permissions=perms)


async def country(svc, name="France", **kw):
    return (await svc.create(kw.pop("ctx", ctx()), "Country", {"country_name": name}))["name"]


async def client(svc, **data):
    c = data.pop("ctx", ctx())
    return await svc.create(c, "Client", {"client_name": "ACME", **data})


class TestWrite:
    async def test_an_existing_target_is_accepted(self, svc):
        fr = await country(svc)
        doc = await client(svc, country=fr)
        assert doc["data"]["country"] == fr

    async def test_a_missing_target_is_refused_with_the_field_named(self, svc):
        with pytest.raises(ValidationFailed) as e:
            await client(svc, country="nope")
        assert e.value.status == 422
        assert e.value.details == [{"field": "country", "msg": "Pays introuvable : 'nope'", "type": "link_not_found"}]
        assert (await svc.list(ctx(), "Client"))["total"] == 0  # rien n'a été écrit

    async def test_every_bad_field_is_reported_at_once(self, svc):
        with pytest.raises(ValidationFailed) as e:
            await client(svc, country="a", billing_country="b", parent="c")
        assert sorted(d["field"] for d in e.value.details) == ["billing_country", "country", "parent"]

    async def test_the_same_missing_value_in_two_fields_is_reported_for_both(self, svc):
        with pytest.raises(ValidationFailed) as e:
            await client(svc, country="x", billing_country="x")
        assert sorted(d["field"] for d in e.value.details) == ["billing_country", "country"]

    async def test_an_unreadable_target_looks_exactly_like_a_missing_one(self, svc):
        fr = await country(svc)
        no_country_read = ctx(perms=["sales:client:read", "sales:client:write"])
        with pytest.raises(ValidationFailed) as unreadable:
            await client(svc, ctx=no_country_read, country=fr)
        with pytest.raises(ValidationFailed) as missing:
            await client(svc, ctx=no_country_read, country="zzz-not-there")
        # Aucun indice sur l'existence : mêmes champ, même type, même forme de message.
        assert unreadable.value.details[0]["type"] == missing.value.details[0]["type"] == "link_not_found"
        assert unreadable.value.details[0]["msg"].split(":")[0] == missing.value.details[0]["msg"].split(":")[0]

    async def test_a_target_outside_the_row_scope_is_not_linkable(self, db, tables):
        reg = DocTypeRegistry()
        reg.register(parse_definition({**COUNTRY, "permissions": {
            "read": [{"grant": "sales:country:own", "if_owner": True}, "sales:country:read"],
            "write": ["sales:country:write"], "create": ["sales:country:write"],
        }}))
        reg.register(parse_definition(CLIENT))
        s = DocumentService(reg, db, tables, Log())
        mine_perms = ["sales:country:own", "sales:country:write", "sales:client:read", "sales:client:write"]
        alice, bob = ctx(perms=mine_perms), ctx(user="bob", perms=mine_perms)
        fr = await country(s, ctx=alice)
        assert (await client(s, ctx=alice, country=fr))["data"]["country"] == fr  # son propre pays
        with pytest.raises(ValidationFailed):  # le pays d'alice n'est pas lisible par bob
            await client(s, ctx=bob, country=fr)

    async def test_a_target_of_another_tenant_does_not_exist(self, svc):
        fr_globex = await country(svc, ctx=ctx("globex", "bob"))
        with pytest.raises(ValidationFailed):
            await client(svc, country=fr_globex)  # acme n'a pas ce pays

    async def test_the_system_context_still_needs_the_target_to_exist(self, svc):
        from xcore_doctype.context import RequestContext

        system = RequestContext.system("acme", "import")
        with pytest.raises(ValidationFailed):
            await client(svc, ctx=system, country="nope")
        fr = await country(svc, ctx=system)
        assert (await client(svc, ctx=system, country=fr))["data"]["country"] == fr


class TestUpdate:
    async def test_a_changed_link_is_checked(self, svc):
        fr, de = await country(svc, "France"), await country(svc, "Allemagne")
        doc = await client(svc, country=fr)
        ok = await svc.update(ctx(), "Client", doc["name"], {"client_name": "ACME", "country": de}, doc["version"])
        assert ok["data"]["country"] == de
        with pytest.raises(ValidationFailed):
            await svc.update(ctx(), "Client", doc["name"], {"client_name": "ACME", "country": "nope"}, ok["version"])

    async def test_an_unchanged_link_is_not_rechecked_and_a_cleared_one_is_fine(self, svc, db, tables):
        fr = await country(svc)
        doc = await client(svc, country=fr)
        # Le pays disparaît par un chemin qui contourne le service (import, ancienne donnée) : le lien devient orphelin.
        async with db.session() as s:
            await Query(s, ctx(), tables).delete("Country", fr, scope=__import__("xcore_doctype.permissions", fromlist=["Scope"]).Scope.everything("delete"))
        renamed = await svc.update(ctx(), "Client", doc["name"], {"client_name": "ACME 2", "country": fr}, doc["version"])
        assert renamed["data"]["client_name"] == "ACME 2"  # corriger autre chose reste possible
        cleared = await svc.update(ctx(), "Client", doc["name"], {"client_name": "ACME 2"}, renamed["version"])
        assert "country" not in cleared["data"]


class TestDelete:
    async def test_a_referenced_document_cannot_be_deleted(self, svc):
        fr = await country(svc)
        doc = await client(svc, country=fr)
        with pytest.raises(DocumentInUse) as e:
            await svc.delete(ctx(), "Country", fr)
        assert (e.value.status, e.value.code) == (409, "document_in_use")
        assert "Client" in e.value.message and "Pays" in e.value.message
        assert e.value.details == [{"doctype": "Client", "field": "country"}]
        assert doc["name"] not in e.value.message  # jamais le nom d'un document que l'appelant ne peut peut-être pas lire
        assert (await svc.get(ctx(), "Country", fr))["name"] == fr  # toujours là

    async def test_the_message_names_the_document_by_its_title_not_its_identifier(self, svc):
        """« Pays « France » est utilisé par Client », pas « Pays 'a1b2c3d4e5f6' » : l'utilisateur ne connaît pas l'identifiant."""
        fr = await country(svc)  # son champ titre est country_name
        await client(svc, country=fr)
        with pytest.raises(DocumentInUse) as e:
            await svc.delete(ctx(), "Country", fr)
        assert "Pays « " in e.value.message and fr not in e.value.message

    async def test_it_can_be_deleted_once_nothing_cites_it(self, svc):
        fr = await country(svc)
        doc = await client(svc, country=fr)
        await svc.update(ctx(), "Client", doc["name"], {"client_name": "ACME"}, doc["version"])  # le lien est retiré
        await svc.delete(ctx(), "Country", fr)
        deleted = await client(svc)
        await svc.delete(ctx(), "Client", deleted["name"])

    async def test_a_reference_the_caller_cannot_read_still_blocks(self, svc):
        fr = await country(svc)
        await client(svc, country=fr)
        only_countries = ctx(user="carol", perms=["sales:country:read", "sales:country:delete"])
        with pytest.raises(DocumentInUse):
            await svc.delete(only_countries, "Country", fr)

    async def test_references_of_another_tenant_do_not_block(self, svc):
        fr_acme = await country(svc, ctx=ctx("acme"))
        fr_globex = await country(svc, ctx=ctx("globex", "bob"))
        await client(svc, ctx=ctx("globex", "bob"), country=fr_globex)
        await svc.delete(ctx("acme"), "Country", fr_acme)  # rien chez acme ne le cite
        with pytest.raises(DocumentInUse):
            await svc.delete(ctx("globex", "bob"), "Country", fr_globex)

    async def test_a_document_citing_itself_is_not_blocked_by_itself(self, svc):
        a = await client(svc)
        b = await client(svc, parent=a["name"])
        with pytest.raises(DocumentInUse):
            await svc.delete(ctx(), "Client", a["name"])  # b le cite
        await svc.delete(ctx(), "Client", b["name"])
        await svc.delete(ctx(), "Client", a["name"])

    async def test_a_failed_delete_leaves_the_document_alone(self, svc):
        fr = await country(svc)
        await client(svc, country=fr)
        for _ in range(2):
            with pytest.raises(DocumentInUse):
                await svc.delete(ctx(), "Country", fr)
        assert (await svc.list(ctx(), "Country"))["total"] == 1


class TestRace:
    """Pas de clé étrangère : l'intégrité tient par des verrous de ligne (`FOR KEY SHARE` à l'écriture, `FOR UPDATE` à la
    suppression). SQLite n'a pas de verrou de ligne : la fenêtre entre le contrôle et la suppression y est documentée
    (phase3.md), les entrelacements précis ne sont donc exigés que sur PostgreSQL."""

    @staticmethod
    def _need_row_locks(db):
        if db.backend != "postgres":
            pytest.skip("SQLite n'a pas de verrou de ligne : fenêtre documentée, voir doc/phase3.md")

    async def test_a_deletion_waits_for_a_link_being_written_then_sees_it(self, svc, db, monkeypatch):
        self._need_row_locks(db)
        fr = await country(svc)
        linked, proceed = asyncio.Event(), asyncio.Event()
        real_insert = Query.insert

        async def paused_insert(self, doctype, name, data, **kw):
            if doctype == "Client":
                linked.set()  # le lien vient d'être contrôlé (et verrouillé), le client n'est pas encore écrit
                await proceed.wait()
            return await real_insert(self, doctype, name, data, **kw)

        monkeypatch.setattr(Query, "insert", paused_insert)
        writer = asyncio.create_task(client(svc, country=fr))
        deleter = None
        try:
            await asyncio.wait_for(linked.wait(), 5)
            deleter = asyncio.create_task(svc.delete(ctx(), "Country", fr))
            await asyncio.sleep(0.5)
            assert not deleter.done(), "la suppression aurait dû attendre le verrou pris par le contrôle du lien"
        finally:
            proceed.set()  # même en cas d'échec : une transaction restée ouverte bloquerait le nettoyage de la base
        await writer  # le client est écrit et validé
        with pytest.raises(DocumentInUse):
            await deleter  # la suppression reprend, voit le client, et refuse
        assert (await svc.get(ctx(), "Country", fr))["name"] == fr

    async def test_a_link_waits_for_a_deletion_in_progress_then_finds_the_target_gone(self, svc, db, monkeypatch):
        self._need_row_locks(db)
        fr = await country(svc)
        locked, proceed = asyncio.Event(), asyncio.Event()
        real_delete = Query.delete

        async def paused_delete(self, doctype, name, *, scope):
            if doctype == "Country":
                locked.set()  # la ligne est verrouillée et sans référence : on suspend juste avant de la supprimer
                await proceed.wait()
            return await real_delete(self, doctype, name, scope=scope)

        monkeypatch.setattr(Query, "delete", paused_delete)
        deleter = asyncio.create_task(svc.delete(ctx(), "Country", fr))
        writer = None
        try:
            await asyncio.wait_for(locked.wait(), 5)
            writer = asyncio.create_task(client(svc, country=fr))
            await asyncio.sleep(0.5)
            assert not writer.done(), "le contrôle du lien aurait dû attendre la fin de la suppression"
        finally:
            proceed.set()
        await deleter
        with pytest.raises(ValidationFailed) as e:
            await writer
        assert e.value.details[0]["type"] == "link_not_found"
        assert (await svc.list(ctx(), "Client"))["total"] == 0  # aucun lien orphelin

    async def test_linking_and_deleting_at_once_only_ever_fails_cleanly(self, svc, db):
        """Sans orchestration : chaque issue est un refus propre, jamais une erreur inattendue (verrou SQLite, 500…)."""
        clean = (ValidationFailed, DocumentInUse)
        orphans = 0
        for i in range(12):
            fr = await country(svc, f"c{i}")
            created, deleted = await asyncio.gather(
                client(svc, client_name=f"n{i}", country=fr), svc.delete(ctx(), "Country", fr), return_exceptions=True
            )
            for outcome in (created, deleted):
                assert not isinstance(outcome, BaseException) or isinstance(outcome, clean), repr(outcome)
            if not isinstance(created, BaseException) and not isinstance(deleted, BaseException):
                orphans += 1
        if db.backend == "postgres":
            assert orphans == 0


class TestRegistry:
    def test_an_unknown_target_fails_the_load(self):
        reg = DocTypeRegistry()
        reg.register(parse_definition(CLIENT))  # `Country` n'est pas chargé
        with pytest.raises(InvalidDefinition, match=r"Client\.country: DocType cible inconnu 'Country'"):
            reg.validate_links()

    def test_every_bad_link_is_listed(self):
        reg = DocTypeRegistry()
        reg.register(parse_definition(CLIENT))
        with pytest.raises(InvalidDefinition) as e:
            reg.validate_links()
        assert str(e.value).count("DocType cible inconnu") == 2  # country et billing_country ; `parent` vise Client

    def test_complete_registry_passes_and_lists_referrers(self, registry):
        registry.validate_links()
        refs = {(r.doctype, r.fieldname) for r in registry.referrers("Country")}
        assert refs == {("Client", "country"), ("Client", "billing_country")}
        assert {(r.doctype, r.fieldname) for r in registry.referrers("Client")} == {("Client", "parent")}
