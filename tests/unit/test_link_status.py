"""État exigé de la cible d'un lien (`target_status`, D23) : un Link ou un DynamicLink peut refuser un document annulé, un brouillon…

Sans `target_status`, un lien ne regarde que l'existence et la lisibilité (test_links.py). Avec, l'état (`docstatus`) de la cible est
lu dans la même requête que son existence, et un état refusé échoue en `link_status` (422), distinct de `link_not_found` : la cible
existe, c'est son état qui gêne. Ce qui est défendu ici, chacun par un test qui casse sans la règle :
- chaque forme (`not_cancelled`, un état, une liste) admet exactement ses états, avec un message qui nomme l'état ;
- un document illisible reste « introuvable » : son état ne se devine pas ;
- une valeur inchangée n'est pas rejugée (la cible annulée depuis n'empêche pas de corriger autre chose) ;
- les DynamicLink et les lignes de table suivent la même règle, au même chemin d'erreur que les autres contrôles ;
- au chargement, viser un DocType jamais soumettable avec un état qu'il n'aura jamais est une erreur de définition.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from tests.conftest import make_ctx
from xcore_doctype.document import DocumentService
from xcore_doctype.errors import InvalidDefinition, ValidationFailed
from xcore_doctype.query import CANCELLED, StoredDoc
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
