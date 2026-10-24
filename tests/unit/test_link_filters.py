"""`link_filters` (D29) : un champ Link ne propose — et n'accepte — que les documents qui satisfont ses filtres.

Le point du contrat : ce n'est PAS une aide d'interface. Le Desk s'en sert pour l'autocomplétion, mais un client d'API qui ne passe
pas par le Desk se fait refuser la même valeur, comme pour `target_status` (D23). Les fautes de définition, elles, échouent au
chargement et jamais à la première saisie.
"""

from __future__ import annotations

import pytest

from tests.conftest import make_ctx
from xcore_doctype.document import DocumentService
from xcore_doctype.errors import InvalidDefinition, ValidationFailed
from xcore_doctype.registry import DocTypeRegistry
from xcore_doctype.schema import parse_definition

PAYS = {
    "schema_version": 1, "name": "Pays", "module": "geo", "naming": {"field": "code"}, "title_field": "nom",
    "fields": [
        {"fieldname": "code", "fieldtype": "Text", "required": True},
        {"fieldname": "nom", "fieldtype": "Text", "required": True},
    ],
}
ENTREPOT = {
    "schema_version": 1, "name": "Entrepot", "module": "stock", "title_field": "nom",
    "fields": [
        {"fieldname": "nom", "fieldtype": "Text", "required": True},
        {"fieldname": "pays", "fieldtype": "Link", "options": "Pays"},
        {"fieldname": "actif", "fieldtype": "Boolean", "default": True},
        {"fieldname": "genre", "fieldtype": "Select", "options": ["Sec", "Froid"]},
    ],
}
# Le cas réel : la livraison se fait dans un entrepôt DU PAYS du bon de livraison, et jamais dans un entrepôt fermé.
LIVRAISON = {
    "schema_version": 1, "name": "Livraison", "module": "stock", "title_field": "reference",
    "fields": [
        {"fieldname": "reference", "fieldtype": "Text", "required": True},
        {"fieldname": "pays", "fieldtype": "Link", "options": "Pays"},
        {"fieldname": "entrepot", "fieldtype": "Link", "options": "Entrepot", "link_filters": {"pays": "$pays", "actif": True}},
        {"fieldname": "lignes", "fieldtype": "Table", "options": "Ligne de livraison"},
    ],
}
LIGNE = {
    "schema_version": 1, "name": "Ligne de livraison", "istable": True,
    "fields": [
        {"fieldname": "qte", "fieldtype": "Number"},
        {"fieldname": "genre", "fieldtype": "Select", "options": ["Sec", "Froid"]},
        {"fieldname": "entrepot", "fieldtype": "Link", "options": "Entrepot", "link_filters": {"genre": "$genre"}},
    ],
}
ALL = [f"{m}:{t}:{a}" for m, t in (("geo", "pays"), ("stock", "entrepot"), ("stock", "livraison")) for a in ("read", "write", "delete")]


class Log:
    def info(self, *a, **k): ...
    def error(self, *a, **k): ...


@pytest.fixture
def registry():
    reg = DocTypeRegistry()
    for d in (PAYS, ENTREPOT, LIGNE, LIVRAISON):
        reg.register(parse_definition(d))
    reg.validate_links()
    return reg


@pytest.fixture
def svc(db, tables, registry):
    return DocumentService(registry, db, tables, Log())


def ctx():
    return make_ctx("acme", "alice", permissions=ALL)


async def world(svc):
    """Deux pays, trois entrepôts : un bon, un dans l'autre pays, un fermé."""
    for code, nom in (("CI", "Côte d'Ivoire"), ("FR", "France")):
        await svc.create(ctx(), "Pays", {"code": code, "nom": nom})
    out = {}
    for nom, pays, actif, genre in (("Abidjan", "CI", True, "Sec"), ("Paris", "FR", True, "Froid"), ("Vieux dépôt", "CI", False, "Sec")):
        out[nom] = (await svc.create(ctx(), "Entrepot", {"nom": nom, "pays": pays, "actif": actif, "genre": genre}))["name"]
    return out


class TestTheServerEnforcesThem:
    async def test_a_link_that_satisfies_the_filters_is_accepted(self, svc):
        w = await world(svc)
        doc = await svc.create(ctx(), "Livraison", {"reference": "BL-1", "pays": "CI", "entrepot": w["Abidjan"]})
        assert doc["data"]["entrepot"] == w["Abidjan"]

    async def test_the_wrong_country_is_refused_even_without_the_desk(self, svc):
        w = await world(svc)
        with pytest.raises(ValidationFailed) as e:
            await svc.create(ctx(), "Livraison", {"reference": "BL-2", "pays": "CI", "entrepot": w["Paris"]})
        assert [(d["field"], d["type"]) for d in e.value.details] == [("entrepot", "link_filtered")]

    async def test_a_literal_filter_is_enforced_too(self, svc):
        w = await world(svc)
        with pytest.raises(ValidationFailed) as e:
            await svc.create(ctx(), "Livraison", {"reference": "BL-3", "pays": "CI", "entrepot": w["Vieux dépôt"]})
        assert e.value.details[0]["type"] == "link_filtered"  # `actif: true`

    async def test_an_empty_source_field_restricts_nothing(self, svc):
        """Sans pays sur le bon, le filtre `$pays` ne dit rien : on ne refuse pas ce qu'on n'a pas restreint."""
        w = await world(svc)
        doc = await svc.create(ctx(), "Livraison", {"reference": "BL-4", "entrepot": w["Paris"]})
        assert doc["data"]["entrepot"] == w["Paris"]

    async def test_an_unchanged_link_is_not_re_judged(self, svc):
        """Même règle que le contrôle des liens : on ne rejuge que ce qui change, sinon on bloque une correction sans rapport."""
        w = await world(svc)
        doc = await svc.create(ctx(), "Livraison", {"reference": "BL-5", "pays": "CI", "entrepot": w["Abidjan"]})
        got = await svc.get(ctx(), "Entrepot", w["Abidjan"])
        await svc.update(ctx(), "Entrepot", w["Abidjan"], {**got["data"], "actif": False}, got["version"])  # l'entrepôt ferme
        edited = await svc.update(ctx(), "Livraison", doc["name"], {"reference": "BL-5 bis", "pays": "CI", "entrepot": w["Abidjan"]}, doc["version"])
        assert edited["data"]["reference"] == "BL-5 bis"  # le lien n'a pas bougé : la livraison reste modifiable
        with pytest.raises(ValidationFailed):  # mais le choisir À NOUVEAU est refusé
            await svc.create(ctx(), "Livraison", {"reference": "BL-6", "pays": "CI", "entrepot": w["Abidjan"]})

    async def test_rows_are_judged_on_their_own_cells(self, svc):
        """Dans une ligne, `$genre` lit le genre de LA LIGNE, pas celui du document."""
        w = await world(svc)
        ok = await svc.create(ctx(), "Livraison", {"reference": "BL-7", "lignes": [{"qte": 1, "genre": "Sec", "entrepot": w["Abidjan"]}]})
        assert ok["data"]["lignes"][0]["entrepot"] == w["Abidjan"]
        with pytest.raises(ValidationFailed) as e:
            await svc.create(ctx(), "Livraison", {"reference": "BL-8", "lignes": [{"qte": 1, "genre": "Froid", "entrepot": w["Abidjan"]}]})
        assert [(d["field"], d["type"]) for d in e.value.details] == [("lignes.0.entrepot", "link_filtered")]

    async def test_an_unreadable_target_is_refused_like_a_missing_one(self, svc):
        w = await world(svc)
        blind = make_ctx("acme", "bob", permissions=["stock:livraison:read", "stock:livraison:write"])  # ne lit pas les entrepôts
        with pytest.raises(ValidationFailed) as e:
            await svc.create(blind, "Livraison", {"reference": "BL-9", "pays": "CI", "entrepot": w["Abidjan"]})
        assert {d["type"] for d in e.value.details} <= {"link_not_found", "link_filtered"}


class TestAmendment:
    async def test_an_amendment_carries_its_links_without_re_judging_them_but_a_new_choice_is_judged(self, db, tables, registry):
        """Comme pour l'état d'une cible (`target_status`) : un amendement RECOPIE les liens du document annulé ; un entrepôt fermé depuis
        n'empêche pas de l'amender. Ce que l'utilisateur change ensuite dans l'amendement, lui, se juge."""
        registry.register(parse_definition({**LIVRAISON, "name": "Expedition", "submittable": True, "fields": LIVRAISON["fields"][:3]}))
        svc = DocumentService(registry, db, tables, Log())
        who = make_ctx("acme", "alice", permissions=ALL + [f"stock:expedition:{a}" for a in ("read", "write", "delete", "submit", "cancel")])
        w = await world(svc)
        doc = await svc.create(who, "Expedition", {"reference": "EX-1", "pays": "CI", "entrepot": w["Abidjan"]})
        doc = await svc.submit(who, "Expedition", doc["name"], doc["version"])
        await svc.cancel(who, "Expedition", doc["name"], doc["version"])
        depot = await svc.get(who, "Entrepot", w["Abidjan"])
        await svc.update(who, "Entrepot", w["Abidjan"], {**depot["data"], "actif": False}, depot["version"])  # fermé depuis
        amended = await svc.amend(who, "Expedition", doc["name"])
        assert amended["data"]["entrepot"] == w["Abidjan"]
        with pytest.raises(ValidationFailed) as e:
            await svc.update(who, "Expedition", amended["name"], {**amended["data"], "entrepot": w["Vieux dépôt"]}, amended["version"])
        assert [(d["field"], d["type"]) for d in e.value.details] == [("entrepot", "link_filtered")]


class TestTheDeskGetsTheSameAnswer:
    async def test_search_applies_the_filters(self, svc):
        """L'autocomplétion passe les mêmes filtres : le Desk ne propose que ce que l'écriture accepterait."""
        w = await world(svc)
        every = await svc.search(ctx(), "Entrepot", "")
        assert len(every["items"]) == 3
        kept = await svc.search(ctx(), "Entrepot", "", filters={"pays": "CI", "actif": True})
        assert [i["name"] for i in kept["items"]] == [w["Abidjan"]]

    async def test_a_filter_on_an_unknown_field_is_refused(self, svc):
        await world(svc)
        with pytest.raises(ValidationFailed):
            await svc.search(ctx(), "Entrepot", "", filters={"inconnu": 1})

    async def test_the_definition_travels_in_meta(self, svc):
        meta = await svc.fetch_meta(ctx(), "Livraison")
        field = next(f for f in meta["fields"] if f["fieldname"] == "entrepot")
        assert field["link_filters"] == {"pays": "$pays", "actif": True}
        row = next(f for f in meta["children"]["Ligne de livraison"]["fields"] if f["fieldname"] == "entrepot")
        assert row["link_filters"] == {"genre": "$genre"}


class TestMistakesFailAtLoad:
    def dt(self, **filters):
        return {**LIVRAISON, "fields": [f if f["fieldname"] != "entrepot" else {**f, "link_filters": filters} for f in LIVRAISON["fields"]]}

    def load(self, defn):
        reg = DocTypeRegistry()
        for d in (PAYS, ENTREPOT, LIGNE):
            reg.register(parse_definition(d))
        reg.register(parse_definition(defn))
        reg.validate_links()

    def test_a_field_the_target_does_not_have(self):
        with pytest.raises(InvalidDefinition, match="n'a pas de champ 'couleur'"):
            self.load(self.dt(couleur="rouge"))

    def test_a_literal_of_the_wrong_type(self):
        with pytest.raises(InvalidDefinition, match="link_filters.actif"):
            self.load(self.dt(actif="oui"))  # Boolean, pas Text

    def test_a_source_field_that_does_not_exist(self):
        with pytest.raises(InvalidDefinition, match="ne nomme aucun champ"):
            parse_definition(self.dt(pays="$inconnu"))

    def test_a_source_field_of_another_type(self):
        with pytest.raises(InvalidDefinition, match="le champ qui le filtre est un Text"):
            self.load(self.dt(pays="$reference"))  # `reference` est un Text, `Entrepot.pays` un Link

    def test_a_link_filter_on_something_else_than_a_link(self):
        bad = {**LIVRAISON, "fields": [f if f["fieldname"] != "reference" else {**f, "link_filters": {"nom": "x"}} for f in LIVRAISON["fields"]]}
        with pytest.raises(InvalidDefinition, match="n'a de sens que pour un Link"):
            parse_definition(bad)

    def test_the_field_cannot_filter_on_itself(self):
        with pytest.raises(InvalidDefinition, match="ne peut pas lire le champ Link lui-même"):
            parse_definition(self.dt(pays="$entrepot"))

    def test_an_empty_block_is_refused(self):
        with pytest.raises(InvalidDefinition, match="ne restreint rien"):
            parse_definition(self.dt())

    def test_a_dynamic_link_cannot_carry_them(self):
        bad = {
            "schema_version": 1, "name": "Note", "module": "stock",
            "fields": [
                {"fieldname": "genre", "fieldtype": "Text"},
                {"fieldname": "cible", "fieldtype": "DynamicLink", "options": "genre", "link_filters": {"actif": True}},
            ],
        }
        with pytest.raises(InvalidDefinition, match="n'a de sens que pour un Link"):
            parse_definition(bad)
