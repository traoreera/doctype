"""État exigé de la cible d'un lien (`target_status`, D23) : un Link ou un DynamicLink peut refuser un document annulé, un brouillon…

Sans `target_status`, un lien ne regarde que l'existence et la lisibilité (test_links.py). Avec, l'état (`docstatus`) de la cible est
lu dans la même requête que son existence, et un état refusé échoue en `link_status` (422), distinct de `link_not_found` : la cible
existe, c'est son état qui gêne. Ce qui est défendu ici, chacun par un test qui casse sans la règle :
- chaque forme (`not_cancelled`, un état, une liste) admet exactement ses états, avec un message qui nomme l'état ;
- un document illisible reste « introuvable » : son état ne se devine pas ;
- une valeur inchangée n'est pas rejugée (la cible annulée depuis n'empêche pas de corriger autre chose) ;
- les DynamicLink et les lignes de table suivent la même règle, au même chemin d'erreur que les autres contrôles ; une seconde
  ligne qui cite une cible déjà citée est un lien nouveau ;
- la soumission rejuge tous les liens contraints (la cible a pu être annulée depuis), avec le périmètre de celui qui soumet ;
  l'amendement recopie ses liens sans juger l'état de leur cible (existence et lisibilité, si) ;
- une écriture d'un hook (`event.tx`) obéit à la même contrainte ;
- au chargement, viser un DocType jamais soumettable avec un état qu'il n'aura jamais est une erreur de définition.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from tests.conftest import make_ctx
from xcore_doctype.document import DocumentService
from xcore_doctype.errors import DoctypeError, DocumentInUse, InvalidDefinition, ValidationFailed
from xcore_doctype.hooks import HookRegistry
from xcore_doctype.query import CANCELLED, Query, StoredDoc
from xcore_doctype.registry import DocTypeChannel, DocTypeRegistry
from xcore_doctype.schema import parse_definition
from xcore_doctype.sources import DocumentSources, ReadOnlySource

# La cible soumettable. Lecture `if_owner` possible : celui qui n'a que `acct:invoice:own` ne voit que ses factures.
INVOICE = {
    "schema_version": 1,
    "name": "Invoice",
    "module": "acct",
    "submittable": True,
    "fields": [{"fieldname": "title", "fieldtype": "Text", "required": True}],
    "permissions": {
        "read": [{"grant": "acct:invoice:own", "if_owner": True}, "acct:invoice:read"],
        "write": ["acct:invoice:write"], "create": ["acct:invoice:write"], "delete": ["acct:invoice:delete"],
        "submit": ["acct:invoice:submit"], "cancel": ["acct:invoice:cancel"],
    },
}
# Une cible qui n'est JAMAIS soumettable : tous ses documents sont des brouillons.
# `naming: prompt` : une note peut porter le nom d'une facture (même nom, autre DocType : voir TestDynamicLinks).
NOTE = {"schema_version": 1, "name": "Note", "module": "acct", "naming": "prompt", "fields": [{"fieldname": "text", "fieldtype": "Text"}]}
LINE = {
    "schema_version": 1,
    "name": "Payment Line",
    "istable": True,
    "fields": [
        {"fieldname": "invoice", "fieldtype": "Link", "options": "Invoice", "target_status": "submitted"},
        {"fieldname": "ref_type", "fieldtype": "Select", "options": ["Invoice", "Note"]},
        {"fieldname": "ref", "fieldtype": "DynamicLink", "options": "ref_type", "target_status": "not_cancelled"},
    ],
}
PAYMENT = {
    "schema_version": 1,
    "name": "Payment",
    "module": "acct",
    "fields": [
        {"fieldname": "memo", "fieldtype": "Text"},
        {"fieldname": "invoice", "fieldtype": "Link", "options": "Invoice", "target_status": "not_cancelled"},
        {"fieldname": "settled", "fieldtype": "Link", "options": "Invoice", "target_status": "submitted"},
        {"fieldname": "archived", "fieldtype": "Link", "options": "Invoice", "target_status": ["submitted", "cancelled"]},
        {"fieldname": "loose", "fieldtype": "Link", "options": "Invoice"},  # sans contrainte : le comportement d'avant D23
        {"fieldname": "ref_type", "fieldtype": "Select", "options": ["Invoice", "Note"]},
        {"fieldname": "ref", "fieldtype": "DynamicLink", "options": "ref_type", "target_status": "not_cancelled"},
        {"fieldname": "lines", "fieldtype": "Table", "options": "Payment Line"},
    ],
}
GRANTS = [
    "acct:invoice:read", "acct:invoice:write", "acct:invoice:delete", "acct:invoice:submit", "acct:invoice:cancel",
    "acct:note:read", "acct:note:write", "acct:payment:read", "acct:payment:write",
]
ALICE = make_ctx("acme", "alice", permissions=GRANTS)
BOB = make_ctx("acme", "bob", permissions=GRANTS)
# Ne lit que SES factures (`if_owner`) ; écrit librement des paiements.
OWN_ONLY = make_ctx("acme", "olga", permissions=["acct:invoice:own", "acct:payment:read", "acct:payment:write", "acct:note:read"])
# Ne lit aucune facture.
BLIND = make_ctx("acme", "boris", permissions=["acct:payment:read", "acct:payment:write", "acct:note:read"])


class Log:
    def info(self, *a, **k): ...
    def error(self, *a, **k): ...


def registry(*defs: dict) -> DocTypeRegistry:
    reg = DocTypeRegistry()
    for d in defs:
        reg.register(parse_definition(d))
    return reg


@pytest.fixture
def svc(db, tables):
    reg = registry(INVOICE, NOTE, LINE, PAYMENT)
    reg.validate_links()
    return DocumentService(reg, db, tables, Log())


async def invoice(svc, state: str = "draft", ctx=ALICE) -> str:
    """Une facture dans l'état demandé : brouillon, soumise ou annulée."""
    doc = await svc.create(ctx, "Invoice", {"title": "F"})
    if state in ("submitted", "cancelled"):
        doc = await svc.submit(ctx, "Invoice", doc["name"], doc["version"])
    if state == "cancelled":
        doc = await svc.cancel(ctx, "Invoice", doc["name"], doc["version"])
    return doc["name"]


async def note(svc, name: str = "N-1") -> str:
    return (await svc.create(ALICE, "Note", {"text": "n"}, name=name))["name"]


async def refused(svc, data: dict, ctx=ALICE) -> list[dict]:
    with pytest.raises(ValidationFailed) as e:
        await svc.create(ctx, "Payment", data)
    assert e.value.status == 422
    return e.value.details


class TestStates:
    async def test_without_target_status_any_state_is_accepted(self, svc):
        for state in ("draft", "submitted", "cancelled"):
            doc = await svc.create(ALICE, "Payment", {"loose": await invoice(svc, state)})
            assert doc["data"]["loose"]

    async def test_not_cancelled_refuses_a_cancelled_target_and_names_its_state(self, svc):
        for state in ("draft", "submitted"):
            await svc.create(ALICE, "Payment", {"invoice": await invoice(svc, state)})
        gone = await invoice(svc, "cancelled")
        assert await refused(svc, {"invoice": gone}) == [
            {"field": "invoice", "msg": f"Invoice {gone!r} est annulé : ce lien n'accepte pas cet état", "type": "link_status"}
        ]
        assert (await svc.list(ALICE, "Payment"))["total"] == 2  # le refus n'a rien écrit

    async def test_a_single_state_admits_only_that_state(self, svc):
        await svc.create(ALICE, "Payment", {"settled": await invoice(svc, "submitted")})
        draft, gone = await invoice(svc, "draft"), await invoice(svc, "cancelled")
        assert await refused(svc, {"settled": draft}) == [
            {"field": "settled", "msg": f"Invoice {draft!r} est un brouillon : ce lien n'accepte pas cet état", "type": "link_status"}
        ]
        assert [d["type"] for d in await refused(svc, {"settled": gone})] == ["link_status"]

    async def test_a_list_admits_each_of_its_states_and_nothing_else(self, svc):
        for state in ("submitted", "cancelled"):
            await svc.create(ALICE, "Payment", {"archived": await invoice(svc, state)})
        draft = await invoice(svc, "draft")
        assert [(d["field"], d["type"]) for d in await refused(svc, {"archived": draft})] == [("archived", "link_status")]

    async def test_the_same_target_is_judged_per_field(self, svc):
        """Une même facture citée par deux champs : chacun applique SA contrainte, un seul refus."""
        draft = await invoice(svc, "draft")
        assert [d["field"] for d in await refused(svc, {"invoice": draft, "settled": draft, "loose": draft})] == ["settled"]

    async def test_a_missing_target_is_not_found_and_reported_with_the_wrong_states(self, svc):
        gone = await invoice(svc, "cancelled")
        details = await refused(svc, {"invoice": "nope", "settled": gone})
        assert sorted((d["field"], d["type"]) for d in details) == [("invoice", "link_not_found"), ("settled", "link_status")]


class TestReadability:
    async def test_an_unreadable_target_is_not_found_whatever_its_state(self, svc):
        """Sans droit de lire les factures : « introuvable », jamais « annulé » (l'état d'un document illisible ne se devine pas)."""
        gone = await invoice(svc, "cancelled")
        assert [d["type"] for d in await refused(svc, {"invoice": gone}, ctx=BLIND)] == ["link_not_found"]

    async def test_the_row_scope_hides_the_state_of_the_others_documents(self, svc):
        """`if_owner` : la facture annulée de Bob est introuvable pour Olga ; la sienne, annulée, est refusée pour son état."""
        theirs = await invoice(svc, "cancelled", ctx=BOB)
        mine = await invoice(svc, "cancelled", ctx=make_ctx("acme", "olga", permissions=GRANTS))  # Olga en est la propriétaire
        assert [d["type"] for d in await refused(svc, {"invoice": theirs}, ctx=OWN_ONLY)] == ["link_not_found"]
        assert [d["type"] for d in await refused(svc, {"invoice": mine}, ctx=OWN_ONLY)] == ["link_status"]


class TestUnchangedValues:
    async def test_a_target_cancelled_since_does_not_block_other_changes(self, svc):
        inv = await svc.create(ALICE, "Invoice", {"title": "F"})
        inv = await svc.submit(ALICE, "Invoice", inv["name"], inv["version"])
        pay = await svc.create(ALICE, "Payment", {"settled": inv["name"], "memo": "a"})
        await svc.cancel(ALICE, "Invoice", inv["name"], inv["version"])
        # Le lien n'a pas changé : on corrige le mémo sans être bloqué par la facture annulée depuis.
        pay = await svc.update(ALICE, "Payment", pay["name"], {"settled": inv["name"], "memo": "b"}, pay["version"])
        assert pay["data"]["memo"] == "b"
        # Mais on ne POSE pas un nouveau lien vers une facture annulée.
        other = await invoice(svc, "cancelled")
        with pytest.raises(ValidationFailed) as e:
            await svc.update(ALICE, "Payment", pay["name"], {"settled": other, "memo": "b"}, pay["version"])
        assert [(d["field"], d["type"]) for d in e.value.details] == [("settled", "link_status")]


class TestDynamicLinks:
    async def test_a_dynamic_link_judges_the_state_of_the_type_it_names(self, svc):
        gone = await invoice(svc, "cancelled")
        assert await refused(svc, {"ref_type": "Invoice", "ref": gone}) == [
            {"field": "ref", "msg": f"Invoice {gone!r} est annulé : ce lien n'accepte pas cet état", "type": "link_status"}
        ]
        # Une Note n'est jamais soumise : ses documents sont des brouillons, que `not_cancelled` admet.
        assert (await svc.create(ALICE, "Payment", {"ref_type": "Note", "ref": await note(svc)}))["data"]["ref_type"] == "Note"

    async def test_changing_only_the_type_is_a_new_link(self, svc):
        """Même nom, autre DocType : c'est un autre document, donc un lien nouveau, rejugé."""
        gone = await invoice(svc, "cancelled")
        await svc.create(ALICE, "Note", {"text": "homonyme"}, name=gone)  # une note qui porte le nom de la facture annulée
        pay = await svc.create(ALICE, "Payment", {"ref_type": "Note", "ref": gone})
        with pytest.raises(ValidationFailed) as e:
            await svc.update(ALICE, "Payment", pay["name"], {"ref_type": "Invoice", "ref": gone}, pay["version"])
        assert [(d["field"], d["type"]) for d in e.value.details] == [("ref", "link_status")]


class TestTableRows:
    async def test_rows_are_judged_with_their_path(self, svc):
        draft, gone, ok = await invoice(svc, "draft"), await invoice(svc, "cancelled"), await invoice(svc, "submitted")
        details = await refused(svc, {"lines": [
            {"invoice": ok},
            {"invoice": draft},
            {"ref_type": "Invoice", "ref": gone},
            {"ref_type": "Invoice", "ref": ok},
        ]})
        assert details == [
            {"field": "lines.1.invoice", "msg": f"Invoice {draft!r} est un brouillon : ce lien n'accepte pas cet état", "type": "link_status"},
            {"field": "lines.2.ref", "msg": f"Invoice {gone!r} est annulé : ce lien n'accepte pas cet état", "type": "link_status"},
        ]

    async def test_a_kept_row_is_not_judged_again(self, svc):
        """Une ligne déplacée garde son lien : la facture annulée depuis ne bloque pas le réordonnancement."""
        inv = await svc.create(ALICE, "Invoice", {"title": "F"})
        inv = await svc.submit(ALICE, "Invoice", inv["name"], inv["version"])
        ok = await invoice(svc, "submitted")
        pay = await svc.create(ALICE, "Payment", {"lines": [{"invoice": inv["name"]}, {"invoice": ok}]})
        await svc.cancel(ALICE, "Invoice", inv["name"], inv["version"])
        moved = await svc.update(ALICE, "Payment", pay["name"], {"lines": [{"invoice": ok}, {"invoice": inv["name"]}]}, pay["version"])
        assert [r["invoice"] for r in moved["data"]["lines"]] == [ok, inv["name"]]


    async def test_a_second_row_citing_an_already_cited_target_is_a_new_link(self, svc):
        """Une ligne déjà là n'excuse qu'UNE ligne : ajouter une seconde ligne vers la facture annulée depuis est un lien nouveau, refusé."""
        inv = await svc.create(ALICE, "Invoice", {"title": "F"})
        inv = await svc.submit(ALICE, "Invoice", inv["name"], inv["version"])
        pay = await svc.create(ALICE, "Payment", {"lines": [{"invoice": inv["name"]}]})
        await svc.cancel(ALICE, "Invoice", inv["name"], inv["version"])
        with pytest.raises(ValidationFailed) as e:
            await svc.update(ALICE, "Payment", pay["name"], {"lines": [{"invoice": inv["name"]}, {"invoice": inv["name"]}]}, pay["version"])
        assert [(d["field"], d["type"]) for d in e.value.details] == [("lines.1.invoice", "link_status")]
        # La ligne qu'on garde, elle, ne se rejuge pas (le cas d'avant reste vrai avec le comptage).
        kept = await svc.update(ALICE, "Payment", pay["name"], {"memo": "m", "lines": [{"invoice": inv["name"]}]}, pay["version"])
        assert kept["data"]["memo"] == "m"

    async def test_a_row_dynamic_link_changing_only_its_type_is_judged_again(self, svc):
        """Le même nom dans une ligne, sous un autre type : c'est un autre document, donc un lien nouveau (le type entre dans la comparaison)."""
        gone = await invoice(svc, "cancelled")
        await note(svc, gone)  # une note homonyme de la facture annulée
        pay = await svc.create(ALICE, "Payment", {"lines": [{"ref_type": "Note", "ref": gone}]})
        with pytest.raises(ValidationFailed) as e:
            await svc.update(ALICE, "Payment", pay["name"], {"lines": [{"ref_type": "Invoice", "ref": gone}]}, pay["version"])
        assert [(d["field"], d["type"]) for d in e.value.details] == [("lines.0.ref", "link_status")]


# ── Soumission, amendement, écritures des hooks ─────────────────────────────
# Un document SOUMETTABLE qui cite des factures : figer, c'est écrire, ses liens contraints se rejugent. `loose` n'a pas de contrainte.
REMITTANCE = {
    "schema_version": 1,
    "name": "Remittance",
    "module": "acct",
    "submittable": True,
    "fields": [
        {"fieldname": "memo", "fieldtype": "Text"},
        {"fieldname": "invoice", "fieldtype": "Link", "options": "Invoice", "target_status": "not_cancelled"},
        {"fieldname": "loose", "fieldtype": "Link", "options": "Invoice"},
        {"fieldname": "ref_type", "fieldtype": "Select", "options": ["Invoice", "Note"]},
        {"fieldname": "ref", "fieldtype": "DynamicLink", "options": "ref_type", "target_status": "not_cancelled"},
        {"fieldname": "lines", "fieldtype": "Table", "options": "Payment Line"},
    ],
    "hooks": {"on_submit": ["acct.memo"]},
}
# Ce que le hook `on_submit` écrit par `event.tx` : un mémo qui cite une facture (contrainte `not_cancelled`).
MEMO = {"schema_version": 1, "name": "Memo", "module": "acct", "track_changes": False,
        "fields": [{"fieldname": "invoice", "fieldtype": "Link", "options": "Invoice", "target_status": "not_cancelled"}]}
REMIT = ["acct:remittance:read", "acct:remittance:write", "acct:remittance:submit", "acct:remittance:cancel"]
CLERK = make_ctx("acme", "alice", permissions=GRANTS + REMIT)
# Soumet et amende des remises, mais ne lit que SES factures.
OLGA_CLERK = make_ctx("acme", "olga", permissions=["acct:invoice:own", *REMIT])


@pytest.fixture
def remit(db, tables):
    reg = registry(INVOICE, NOTE, LINE, REMITTANCE, MEMO)
    reg.validate_links()
    hooks, box = HookRegistry(), {"memo": None}

    async def memo(event):
        if box["memo"]:
            await event.tx.create("Memo", {"invoice": box["memo"]})

    hooks.register("acct.memo", memo)
    return DocumentService(reg, db, tables, Log(), hooks=hooks), box


async def remittance(svc, data: dict, ctx=CLERK) -> dict:
    return await svc.create(ctx, "Remittance", data)


async def submit_refused(svc, doc: dict, ctx=CLERK) -> list[dict]:
    with pytest.raises(ValidationFailed) as e:
        await svc.submit(ctx, "Remittance", doc["name"], doc["version"])
    assert e.value.status == 422 and "corrigez-le avant de le soumettre" in e.value.message
    assert (await svc.get(CLERK, "Remittance", doc["name"]))["docstatus"] == 0  # rien de figé
    return e.value.details


class TestSubmit:
    async def test_a_target_cancelled_since_the_draft_blocks_the_submission(self, remit):
        svc, _ = remit
        inv = await svc.create(CLERK, "Invoice", {"title": "F"})
        inv = await svc.submit(CLERK, "Invoice", inv["name"], inv["version"])
        doc = await remittance(svc, {"invoice": inv["name"]})
        await svc.cancel(CLERK, "Invoice", inv["name"], inv["version"])
        assert await submit_refused(svc, doc) == [
            {"field": "invoice", "msg": f"Invoice {inv['name']!r} est annulé : ce lien n'accepte pas cet état", "type": "link_status"}
        ]

    async def test_rows_and_dynamic_links_are_judged_too(self, remit):
        svc, _ = remit
        ok = await invoice(svc, "submitted", ctx=CLERK)
        first = await svc.create(CLERK, "Invoice", {"title": "F"})
        first = await svc.submit(CLERK, "Invoice", first["name"], first["version"])
        doc = await remittance(svc, {"ref_type": "Invoice", "ref": first["name"], "lines": [{"invoice": ok}, {"invoice": first["name"]}]})
        await svc.cancel(CLERK, "Invoice", first["name"], first["version"])
        assert [(d["field"], d["type"]) for d in await submit_refused(svc, doc)] == [("ref", "link_status"), ("lines.1.invoice", "link_status")]

    async def test_the_submitter_scope_applies_and_unconstrained_links_are_not_judged_again(self, remit):
        """Olga ne lit que ses factures : celle de Bob, citée par le lien contraint, est « introuvable » pour elle ; citée par `loose`
        (sans contrainte), elle ne l'empêche pas de soumettre (l'existence ne se rejuge qu'avec un état exigé)."""
        svc, _ = remit
        bobs = await invoice(svc, "submitted", ctx=BOB)
        constrained = await remittance(svc, {"invoice": bobs})
        assert [(d["field"], d["type"]) for d in await submit_refused(svc, constrained, ctx=OLGA_CLERK)] == [("invoice", "link_not_found")]
        loose = await remittance(svc, {"loose": bobs})
        assert (await svc.submit(OLGA_CLERK, "Remittance", loose["name"], loose["version"]))["docstatus"] == 1

    async def test_a_bulk_submission_reports_the_refusal_per_document(self, remit):
        svc, _ = remit
        inv = await svc.create(CLERK, "Invoice", {"title": "F"})
        inv = await svc.submit(CLERK, "Invoice", inv["name"], inv["version"])
        stale, fine = await remittance(svc, {"invoice": inv["name"]}), await remittance(svc, {"memo": "x"})
        await svc.cancel(CLERK, "Invoice", inv["name"], inv["version"])
        res = await svc.bulk(CLERK, "Remittance", "submit", [stale["name"], fine["name"]])
        assert [(r["name"], r["ok"], r.get("code")) for r in res["results"]] == [(stale["name"], False, "validation_failed"), (fine["name"], True, None)]
        assert res["results"][0]["details"][0]["type"] == "link_status"


class TestAmend:
    async def test_an_amendment_copies_a_link_to_a_target_cancelled_since_and_its_submission_judges_it(self, remit):
        svc, _ = remit
        inv = await svc.create(CLERK, "Invoice", {"title": "F"})
        inv = await svc.submit(CLERK, "Invoice", inv["name"], inv["version"])
        doc = await remittance(svc, {"invoice": inv["name"], "lines": [{"invoice": await invoice(svc, "submitted", ctx=CLERK)}]})
        doc = await svc.submit(CLERK, "Remittance", doc["name"], doc["version"])
        doc = await svc.cancel(CLERK, "Remittance", doc["name"], doc["version"])
        await svc.cancel(CLERK, "Invoice", inv["name"], inv["version"])  # souvent la raison même de l'amendement
        amended = await svc.amend(CLERK, "Remittance", doc["name"])
        assert (amended["docstatus"], amended["data"]["invoice"]) == (0, inv["name"])
        assert [(d["field"], d["type"]) for d in await submit_refused(svc, amended)] == [("invoice", "link_status")]
        # Corrigé, il se soumet ; un lien NOUVEAU posé à l'amendement se juge, lui, sur son état.
        gone = await invoice(svc, "cancelled", ctx=CLERK)
        with pytest.raises(ValidationFailed) as e:
            await svc.update(CLERK, "Remittance", amended["name"], {"invoice": gone}, amended["version"])
        assert [d["type"] for d in e.value.details] == ["link_status"]
        fixed = await svc.update(CLERK, "Remittance", amended["name"], {"invoice": await invoice(svc, "submitted", ctx=CLERK)}, amended["version"])
        assert (await svc.submit(CLERK, "Remittance", fixed["name"], fixed["version"]))["docstatus"] == 1

    async def test_the_copied_links_must_still_exist_and_be_readable(self, remit):
        """Seul l'ÉTAT est excusé : Olga, qui ne lit pas la facture de Bob, n'amende pas une remise qui la cite (« introuvable »)."""
        svc, _ = remit
        doc = await remittance(svc, {"invoice": await invoice(svc, "submitted", ctx=BOB)})
        doc = await svc.submit(CLERK, "Remittance", doc["name"], doc["version"])
        doc = await svc.cancel(CLERK, "Remittance", doc["name"], doc["version"])
        with pytest.raises(ValidationFailed) as e:
            await svc.amend(OLGA_CLERK, "Remittance", doc["name"])
        assert [(d["field"], d["type"]) for d in e.value.details] == [("invoice", "link_not_found")]


class TestHookWrites:
    async def test_a_tx_write_from_a_hook_obeys_target_status(self, remit):
        """Un hook `on_submit` qui écrit (`event.tx`) un mémo vers une facture annulée : défaut du hook (500), la soumission est défaite."""
        svc, box = remit
        box["memo"] = await invoice(svc, "cancelled", ctx=CLERK)
        doc = await remittance(svc, {"memo": "x"})
        with pytest.raises(DoctypeError) as e:
            await svc.submit(CLERK, "Remittance", doc["name"], doc["version"])
        assert (e.value.code, e.value.status) == ("hook_invalid_write", 500)
        assert [d["type"] for d in e.value.details] == ["link_status"]
        assert (await svc.get(CLERK, "Remittance", doc["name"]))["docstatus"] == 0
        assert (await svc.list(make_ctx("acme", "alice", permissions=["acct:memo:read"]), "Memo"))["total"] == 0
        box["memo"] = await invoice(svc, "submitted", ctx=CLERK)  # vers une facture soumise, le même hook passe
        assert (await svc.submit(CLERK, "Remittance", doc["name"], doc["version"]))["docstatus"] == 1


class TestRace:
    async def test_a_constrained_link_locks_its_target_against_deletion(self, svc, db, monkeypatch):
        """PostgreSQL : le contrôle d'état (`existing_docstatus`) verrouille la cible comme le contrôle d'existence (`FOR KEY SHARE`) :
        une suppression concurrente de la facture brouillon attend le commit, puis voit le paiement qui la cite."""
        if db.backend != "postgres":
            pytest.skip("SQLite n'a pas de verrou de ligne : fenêtre documentée, voir doc/phase3.md")
        draft = await invoice(svc, "draft")
        linked, proceed = asyncio.Event(), asyncio.Event()
        real_insert = Query.insert

        async def paused_insert(self, doctype, name, data, **kw):
            if doctype == "Payment":
                linked.set()  # le lien vient d'être contrôlé (et verrouillé), le paiement n'est pas encore écrit
                await proceed.wait()
            return await real_insert(self, doctype, name, data, **kw)

        monkeypatch.setattr(Query, "insert", paused_insert)
        writer = asyncio.create_task(svc.create(ALICE, "Payment", {"invoice": draft}))
        deleter = None
        try:
            await asyncio.wait_for(linked.wait(), 5)
            deleter = asyncio.create_task(svc.delete(ALICE, "Invoice", draft))
            await asyncio.sleep(0.5)
            assert not deleter.done(), "la suppression aurait dû attendre le verrou pris par le contrôle d'état du lien"
        finally:
            proceed.set()  # même en cas d'échec : une transaction restée ouverte bloquerait le nettoyage de la base
        await writer
        with pytest.raises(DocumentInUse):
            await deleter


# Un DocType virtuel (sources.py) : ses documents vivent chez une source, sans état que le moteur pourrait juger.
PERSON = {"schema_version": 1, "name": "Person", "module": "acct", "source": "people", "fields": [{"fieldname": "label", "fieldtype": "Text"}]}


class People(ReadOnlySource):
    """Une source qui prétend qu'un de ses documents est « annulé » : le moteur n'en croit rien (un DocType virtuel n'a pas d'état)."""

    async def fetch_all(self, ctx, doctype):
        at = datetime(2026, 10, 1, tzinfo=UTC)
        return [StoredDoc(doctype.name, "ann", {"label": "Ann"}, "alice", at, at, 1, docstatus=CANCELLED)]


class TestVirtualTargets:
    async def test_a_virtual_target_counts_as_a_draft(self, db, tables):
        fields = [
            {"fieldname": "who", "fieldtype": "Link", "options": "Person", "target_status": "not_cancelled"},
            {"fieldname": "only_draft", "fieldtype": "Link", "options": "Person", "target_status": "draft"},
        ]
        reg = registry(PERSON, {**PAYMENT, "fields": fields})
        reg.validate_links()
        sources = DocumentSources()
        sources.register("acct", "people", People())
        svc = DocumentService(reg, db, tables, Log(), sources=sources)
        ctx = make_ctx("acme", "alice", permissions=["acct:person:read", "acct:payment:read", "acct:payment:write"])
        doc = await svc.create(ctx, "Payment", {"who": "ann", "only_draft": "ann"})
        assert doc["data"]["who"] == "ann"
        with pytest.raises(ValidationFailed) as e:
            await svc.create(ctx, "Payment", {"who": "bob"})
        assert [d["type"] for d in e.value.details] == ["link_not_found"]  # l'existence, elle, se juge toujours

    def test_a_virtual_target_can_never_be_required_submitted(self):
        reg = registry(PERSON, {**PAYMENT, "fields": [{"fieldname": "who", "fieldtype": "Link", "options": "Person", "target_status": "submitted"}]})
        with pytest.raises(InvalidDefinition, match="jamais soumettable"):
            reg.validate_links()


class TestSearch:
    """L'autocomplétion peut ne proposer que les états qu'un lien admet (`docstatus`) : le Desk n'offre pas une cible que l'écriture refuserait."""

    async def test_only_the_asked_states_are_offered(self, svc):
        names = {state: await invoice(svc, state) for state in ("draft", "submitted", "cancelled")}

        async def found(states):
            return {i["name"] for i in (await svc.search(ALICE, "Invoice", "", limit=10, docstatus=states))["items"]}

        assert await found(None) == set(names.values())
        assert await found([0, 1]) == {names["draft"], names["submitted"]}  # `not_cancelled`
        assert await found([1]) == {names["submitted"]}
        assert await found([2, 1]) == {names["submitted"], names["cancelled"]}

    async def test_the_read_scope_still_applies(self, svc):
        await invoice(svc, "submitted", ctx=BOB)
        mine = await invoice(svc, "submitted", ctx=make_ctx("acme", "olga", permissions=GRANTS))
        found = await svc.search(OWN_ONLY, "Invoice", "", limit=10, docstatus=[1])
        assert [i["name"] for i in found["items"]] == [mine]

    async def test_a_virtual_target_counts_as_a_draft(self, db, tables):
        reg = registry(PERSON)
        sources = DocumentSources()
        sources.register("acct", "people", People())
        svc = DocumentService(reg, db, tables, Log(), sources=sources)
        ctx = make_ctx("acme", "alice", permissions=["acct:person:read"])
        assert [i["name"] for i in (await svc.search(ctx, "Person", "", docstatus=[0, 1]))["items"]] == ["ann"]
        assert (await svc.search(ctx, "Person", "", docstatus=[1, 2]))["items"] == []  # « annulé » à la source : le moteur n'en croit rien

    @pytest.mark.parametrize("states", [[], [3], [-1], [True], ["0"], "0", 1, [0.0]])
    async def test_malformed_states_are_refused(self, svc, states):
        with pytest.raises(ValidationFailed, match="docstatus"):
            await svc.search(ALICE, "Invoice", "", docstatus=states)


class TestMeta:
    async def test_meta_sends_the_constraint_for_fields_and_rows(self, svc):
        meta = svc.get_meta(ALICE, "Payment")
        by_name = {f["fieldname"]: f for f in meta["fields"]}
        assert (by_name["invoice"]["target_status"], by_name["archived"]["target_status"]) == ("not_cancelled", ["submitted", "cancelled"])
        assert "target_status" not in by_name["loose"]
        rows = {f["fieldname"]: f for f in meta["children"]["Payment Line"]["fields"]}
        assert rows["invoice"]["target_status"] == "submitted"


class TestDefinition:
    def field(self, **kw) -> dict:
        return {"schema_version": 1, "name": "Payment", "module": "acct", "fields": [{"fieldname": "x", **kw}]}

    @pytest.mark.parametrize("value, expected", [
        ("not_cancelled", {0, 1}),
        ("draft", {0}),
        ("submitted", {1}),
        ("cancelled", {2}),
        (["submitted", "cancelled"], {1, 2}),
        (["draft"], {0}),
    ])
    def test_each_form_maps_to_its_docstatus(self, value, expected):
        f = parse_definition(self.field(fieldtype="Link", options="Invoice", target_status=value)).fields[0]
        assert f.allowed_docstatus == frozenset(expected)

    def test_no_constraint_is_none(self):
        assert parse_definition(self.field(fieldtype="Link", options="Invoice")).fields[0].allowed_docstatus is None

    @pytest.mark.parametrize("kw, why", [
        ({"fieldtype": "Text", "target_status": "submitted"}, "n'a de sens que pour un Link ou un DynamicLink"),
        ({"fieldtype": "Link", "options": "Invoice", "target_status": []}, "exige au moins un état"),
        ({"fieldtype": "Link", "options": "Invoice", "target_status": ["submitted", "submitted"]}, "répète un état"),
        ({"fieldtype": "Link", "options": "Invoice", "target_status": ["not_cancelled"]}, None),  # se dit seul, jamais dans une liste
        ({"fieldtype": "Link", "options": "Invoice", "target_status": "validated"}, None),  # état inconnu
    ])
    def test_malformed_constraints_are_refused(self, kw, why):
        with pytest.raises(InvalidDefinition) as e:
            parse_definition(self.field(**kw))
        if why is not None:
            assert why in str(e.value)

    def test_a_virtual_doctype_has_no_state_to_judge(self):
        with pytest.raises(InvalidDefinition, match="target_status : un DocType virtuel"):
            parse_definition({**self.field(fieldtype="Link", options="Invoice", target_status="submitted"), "source": "people"})


class TestLoad:
    """Au chargement : une contrainte qu'aucun document de la cible ne pourra jamais satisfaire est une erreur de définition."""

    @pytest.mark.parametrize("status", ["submitted", "cancelled", ["submitted", "cancelled"]])
    def test_a_never_submittable_target_with_an_unreachable_state_fails(self, status):
        reg = registry(NOTE, {**PAYMENT, "fields": [{"fieldname": "n", "fieldtype": "Link", "options": "Note", "target_status": status}]})
        with pytest.raises(InvalidDefinition, match=r"Payment\.n: `target_status` vise 'Note', qui n'est jamais soumettable"):
            reg.validate_links()

    @pytest.mark.parametrize("status", ["not_cancelled", "draft", ["draft", "submitted"]])
    def test_a_state_that_drafts_satisfy_is_fine_for_a_never_submittable_target(self, status):
        reg = registry(NOTE, {**PAYMENT, "fields": [{"fieldname": "n", "fieldtype": "Link", "options": "Note", "target_status": status}]})
        reg.validate_links()

    def test_a_dynamic_link_is_checked_against_every_option_of_its_select(self):
        fields = [
            {"fieldname": "ref_type", "fieldtype": "Select", "options": ["Invoice", "Note"]},
            {"fieldname": "ref", "fieldtype": "DynamicLink", "options": "ref_type", "target_status": "submitted"},
        ]
        reg = registry(INVOICE, NOTE, {**PAYMENT, "fields": fields})
        with pytest.raises(InvalidDefinition, match=r"Payment\.ref: `target_status` peut viser 'Note', qui n'est jamais soumettable"):
            reg.validate_links()

    def test_a_row_link_is_checked_like_a_document_link(self):
        line = {**LINE, "fields": [{"fieldname": "n", "fieldtype": "Link", "options": "Note", "target_status": "submitted"}]}
        reg = registry(NOTE, line, {**PAYMENT, "fields": [{"fieldname": "lines", "fieldtype": "Table", "options": "Payment Line"}]})
        with pytest.raises(InvalidDefinition, match=r"Payment Line\.n: `target_status` vise 'Note'"):
            reg.validate_links()

    def test_a_plugin_folder_with_such_a_link_loads_nothing(self, tmp_path):
        import yaml

        for d in (NOTE, {**PAYMENT, "fields": [{"fieldname": "n", "fieldtype": "Link", "options": "Note", "target_status": "cancelled"}]}):
            (tmp_path / f"{d['name'].lower()}.yaml").write_text(yaml.safe_dump(d), encoding="utf-8")
        reg = DocTypeRegistry()
        with pytest.raises(InvalidDefinition, match="jamais soumettable"):
            DocTypeChannel(reg).load_dir("acct", tmp_path)
        assert not reg.has("Payment") and not reg.has("Note")  # tout ou rien
