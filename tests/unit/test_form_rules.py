"""Habillage de formulaire, retouches d'ÉCRITURE (`read_only`, `mandatory`, `default`) : validées quand on enregistre l'habillage, montrées par
`/meta` et APPLIQUÉES par le serveur à l'écriture d'un client de l'organisation (`overrides.form_rules`, `builder.FormRules`).

Un habillage ne fait que RESSERRER le YAML ; les hooks en sont exempts (comme du `read_only` du YAML) ; une autre organisation n'en voit rien.
Chaque test tourne sur SQLite et, si configuré, sur PostgreSQL.
"""

from __future__ import annotations

import itertools
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
import yaml

from tests.conftest import make_ctx
from xcore_doctype.builder import NO_RULES
from xcore_doctype.document import DocumentService
from xcore_doctype.errors import ValidationFailed
from xcore_doctype.hooks import HookRegistry
from xcore_doctype.overrides import (
    MAX_FORM_TWEAKS,
    MAX_OVERRIDE_BYTES,
    apply_form_override,
    changed_rules,
    form_rules,
    validate_dashboard_override,
    validate_form_override,
    validate_workspace_override,
)
from xcore_doctype.query import StoredDoc
from xcore_doctype.registry import DocTypeRegistry
from xcore_doctype.schema import parse_definition
from xcore_doctype.sources import DocumentSources, ReadOnlySource, content_version

# 23 h 30 UTC le 17 : déjà le 18 à Auckland (UTC+13 en octobre). Un défaut `today` d'habillage suit le fuseau de l'organisation.
NOW = datetime(2026, 10, 17, 23, 30, tzinfo=UTC)
AUCKLAND = ZoneInfo("Pacific/Auckland")

LINE = {"schema_version": 1, "name": "Ticket Line", "istable": True, "fields": [{"fieldname": "qty", "fieldtype": "Number"}]}
CLIENT = {"schema_version": 1, "name": "Client", "module": "crm", "fields": [
    {"fieldname": "city", "fieldtype": "Text"},
    {"fieldname": "client_name", "fieldtype": "Text"},
    {"fieldname": "kind", "fieldtype": "Select", "options": ["Particulier", "Société"]},
]}
TICKET = {
    "schema_version": 1,
    "name": "Ticket",
    "module": "crm",
    "submittable": True,
    "hooks": {"before_save": ["t.before_save"], "on_submit": ["t.on_submit"]},
    "fields": [
        {"fieldname": "title", "fieldtype": "Text", "required": True},
        {"fieldname": "status", "fieldtype": "Select", "options": ["Ouvert", "Fermé"], "default": "Ouvert"},
        {"fieldname": "ref", "fieldtype": "Text", "read_only": True},
        {"fieldname": "prio", "fieldtype": "Number"},
        {"fieldname": "amount", "fieldtype": "Decimal", "decimals": 2},
        {"fieldname": "due", "fieldtype": "Date"},
        {"fieldname": "reason", "fieldtype": "Text", "depends_on": "status == 'Fermé'"},
        {"fieldname": "note", "fieldtype": "Text", "hidden": True},
        {"fieldname": "secret", "fieldtype": "Password"},
        {"fieldname": "photo", "fieldtype": "Image"},
        {"fieldname": "client", "fieldtype": "Link", "options": "Client"},
        {"fieldname": "city", "fieldtype": "Text", "fetch_from": "client.city"},
        {"fieldname": "lines", "fieldtype": "Table", "options": "Ticket Line"},
        {"fieldname": "code", "fieldtype": "Text", "unique": True},
        {"fieldname": "comment", "fieldtype": "Text", "allow_on_submit": True},
        {"fieldname": "urgent", "fieldtype": "Boolean"},
        {"fieldname": "stamp", "fieldtype": "Datetime"},
        {"fieldname": "payload", "fieldtype": "JSON"},
        {"fieldname": "body", "fieldtype": "TextEditor"},
        # Les conditions du YAML : obligatoire au-delà de 10 (comme `Customer.approver`), figé une fois fermé.
        {"fieldname": "approver", "fieldtype": "Text", "mandatory_depends_on": "prio > 10"},
        {"fieldname": "closed_by", "fieldtype": "Text", "read_only_depends_on": "status == 'Fermé'"},
    ],
}
SETTINGS = {
    "schema_version": 1,
    "name": "Crm Settings",
    "module": "crm",
    "single": True,
    "fields": [
        {"fieldname": "motto", "fieldtype": "Text"},
        {"fieldname": "level", "fieldtype": "Int", "default": 1},
        {"fieldname": "company", "fieldtype": "Text", "required": True},
    ],
}
LEAD = {"schema_version": 1, "name": "Lead", "module": "crm", "source": "memory", "fields": [
    {"fieldname": "lead_name", "fieldtype": "Text"}, {"fieldname": "stage", "fieldtype": "Select", "options": ["Nouveau", "Qualifié"]},
]}
# Comme `User Profile` ou `Customer` : chacun écrit les SIENS (`crm:profile:own`, `if_owner`), un gestionnaire tous (`crm:profile:write`).
PROFILE = {"schema_version": 1, "name": "Profile", "module": "crm", "fields": [
    {"fieldname": "full_name", "fieldtype": "Text", "required": True},
    {"fieldname": "phone", "fieldtype": "Text"},
    {"fieldname": "job_title", "fieldtype": "Text"},
], "permissions": {
    "read": ["crm:profile:read"],
    "write": ["crm:profile:write", {"grant": "crm:profile:own", "if_owner": True}],
    "create": ["crm:profile:write", "crm:profile:own"],
}}

GRANTS = [f"crm:{t}:{a}" for t in ("ticket", "client", "crm_settings", "lead", "profile") for a in ("read", "write", "delete", "submit", "cancel")]
ALICE = make_ctx("acme", "alice", permissions=GRANTS)
GLOBEX = make_ctx("globex", "gus", permissions=GRANTS)
OLGA = make_ctx("acme", "olga", permissions=["crm:profile:read", "crm:profile:own"])  # n'écrit que SES profils


class Log:
    def info(self, *a, **k): ...
    def error(self, *a, **k): ...


class MemorySource(ReadOnlySource):
    """Une source en mémoire pour le DocType virtuel `Lead` : l'habillage s'applique aussi à ce que le moteur ne stocke pas."""

    def __init__(self) -> None:
        self.docs: dict[str, StoredDoc] = {}
        self._ids = itertools.count(1)

    async def fetch_all(self, ctx, doctype):
        return list(self.docs.values())

    async def insert(self, ctx, doctype, data):
        doc = StoredDoc(doctype.name, f"lead-{next(self._ids)}", dict(data), ctx.owner_id, NOW, NOW, content_version(data))
        self.docs[doc.name] = doc
        return doc

    async def update(self, ctx, doctype, name, data, version):
        old = self.docs[name]
        doc = StoredDoc(doctype.name, name, dict(data), old.owner, old.creation, NOW, content_version(data))
        self.docs[name] = doc
        return doc


class Env:
    def __init__(self, db, tables) -> None:
        reg = DocTypeRegistry()
        for raw in (LINE, CLIENT, TICKET, SETTINGS, LEAD, PROFILE):
            reg.register(parse_definition(raw))
        reg.validate_links()
        self.patch: dict = {}  # ce que le hook `t.before_save` pose (None : retire le champ)
        self.spawn: dict | None = None  # le Client que le hook `t.on_submit` crée dans la transaction (`tx.create`)
        self.spawned: list[dict] = []
        hooks = HookRegistry()

        async def before_save(event):
            for key, value in self.patch.items():
                if value is None:
                    event.data.pop(key, None)
                else:
                    event.data[key] = value

        async def on_submit(event):
            if self.spawn is not None:
                self.spawned.append(await event.tx.create("Client", self.spawn))

        hooks.register("t.before_save", before_save)
        hooks.register("t.on_submit", on_submit)
        sources = DocumentSources()
        sources.register("crm", "memory", MemorySource())
        self.svc = DocumentService(reg, db, tables, Log(), hooks=hooks, timezone=AUCKLAND, clock=lambda: NOW, sources=sources)
        self.reg = reg

    async def dress(self, fields: dict, ctx=ALICE, doctype: str = "Ticket") -> dict:
        return await self.svc.save_form_override(ctx, doctype, {"fields": fields})

    async def make(self, ctx=ALICE, **data) -> dict:
        return await self.svc.create(ctx, "Ticket", data)


@pytest.fixture
def env(db, tables):
    return Env(db, tables)


def refusal(e: pytest.ExceptionInfo) -> dict[str, str]:
    return {d["field"]: d["type"] for d in (e.value.details or [])}


def ticket():
    reg = DocTypeRegistry()
    for raw in (LINE, CLIENT, TICKET):
        reg.register(parse_definition(raw))
    return reg.get("Ticket")


# ── Validation de l'habillage ─────────────────────────────────────────────────────────────────


class TestValidation:
    def test_write_properties_are_kept_in_their_canonical_form(self):
        got = validate_form_override(ticket(), {"fields": {
            "prio": {"mandatory": True, "read_only": False},
            "amount": {"default": "12.5"},  # un Decimal se range sous sa forme canonique, comme à l'écriture
            "due": {"default": "today"},
            "status": {"default": "Fermé", "label": "État"},
            "title": {"mandatory": True, "read_only": True, "default": "Sans titre"},  # obligatoire du YAML : verrouillé, il lui faut un défaut
            "ref": {"read_only": True},  # déjà en lecture seule dans le YAML : redondant, pas une erreur
            "client": {"read_only": True},
            "note": {"default": None},  # null : pas de défaut d'habillage
            "urgent": {"default": False},  # `false` est une valeur par défaut, pas une absence
        }})
        assert got["fields"] == {
            "prio": {"mandatory": True, "read_only": False},
            "amount": {"default": "12.50"},
            "due": {"default": "today"},
            "status": {"label": "État", "default": "Fermé"},
            "title": {"default": "Sans titre", "read_only": True, "mandatory": True},
            "ref": {"read_only": True},
            "client": {"read_only": True},
            "urgent": {"default": False},
        }

    @pytest.mark.parametrize(
        "tweaks, fragment",
        [
            # Un habillage ne fait que resserrer.
            ({"title": {"mandatory": False}}, "obligatoire dans la définition"),
            ({"ref": {"read_only": False}}, "lecture seule dans la définition"),
            # Un champ obligatoire doit pouvoir être rempli.
            ({"note": {"mandatory": True}}, "caché"),
            ({"prio": {"hidden": True, "mandatory": True}}, "caché"),
            ({"ref": {"mandatory": True}}, "lecture seule"),
            ({"prio": {"read_only": True, "mandatory": True}}, "lecture seule"),
            # Un champ obligatoire du YAML ne se verrouille que s'il a une valeur par défaut (le Desk ne saisit pas un champ verrouillé).
            ({"title": {"read_only": True}}, "sans valeur par défaut"),
            # Ni table, ni secret, ni fichier, ni `fetch_from` : aucune retouche d'écriture, le serveur les écrit à sa façon.
            ({"lines": {"mandatory": True}}, "table"),
            ({"lines": {"read_only": True}}, "table"),
            ({"lines": {"default": []}}, "table"),
            ({"secret": {"mandatory": True}}, "secret"),
            ({"secret": {"read_only": True}}, "secret"),
            ({"secret": {"default": "s3cret"}}, "secret"),
            ({"photo": {"mandatory": True}}, "fichier"),
            ({"photo": {"read_only": True}}, "fichier"),
            ({"photo": {"default": "0" * 32}}, "fichier"),
            ({"city": {"mandatory": True}}, "fetch_from"),
            ({"city": {"read_only": True}}, "fetch_from"),
            ({"city": {"default": "Lyon"}}, "fetch_from"),
            # Défaut : validé par le type, strictement ; jamais sur une valeur unique (la seconde création serait un doublon).
            ({"code": {"default": "T-1"}}, "unique"),
            ({"amount": {"default": 1.5}}, "écriture stricte"),
            ({"prio": {"default": "12"}}, "écriture stricte"),
            ({"status": {"default": "Perdu"}}, "écriture stricte"),
            ({"due": {"default": "2026-1-5"}}, "écriture stricte"),
            ({"prio": {"mandatory": "oui"}}, "booléen"),
            ({"prio": {"read_only": 1}}, "booléen"),
            # Les types sans valeur par défaut dans le YAML n'en reçoivent pas d'un habillage (`schema.NO_DEFAULT_TYPES`).
            ({"payload": {"default": {"a": 1}}}, "un champ JSON n'a pas de valeur par défaut"),
            ({"body": {"default": "<p>x</p>"}}, "un champ TextEditor n'a pas de valeur par défaut"),
            # Une condition du YAML : verrouillé, `approver` ne se remplirait plus quand `prio > 10` l'exige ; exigé, `closed_by` resterait
            # figé une fois le ticket fermé (ni rempli, ni rouvert).
            ({"approver": {"read_only": True}}, "mandatory_depends_on"),
            ({"approver": {"read_only": True, "default": "Zoé"}}, "mandatory_depends_on"),
            ({"closed_by": {"mandatory": True}}, "read_only_depends_on"),
            # Masquer un champ que le YAML exige sans défaut : le Desk ne le montre ni ne le vérifie, le serveur l'exige, plus rien ne se crée.
            ({"title": {"hidden": True}}, "ne se masque pas"),
            ({"title": {"hidden": True, "mandatory": True}}, "ne se masque pas"),
            ({"approver": {"hidden": True}}, "ne se masque pas"),
        ],
    )
    def test_what_would_loosen_or_break_the_yaml_is_refused(self, tweaks, fragment):
        with pytest.raises(ValidationFailed) as e:
            validate_form_override(ticket(), {"fields": tweaks})
        assert fragment in str(e.value)

    def test_a_required_field_with_a_default_may_be_hidden(self):
        # Le défaut (de l'habillage, ou du YAML pour `status`) : le Desk pré-remplit le champ masqué et l'envoie.
        got = validate_form_override(ticket(), {"fields": {"title": {"hidden": True, "default": "Sans titre"}, "approver": {"hidden": True, "default": "Zoé"}}})
        assert got["fields"]["title"] == {"hidden": True, "default": "Sans titre"} and got["fields"]["approver"] == {"hidden": True, "default": "Zoé"}

    def test_the_field_that_names_the_document_gets_no_default(self):
        reg = DocTypeRegistry()
        reg.register(parse_definition({"schema_version": 1, "name": "Code", "module": "crm", "naming": {"field": "code"},
                                       "fields": [{"fieldname": "code", "fieldtype": "Text", "required": True}]}))
        with pytest.raises(ValidationFailed, match="nomme le document"):  # chaque création sans code prendrait le même nom
            validate_form_override(reg.get("Code"), {"fields": {"code": {"default": "C-1"}}})

    def test_reading_is_tolerant_of_a_stored_override_that_became_invalid(self):
        rules = form_rules(ticket(), {"fields": {
            "gone": {"mandatory": True},  # champ disparu du YAML
            "note": {"mandatory": True},  # devenu caché dans le YAML
            "amount": {"default": 1.5, "read_only": True},  # le défaut ne vaut plus rien, la lecture seule tient toujours
            "prio": "pas un objet",
            "status": {"default": "Fermé"},
        }})
        assert rules.mandatory == frozenset() and rules.read_only == {"amount"} and dict(rules.defaults) == {"status": "Fermé"}
        assert form_rules(ticket(), {"fields": "pas un objet"}) is NO_RULES
        assert form_rules(ticket(), None) is NO_RULES

    def test_a_stored_rule_that_conditions_forbid_is_not_applied(self):
        # Enregistrée avant que le YAML n'ajoute la condition : relue, elle est ignorée (tolérante), elle ne bloque aucun état.
        assert form_rules(ticket(), {"fields": {"approver": {"read_only": True}, "closed_by": {"mandatory": True}}}) is NO_RULES

    def test_a_stored_hiding_of_a_duty_is_not_shown(self):
        """Devenu obligatoire dans le YAML après qu'on l'a masqué : `/meta` le montre de nouveau (sinon aucune création ne passe dans le Desk)."""
        def meta():
            return {"fields": [{"fieldname": "title", "required": True}, {"fieldname": "approver", "mandatory_depends_on": {"t": "lit", "v": True}},
                               {"fieldname": "prio"}, {"fieldname": "kind", "required": True, "default": "A"}]}
        config = {"fields": {name: {"hidden": True} for name in ("title", "approver", "prio", "kind")}}
        got = meta()
        apply_form_override(got, config, NO_RULES)
        assert [f.get("hidden", False) for f in got["fields"]] == [False, False, True, True]
        dressed = {"fields": {**config["fields"], "title": {"hidden": True, "default": "Sans titre"}}}
        got = meta()
        apply_form_override(got, dressed, form_rules(ticket(), dressed))  # avec un défaut d'habillage, le masquage tient
        assert got["fields"][0]["hidden"] is True and got["fields"][0]["default"] == "Sans titre"

    def test_meta_shows_what_the_server_will_apply(self):
        meta = {"fields": [{"fieldname": "prio", "required": False, "read_only": False}, {"fieldname": "status", "default": "Ouvert"}]}
        config = {"fields": {"prio": {"mandatory": True, "read_only": False}, "status": {"default": "Fermé"}}}
        apply_form_override(meta, config, form_rules(ticket(), config))
        assert meta["fields"][0] == {"fieldname": "prio", "required": True, "read_only": False}
        assert meta["fields"][1]["default"] == "Fermé"


class TestSize:
    def test_too_many_field_entries_are_refused_before_anything_else(self):
        with pytest.raises(ValidationFailed) as e:
            validate_form_override(ticket(), {"fields": {f"f{i}": {} for i in range(MAX_FORM_TWEAKS + 1)}})
        assert e.value.code == "override_too_large" and str(MAX_FORM_TWEAKS) in e.value.message

    @pytest.mark.parametrize("validate, config", [
        (validate_form_override, {"fields": {"title": {"label": "x" * MAX_OVERRIDE_BYTES}}}),
        (validate_form_override, {"layout": [{"section": "é" * (MAX_OVERRIDE_BYTES // 2), "columns": [["title"]]}]}),  # en OCTETS
        (validate_dashboard_override, {"blocks": [{"type": "header", "text": "x" * MAX_OVERRIDE_BYTES}]}),
        (lambda compiled, config: validate_workspace_override(DocTypeRegistry(), "crm", config), {"label": "x" * MAX_OVERRIDE_BYTES}),
    ])
    def test_a_configuration_heavier_than_the_bound_is_refused(self, validate, config):
        with pytest.raises(ValidationFailed) as e:
            validate(ticket(), config)
        assert e.value.code == "override_too_large" and str(MAX_OVERRIDE_BYTES) in e.value.message

    def test_a_configuration_under_the_bound_passes(self):
        assert validate_form_override(ticket(), {"fields": {"title": {"label": "x" * 1000}}})["fields"]["title"] == {"label": "x" * 1000}


# ── Qui règle les propriétés d'écriture ───────────────────────────────────────────────────────


class TestRuleAuthor:
    """Une règle d'écriture d'organisation lie tous les rédacteurs : seul qui peut modifier TOUS les documents du type la pose, la change ou la
    retire (sinon chacun lèverait celle qui le lie, ou en imposerait une aux documents des autres). Libellés, masquage, disposition : `write` suffit."""

    async def test_the_signal_the_desk_reads_is_the_scope_the_server_checks(self, env):
        assert (await env.svc.fetch_meta(OLGA, "Profile"))["rights"]["write"] == "restricted"
        assert (await env.svc.fetch_meta(ALICE, "Profile"))["rights"]["write"] == "all"

    @pytest.mark.parametrize("fields", [{"phone": {"read_only": True}}, {"job_title": {"mandatory": True}}, {"phone": {"default": "+33"}}])
    async def test_an_owner_only_writer_does_not_set_a_rule(self, env, fields):
        with pytest.raises(ValidationFailed) as e:
            await env.dress(fields, ctx=OLGA, doctype="Profile")
        assert e.value.code == "form_rules_reserved" and "TOUS les documents de Profile" in e.value.message
        assert refusal(e) == {f"fields.{name}": "form_rules_reserved" for name in fields}
        assert (await env.svc.form_override(ALICE, "Profile"))["config"] == {}

    async def test_an_owner_only_writer_keeps_the_rules_and_changes_the_rest(self, env):
        assert (await env.dress({"phone": {"label": "Tél."}}, ctx=OLGA, doctype="Profile"))["config"] == {"fields": {"phone": {"label": "Tél."}}}
        rules = {"phone": {"read_only": True}, "job_title": {"mandatory": True, "default": "Dev"}}
        await env.dress(rules, ctx=ALICE, doctype="Profile")
        # Les règles renvoyées telles quelles (le Desk renvoie tout le JSON) : un libellé change, rien d'autre.
        kept = {"phone": {"read_only": True, "label": "Téléphone"}, "job_title": {"mandatory": True, "default": "Dev"}}
        assert (await env.dress(kept, ctx=OLGA, doctype="Profile"))["config"]["fields"] == kept
        for changed, names in (
            ({"phone": {"label": "Téléphone"}, "job_title": {"mandatory": True, "default": "Dev"}}, {"phone"}),  # en retirer une
            ({**kept, "job_title": {"mandatory": True, "default": "PDG"}}, {"job_title"}),  # en changer une
            ({"phone": {"label": "Téléphone"}}, {"phone", "job_title"}),
        ):
            with pytest.raises(ValidationFailed) as e:
                await env.dress(changed, ctx=OLGA, doctype="Profile")
            assert set(refusal(e)) == {f"fields.{n}" for n in names}
        with pytest.raises(ValidationFailed) as e:  # retirer l'habillage, c'est retirer ses règles
            await env.svc.delete_form_override(OLGA, "Profile")
        assert e.value.code == "form_rules_reserved"
        made = await env.svc.create(OLGA, "Profile", {"full_name": "Olga", "phone": "0102"})
        with pytest.raises(ValidationFailed) as e:  # la règle tient toujours celle qui ne pouvait pas la lever
            await env.svc.update(OLGA, "Profile", made["name"], {**made["data"], "phone": "0999"}, made["version"])
        assert refusal(e) == {"phone": "read_only"}
        await env.svc.delete_form_override(ALICE, "Profile")  # le gestionnaire, lui, la retire

    async def test_an_owner_only_writer_removes_an_override_without_rules(self, env):
        await env.dress({"phone": {"label": "Tél."}}, ctx=ALICE, doctype="Profile")
        await env.svc.delete_form_override(OLGA, "Profile")
        assert (await env.svc.form_override(ALICE, "Profile"))["config"] == {}

    def test_rules_compare_as_the_server_applies_them(self):
        stale = {"fields": {"gone": {"mandatory": True}, "prio": {"read_only": True}}}  # `gone` n'existe plus : retirer sa retouche ne change rien
        assert changed_rules(ticket(), stale, {"fields": {"prio": {"read_only": True, "label": "P"}}}) == []
        assert changed_rules(ticket(), {"fields": {"prio": {"read_only": False}}}, {}) == []  # `false` n'est pas une règle
        assert changed_rules(ticket(), {"fields": {"prio": {"default": 1}}}, {"fields": {"prio": {"default": 1.0}}}) == ["prio"]
        assert changed_rules(ticket(), None, {"fields": {"due": {"default": "today"}, "prio": {"mandatory": True}}}) == ["due", "prio"]


# ── Application à l'écriture ──────────────────────────────────────────────────────────────────


class TestMeta:
    async def test_meta_reflects_the_write_properties_for_this_organisation_only(self, env):
        await env.dress({"prio": {"mandatory": True}, "client": {"read_only": True}, "amount": {"default": "12.5"}, "due": {"default": "today"}})
        fields = {f["fieldname"]: f for f in (await env.svc.fetch_meta(ALICE, "Ticket"))["fields"]}
        assert fields["prio"]["required"] is True and fields["client"]["read_only"] is True
        assert fields["amount"]["default"] == "12.50" and fields["due"]["default"] == "today"
        other = {f["fieldname"]: f for f in (await env.svc.fetch_meta(GLOBEX, "Ticket"))["fields"]}
        assert other["prio"]["required"] is False and other["client"]["read_only"] is False and "default" not in other["amount"]


    async def test_the_override_endpoint_says_what_the_definition_imposes(self, env):
        await env.dress({"prio": {"mandatory": True}, "status": {"default": "Fermé"}, "note": {"label": "Note interne"}})
        got = await env.svc.form_override(ALICE, "Ticket")
        assert got["config"]["fields"]["prio"] == {"mandatory": True}
        # Le YAML seul, habillage à part : `/meta` ne le distingue plus (prio y est `required`), l'éditeur du Desk grise d'après lui.
        base = got["definition"]
        assert base["title"] == {"required": True} and base["ref"] == {"read_only": True} and base["note"] == {"hidden": True}
        assert base["status"] == {"default": "Ouvert"} and "prio" not in base
        # Le libellé de l'application, que `/meta` ne montre plus quand l'organisation en a donné un : le Form Builder dit les deux.
        assert got["labels"]["note"] != "Note interne" and set(got["labels"]) >= {"title", "prio", "note", "status"}


class TestMandatory:
    async def test_a_field_made_mandatory_is_required_from_a_client(self, env):
        await env.make(title="Avant")  # sans habillage, `prio` est facultatif
        await env.dress({"prio": {"mandatory": True}})
        with pytest.raises(ValidationFailed) as e:
            await env.make(title="Sans priorité")
        assert refusal(e) == {"prio": "missing"}
        made = await env.make(title="Avec", prio=2)
        with pytest.raises(ValidationFailed) as e:  # une mise à jour ne peut pas le vider non plus
            await env.svc.update(ALICE, "Ticket", made["name"], {"title": "Avec"}, made["version"])
        assert refusal(e) == {"prio": "missing"}
        await env.svc.delete_form_override(ALICE, "Ticket")
        await env.make(title="Après")  # habillage retiré : le YAML reprend la main

    async def test_a_mandatory_field_hidden_by_its_condition_is_not_required(self, env):
        await env.dress({"reason": {"mandatory": True}})
        await env.make(title="Ouvert", status="Ouvert")  # `reason` n'est visible qu'une fois fermé
        with pytest.raises(ValidationFailed) as e:
            await env.make(title="Fermé", status="Fermé")
        assert refusal(e) == {"reason": "missing"}

    async def test_another_organisation_is_not_bound(self, env):
        await env.dress({"prio": {"mandatory": True}})
        await env.make(GLOBEX, title="Ailleurs")

    async def test_submit_refuses_a_draft_that_misses_a_field_made_mandatory_since(self, env):
        draft = await env.make(title="Brouillon")
        await env.dress({"prio": {"mandatory": True}})
        with pytest.raises(ValidationFailed) as e:
            await env.svc.submit(ALICE, "Ticket", draft["name"], draft["version"])
        assert refusal(e) == {"prio": "missing"}


    async def test_after_submit_only_the_fields_still_editable_are_bound(self, env):
        doc = await env.make(title="Soumis")
        doc = await env.svc.submit(ALICE, "Ticket", doc["name"], doc["version"])
        await env.dress({"prio": {"mandatory": True}, "comment": {"mandatory": True}})
        # `prio` est figé (il ne se remplira plus) : l'exiger rendrait le document soumis immodifiable. `comment` (allow_on_submit) l'est.
        with pytest.raises(ValidationFailed) as e:
            await env.svc.update(ALICE, "Ticket", doc["name"], doc["data"], doc["version"])
        assert refusal(e) == {"comment": "missing"}
        done = await env.svc.update(ALICE, "Ticket", doc["name"], {**doc["data"], "comment": "vu"}, doc["version"])
        assert done["data"]["comment"] == "vu" and "prio" not in done["data"]

    async def test_an_amendment_is_created_then_completed(self, env):
        doc = await env.make(title="Annulé")
        doc = await env.svc.submit(ALICE, "Ticket", doc["name"], doc["version"])
        doc = await env.svc.cancel(ALICE, "Ticket", doc["name"], doc["version"])
        await env.dress({"prio": {"mandatory": True}, "due": {"default": "today"}})
        # La copie se fait sans saisie : elle naît sans `prio` (sinon impossible à amender) mais reçoit les défauts de l'organisation.
        amended = await env.svc.amend(ALICE, "Ticket", doc["name"])
        assert "prio" not in amended["data"] and amended["data"]["due"] == "2026-10-18"
        with pytest.raises(ValidationFailed) as e:  # ... et la soumission, comme toute mise à jour, l'exige
            await env.svc.submit(ALICE, "Ticket", amended["name"], amended["version"])
        assert refusal(e) == {"prio": "missing"}
        fixed = await env.svc.update(ALICE, "Ticket", amended["name"], {**amended["data"], "prio": 1}, amended["version"])
        assert (await env.svc.submit(ALICE, "Ticket", fixed["name"], fixed["version"]))["docstatus"] == 1


class TestReadOnly:
    async def test_a_field_made_read_only_cannot_be_changed_by_a_client_update(self, env):
        await env.dress({"prio": {"read_only": True}})
        made = await env.make(title="T", prio=3)  # à la création il se pose, comme un `read_only` du YAML
        with pytest.raises(ValidationFailed) as e:
            await env.svc.update(ALICE, "Ticket", made["name"], {"title": "T", "prio": 4}, made["version"])
        assert refusal(e) == {"prio": "read_only"}
        kept = await env.svc.update(ALICE, "Ticket", made["name"], {"title": "T2", "prio": 3}, made["version"])
        assert kept["data"]["prio"] == 3 and kept["data"]["title"] == "T2"

    async def test_bulk_refuses_it_up_front(self, env):
        made = await env.make(title="T", prio=3)
        await env.dress({"prio": {"read_only": True}})
        with pytest.raises(ValidationFailed) as e:
            await env.svc.bulk(ALICE, "Ticket", "update", [made["name"]], data={"prio": 5})
        assert refusal(e) == {"prio": "bulk_field"}


class TestDefault:
    async def test_a_default_fills_what_a_creation_leaves_absent(self, env):
        await env.dress({"status": {"default": "Fermé"}, "amount": {"default": "12.5"}, "due": {"default": "today"}, "reason": {"default": "doublon"},
                         "stamp": {"default": "now"}})
        made = await env.make(title="T")
        assert made["data"]["status"] == "Fermé"  # avant le défaut du YAML (« Ouvert »)
        assert made["data"]["amount"] == "12.50" and made["data"]["due"] == "2026-10-18"  # aujourd'hui À AUCKLAND
        assert made["data"]["stamp"] == "2026-10-17T23:30:00Z"  # maintenant, en UTC
        chosen = await env.make(title="T", status="Ouvert", amount="1.00")
        assert chosen["data"]["status"] == "Ouvert" and chosen["data"]["amount"] == "1.00"  # ce que le client envoie gagne

    async def test_on_a_yaml_required_field_the_default_only_prefills_the_desk(self, env):
        """Un champ obligatoire du YAML n'a pas de défaut serveur, même déclaré (un client d'API DOIT l'envoyer) : celui de l'organisation non
        plus, sinon il le rendrait facultatif. `/meta` le donne au Desk, qui pré-remplit le formulaire et l'envoie."""
        await env.dress({"title": {"default": "Sans titre"}})
        with pytest.raises(ValidationFailed) as e:
            await env.make(prio=1)
        assert refusal(e) == {"title": "missing"}
        meta = {f["fieldname"]: f for f in (await env.svc.fetch_meta(ALICE, "Ticket"))["fields"]}
        assert meta["title"]["default"] == "Sans titre" and meta["title"]["required"] is True
        assert (await env.make(title="Sans titre"))["data"]["title"] == "Sans titre"

    async def test_a_default_is_for_creation_only(self, env):
        made = await env.make(title="T")
        await env.dress({"prio": {"default": 5}})
        updated = await env.svc.update(ALICE, "Ticket", made["name"], {"title": "T2"}, made["version"])
        assert "prio" not in updated["data"]

    async def test_a_single_document_opens_and_saves_with_the_org_default(self, env):
        await env.dress({"motto": {"default": "Bonjour"}, "level": {"default": 3}, "company": {"default": "ACME"}}, doctype="Crm Settings")
        opened = await env.svc.get(ALICE, "Crm Settings", "Crm Settings")
        # Pas encore enregistré : la page s'ouvre pré-remplie, y compris le champ obligatoire (que le Desk renverra).
        assert opened["version"] == 0 and opened["data"] == {"motto": "Bonjour", "level": 3, "company": "ACME"}
        with pytest.raises(ValidationFailed) as e:  # ... mais le serveur ne le pose pas lui-même
            await env.svc.update(ALICE, "Crm Settings", "Crm Settings", {}, 0)
        assert refusal(e) == {"company": "missing"}
        saved = await env.svc.update(ALICE, "Crm Settings", "Crm Settings", {"company": "ACME"}, 0)
        assert saved["data"] == {"motto": "Bonjour", "level": 3, "company": "ACME"}
        assert (await env.svc.get(GLOBEX, "Crm Settings", "Crm Settings"))["data"] == {"level": 1}


class TestHooksAndSources:
    async def test_trusted_hooks_are_exempt(self, env):
        await env.dress({"prio": {"mandatory": True}, "amount": {"read_only": True}})
        made = await env.make(title="T", prio=1, amount="1.00")
        env.patch = {"amount": "99.00", "prio": None}  # le hook change un champ verrouillé et vide un champ obligatoire
        updated = await env.svc.update(ALICE, "Ticket", made["name"], {"title": "T2", "prio": 1, "amount": "1.00"}, made["version"])
        assert updated["data"]["amount"] == "99.00" and "prio" not in updated["data"]

    async def test_a_hook_writing_in_the_transaction_is_exempt(self, env):
        await env.dress({"client_name": {"mandatory": True}, "kind": {"default": "Société"}}, doctype="Client")
        with pytest.raises(ValidationFailed):  # un client de l'organisation y est tenu...
            await env.svc.create(ALICE, "Client", {"city": "Lyon"})
        env.spawn = {"city": "Lyon"}  # ... un hook qui écrit dans la transaction (`tx.create`) ne l'est pas : il écrit ce que sa règle dit
        doc = await env.make(title="T")
        await env.svc.submit(ALICE, "Ticket", doc["name"], doc["version"])
        assert env.spawned[0]["data"] == {"city": "Lyon"}  # ni obligation, ni défaut d'habillage

    async def test_a_virtual_doctype_is_bound_too(self, env):
        await env.dress({"lead_name": {"mandatory": True}, "stage": {"default": "Nouveau"}}, doctype="Lead")
        with pytest.raises(ValidationFailed) as e:
            await env.svc.create(ALICE, "Lead", {})
        assert refusal(e) == {"lead_name": "missing"}
        made = await env.svc.create(ALICE, "Lead", {"lead_name": "Ada"})
        assert made["data"] == {"lead_name": "Ada", "stage": "Nouveau"}

    async def test_a_virtual_doctype_update_is_bound_too(self, env):
        made = await env.svc.create(ALICE, "Lead", {"lead_name": "Ada", "stage": "Nouveau"})
        await env.dress({"lead_name": {"mandatory": True}, "stage": {"read_only": True}}, doctype="Lead")
        with pytest.raises(ValidationFailed) as e:
            await env.svc.update(ALICE, "Lead", made["name"], {"lead_name": "Ada", "stage": "Qualifié"}, made["version"])
        assert refusal(e) == {"stage": "read_only"}
        with pytest.raises(ValidationFailed) as e:
            await env.svc.update(ALICE, "Lead", made["name"], {"stage": "Nouveau"}, made["version"])
        assert refusal(e) == {"lead_name": "missing"}
        done = await env.svc.update(ALICE, "Lead", made["name"], {"lead_name": "Ada L.", "stage": "Nouveau"}, made["version"])
        assert done["data"] == {"lead_name": "Ada L.", "stage": "Nouveau"}


class TestExtendedDocType:
    """D26 : les champs qu'un autre plugin ajoute se retouchent comme les autres ; l'extension retirée, la retouche dort (lecture tolérante)."""

    async def test_a_field_added_by_an_extension_is_bound_then_forgotten_with_it(self, db, tables, tmp_path):
        def put(directory, name, doc):
            directory.mkdir(parents=True, exist_ok=True)
            (directory / f"{name}.yaml").write_text(yaml.safe_dump(doc, allow_unicode=True))
            return directory

        reg = DocTypeRegistry()
        reg.load_dir(put(tmp_path / "crm", "client", CLIENT), owner="crm")
        extension = {"schema_version": 1, "doctype": "Client", "fields": [{"fieldname": "sales_ref", "fieldtype": "Text"}]}
        ext_dir = put(tmp_path / "sales", "client", extension)
        reg.load_extensions(ext_dir, owner="sales")
        svc = DocumentService(reg, db, tables, Log(), timezone=AUCKLAND, clock=lambda: NOW)
        await svc.save_form_override(ALICE, "Client", {"fields": {"sales_ref": {"mandatory": True}}})
        with pytest.raises(ValidationFailed) as e:
            await svc.create(ALICE, "Client", {"city": "Lyon"})
        assert refusal(e) == {"sales_ref": "missing"}
        reg.unregister_owner("sales")  # l'extension s'en va : son champ n'existe plus, la retouche ne vise plus rien
        await svc.create(ALICE, "Client", {"city": "Lyon"})
        assert "sales_ref" not in {f["fieldname"] for f in (await svc.fetch_meta(ALICE, "Client"))["fields"]}
        reg.load_extensions(ext_dir, owner="sales")  # elle revient : la retouche enregistrée reprend effet
        with pytest.raises(ValidationFailed):
            await svc.create(ALICE, "Client", {"city": "Lyon"})
