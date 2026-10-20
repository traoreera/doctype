"""Propriétés de champ et de DocType du palier 70 % (phase H2) : unique, set_only_once, no_copy, fetch_from, allow_on_submit, nommage choisi ou
tiré d'un champ, hooks de création/suppression/annulation, renommer, DynamicLink.

Ce que ces tests défendent, chacun par une mutation qui le casse :
- une valeur `unique` n'est portée que par UN document de l'organisation, y compris sous concurrence, et se libère à la suppression, au changement
  de valeur et à l'annulation ;
- `fetch_from` écrase ce que le client envoie et se met à jour à chaque écriture ;
- un document soumis ne laisse modifier que ses champs `allow_on_submit` ;
- renommer réécrit TOUS les liens (champs, lignes de tables, DynamicLink) dans une seule transaction, et un nom pris la défait entièrement ;
- un DynamicLink est vérifié contre le DocType que son champ de type désigne, et protège sa cible contre la suppression.
"""

from __future__ import annotations

import asyncio

import pytest

from tests.conftest import make_ctx
from xcore_doctype import DocumentValidationError
from xcore_doctype.document import DocumentService, valid_name
from xcore_doctype.errors import (
    DoctypeError,
    DocumentInUse,
    DocumentLocked,
    InvalidDefinition,
    NotFound,
    ValidationFailed,
)
from xcore_doctype.hooks import HookRegistry
from xcore_doctype.registry import DocTypeRegistry
from xcore_doctype.schema import parse_definition

COMPANY = {
    "schema_version": 1, "name": "Company", "module": "crm", "title_field": "title",
    "fields": [
        {"fieldname": "title", "fieldtype": "Text", "required": True},
        {"fieldname": "city", "fieldtype": "Text"},
        {"fieldname": "rate", "fieldtype": "Decimal", "decimals": 2},
    ],
}
PRODUCT = {
    "schema_version": 1, "name": "Product", "module": "crm",
    "fields": [{"fieldname": "title", "fieldtype": "Text", "required": True}, {"fieldname": "price", "fieldtype": "Number"}],
}
INVOICE_LINE = {
    "schema_version": 1, "name": "Invoice Line", "istable": True,
    "fields": [
        {"fieldname": "product", "fieldtype": "Link", "options": "Product"},
        {"fieldname": "product_title", "fieldtype": "Text", "fetch_from": "product.title"},
        {"fieldname": "qty", "fieldtype": "Number"},
    ],
}
INVOICE = {
    "schema_version": 1, "name": "Invoice", "module": "crm",
    "fields": [
        {"fieldname": "company", "fieldtype": "Link", "options": "Company", "required": True},
        {"fieldname": "company_title", "fieldtype": "Text", "fetch_from": "company.title"},
        {"fieldname": "company_rate", "fieldtype": "Decimal", "decimals": 2, "fetch_from": "company.rate"},
        {"fieldname": "items", "fieldtype": "Table", "options": "Invoice Line"},
    ],
}
CONTACT = {
    "schema_version": 1, "name": "Contact", "module": "crm",
    "fields": [
        {"fieldname": "email", "fieldtype": "Text", "unique": True},
        {"fieldname": "code", "fieldtype": "Int", "unique": True},
        {"fieldname": "badge", "fieldtype": "Text", "set_only_once": True},
        {"fieldname": "nickname", "fieldtype": "Text", "no_copy": True},
    ],
}
TAG = {"schema_version": 1, "name": "Tag", "module": "crm", "naming": "prompt", "fields": [{"fieldname": "color", "fieldtype": "Text"}]}
SKU = {
    "schema_version": 1, "name": "Sku", "module": "crm", "naming": {"field": "code"},
    "fields": [{"fieldname": "code", "fieldtype": "Text", "required": True}, {"fieldname": "label", "fieldtype": "Text"}],
}
TASK = {
    "schema_version": 1, "name": "Task", "module": "crm",
    "fields": [{"fieldname": "title", "fieldtype": "Text", "required": True}, {"fieldname": "stamp", "fieldtype": "Text"}],
    "hooks": {
        "before_insert": ["t.before_insert"], "after_insert": ["t.after_insert"], "before_save": ["t.before_save"], "after_save": ["t.after_save"],
        "on_trash": ["t.on_trash"], "after_delete": ["t.after_delete"],
    },
}
ORDER = {
    "schema_version": 1, "name": "Order", "module": "crm", "submittable": True,
    "fields": [
        {"fieldname": "title", "fieldtype": "Text", "required": True},
        {"fieldname": "ref", "fieldtype": "Text", "unique": True},
        {"fieldname": "follow_up", "fieldtype": "Text", "allow_on_submit": True},
    ],
    "hooks": {"before_save": ["o.before_save"], "before_cancel": ["o.before_cancel"], "on_update_after_submit": ["o.after_edit"]},
}
GADGET = {"schema_version": 1, "name": "Gadget", "module": "crm", "naming": "prompt", "fields": [{"fieldname": "label", "fieldtype": "Text"}]}
PLACE = {"schema_version": 1, "name": "Place", "module": "crm", "allow_rename": True, "fields": [{"fieldname": "label", "fieldtype": "Text"}]}
VISIT_LINE = {"schema_version": 1, "name": "Visit Line", "istable": True, "fields": [{"fieldname": "place", "fieldtype": "Link", "options": "Place"}]}
VISIT = {
    "schema_version": 1, "name": "Visit", "module": "crm",
    "fields": [
        {"fieldname": "place", "fieldtype": "Link", "options": "Place"},
        {"fieldname": "stops", "fieldtype": "Table", "options": "Visit Line"},
    ],
}
NOTE = {
    "schema_version": 1, "name": "Note", "module": "crm",
    "fields": [
        {"fieldname": "ref_type", "fieldtype": "Select", "options": ["Place", "Product", "Gadget"]},
        {"fieldname": "ref_name", "fieldtype": "DynamicLink", "options": "ref_type"},
    ],
}
ALL = [
    COMPANY, PRODUCT, INVOICE_LINE, INVOICE, CONTACT, TAG, SKU, TASK, ORDER, GADGET, PLACE, VISIT_LINE, VISIT, NOTE,
]
GRANTS = [
    f"crm:{n}:{a}"
    for n in ("company", "product", "invoice", "contact", "tag", "sku", "task", "order", "gadget", "place", "visit", "note")
    for a in ("read", "write", "delete")
] + ["crm:order:submit", "crm:order:cancel"]
ALICE = make_ctx("acme", "alice", permissions=GRANTS)
BOB = make_ctx("acme", "bob", permissions=GRANTS)
GLOBEX = make_ctx("globex", "gina", permissions=GRANTS)
NO_COMPANY = make_ctx("acme", "carl", permissions=[g for g in GRANTS if not g.startswith("crm:company")])


class Bus:
    def __init__(self):
        self.sent = []

    async def emit(self, topic, payload, source=None):
        self.sent.append((topic, payload))


class Log:
    def __init__(self):
        self.errors = []

    def info(self, *a, **k): ...
    def error(self, msg, **k):
        self.errors.append((msg, k))


class Env:
    def __init__(self, db, tables):
        reg = DocTypeRegistry()
        for raw in ALL:
            reg.register(parse_definition(raw))
        reg.validate_links()
        self.hooks, self.bus, self.log = HookRegistry(), Bus(), Log()
        self.calls: list[tuple] = []
        self.refuse: dict[str, str] = {}  # événement -> message : le hook refuse
        self.sneak = False  # `o.before_save` tente de modifier un champ figé d'un document soumis
        for ref in ("t.before_insert", "t.after_insert", "t.before_save", "t.after_save", "t.on_trash", "t.after_delete", "o.before_save", "o.before_cancel", "o.after_edit"):
            self.hooks.register(ref, self._recorder(ref))
        self.svc = DocumentService(reg, db, tables, self.log, hooks=self.hooks, events=self.bus)

    def _recorder(self, ref):
        async def hook(event):
            self.calls.append((event.event, event.name, dict(event.data), None if event.old is None else dict(event.old), event.tx is not None))
            if ref == "t.before_insert":
                event.data["stamp"] = "inserted"  # modifiable en place, comme before_save
            if ref == "o.before_save" and self.sneak and event.old is not None:
                event.data["title"] = "volé"
            if event.event in self.refuse:
                raise DocumentValidationError(self.refuse[event.event])

        return hook

    def seen(self, event):
        return [c for c in self.calls if c[0] == event]

    async def make(self, doctype, ctx=ALICE, **data):
        return await self.svc.create(ctx, doctype, data)


@pytest.fixture
def env(db, tables):
    return Env(db, tables)


def codes(e: pytest.ExceptionInfo) -> set[str]:
    return {d["type"] for d in (e.value.details or [])}


# ── unique ────────────────────────────────────────────────────────────────────────────────────


class TestUnique:
    async def test_a_second_document_cannot_take_a_value(self, env):
        await env.make("Contact", email="a@x.fr", code=1)
        with pytest.raises(ValidationFailed) as e:
            await env.make("Contact", email="a@x.fr", code=2)
        assert e.value.details[0]["field"] == "email" and codes(e) == {"unique"}
        assert (await env.svc.list(ALICE, "Contact", limit=10))["total"] == 1  # la création refusée n'a rien laissé

    async def test_an_int_is_compared_as_a_value_not_as_a_prefix(self, env):
        await env.make("Contact", code=1)
        await env.make("Contact", code=11)  # « 1 » ne bloque pas « 11 »
        with pytest.raises(ValidationFailed):
            await env.make("Contact", code=11)

    async def test_the_tenant_is_part_of_the_guarantee(self, env):
        await env.make("Contact", email="same@x.fr")
        await env.make("Contact", ctx=GLOBEX, email="same@x.fr")  # une autre organisation : sa propre unicité

    async def test_two_values_of_one_document_are_independent_and_absent_values_do_not_collide(self, env):
        await env.make("Contact", email="a@x.fr")
        await env.make("Contact", code=5)  # pas d'email : rien à réserver
        await env.make("Contact")
        await env.make("Contact")  # plusieurs documents sans valeur

    async def test_updating_to_a_taken_value_is_refused_and_leaves_the_document_alone(self, env):
        await env.make("Contact", email="a@x.fr")
        b = await env.make("Contact", email="b@x.fr")
        with pytest.raises(ValidationFailed) as e:
            await env.svc.update(ALICE, "Contact", b["name"], {"email": "a@x.fr"}, b["version"])
        assert codes(e) == {"unique"}
        assert (await env.svc.get(ALICE, "Contact", b["name"]))["data"]["email"] == "b@x.fr"

    async def test_a_value_it_left_is_free_again(self, env):
        a = await env.make("Contact", email="a@x.fr")
        done = await env.svc.update(ALICE, "Contact", a["name"], {"email": "a2@x.fr"}, a["version"])
        await env.make("Contact", email="a@x.fr")  # libérée par le changement
        with pytest.raises(ValidationFailed):
            await env.make("Contact", email="a2@x.fr")  # et la nouvelle est tenue
        assert done["data"]["email"] == "a2@x.fr"

    async def test_saving_the_same_document_again_is_not_a_conflict(self, env):
        a = await env.make("Contact", email="a@x.fr", code=3)
        again = await env.svc.update(ALICE, "Contact", a["name"], {"email": "a@x.fr", "code": 3, "badge": "B"}, a["version"])
        assert again["version"] == a["version"] + 1

    async def test_deleting_frees_the_value(self, env):
        a = await env.make("Contact", email="a@x.fr")
        await env.svc.delete(ALICE, "Contact", a["name"])
        await env.make("Contact", email="a@x.fr")

    async def test_cancelling_frees_the_value_so_the_amendment_can_take_it_back(self, env):
        o = await env.make("Order", title="T", ref="R-1")
        o = await env.svc.submit(ALICE, "Order", o["name"], o["version"])
        with pytest.raises(ValidationFailed):
            await env.make("Order", title="other", ref="R-1")  # un document soumis la tient
        await env.svc.cancel(ALICE, "Order", o["name"], o["version"])
        amended = await env.svc.amend(ALICE, "Order", o["name"])  # la copie reprend la valeur
        assert amended["data"]["ref"] == "R-1"

    async def test_simultaneous_creations_let_exactly_one_through(self, env):
        results = await asyncio.gather(*(env.make("Contact", email="race@x.fr") for _ in range(6)), return_exceptions=True)
        assert sum(isinstance(r, dict) for r in results) == 1
        assert all(isinstance(r, ValidationFailed) for r in results if not isinstance(r, dict))
        assert (await env.svc.list(ALICE, "Contact", limit=10))["total"] == 1

    async def test_a_value_too_long_to_be_reserved_is_refused_clearly(self, env):
        with pytest.raises(ValidationFailed) as e:
            await env.make("Contact", email="x" * 501)
        assert codes(e) == {"unique"}


class TestUniqueSchema:
    def test_unique_is_refused_where_it_means_nothing(self):
        bad = {"schema_version": 1, "name": "X", "module": "m", "fields": [{"fieldname": "n", "fieldtype": "Number", "unique": True}]}
        with pytest.raises(InvalidDefinition, match="unique"):
            parse_definition(bad)

    def test_a_child_row_has_no_identity_to_be_unique(self):
        bad = {"schema_version": 1, "name": "X Row", "istable": True, "fields": [{"fieldname": "n", "fieldtype": "Text", "unique": True}]}
        with pytest.raises(InvalidDefinition, match="lignes"):
            parse_definition(bad)


# ── set_only_once, no_copy ────────────────────────────────────────────────────────────────────


class TestSetOnlyOnce:
    async def test_free_while_empty_then_frozen(self, env):
        c = await env.make("Contact", email="a@x.fr")
        c = await env.svc.update(ALICE, "Contact", c["name"], {"email": "a@x.fr", "badge": "B1"}, c["version"])  # posé une fois
        with pytest.raises(ValidationFailed) as e:
            await env.svc.update(ALICE, "Contact", c["name"], {"email": "a@x.fr", "badge": "B2"}, c["version"])
        assert codes(e) == {"set_only_once"}
        with pytest.raises(ValidationFailed) as e:  # l'effacer est aussi le changer
            await env.svc.update(ALICE, "Contact", c["name"], {"email": "a@x.fr"}, c["version"])
        assert codes(e) == {"set_only_once"}
        same = await env.svc.update(ALICE, "Contact", c["name"], {"email": "a2@x.fr", "badge": "B1"}, c["version"])  # inchangé : accepté
        assert same["data"]["badge"] == "B1"

    async def test_the_field_that_names_a_document_never_changes(self, env):
        s = await env.make("Sku", code="AB-1", label="x")
        with pytest.raises(ValidationFailed) as e:
            await env.svc.update(ALICE, "Sku", s["name"], {"code": "AB-2", "label": "x"}, s["version"])
        assert codes(e) == {"set_only_once"}


class TestNoCopy:
    async def test_an_amendment_does_not_carry_a_no_copy_field(self, env, db, tables):
        reg = DocTypeRegistry()
        reg.register(parse_definition({
            "schema_version": 1, "name": "Deal", "module": "crm", "submittable": True,
            "fields": [{"fieldname": "title", "fieldtype": "Text"}, {"fieldname": "signed_on", "fieldtype": "Text", "no_copy": True}],
        }))
        svc = DocumentService(reg, db, tables, Log(), hooks=HookRegistry())
        ctx = make_ctx("acme", "alice", permissions=["crm:deal:read", "crm:deal:write", "crm:deal:submit", "crm:deal:cancel"])
        d = await svc.create(ctx, "Deal", {"title": "T", "signed_on": "2026-10-01"})
        d = await svc.submit(ctx, "Deal", d["name"], d["version"])
        d = await svc.cancel(ctx, "Deal", d["name"], d["version"])
        amended = await svc.amend(ctx, "Deal", d["name"])
        assert amended["data"] == {"title": "T"}


# ── fetch_from ────────────────────────────────────────────────────────────────────────────────


class TestFetchFrom:
    async def test_the_value_is_read_from_the_linked_document(self, env):
        c = await env.make("Company", title="Acme", rate="19.60")
        inv = await env.make("Invoice", company=c["name"])
        assert inv["data"]["company_title"] == "Acme" and inv["data"]["company_rate"] == "19.60"

    async def test_what_the_client_sends_is_overwritten(self, env):
        c = await env.make("Company", title="Acme")
        inv = await env.make("Invoice", company=c["name"], company_title="Faux")
        assert inv["data"]["company_title"] == "Acme"

    async def test_it_follows_the_link_and_the_linked_document_on_every_write(self, env):
        a = await env.make("Company", title="Acme")
        b = await env.make("Company", title="Bravo")
        inv = await env.make("Invoice", company=a["name"])
        inv = await env.svc.update(ALICE, "Invoice", inv["name"], {"company": b["name"]}, inv["version"])
        assert inv["data"]["company_title"] == "Bravo"
        a2 = await env.svc.update(ALICE, "Company", b["name"], {"title": "Bravo 2"}, b["version"])
        inv = await env.svc.update(ALICE, "Invoice", inv["name"], dict(inv["data"]), inv["version"])
        assert inv["data"]["company_title"] == "Bravo 2" and a2["data"]["title"] == "Bravo 2"

    async def test_an_empty_source_value_leaves_the_field_empty(self, env):
        c = await env.make("Company", title="Acme")  # pas de rate
        inv = await env.make("Invoice", company=c["name"])
        assert "company_rate" not in inv["data"]

    async def test_rows_fetch_from_their_own_link(self, env):
        p1 = await env.make("Product", title="Vis")
        p2 = await env.make("Product", title="Écrou")
        c = await env.make("Company", title="Acme")
        inv = await env.make("Invoice", company=c["name"], items=[{"product": p1["name"], "qty": 2}, {"product": p2["name"], "product_title": "Faux"}, {"qty": 1}])
        assert [r.get("product_title") for r in inv["data"]["items"]] == ["Vis", "Écrou", None]

    async def test_an_unreadable_target_keeps_the_copy_it_already_had(self, env):
        c = await env.make("Company", title="Acme")
        inv = await env.make("Invoice", company=c["name"])
        # carl ne lit pas les sociétés : il ne peut pas CHANGER le lien (introuvable) mais peut corriger autre chose, la copie reste.
        edited = await env.svc.update(NO_COMPANY, "Invoice", inv["name"], dict(inv["data"]), inv["version"])
        assert edited["data"]["company_title"] == "Acme"
        other = await env.make("Company", title="Autre")
        with pytest.raises(ValidationFailed) as e:
            await env.svc.update(NO_COMPANY, "Invoice", inv["name"], {"company": other["name"]}, edited["version"])
        assert codes(e) == {"link_not_found"}

    async def test_a_hook_writing_through_the_transaction_gets_it_too(self, env):
        # `tx.create` passe par la même validation : le champ fetch_from y est rempli.
        c = await env.make("Company", title="Acme")
        compiled = env.svc._registry.get("Invoice")
        ctx = make_ctx("acme", "alice", permissions=GRANTS)
        async with env.svc._query(ctx) as q:
            from xcore_doctype.document import _Transaction

            tx = _Transaction(env.svc, ctx, q, "test")
            doc = await tx.create("Invoice", {"company": c["name"]})
        assert doc["data"]["company_title"] == "Acme" and compiled is not None


class TestFetchSchema:
    def _registry(self, *raws):
        reg = DocTypeRegistry()
        for raw in raws:
            reg.register(parse_definition(raw))
        reg.validate_links()

    def test_a_missing_source_field_is_a_load_error(self):
        broken = {**INVOICE, "fields": [*INVOICE["fields"], {"fieldname": "x", "fieldtype": "Text", "fetch_from": "company.nope"}]}
        with pytest.raises(InvalidDefinition, match="nope"):
            self._registry(COMPANY, INVOICE_LINE, PRODUCT, broken)

    def test_an_incompatible_type_is_a_load_error(self):
        broken = {**INVOICE, "fields": [*INVOICE["fields"], {"fieldname": "n", "fieldtype": "Number", "fetch_from": "company.title"}]}
        with pytest.raises(InvalidDefinition, match="Number ne reçoit pas un Text"):
            self._registry(COMPANY, INVOICE_LINE, PRODUCT, broken)

    def test_decimals_must_match(self):
        broken = {**INVOICE, "fields": [*INVOICE["fields"], {"fieldname": "r", "fieldtype": "Decimal", "decimals": 3, "fetch_from": "company.rate"}]}
        with pytest.raises(InvalidDefinition, match="décimales"):
            self._registry(COMPANY, INVOICE_LINE, PRODUCT, broken)

    def test_a_fetched_field_is_not_required_nor_defaulted(self):
        for extra in ({"required": True}, {"default": "x"}):
            bad = {"schema_version": 1, "name": "X", "module": "m", "fields": [
                {"fieldname": "c", "fieldtype": "Link", "options": "Y"}, {"fieldname": "t", "fieldtype": "Text", "fetch_from": "c.t", **extra}]}
            with pytest.raises(InvalidDefinition):
                parse_definition(bad)

    def test_the_source_of_a_fetch_must_be_a_link_of_the_same_doctype(self):
        bad = {"schema_version": 1, "name": "X", "module": "m", "fields": [{"fieldname": "t", "fieldtype": "Text", "fetch_from": "nolink.t"}]}
        with pytest.raises(InvalidDefinition, match="Link"):
            parse_definition(bad)


# ── nommage ───────────────────────────────────────────────────────────────────────────────────


class TestNaming:
    async def test_prompt_uses_the_chosen_name(self, env):
        doc = await env.svc.create(ALICE, "Tag", {"color": "red"}, name="urgent")
        assert doc["name"] == "urgent"
        assert (await env.svc.get(ALICE, "Tag", "urgent"))["data"]["color"] == "red"

    async def test_prompt_requires_a_name(self, env):
        with pytest.raises(ValidationFailed) as e:
            await env.svc.create(ALICE, "Tag", {"color": "red"})
        assert e.value.details[0]["field"] == "name"

    async def test_a_taken_name_is_a_conflict(self, env):
        await env.svc.create(ALICE, "Tag", {}, name="dup")
        with pytest.raises(DoctypeError) as e:
            await env.svc.create(ALICE, "Tag", {}, name="dup")
        assert e.value.status == 409

    @pytest.mark.parametrize("bad", ["", " x", "x ", "a/b", "a\\b", "a?b", "a#b", "50%", "x" * 141, "new", "NEW", "a\nb", 7])
    async def test_unusable_names_are_refused(self, env, bad):
        with pytest.raises(ValidationFailed) as e:
            await env.svc.create(ALICE, "Tag", {}, name=bad)
        assert codes(e) == {"invalid_name"}

    async def test_other_namings_do_not_take_a_name_from_the_client(self, env):
        with pytest.raises(ValidationFailed) as e:
            await env.svc.create(ALICE, "Company", {"title": "x"}, name="mine")
        assert e.value.code == "name_not_allowed"

    async def test_field_naming_uses_the_field_value(self, env):
        doc = await env.svc.create(ALICE, "Sku", {"code": "AB-1", "label": "x"})
        assert doc["name"] == "AB-1"

    async def test_field_naming_duplicate_is_a_conflict(self, env):
        await env.svc.create(ALICE, "Sku", {"code": "AB-1"})
        with pytest.raises(DoctypeError) as e:
            await env.svc.create(ALICE, "Sku", {"code": "AB-1"})
        assert e.value.status == 409

    async def test_field_naming_refuses_an_unusable_value_on_that_field(self, env):
        with pytest.raises(ValidationFailed) as e:
            await env.svc.create(ALICE, "Sku", {"code": "a/b"})
        assert e.value.details[0]["field"] == "code"

    def test_valid_name_accepts_ordinary_names(self):
        for ok in ("FA-2026-0001", "Acme SA", "é à ü", "a.b_c-d", "x" * 140):
            assert valid_name(ok) == ok


class TestNamingSchema:
    def test_the_naming_field_must_be_required_text_like(self):
        for field in ({"fieldtype": "Text"}, {"fieldtype": "Number", "required": True}):
            bad = {"schema_version": 1, "name": "X", "module": "m", "naming": {"field": "f"}, "fields": [{"fieldname": "f", **field}]}
            with pytest.raises(InvalidDefinition, match="naming.field"):
                parse_definition(bad)

    def test_allow_rename_needs_a_free_name(self):
        bad = {"schema_version": 1, "name": "X", "module": "m", "allow_rename": True, "naming": {"series": "X-{#####}"}, "fields": [{"fieldname": "f", "fieldtype": "Text"}]}
        with pytest.raises(InvalidDefinition, match="allow_rename"):
            parse_definition(bad)


# ── hooks ─────────────────────────────────────────────────────────────────────────────────────


class TestHooks:
    async def test_creation_order_and_a_before_insert_that_edits(self, env):
        doc = await env.make("Task", title="T")
        order = [c[0] for c in env.calls]
        assert order == ["before_save", "before_insert", "after_insert", "after_save"]
        assert doc["data"]["stamp"] == "inserted"  # before_insert a modifié le document, validé ensuite
        assert env.seen("before_insert")[0][3] is None  # pas d'ancienne valeur
        assert env.seen("after_insert")[0][1] == doc["name"]  # le nom définitif

    async def test_an_update_runs_neither_insert_hook(self, env):
        doc = await env.make("Task", title="T")
        env.calls.clear()
        await env.svc.update(ALICE, "Task", doc["name"], {"title": "T2"}, doc["version"])
        assert [c[0] for c in env.calls] == ["before_save", "after_save"]

    async def test_before_insert_can_refuse_and_nothing_is_stored(self, env):
        env.refuse["before_insert"] = "non"
        with pytest.raises(DocumentValidationError):
            await env.make("Task", title="T")
        assert (await env.svc.list(ALICE, "Task", limit=10))["total"] == 0
        assert env.seen("after_insert") == []

    async def test_on_trash_runs_before_the_delete_and_can_refuse_it(self, env):
        doc = await env.make("Task", title="T")
        env.refuse["on_trash"] = "pas maintenant"
        with pytest.raises(DocumentValidationError):
            await env.svc.delete(ALICE, "Task", doc["name"])
        assert (await env.svc.get(ALICE, "Task", doc["name"]))["name"] == doc["name"]
        assert env.seen("after_delete") == []
        env.refuse.clear()
        env.calls.clear()
        await env.svc.delete(ALICE, "Task", doc["name"])
        assert [c[0] for c in env.calls] == ["on_trash", "after_delete"]
        assert env.seen("on_trash")[0][4] is True  # `tx` est là : on peut écrire dans la transaction de la suppression
        assert env.seen("after_delete")[0][2]["title"] == "T"  # ce que le document contenait
        assert ("doctype.Task.after_delete", {"doctype": "Task", "name": doc["name"], "tenant_id": "acme", "user": "alice"}) in env.bus.sent

    async def test_before_cancel_runs_before_on_cancel_in_the_transaction_and_can_refuse(self, env):
        o = await env.make("Order", title="O")
        o = await env.svc.submit(ALICE, "Order", o["name"], o["version"])
        env.refuse["before_cancel"] = "verrouillé"
        with pytest.raises(DocumentValidationError):
            await env.svc.cancel(ALICE, "Order", o["name"], o["version"])
        assert (await env.svc.get(ALICE, "Order", o["name"]))["docstatus"] == 1  # l'état est défait avec le refus
        env.refuse.clear()
        await env.svc.cancel(ALICE, "Order", o["name"], o["version"])
        assert env.seen("before_cancel")[-1][4] is True

    def test_transition_hooks_need_a_submittable_doctype(self):
        bad = {"schema_version": 1, "name": "X", "module": "m", "fields": [{"fieldname": "f", "fieldtype": "Text"}], "hooks": {"before_cancel": ["a.b"]}}
        with pytest.raises(InvalidDefinition, match="submittable"):
            parse_definition(bad)

    def test_sandboxed_actions_only_run_before_the_transaction(self):
        for event in ("before_insert", "after_insert", "on_trash", "after_delete"):
            bad = {"schema_version": 1, "name": "X", "module": "m", "fields": [{"fieldname": "f", "fieldtype": "Text"}], "hooks": {event: ["action:p.a"]}}
            with pytest.raises(InvalidDefinition, match="action"):
                parse_definition(bad)


# ── allow_on_submit ───────────────────────────────────────────────────────────────────────────


class TestAllowOnSubmit:
    async def _submitted(self, env, **data):
        o = await env.make("Order", title="O", **data)
        return await env.svc.submit(ALICE, "Order", o["name"], o["version"])

    async def test_an_allowed_field_can_change_on_a_submitted_document(self, env):
        o = await self._submitted(env)
        env.calls.clear()
        edited = await env.svc.update(ALICE, "Order", o["name"], {"title": "O", "follow_up": "rappeler"}, o["version"])
        assert (edited["docstatus"], edited["data"]["follow_up"], edited["version"]) == (1, "rappeler", o["version"] + 1)
        hook = env.seen("on_update_after_submit")
        assert len(hook) == 1 and hook[0][2]["follow_up"] == "rappeler" and hook[0][3].get("follow_up") is None and hook[0][4] is True
        history = await env.svc.history(ALICE, "Order", o["name"])
        assert history["items"][0]["changes"] == {"follow_up": {"old": None, "new": "rappeler"}}

    async def test_any_other_field_stays_frozen(self, env):
        o = await self._submitted(env)
        with pytest.raises(ValidationFailed) as e:
            await env.svc.update(ALICE, "Order", o["name"], {"title": "changé", "follow_up": "x"}, o["version"])
        assert codes(e) == {"locked_after_submit"} and e.value.details[0]["field"] == "title"
        with pytest.raises(ValidationFailed):  # retirer un champ figé est aussi le changer
            await env.svc.update(ALICE, "Order", o["name"], {"follow_up": "x"}, o["version"])
        assert (await env.svc.get(ALICE, "Order", o["name"]))["data"] == {"title": "O"}

    async def test_a_cancelled_document_is_frozen_entirely(self, env):
        o = await self._submitted(env)
        o = await env.svc.cancel(ALICE, "Order", o["name"], o["version"])
        with pytest.raises(DocumentLocked):
            await env.svc.update(ALICE, "Order", o["name"], {"title": "O", "follow_up": "x"}, o["version"])

    async def test_writing_needs_the_write_grant_and_a_fresh_version(self, env):
        o = await self._submitted(env)
        reader = make_ctx("acme", "rita", permissions=["crm:order:read"])
        with pytest.raises(DoctypeError) as e:
            await env.svc.update(reader, "Order", o["name"], {"title": "O", "follow_up": "x"}, o["version"])
        assert e.value.status == 403
        with pytest.raises(DoctypeError) as e:
            await env.svc.update(ALICE, "Order", o["name"], {"title": "O", "follow_up": "x"}, o["version"] + 5)
        assert e.value.status == 409

    async def test_a_hook_cannot_unfreeze_a_field(self, env):
        o = await self._submitted(env)
        env.sneak = True  # `before_save` change le titre d'un document soumis
        with pytest.raises(ValidationFailed) as e:
            await env.svc.update(ALICE, "Order", o["name"], {"title": "O", "follow_up": "x"}, o["version"])
        assert codes(e) == {"locked_after_submit"}
        assert (await env.svc.get(ALICE, "Order", o["name"]))["data"] == {"title": "O"}

    def test_allow_on_submit_needs_a_submittable_doctype(self):
        bad = {"schema_version": 1, "name": "X", "module": "m", "fields": [{"fieldname": "f", "fieldtype": "Text", "allow_on_submit": True}]}
        with pytest.raises(InvalidDefinition, match="submittable"):
            parse_definition(bad)


# ── renommer ──────────────────────────────────────────────────────────────────────────────────


class TestRename:
    async def test_the_document_and_every_link_to_it_follow(self, env):
        p = await env.make("Place", label="Paris")
        other = await env.make("Place", label="Lyon")
        v1 = await env.make("Visit", place=p["name"], stops=[{"place": other["name"]}, {"place": p["name"]}])
        v2 = await env.make("Visit", place=other["name"])
        note = await env.make("Note", ref_type="Place", ref_name=p["name"])
        await env.svc.create(ALICE, "Gadget", {"label": "même nom"}, name=p["name"])  # un autre DocType qui porte le même nom
        gadget_note = await env.make("Note", ref_type="Gadget", ref_name=p["name"])
        renamed = await env.svc.rename(ALICE, "Place", p["name"], "Paris 8e")
        assert renamed["name"] == "Paris 8e" and renamed["data"] == p["data"]
        with pytest.raises(NotFound):
            await env.svc.get(ALICE, "Place", p["name"])
        got = (await env.svc.get(ALICE, "Visit", v1["name"]))["data"]
        assert got["place"] == "Paris 8e" and [s["place"] for s in got["stops"]] == [other["name"], "Paris 8e"]
        assert (await env.svc.get(ALICE, "Visit", v2["name"]))["data"]["place"] == other["name"]  # un autre lieu : intact
        assert (await env.svc.get(ALICE, "Note", note["name"]))["data"]["ref_name"] == "Paris 8e"
        assert (await env.svc.get(ALICE, "Note", gadget_note["name"]))["data"]["ref_name"] == p["name"]  # elle visait un Gadget, pas ce Place

    async def test_history_comments_and_unique_values_follow_the_document(self, env):
        c = await env.make("Contact", email="a@x.fr")  # Contact n'est pas renommable : on teste sur un Place avec commentaire
        assert c["name"]
        p = await env.make("Place", label="Paris")
        await env.svc.update(ALICE, "Place", p["name"], {"label": "Paris!"}, p["version"])
        renamed = await env.svc.rename(ALICE, "Place", p["name"], "Capitale")
        history = await env.svc.history(ALICE, "Place", "Capitale")
        actions = [h["action"] for h in history["items"]]
        assert actions[0] == "rename" and set(actions) == {"rename", "update", "create"}  # l'historique a suivi, avec l'entrée du renommage
        assert history["items"][0]["changes"] == {"name": {"old": p["name"], "new": "Capitale"}}
        assert renamed["version"] == p["version"] + 2

    async def test_the_new_name_is_taken_or_unusable(self, env):
        a = await env.make("Place", label="A")
        b = await env.make("Place", label="B")
        with pytest.raises(DoctypeError) as e:
            await env.svc.rename(ALICE, "Place", a["name"], b["name"])
        assert e.value.status == 409
        for bad in ("", "a/b", "new", " x"):
            with pytest.raises(ValidationFailed):
                await env.svc.rename(ALICE, "Place", a["name"], bad)
        with pytest.raises(ValidationFailed) as e:
            await env.svc.rename(ALICE, "Place", a["name"], a["name"])
        assert e.value.code == "rename_same"

    async def test_a_refusal_midway_leaves_everything_as_it_was(self, env):
        a = await env.make("Place", label="A")
        v = await env.make("Visit", place=a["name"])
        b = await env.make("Place", label="B")
        with pytest.raises(DoctypeError):
            await env.svc.rename(ALICE, "Place", a["name"], b["name"])
        assert (await env.svc.get(ALICE, "Visit", v["name"]))["data"]["place"] == a["name"]
        assert (await env.svc.get(ALICE, "Place", a["name"]))["data"]["label"] == "A"

    async def test_only_a_doctype_that_allows_it_renames(self, env):
        c = await env.make("Company", title="Acme")
        with pytest.raises(ValidationFailed) as e:
            await env.svc.rename(ALICE, "Company", c["name"], "autre")
        assert e.value.code == "rename_not_allowed"

    async def test_it_needs_the_right_to_write_and_sees_only_its_organisation(self, env):
        p = await env.make("Place", label="A")
        reader = make_ctx("acme", "rita", permissions=["crm:place:read"])
        with pytest.raises(DoctypeError) as e:
            await env.svc.rename(reader, "Place", p["name"], "B")
        assert e.value.status == 403
        with pytest.raises(NotFound):
            await env.svc.rename(GLOBEX, "Place", p["name"], "B")  # une autre organisation ne le voit pas

    async def test_a_submitted_document_is_not_renamed(self, env, db, tables):
        reg = DocTypeRegistry()
        reg.register(parse_definition({"schema_version": 1, "name": "Deed", "module": "crm", "submittable": True, "allow_rename": True, "fields": [{"fieldname": "f", "fieldtype": "Text"}]}))
        svc = DocumentService(reg, db, tables, Log(), hooks=HookRegistry())
        ctx = make_ctx("acme", "alice", permissions=["crm:deed:read", "crm:deed:write", "crm:deed:submit"])
        d = await svc.create(ctx, "Deed", {})
        d = await svc.submit(ctx, "Deed", d["name"], d["version"])
        with pytest.raises(DocumentLocked):
            await svc.rename(ctx, "Deed", d["name"], "autre")

    async def test_an_event_tells_the_old_and_new_name(self, env):
        p = await env.make("Place", label="A")
        await env.svc.rename(ALICE, "Place", p["name"], "B")
        assert ("doctype.Place.after_rename", {"doctype": "Place", "old_name": p["name"], "name": "B", "tenant_id": "acme", "user": "alice"}) in env.bus.sent


# ── DynamicLink ───────────────────────────────────────────────────────────────────────────────


class TestDynamicLink:
    async def test_the_target_is_the_doctype_the_type_field_names(self, env):
        place = await env.make("Place", label="Paris")
        product = await env.make("Product", title="Vis")
        ok = await env.make("Note", ref_type="Place", ref_name=place["name"])
        assert ok["data"]["ref_name"] == place["name"]
        await env.make("Note", ref_type="Product", ref_name=product["name"])
        with pytest.raises(ValidationFailed) as e:  # ce nom est un Place, pas un Product
            await env.make("Note", ref_type="Product", ref_name=place["name"])
        assert codes(e) == {"link_not_found"} and e.value.details[0]["field"] == "ref_name"

    async def test_it_needs_a_type_and_the_type_must_be_citable(self, env):
        place = await env.make("Place", label="Paris")
        with pytest.raises(ValidationFailed) as e:
            await env.make("Note", ref_name=place["name"])
        assert codes(e) == {"link_not_found"}

    async def test_an_unreadable_target_looks_like_a_missing_one(self, env):
        place = await env.make("Place", label="Paris")
        blind = make_ctx("acme", "bea", permissions=["crm:note:read", "crm:note:write"])
        with pytest.raises(ValidationFailed) as e:
            await env.svc.create(blind, "Note", {"ref_type": "Place", "ref_name": place["name"]})
        assert codes(e) == {"link_not_found"}

    async def test_the_target_cannot_be_deleted_while_cited_but_a_namesake_of_another_type_is_free(self, env):
        place = await env.make("Place", label="Paris")
        await env.make("Note", ref_type="Place", ref_name=place["name"])
        with pytest.raises(DocumentInUse):
            await env.svc.delete(ALICE, "Place", place["name"])
        free = await env.make("Place", label="Lyon")
        await env.svc.delete(ALICE, "Place", free["name"])

    async def test_changing_only_the_type_rechecks_the_link(self, env):
        place = await env.make("Place", label="Paris")
        note = await env.make("Note", ref_type="Place", ref_name=place["name"])
        with pytest.raises(ValidationFailed):
            await env.svc.update(ALICE, "Note", note["name"], {"ref_type": "Product", "ref_name": place["name"]}, note["version"])

    async def test_it_filters_like_a_link(self, env):
        place = await env.make("Place", label="Paris")
        await env.make("Note", ref_type="Place", ref_name=place["name"])
        got = await env.svc.list(ALICE, "Note", filters={"ref_name": place["name"]}, limit=10)
        assert got["total"] == 1


class TestDynamicLinkSchema:
    def test_the_type_field_must_exist_and_be_text_like(self):
        bad = {"schema_version": 1, "name": "X", "module": "m", "fields": [{"fieldname": "d", "fieldtype": "DynamicLink", "options": "kind"}]}
        with pytest.raises(InvalidDefinition, match="DynamicLink"):
            parse_definition(bad)

    def test_a_select_type_field_may_only_offer_loaded_doctypes(self):
        reg = DocTypeRegistry()
        reg.register(parse_definition({
            "schema_version": 1, "name": "X", "module": "m",
            "fields": [{"fieldname": "kind", "fieldtype": "Select", "options": ["Ghost"]}, {"fieldname": "d", "fieldtype": "DynamicLink", "options": "kind"}],
        }))
        with pytest.raises(InvalidDefinition, match="Ghost"):
            reg.validate_links()
