"""`depends_on`, `mandatory_depends_on`, `read_only_depends_on` : ce que le SERVEUR revérifie à l'écriture (D22)."""

from __future__ import annotations

import pytest

from tests.conftest import make_ctx
from xcore_doctype import DocumentValidationError
from xcore_doctype.builder import CompiledDocType
from xcore_doctype.context import RequestContext
from xcore_doctype.document import DocumentService
from xcore_doctype.errors import ValidationFailed
from xcore_doctype.hooks import HookRegistry
from xcore_doctype.registry import DocTypeRegistry
from xcore_doctype.schema import parse_definition

FIELDS = [
    {"fieldname": "type", "fieldtype": "Select", "options": ["Particulier", "Entreprise"]},
    {"fieldname": "company_id", "fieldtype": "Text", "required": True, "depends_on": "type == 'Entreprise'"},
    {"fieldname": "credit_limit", "fieldtype": "Number", "default": 0},
    {"fieldname": "approver", "fieldtype": "Text", "mandatory_depends_on": "credit_limit > 1000"},
    {"fieldname": "locked", "fieldtype": "Boolean"},
    {"fieldname": "note", "fieldtype": "Text", "read_only_depends_on": "locked"},
    {"fieldname": "plain", "fieldtype": "Text", "required": True},
]


def compiled(fields=FIELDS):
    return CompiledDocType.compile(parse_definition({"schema_version": 1, "name": "T", "module": "t", "fields": fields}))


def errors(c, payload, **kw):
    with pytest.raises(ValidationFailed) as e:
        c.validate_data(payload, **kw)
    return {d["field"]: d["type"] for d in e.value.details}


BASE = {"plain": "x"}


class TestRequiredAndVisibility:
    def test_a_required_field_that_depends_on_is_required_only_when_visible(self):
        c = compiled()
        assert errors(c, {**BASE, "type": "Entreprise"}) == {"company_id": "missing"}  # visible : obligatoire
        assert c.validate_data({**BASE, "type": "Particulier"})["plain"] == "x"  # masqué : plus obligatoire
        assert c.validate_data(BASE)  # `type` absent = null : masqué aussi

    def test_visible_and_filled_is_fine(self):
        assert c_ok(compiled(), {**BASE, "type": "Entreprise", "company_id": "FR-1"})["company_id"] == "FR-1"

    def test_a_plain_required_field_is_still_enforced_by_the_model(self):
        assert errors(compiled(), {}) == {"plain": "missing"}

    def test_hidden_values_are_kept_not_stripped(self):
        """Un champ masqué garde sa valeur : l'utilisateur qui rebascule ne la perd pas, et le serveur n'invente rien."""
        out = compiled().validate_data({**BASE, "type": "Particulier", "company_id": "OLD"})
        assert out["company_id"] == "OLD"

    def test_all_errors_are_reported_at_once_in_the_usual_shape(self):
        with pytest.raises(ValidationFailed) as e:
            compiled().validate_data({"type": "Entreprise", "credit_limit": 5000})
        # Ordre déterministe : le modèle d'abord. `plain` manque, donc seule cette erreur ; les conditions ne sont évaluées
        # que sur un payload que le modèle a accepté.
        assert {d["field"] for d in e.value.details} == {"plain"}
        full = errors(compiled(), {**BASE, "type": "Entreprise", "credit_limit": 5000})
        assert full == {"company_id": "missing", "approver": "missing"}
        with pytest.raises(ValidationFailed) as e2:
            compiled().validate_data({**BASE, "type": "Entreprise", "credit_limit": 5000})
        assert all(set(d) == {"field", "msg", "type"} for d in e2.value.details)


class TestMandatoryDependsOn:
    def test_becomes_required_when_the_condition_holds(self):
        c = compiled()
        assert errors(c, {**BASE, "credit_limit": 1500}) == {"approver": "missing"}
        assert c.validate_data({**BASE, "credit_limit": 1500, "approver": "bob"})["approver"] == "bob"

    def test_not_required_otherwise(self):
        assert compiled().validate_data({**BASE, "credit_limit": 1000})  # 1000 > 1000 est faux
        assert compiled().validate_data(BASE)

    def test_the_condition_sees_defaults(self):
        c = compiled([{"fieldname": "n", "fieldtype": "Number", "default": 5}, {"fieldname": "m", "fieldtype": "Text", "mandatory_depends_on": "n == 5"}])
        assert errors(c, {}) == {"m": "missing"}  # `n` vaut 5 par défaut, jamais envoyé

    def test_the_condition_is_evaluated_on_the_submitted_values_not_on_stale_ones(self):
        c = compiled()
        assert c.validate_data({**BASE, "credit_limit": 10}, old={"credit_limit": 9999})  # l'ancien état ne compte pas


class TestReadOnlyDependsOn:
    def test_a_change_is_refused_when_the_condition_held_before_the_edit(self):
        c = compiled()
        old = {"plain": "x", "locked": True, "note": "avant"}
        assert errors(c, {**BASE, "locked": True, "note": "après"}, old=old) == {"note": "read_only"}

    def test_unchanged_value_is_fine(self):
        c = compiled()
        old = {"plain": "x", "locked": True, "note": "avant"}
        assert c.validate_data({**BASE, "locked": True, "note": "avant"}, old=old)["note"] == "avant"

    def test_removing_a_locked_value_is_a_change(self):
        assert errors(compiled(), BASE | {"locked": True}, old={"plain": "x", "locked": True, "note": "avant"}) == {"note": "read_only"}

    def test_free_when_the_condition_was_false_before(self):
        assert compiled().validate_data({**BASE, "locked": True, "note": "x"}, old={"plain": "x", "note": "avant"})

    def test_unlocking_and_editing_in_one_save_is_refused_because_the_old_state_was_locked(self):
        """Évalué sur l'état d'AVANT : on ne peut pas se déverrouiller et modifier d'un seul enregistrement."""
        assert errors(compiled(), {**BASE, "locked": False, "note": "x"}, old={"plain": "x", "locked": True, "note": "avant"}) == {"note": "read_only"}

    def test_not_enforced_without_old_state(self):
        """À la création, ou pour le résultat d'un hook : un hook a le droit de poser un champ que l'interface verrouille."""
        assert compiled().validate_data({**BASE, "locked": True, "note": "posé par un hook"})


def c_ok(c, payload):
    return c.validate_data(payload)


# ── Au niveau du service : qui fournit `old`, et la sortie d'un hook ────────────────────────────


@pytest.fixture
def svc(db, tables):
    reg = DocTypeRegistry()
    reg.register(parse_definition({
        "schema_version": 1, "name": "T", "module": "t", "fields": FIELDS,
        "hooks": {"before_save": ["t.stamp"], "validate": []},
    }))
    hooks = HookRegistry()

    async def stamp(event):
        if event.data.get("note") == "AUTO":
            event.data["note"] = "posé par le serveur"

    hooks.register("t.stamp", stamp)

    class Log:
        def info(self, *a, **k): ...
        def error(self, *a, **k): ...

    return DocumentService(reg, db, tables, Log(), hooks=hooks)


CTX = make_ctx("acme", "alice", permissions=["t:t:read", "t:t:write", "t:t:delete"])


async def test_service_enforces_read_only_on_client_updates(svc):
    doc = await svc.create(CTX, "T", {**BASE, "locked": True, "note": "avant"})
    with pytest.raises(ValidationFailed) as e:
        await svc.update(CTX, "T", doc["name"], {**BASE, "locked": True, "note": "après"}, doc["version"])
    assert e.value.details == [{"field": "note", "msg": "Champ en lecture seule", "type": "read_only"}]
    again = await svc.update(CTX, "T", doc["name"], {**BASE, "locked": True, "note": "avant", "credit_limit": 3}, doc["version"])
    assert again["data"]["credit_limit"] == 3


async def test_a_hook_may_set_a_field_the_ui_locks(svc):
    """Le `before_save` écrit dans `note` alors que `locked` la verrouille pour les clients : permis (c'est le serveur)."""
    doc = await svc.create(CTX, "T", {**BASE, "locked": True, "note": "avant"})
    out = await svc.update(CTX, "T", doc["name"], {**BASE, "locked": True, "note": "avant"}, doc["version"])
    assert out["data"]["note"] == "avant"  # inchangé : le hook ne réagit qu'à "AUTO"
    with pytest.raises(ValidationFailed):  # le CLIENT, lui, ne peut pas envoyer AUTO sur un champ verrouillé
        await svc.update(CTX, "T", doc["name"], {**BASE, "locked": True, "note": "AUTO"}, out["version"])
    free = await svc.create(CTX, "T", {**BASE, "note": "AUTO"})
    assert free["data"]["note"] == "posé par le serveur"


async def test_service_applies_conditions_on_create_too(svc):
    with pytest.raises(ValidationFailed) as e:
        await svc.create(CTX, "T", {**BASE, "type": "Entreprise"})
    assert e.value.details[0]["field"] == "company_id"
