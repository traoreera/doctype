"""Réutiliser le référentiel d'un autre plugin : `stock` et `crm` citent les pays et devises de `geo`.

Le scénario demandé : un plugin publie des DocTypes (ici `geo` : `Pays`, `Devise`), un autre les RÉUTILISE en précisant
ce qu'il veut (un champ Link, et `fetch_from` pour les valeurs qu'il recopie), et l'utilisateur du second n'a aucun
grant du premier. Rien de tout cela n'est du code du moteur : les trois plugins sont du YAML, et ces tests tournent sur
les fichiers RÉELLEMENT livrés (`tests/support/plugins/{geo,stock,crm}`), pas sur des définitions écrites ici.

Le prix de ce montage est explicite et testé : la lecture du référentiel est ouverte à tout utilisateur identifié
(`permissions: {read: ["@authenticated"]}`), sans quoi un utilisateur de `crm` ne pourrait pas même compléter son champ
Link. L'écriture, elle, reste à `geo`.
"""

from __future__ import annotations

import pytest

from tests.conftest import make_ctx
from tests.support import FIXTURES
from xcore_doctype.document import DocumentService
from xcore_doctype.errors import AlreadyExists, ContextError, DocumentInUse, PermissionDenied, ValidationFailed
from xcore_doctype.registry import DocTypeRegistry, InvalidDefinition

GEO, STOCK, CRM = FIXTURES / "geo", FIXTURES / "stock", FIXTURES / "crm"

# Un commercial : les grants de son propre module, RIEN de `geo`.
SELLER = ["crm:lead:read", "crm:lead:write", "crm:lead:delete"]
# Un magasinier : idem côté stock.
KEEPER = ["stock:warehouse:read", "stock:warehouse:write", "stock:warehouse:delete"]
# Celui qui tient le référentiel.
LIBRARIAN = ["geo:pays:write", "geo:pays:delete", "geo:devise:write", "geo:devise:delete"]


class Log:
    def info(self, *a, **k): ...
    def error(self, *a, **k): ...


@pytest.fixture
def registry():
    reg = DocTypeRegistry()
    reg.load_dir(GEO / "doctypes", owner="geo")  # le référentiel d'abord : les autres le citent
    reg.load_dir(STOCK / "doctypes", owner="stock")
    reg.load_dir(CRM / "doctypes", owner="crm")
    reg.validate_links()
    return reg


@pytest.fixture
def svc(db, tables, registry):
    return DocumentService(registry, db, tables, Log())


def ctx(perms, tenant="acme", user="alice"):
    return make_ctx(tenant, user, permissions=perms)


async def referential(svc):
    """Deux devises et deux pays, posés par celui qui tient le référentiel."""
    boss = ctx(LIBRARIAN)
    await svc.create(boss, "Devise", {"code": "XOF", "nom": "Franc CFA", "symbole": "F", "decimales": 0})
    await svc.create(boss, "Devise", {"code": "EUR", "nom": "Euro", "symbole": "€"})
    ci = await svc.create(boss, "Pays", {"code": "CI", "nom": "Côte d'Ivoire", "devise": "XOF", "indicatif": "+225", "langue": "fr"})
    fr = await svc.create(boss, "Pays", {"code": "FR", "nom": "France", "devise": "EUR", "indicatif": "+33", "langue": "fr"})
    return ci["name"], fr["name"]


class TestTheNameIsTheCode:
    async def test_a_country_is_named_by_its_iso_code_and_cannot_be_duplicated(self, svc):
        ci, fr = await referential(svc)
        assert (ci, fr) == ("CI", "FR")  # `naming: {field: code}` : le lien se lit « CI », pas un identifiant aléatoire
        with pytest.raises(AlreadyExists):
            await svc.create(ctx(LIBRARIAN), "Pays", {"code": "CI", "nom": "Doublon"})


class TestReadingWithoutAGrantOfTheOtherModule:
    async def test_a_seller_reads_and_searches_the_referential_but_cannot_write_it(self, svc):
        await referential(svc)
        seller = ctx(SELLER)
        # Lire : le pays est lisible bien que `SELLER` ne contienne aucun grant `geo` (`@authenticated`).
        assert (await svc.get(seller, "Pays", "CI"))["data"]["nom"] == "Côte d'Ivoire"
        assert (await svc.list(seller, "Pays"))["total"] == 2
        # Chercher : c'est ce que fait l'autocomplétion d'un champ Link du Desk, avec le même périmètre de lecture.
        found = await svc.search(seller, "Pays", "Ivoire")
        assert [i["name"] for i in found["items"]] == ["CI"]
        # Écrire : refusé. Le référentiel n'appartient pas à `crm`.
        with pytest.raises(PermissionDenied):
            await svc.create(seller, "Pays", {"code": "ML", "nom": "Mali"})
        with pytest.raises(PermissionDenied):
            await svc.delete(seller, "Pays", "CI")

    async def test_an_anonymous_caller_reads_nothing(self, svc):
        """`@authenticated` veut dire IDENTIFIÉ : sans identité, la demande tombe avant tout contrôle de droits (401)."""
        await referential(svc)
        with pytest.raises(ContextError):
            await svc.list(make_ctx("acme", None, permissions=[]), "Pays")

    async def test_the_right_to_add_a_country_from_a_crm_form_is_the_geo_grant(self, svc):
        """Le « + » du champ Link (saisie rapide du Desk) n'est offert que si `create` est permis : c'est ce droit-là."""
        await referential(svc)
        assert (await svc.create(ctx(SELLER + ["geo:pays:write"]), "Pays", {"code": "ML", "nom": "Mali"}))["name"] == "ML"


class TestWhatCrmWantsFromTheCountry:
    async def test_the_asked_fields_are_copied_from_the_country_at_every_write(self, svc):
        await referential(svc)
        seller = ctx(SELLER)
        # Le client envoie n'importe quoi dans les champs recopiés : le serveur les écrase avec le référentiel.
        lead = await svc.create(seller, "Lead", {"lead_name": "Soco", "pays": "CI", "indicatif": "+1", "langue": "en"})
        assert (lead["data"]["indicatif"], lead["data"]["langue"]) == ("+225", "fr")
        assert lead["data"]["pays"] == "CI"
        # Changer le pays recalcule la copie (le `Select` du pays arrive dans un `Text` du prospect).
        moved = await svc.update(seller, "Lead", lead["name"], {"lead_name": "Soco", "pays": "FR"}, lead["version"])
        assert (moved["data"]["indicatif"], moved["data"]["langue"]) == ("+33", "fr")

    async def test_an_unknown_country_is_refused_like_any_link(self, svc):
        await referential(svc)
        with pytest.raises(ValidationFailed) as e:
            await svc.create(ctx(SELLER), "Lead", {"lead_name": "Soco", "pays": "ZZ"})
        assert [(d["field"], d["type"]) for d in e.value.details] == [("pays", "link_not_found")]

    async def test_the_currency_stays_a_link_the_user_chooses(self, svc):
        """`fetch_from` ne recopie jamais un lien (registry.FETCHABLE_TYPES) : la devise d'une affaire est choisie, pas déduite."""
        await referential(svc)
        lead = await svc.create(ctx(SELLER), "Lead", {"lead_name": "Soco", "pays": "CI", "devise": "EUR"})
        assert lead["data"]["devise"] == "EUR"  # pas « XOF », la devise du pays


class TestTheReferentialIsProtected:
    async def test_a_country_cited_by_another_plugin_cannot_be_deleted(self, svc):
        await referential(svc)
        await svc.create(ctx(SELLER), "Lead", {"lead_name": "Soco", "pays": "CI"})
        await svc.create(ctx(KEEPER), "Warehouse", {"warehouse_name": "Abidjan", "pays": "CI"})
        # Celui qui tient le référentiel ne voit ni les prospects ni les entrepôts, et ne peut pourtant pas casser le lien :
        # la recherche des références ignore volontairement son périmètre de lecture, et ne nomme que les types et les champs.
        with pytest.raises(DocumentInUse) as e:
            await svc.delete(ctx(LIBRARIAN), "Pays", "CI")
        cited = {(d["doctype"], d["field"]) for d in e.value.details}
        assert cited == {("Lead", "pays"), ("Warehouse", "pays")}
        # Le pays que personne ne cite s'en va.
        await svc.delete(ctx(LIBRARIAN), "Pays", "FR")
        assert (await svc.list(ctx(SELLER), "Pays"))["total"] == 1

    async def test_a_currency_cited_by_the_referential_itself_is_held_too(self, svc):
        await referential(svc)
        with pytest.raises(DocumentInUse):
            await svc.delete(ctx(LIBRARIAN), "Devise", "XOF")  # `Pays.devise` la cite


class TestTheDependencyIsDeclared:
    def test_crm_alone_does_not_load(self):
        """`requires: [geo]` n'est pas décoratif : sans le référentiel, la cible du Link de `Lead` est inconnue."""
        reg = DocTypeRegistry()
        reg.load_dir(CRM / "doctypes", owner="crm")
        with pytest.raises(InvalidDefinition) as e:
            reg.validate_links()
        assert "Lead.pays" in str(e.value) and "'Pays'" in str(e.value)

    def test_stock_alone_does_not_load_either(self):
        reg = DocTypeRegistry()
        reg.load_dir(STOCK / "doctypes", owner="stock")
        with pytest.raises(InvalidDefinition) as e:
            reg.validate_links()
        assert "Warehouse.pays" in str(e.value)

    def test_no_plugin_can_write_in_the_referential_module(self, tmp_path):
        """Le corollaire : réutiliser les DocTypes de `geo` ne donne à personne le droit d'en ajouter dans son module."""
        reg = DocTypeRegistry()
        reg.load_dir(GEO / "doctypes", owner="geo")
        with pytest.raises(InvalidDefinition) as e:
            reg.load_dir(CRM / "doctypes", owner="geo")  # `crm` voudrait passer pour `geo`
        assert "`module` doit être 'geo'" in str(e.value)
