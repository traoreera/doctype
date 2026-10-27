"""« Créer depuis » contre une vraie base : le reste à faire, les droits, l'état de la source (doc/mapper.md §3/§5).

La logique de recopie est testée à part, sur des dictionnaires (`test_mapping.py`). Ce qui se joue ICI, et qui
exige la base : retrouver ce qui a déjà été émis pour une source, et ne jamais l'ignorer.
"""

from __future__ import annotations

import pytest

from tests.conftest import make_ctx
from xcore_doctype.document import DocumentService
from xcore_doctype.errors import InvalidDocStatus, NotFound, PermissionDenied, ValidationFailed
from xcore_doctype.registry import DocTypeRegistry
from xcore_doctype.schema import parse_definition

ORDER_LINE = {
    "schema_version": 1, "name": "Order Line", "istable": True,
    "fields": [{"fieldname": "item", "fieldtype": "Text"}, {"fieldname": "qty", "fieldtype": "Number"}],
}
MOVE_LINE = {
    "schema_version": 1, "name": "Move Line", "istable": True,
    "fields": [
        {"fieldname": "item", "fieldtype": "Text"},
        {"fieldname": "qty", "fieldtype": "Number"},
        {"fieldname": "src_row", "fieldtype": "Text", "read_only": True},
        {"fieldname": "src_doc", "fieldtype": "Link", "options": "Order", "read_only": True},
    ],
}
ORDER = {
    "schema_version": 1, "name": "Order", "module": "sales", "submittable": True,
    "fields": [
        {"fieldname": "client", "fieldtype": "Text"},
        {"fieldname": "items", "fieldtype": "Table", "options": "Order Line"},
    ],
}
MOVE = {
    "schema_version": 1, "name": "Move", "module": "sales",
    "fields": [
        {"fieldname": "client", "fieldtype": "Text"},
        {"fieldname": "items", "fieldtype": "Table", "options": "Move Line"},
    ],
    "created_from": [{
        "source": "Order", "label": "Mouvement",
        "lines": {"items": {"from": "items", "reference": "src_row", "document": "src_doc",
                            "quantity": {"source": "qty", "target": "qty"}}},
    }],
}

ALL = ["sales:order:read", "sales:order:write", "sales:order:submit", "sales:move:read", "sales:move:write", "sales:move:delete"]
ALICE = make_ctx("acme", "alice", permissions=ALL)


def why(excinfo) -> str:
    """Le message du refus : `ValidationFailed` porte « validation_failed », le détail est dans `details`."""
    return " ".join(d["msg"] for d in excinfo.value.details)


class Log:
    def info(self, *a, **k): pass
    def warning(self, *a, **k): pass
    def error(self, *a, **k): pass


@pytest.fixture
async def svc(db, tables):
    reg = DocTypeRegistry()
    for raw in (ORDER_LINE, MOVE_LINE, ORDER, MOVE):
        reg.register(parse_definition(raw))
    reg.validate_links()
    return DocumentService(reg, db, tables, Log())


async def submitted_order(svc, rows=((("A", 120.0),))):
    doc = await svc.create(ALICE, "Order", {"client": "C1", "items": [{"item": i, "qty": q} for i, q in rows]})
    await svc.submit(ALICE, "Order", doc["name"], doc["version"])
    return doc["name"]


class TestWhatIsStillToDo:
    async def test_a_fresh_order_prefills_in_full(self, svc):
        name = await submitted_order(svc)
        out = await svc.mapped_values(ALICE, "Move", "Order", name)
        assert out["values"]["client"] == "C1"
        assert out["values"]["items"] == [{"item": "A", "qty": 120.0, "src_row": "0", "src_doc": name}]

    async def test_an_already_served_quantity_is_deducted(self, svc):
        """Le cœur du sujet : 120 commandés, 100 servis, 20 préremplis."""
        name = await submitted_order(svc)
        await svc.create(ALICE, "Move", {"items": [{"item": "A", "qty": 100.0, "src_row": "0", "src_doc": name}]})
        out = await svc.mapped_values(ALICE, "Move", "Order", name)
        assert out["values"]["items"] == [{"item": "A", "qty": 20.0, "src_row": "0", "src_doc": name}]

    async def test_several_movements_add_up(self, svc):
        name = await submitted_order(svc)
        for served in (60.0, 50.0):
            await svc.create(ALICE, "Move", {"items": [{"item": "A", "qty": served, "src_row": "0", "src_doc": name}]})
        out = await svc.mapped_values(ALICE, "Move", "Order", name)
        assert out["values"]["items"][0]["qty"] == 10.0

    async def test_a_settled_order_offers_nothing_and_no_button(self, svc):
        name = await submitted_order(svc)
        await svc.create(ALICE, "Move", {"items": [{"item": "A", "qty": 120.0, "src_row": "0", "src_doc": name}]})
        assert (await svc.mapped_values(ALICE, "Move", "Order", name))["values"]["items"] == []
        # Et le bouton disparaît : c'est CE point qui empêche le double transfert.
        assert (await svc.available_mappings(ALICE, "Order", name))["mappings"] == []

    async def test_a_movement_for_another_order_does_not_count(self, svc):
        first, second = await submitted_order(svc), await submitted_order(svc)
        await svc.create(ALICE, "Move", {"items": [{"item": "A", "qty": 120.0, "src_row": "0", "src_doc": second}]})
        out = await svc.mapped_values(ALICE, "Move", "Order", first)
        assert out["values"]["items"][0]["qty"] == 120.0

    async def test_only_the_settled_line_drops_out(self, svc):
        name = await submitted_order(svc, rows=(("A", 10.0), ("B", 5.0)))
        await svc.create(ALICE, "Move", {"items": [{"item": "A", "qty": 10.0, "src_row": "0", "src_doc": name}]})
        rows = (await svc.mapped_values(ALICE, "Move", "Order", name))["values"]["items"]
        assert [(r["item"], r["src_row"]) for r in rows] == [("B", "1")]


class TestDraftsAndCancellations:
    async def test_a_draft_movement_already_counts(self, svc):
        """Un brouillon est du travail engagé : l'ignorer préremplirait deux fois la même quantité."""
        name = await submitted_order(svc)
        await svc.create(ALICE, "Move", {"items": [{"item": "A", "qty": 100.0, "src_row": "0", "src_doc": name}]})
        assert (await svc.mapped_values(ALICE, "Move", "Order", name))["values"]["items"][0]["qty"] == 20.0

    async def test_deleting_the_draft_gives_the_quantity_back(self, svc):
        name = await submitted_order(svc)
        move = await svc.create(ALICE, "Move", {"items": [{"item": "A", "qty": 100.0, "src_row": "0", "src_doc": name}]})
        await svc.delete(ALICE, "Move", move["name"])
        assert (await svc.mapped_values(ALICE, "Move", "Order", name))["values"]["items"][0]["qty"] == 120.0


class TestRightsAndState:
    async def test_a_draft_source_cannot_be_mapped(self, svc):
        """`source_status: submitted` par défaut : on ne transfère pas une commande non soumise."""
        doc = await svc.create(ALICE, "Order", {"client": "C1", "items": [{"item": "A", "qty": 1.0}]})
        with pytest.raises(InvalidDocStatus):
            await svc.mapped_values(ALICE, "Move", "Order", doc["name"])
        assert (await svc.available_mappings(ALICE, "Order", doc["name"]))["mappings"] == []

    async def test_it_takes_the_right_to_create_the_target(self, svc):
        name = await submitted_order(svc)
        reader = make_ctx("acme", "bob", permissions=["sales:order:read"])
        with pytest.raises(PermissionDenied):
            await svc.mapped_values(reader, "Move", "Order", name)
        # Et le menu ne le propose pas plutôt que de refuser : un bouton ne s'affiche jamais en vain.
        assert (await svc.available_mappings(reader, "Order", name))["mappings"] == []

    async def test_it_takes_the_right_to_read_the_source(self, svc):
        name = await submitted_order(svc)
        creator = make_ctx("acme", "bob", permissions=["sales:move:write", "sales:move:read"])
        with pytest.raises(NotFound):
            await svc.mapped_values(creator, "Move", "Order", name)

    async def test_another_tenant_sees_nothing(self, svc):
        name = await submitted_order(svc)
        with pytest.raises(NotFound):
            await svc.mapped_values(make_ctx("other", "eve", permissions=ALL), "Move", "Order", name)

    async def test_a_type_that_declares_no_mapping_is_refused(self, svc):
        name = await submitted_order(svc)
        with pytest.raises(NotFound, match="mapping_not_found|ne se crée pas"):
            await svc.mapped_values(ALICE, "Order", "Order", name)

    async def test_the_menu_names_the_target_and_its_label(self, svc):
        name = await submitted_order(svc)
        assert (await svc.available_mappings(ALICE, "Order", name))["mappings"] == [
            {"doctype": "Move", "label": "Mouvement"}
        ]


class TestProvenanceIsVerified:
    """§4 : la provenance qu'une ligne revendique doit être vraie, sinon le reste à faire ne vaut rien.

    `read_only` ne protège RIEN dans une table (mesuré : il n'est appliqué qu'aux champs de haut niveau, et
    seulement contre la valeur stockée). C'est donc ce contrôle, et lui seul, qui empêche un client d'inventer
    une référence pour faire baisser la quantité restante d'une commande.
    """

    async def test_a_row_without_provenance_is_fine(self, svc):
        """Une ligne saisie à la main ne revendique rien : on ne lui demande rien."""
        await svc.create(ALICE, "Move", {"items": [{"item": "A", "qty": 1.0}]})

    async def test_an_index_that_does_not_exist_is_refused(self, svc):
        name = await submitted_order(svc)
        with pytest.raises(ValidationFailed) as e:
            await svc.create(ALICE, "Move", {"items": [{"item": "A", "qty": 1.0, "src_row": "7", "src_doc": name}]})
        assert [d["type"] for d in e.value.details] == ["mapping_reference"]
        assert e.value.details[0]["field"] == "items.0.src_row"

    async def test_a_source_that_does_not_exist_is_refused(self, svc):
        with pytest.raises(ValidationFailed) as e:
            await svc.create(ALICE, "Move", {"items": [{"item": "A", "qty": 1.0, "src_row": "0", "src_doc": "SO-nope"}]})
        assert "introuvable" in why(e)

    async def test_a_draft_source_cannot_be_claimed(self, svc):
        """La référence n'est stable que sur un document FIGÉ : un brouillon peut encore perdre la ligne 0."""
        doc = await svc.create(ALICE, "Order", {"client": "C1", "items": [{"item": "A", "qty": 1.0}]})
        with pytest.raises(ValidationFailed) as e:
            await svc.create(ALICE, "Move", {"items": [{"item": "A", "qty": 1.0, "src_row": "0", "src_doc": doc["name"]}]})
        assert e.value.details[0]["type"] == "mapping_reference"

    @pytest.mark.parametrize("ref", ["", "abc", "-1", "1.5"])
    async def test_a_reference_that_is_not_an_index_is_refused(self, svc, ref):
        name = await submitted_order(svc)
        with pytest.raises(ValidationFailed):
            await svc.create(ALICE, "Move", {"items": [{"item": "A", "qty": 1.0, "src_row": ref, "src_doc": name}]})

    async def test_half_a_provenance_is_refused(self, svc):
        """Une référence sans document ne désigne rien : l'indice 0 de quoi ?"""
        await submitted_order(svc)
        with pytest.raises(ValidationFailed) as e:
            await svc.create(ALICE, "Move", {"items": [{"item": "A", "qty": 1.0, "src_row": "0"}]})
        assert "ensemble" in why(e)

    async def test_someone_who_cannot_read_the_source_cannot_claim_it(self, svc):
        """« Introuvable » couvre « illisible » : on ne révèle pas l'existence d'un document hors périmètre."""
        name = await submitted_order(svc)
        creator = make_ctx("acme", "bob", permissions=["sales:move:write", "sales:move:read"])
        with pytest.raises(ValidationFailed) as e:
            await svc.create(creator, "Move", {"items": [{"item": "A", "qty": 1.0, "src_row": "0", "src_doc": name}]})
        assert "introuvable" in why(e)

    async def test_another_tenants_order_cannot_be_claimed(self, svc):
        name = await submitted_order(svc)
        eve = make_ctx("other", "eve", permissions=ALL)
        with pytest.raises(ValidationFailed) as e:
            await svc.create(eve, "Move", {"items": [{"item": "A", "qty": 1.0, "src_row": "0", "src_doc": name}]})
        assert "introuvable" in why(e)

    async def test_a_provenance_already_there_is_not_judged_again(self, svc):
        """Même règle qu'un Link : une source devenue illisible n'empêche pas de corriger autre chose."""
        name = await submitted_order(svc)
        move = await svc.create(ALICE, "Move", {"items": [{"item": "A", "qty": 10.0, "src_row": "0", "src_doc": name}]})
        await svc.update(ALICE, "Move", move["name"], {"client": "autre", "items": move["data"]["items"]}, move["version"])

    async def test_a_forged_reference_added_later_is_refused(self, svc):
        name = await submitted_order(svc)
        move = await svc.create(ALICE, "Move", {"items": [{"item": "A", "qty": 10.0, "src_row": "0", "src_doc": name}]})
        rows = [*move["data"]["items"], {"item": "B", "qty": 1.0, "src_row": "9", "src_doc": name}]
        with pytest.raises(ValidationFailed) as e:
            await svc.update(ALICE, "Move", move["name"], {"items": rows}, move["version"])
        assert e.value.details[0]["field"] == "items.1.src_row"


class TestTheRemainderIgnoresTheReadScope:
    async def test_a_movement_the_caller_cannot_see_still_counts(self, svc, db, tables):
        """Sinon il préremplirait — donc livrerait — deux fois. Même raison que `references_to` pour l'intégrité.

        Rien du document invisible ne ressort : l'appelant n'obtient qu'une quantité.
        """
        name = await submitted_order(svc)
        other = make_ctx("acme", "bob", permissions=ALL)
        await svc.create(other, "Move", {"items": [{"item": "A", "qty": 100.0, "src_row": "0", "src_doc": name}]})
        # alice ne lit que ses propres Move (`if_owner`), mais la quantité de bob compte quand même.
        restricted = make_ctx("acme", "alice", permissions=["sales:order:read", "sales:move:write"])
        out = await svc.mapped_values(restricted, "Move", "Order", name)
        assert out["values"]["items"][0]["qty"] == 20.0
