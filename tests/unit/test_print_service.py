"""Imprimer contre une vraie base : les droits, le choix du gabarit, le papier à en-tête (process/impression.md §6/§7).

Le rendu HTML est testé à part, sur des dictionnaires (`test_printing.py`). Ce qui se joue ICI, et qui exige la
base : qu'imprimer ne soit QUE lire, que trois niveaux de gabarits se résolvent dans le bon ordre, et qu'un
papier à en-tête absent n'empêche jamais une facture de sortir.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

import pytest
import yaml

from tests.conftest import make_ctx
from xcore_doctype import files as F
from xcore_doctype.document import DocumentService
from xcore_doctype.errors import DoctypeError, NotFound, PermissionDenied, ValidationFailed
from xcore_doctype.file_service import FileService
from xcore_doctype.overrides import KIND_PRINT, validate_print_override
from xcore_doctype.print_service import SOURCE_GENERATED, SOURCE_ORG, SOURCE_PLUGIN, PrintService, target_of
from xcore_doctype.registry import DocTypeRegistry
from xcore_doctype.sealing import Sealer
from xcore_doctype.schema import parse_definition


def png() -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00")) + chunk(b"IEND", b""))


LINE = {
    "schema_version": 1, "name": "Facture Ligne", "istable": True,
    "fields": [{"fieldname": "article", "fieldtype": "Text"}, {"fieldname": "montant", "fieldtype": "Decimal", "decimals": 2}],
}
INVOICE = {
    "schema_version": 1, "name": "Facture", "module": "sales", "label": "Facture de vente",
    "fields": [
        {"fieldname": "client", "fieldtype": "Text", "required": True},
        {"fieldname": "secret", "fieldtype": "Password"},
        {"fieldname": "lignes", "fieldtype": "Table", "options": "Facture Ligne"},
        {"fieldname": "total", "fieldtype": "Decimal", "decimals": 2},
    ],
}
SETTINGS = {
    "schema_version": 1, "name": "Reglages", "module": "sales", "single": True,
    "fields": [{"fieldname": "nom", "fieldtype": "Text"}],
}
# Les réglages de base, TELS QUE le plugin `system` les livre : c'est là que vit le fond PDF de l'organisation (§14).
SYSTEM_SETTINGS = yaml.safe_load(
    (Path(__file__).resolve().parents[2] / "src/xcore_doctype/system_plugin_dir/doctypes/system_settings.yaml").read_text()
)
# Un autre type imprimable, avec un champ Attach : ce qu'un gabarit ne doit JAMAIS pouvoir citer comme fond.
QUOTE = {
    "schema_version": 1, "name": "Devis", "module": "sales",
    "fields": [{"fieldname": "client", "fieldtype": "Text"}, {"fieldname": "piece", "fieldtype": "Attach"}],
}

TEMPLATE = {
    "schema_version": 1, "doctype": "Facture", "name": "standard", "label": "Facture",
    "blocks": [{"type": "title", "text": "Facture {{ name }}"}, {"type": "fields", "fields": ["client"]}],
}

ALL = ["sales:facture:read", "sales:facture:write", "system:system_settings:read", "system:system_settings:write",
       "sales:reglages:read", "sales:reglages:write", "sales:devis:read", "sales:devis:write"]
ALICE = make_ctx("acme", "alice", permissions=ALL)
MINE = make_ctx("acme", "alice", permissions=["sales:facture:own", "sales:facture:write"])
BOB = make_ctx("acme", "bob", permissions=ALL)
STRANGER = make_ctx("acme", "sam", permissions=[])


class Log:
    def info(self, *a, **k): pass
    def warning(self, *a, **k): pass
    def error(self, *a, **k): pass


class MemoryStore:
    def __init__(self):
        self.blobs: dict[tuple[str, str], bytes] = {}

    async def save(self, content, filename, namespace):
        blob_id = f"b{len(self.blobs)}"
        self.blobs[(namespace, blob_id)] = content
        return type("B", (), {"file_id": blob_id, "stored_name": blob_id, "size_bytes": len(content)})()

    async def read(self, blob_id, namespace, stored_name=None):
        return self.blobs.get((namespace, stored_name or blob_id))

    async def delete(self, blob_id, namespace, stored_name=None):
        return self.blobs.pop((namespace, stored_name or blob_id), None) is not None


@pytest.fixture
def env(db, tables):
    reg = DocTypeRegistry()
    for raw in (LINE, INVOICE, SETTINGS, SYSTEM_SETTINGS, QUOTE):
        reg.register(parse_definition(raw))
    reg.validate_links()
    docs = DocumentService(reg, db, tables, Log(), sealer=Sealer("k" * 32))
    files = FileService(docs, MemoryStore(), F.UrlSigner("s" * 32))
    docs.files = files
    return type("Env", (), {"reg": reg, "docs": docs, "files": files, "printer": PrintService(docs, files)})


async def invoice(env, ctx=ALICE, **extra):
    doc = await env.docs.create(ctx, "Facture", {"client": "Durand", "lignes": [{"article": "Chaise", "montant": "40.00"}],
                                                 "total": "40.00", **extra})
    return doc["name"]


def ship(env, **extra):
    """Un gabarit LIVRÉ par un plugin, posé directement dans le registre (le chargement depuis un dossier est testé dans `test_printing.py`)."""
    from xcore_doctype.printing import parse_template

    template = parse_template({**TEMPLATE, **extra})
    env.reg._print_templates[(template.doctype, template.name)] = (template, "<test>", "sales")
    return template


async def org(env, ctx=ALICE, **extra):
    """L'habillage d'une ORGANISATION, écrit comme le fera l'éditeur : par la validation partagée, pas à la main."""
    compiled = env.reg.get("Facture")
    cleaned = validate_print_override(compiled, {**{k: v for k, v in TEMPLATE.items() if k != "doctype"}, **extra})
    async with env.docs._query(ctx) as q:
        await q.put_override(KIND_PRINT, target_of("Facture", cleaned["name"]), owner=ctx.owner_id, config=cleaned)
    return cleaned


class TestPrintingIsReading:
    """§6 : la route exige le droit de LIRE le document, avec le périmètre de l'appelant — et rien de plus."""

    async def test_whoever_reads_the_document_prints_it(self, env):
        name = await invoice(env)
        out = await env.printer.render(ALICE, "Facture", name)
        assert name in out["html"] and "Durand" in out["html"]

    async def test_no_grant_at_all_is_refused(self, env):
        """Sur un document NOMMÉ, un sans-rôle a 404 et non 403 : un partage pourrait le lui ouvrir (`require(single=True)`).
        Sur la LISTE des gabarits, qui ne nomme aucun document, c'est 403 — la règle du reste du moteur, tenue ici aussi."""
        name = await invoice(env)
        with pytest.raises(NotFound):
            await env.printer.render(STRANGER, "Facture", name)
        with pytest.raises(PermissionDenied):
            await env.printer.templates(STRANGER, "Facture")

    async def test_a_document_outside_the_read_scope_is_not_found(self, env):
        """Hors périmètre `if_owner` : « introuvable », jamais « interdit » — la règle de tout le moteur."""
        name = await invoice(env, ctx=BOB)
        with pytest.raises(NotFound):
            await env.printer.render(MINE, "Facture", name)

    async def test_another_tenants_document_is_not_found(self, env):
        name = await invoice(env)
        with pytest.raises(NotFound):
            await env.printer.render(make_ctx("globex", "hana", permissions=ALL), "Facture", name)

    async def test_a_secret_never_reaches_the_paper(self, env):
        """Le gabarit ne peut pas citer un Password (refusé au chargement), et la donnée imprimée est déjà MASQUÉE."""
        name = await invoice(env, secret="hunter2")
        out = await env.printer.render(ALICE, "Facture", name)
        assert "hunter2" not in out["html"]

    async def test_printing_writes_nothing(self, env):
        name = await invoice(env)
        before = await env.docs.get(ALICE, "Facture", name)
        await env.printer.render(ALICE, "Facture", name)
        await env.printer.templates(ALICE, "Facture")
        after = await env.docs.get(ALICE, "Facture", name)
        assert (after["version"], after["modified"]) == (before["version"], before["modified"])
        assert (await env.docs.history(ALICE, "Facture", name))["total"] == 1  # la création, et rien d'autre


class TestWhichTemplateWins:
    """§2 : l'organisation PRIME sur le plugin, et à défaut de tout un gabarit est FABRIQUÉ."""

    async def test_with_nothing_declared_a_template_is_made(self, env):
        out = await env.printer.templates(ALICE, "Facture")
        assert [t["source"] for t in out["templates"]] == [SOURCE_GENERATED]
        name = await invoice(env)
        assert "Chaise" in (await env.printer.render(ALICE, "Facture", name))["html"]

    async def test_a_shipped_template_replaces_the_made_one(self, env):
        ship(env)
        out = await env.printer.templates(ALICE, "Facture")
        assert [(t["name"], t["source"]) for t in out["templates"]] == [("standard", SOURCE_PLUGIN)]

    async def test_the_organisation_wins_over_the_plugin(self, env):
        ship(env)
        await org(env, label="La facture de la maison")
        out = await env.printer.templates(ALICE, "Facture")
        assert [(t["label"], t["source"]) for t in out["templates"]] == [("La facture de la maison", SOURCE_ORG)]

    async def test_both_levels_coexist_under_different_names(self, env):
        ship(env)
        await org(env, name="duplicata", label="Duplicata")
        out = await env.printer.templates(ALICE, "Facture")
        assert [(t["name"], t["source"]) for t in out["templates"]] == [("duplicata", SOURCE_ORG), ("standard", SOURCE_PLUGIN)]

    async def test_another_doctypes_override_is_not_mine(self, env):
        """Les habillages d'un genre se lisent d'un coup : il faut donc trier sur la cible, et ce test le prouve."""
        async with env.docs._query(ALICE) as q:
            await q.put_override(KIND_PRINT, target_of("Autre", "standard"), owner="alice",
                                 config={**TEMPLATE, "doctype": "Autre"})
        out = await env.printer.templates(ALICE, "Facture")
        assert [t["source"] for t in out["templates"]] == [SOURCE_GENERATED]

    async def test_another_tenants_override_is_not_mine(self, env):
        await org(env, ctx=make_ctx("globex", "hana", permissions=ALL), label="Chez Globex")
        out = await env.printer.templates(ALICE, "Facture")
        assert [t["source"] for t in out["templates"]] == [SOURCE_GENERATED]

    async def test_the_default_is_standard_then_the_first_by_name(self, env):
        ship(env, name="zebre")
        assert (await env.printer.templates(ALICE, "Facture"))["default"] == "zebre"
        ship(env)
        assert (await env.printer.templates(ALICE, "Facture"))["default"] == "standard"

    async def test_a_named_template_is_printed(self, env):
        ship(env)
        await org(env, name="duplicata", blocks=[{"type": "title", "text": "DUPLICATA"}])
        name = await invoice(env)
        assert "DUPLICATA" in (await env.printer.render(ALICE, "Facture", name, template="duplicata"))["html"]
        assert "DUPLICATA" not in (await env.printer.render(ALICE, "Facture", name))["html"]

    async def test_an_unknown_template_is_not_found(self, env):
        name = await invoice(env)
        with pytest.raises(NotFound, match="introuvable"):
            await env.printer.render(ALICE, "Facture", name, template="inexistant")

    async def test_an_unreadable_override_lets_the_plugin_take_over(self, env):
        """Un habillage que le DocType a cassé sous lui est IGNORÉ : une facture reste imprimable (la règle des autres habillages)."""
        ship(env)
        async with env.docs._query(ALICE) as q:
            await q.put_override(KIND_PRINT, target_of("Facture", "standard"), owner="alice", config={"blocks": "pas une liste"})
        out = await env.printer.templates(ALICE, "Facture")
        assert [t["source"] for t in out["templates"]] == [SOURCE_PLUGIN]


class TestWhatDoesNotPrint:
    async def test_an_unknown_doctype_is_not_found(self, env):
        with pytest.raises(NotFound):
            await env.printer.templates(ALICE, "Inconnu")

    async def test_a_child_doctype_is_not_found(self, env):
        with pytest.raises(NotFound):
            await env.printer.templates(ALICE, "Facture Ligne")

    async def test_a_single_doctype_does_not_print(self, env):
        with pytest.raises(ValidationFailed, match="ne s'imprime pas"):
            await env.printer.templates(ALICE, "Reglages")

    @pytest.mark.parametrize("bad", [12, True, {"nom": "x"}])
    async def test_the_template_name_must_be_a_string(self, env, bad):
        name = await invoice(env)
        with pytest.raises(ValidationFailed, match="template"):
            await env.printer.render(ALICE, "Facture", name, template=bad)


class TestTheOrganisationBackground:
    """§14 : UN fond PDF pour toute l'organisation (Paramètres › Général), employé par toutes les impressions — les
    gabarits livrés et fabriqués compris —, sauf un gabarit qui a le sien. Son absence n'est jamais une erreur."""

    async def settle(self, env, content=b"%PDF-1.4 papier", ctx=ALICE, **extra):
        meta = await env.files.upload(ctx, "System Settings", "print_background", content=content, filename="papier.pdf",
                                      content_type="application/pdf")
        await env.docs.update(ctx, "System Settings", "System Settings", {"print_background": meta["id"], **extra}, version=0)
        return meta["id"]

    async def test_every_print_uses_it_even_a_generated_template(self, env):
        await self.settle(env, print_offset_top=40, print_offset_bottom=15)
        out = await env.printer.render(ALICE, "Facture", await invoice(env))  # aucun gabarit : le fabriqué
        assert out["background"] is True and "@page{margin-top:60mm;margin-bottom:35mm}" in out["html"]

    async def test_first_page_only_and_the_pdf_itself(self, env, monkeypatch):
        from xcore_doctype import print_service as S

        seen = {}
        monkeypatch.setattr(S, "_pdf_bytes", lambda html, background: seen.setdefault("bg", background) and b"%PDF-1.7")
        await self.settle(env, print_background_pages="Première page seulement")
        await env.printer.pdf(ALICE, "Facture", await invoice(env))
        assert seen["bg"].content == b"%PDF-1.4 papier" and seen["bg"].every_page is False

    async def test_a_template_s_own_background_wins(self, env, monkeypatch):
        from xcore_doctype import print_service as S

        seen = {}
        monkeypatch.setattr(S, "_pdf_bytes", lambda html, background: seen.setdefault("bg", background) and b"%PDF-1.7")
        await self.settle(env)
        own = await env.printer.upload_background(ALICE, "Facture", content=b"%PDF-1.4 propre", filename="f.pdf", content_type=None)
        ship(env, letterhead={"pdf": own["id"]})
        await env.printer.pdf(ALICE, "Facture", await invoice(env))
        assert seen["bg"].content == b"%PDF-1.4 propre"

    async def test_none_prints_without_it(self, env):
        await self.settle(env)
        out = await env.printer.render(ALICE, "Facture", await invoice(env), letterhead="none")
        assert out["background"] is False

    async def test_a_reader_with_no_grant_on_the_settings_still_gets_it(self, env):
        """Le précédent de `preferences.py` : le papier de l'entreprise est de la présentation, lue en contexte système."""
        await self.settle(env)
        reader = make_ctx("acme", "rita", permissions=["sales:facture:read"])
        assert (await env.printer.render(reader, "Facture", await invoice(env)))["background"] is True

    async def test_another_tenants_background_is_invisible(self, env):
        await self.settle(env)
        hana = make_ctx("globex", "hana", permissions=ALL)
        await env.docs.create(hana, "Facture", {"client": "G"})
        name = (await env.docs.list(hana, "Facture"))["items"][0]["name"]
        assert (await env.printer.render(hana, "Facture", name))["background"] is False

    async def test_a_file_that_is_not_a_pdf_is_ignored_not_an_error(self, env):
        """Un Attach accepte tout fichier et garde le type DÉCLARÉ : le PDF se reconnaît à ses octets."""
        await self.settle(env, content=png())
        out = await env.printer.render(ALICE, "Facture", await invoice(env))
        assert out["background"] is False and "Durand" in out["html"]

    async def test_only_the_settings_field_s_own_file_is_read(self, env):
        """Le fichier de CE champ de CE document : un PDF d'ailleurs, même du tenant, n'est jamais un fond."""
        elsewhere = await env.files.upload(ALICE, "Devis", "piece", content=b"%PDF-1.4 paie", filename="paie.pdf", content_type=None)
        assert await env.files.print_file("acme", "System Settings", elsewhere["id"], fieldname="print_background") is None

    async def test_without_the_system_plugin_printing_goes_on(self, env):
        """Le plugin `system` n'est pas forcément chargé : l'impression continue sans fond."""
        env.reg.unregister_owner("system")
        out = await env.printer.render(ALICE, "Facture", await invoice(env))
        assert out["background"] is False and "Durand" in out["html"]

    async def test_only_the_background_fields_are_read(self, env):
        """La liste est FERMÉE : un réglage sensible ajouté plus tard à `System Settings` ne sort pas par l'impression."""
        from xcore_doctype import print_service

        assert set(print_service.BACKGROUND_FIELDS) == {"print_background", "print_background_pages", "print_offset_top", "print_offset_bottom"}

    async def test_a_template_saved_with_a_named_letterhead_still_reads(self, env):
        """Avant le 4 nov. un gabarit pouvait citer un `Letter Head` par son nom : il reste lisible (sinon IGNORÉ)."""
        await self.settle(env)
        ship(env, letterhead={"name": "Papier 2026"})
        assert (await env.printer.render(ALICE, "Facture", await invoice(env)))["background"] is True


class TestThePdfRoute:
    async def test_without_the_package_the_pdf_is_a_503_and_the_preview_still_works(self, env, monkeypatch):
        from xcore_doctype import pdf as engine

        monkeypatch.setattr(engine, "_module", lambda name: None)
        name = await invoice(env)
        assert "Durand" in (await env.printer.render(ALICE, "Facture", name))["html"]  # l'aperçu ne demande rien
        with pytest.raises(Exception) as e:
            await env.printer.pdf(ALICE, "Facture", name)
        assert e.value.code == "pdf_engine_missing" and e.value.status == 503 and "plutoprint" in e.value.message

    async def test_the_pdf_is_judged_on_the_read_right_too(self, env, monkeypatch):
        from xcore_doctype import pdf as engine

        monkeypatch.setattr(engine, "_module", lambda name: None)
        name = await invoice(env, ctx=BOB)
        with pytest.raises(NotFound):  # le droit passe AVANT le moteur : pas de 503 qui révèle l'existence
            await env.printer.pdf(MINE, "Facture", name)

    async def test_the_engine_is_reported_to_the_desk(self, env):
        out = await env.printer.templates(ALICE, "Facture")
        from xcore_doctype import pdf as engine

        assert out["pdf"] is engine.engine_available()


class TestThePdfBackground:
    """§5 : le papier à en-tête peut être un PDF, et nos blocs s'impriment PAR-DESSUS."""

    async def background(self, env, ctx=ALICE, content=b"%PDF-1.4 faux", doctype="Facture"):
        meta = await env.printer.upload_background(ctx, doctype, content=content, filename="fond.pdf", content_type="application/pdf")
        return meta["id"]

    async def test_a_background_is_uploaded_for_the_type_and_recognised_by_its_bytes(self, env):
        out = await env.printer.upload_background(ALICE, "Facture", content=b"%PDF-1.7 x", filename="papier.pdf", content_type="text/plain")
        assert out["content_type"] == "application/pdf" and out["filename"] == "papier.pdf"
        with pytest.raises(DoctypeError) as e:  # le NOM ne fait pas le type : un PNG renommé est refusé
            await env.printer.upload_background(ALICE, "Facture", content=png(), filename="faux.pdf", content_type="application/pdf")
        assert e.value.code == "file_type_not_allowed" and e.value.status == 415

    async def test_uploading_a_background_takes_the_template_s_lock(self, env):
        """Le chemin des images (`_upload`), donc le même verrou : l'écrivain partiel est prouvé dans test_print_document."""
        with pytest.raises(PermissionDenied):
            await env.printer.upload_background(STRANGER, "Facture", content=b"%PDF-1.4 x", filename="f.pdf", content_type="application/pdf")

    async def test_a_background_too_large_is_refused(self, env):
        big = b"%PDF-1.4 " + b"0" * (5 * 1024 * 1024)
        with pytest.raises(DoctypeError) as e:
            await env.printer.upload_background(ALICE, "Facture", content=big, filename="f.pdf", content_type="application/pdf")
        assert e.value.code == "file_too_large" and e.value.status == 413

    async def test_any_other_pdf_of_the_tenant_is_never_a_background(self, env):
        """Le verrou : un gabarit cite un IDENTIFIANT, donc ce qu'un client a écrit. Un PDF déposé ailleurs (le
        bulletin de paie, la pièce jointe d'un autre document) ne sort jamais en fond de facture."""
        elsewhere = await env.files.upload(ALICE, "Devis", "piece", content=b"%PDF-1.4 paie", filename="paie.pdf",
                                           content_type="application/pdf")
        ship(env, letterhead={"pdf": elsewhere["id"]})
        assert (await env.printer.render(ALICE, "Facture", await invoice(env)))["background"] is False

    async def test_a_background_of_another_type_is_never_used(self, env):
        other = await self.background(env, doctype="Devis")
        ship(env, letterhead={"pdf": other})
        assert (await env.printer.render(ALICE, "Facture", await invoice(env)))["background"] is False

    async def test_an_image_of_the_template_is_not_a_background(self, env):
        image = await env.printer.upload_image(ALICE, "Facture", content=png(), filename="logo.png", content_type="image/png")
        ship(env, letterhead={"pdf": image["id"]})
        assert (await env.printer.render(ALICE, "Facture", await invoice(env)))["background"] is False

    async def test_a_draft_previews_as_a_pdf_with_its_background(self, env, monkeypatch):
        """L'aperçu PDF d'un BROUILLON : le seul qui montre le fond. Même droit que l'aperçu HTML (lire)."""
        from xcore_doctype import print_service as S

        seen = {}
        monkeypatch.setattr(S, "_pdf_bytes", lambda html, background: seen.setdefault("args", (html, background)) and b"%PDF-1.7")
        file_id = await self.background(env)
        name = await invoice(env)
        draft = {"name": "standard", "content": "<p>Bonjour</p>", "letterhead": {"pdf": file_id, "offset_top": 30}}
        content, filename = await env.printer.preview_pdf(ALICE, "Facture", name, draft)
        html, background = seen["args"]
        assert content == b"%PDF-1.7" and filename.endswith("(aperçu).pdf")
        assert background is not None and background.content.startswith(b"%PDF-1.4") and "Bonjour" in html
        with pytest.raises(NotFound):
            await env.printer.preview_pdf(MINE, "Facture", await invoice(env, ctx=BOB), draft)

    async def test_the_preview_says_a_background_completes_it(self, env):
        """L'aperçu à l'écran ne montre pas le fond (il n'existe qu'au PDF) : le Desk doit pouvoir le DIRE."""
        file_id = await self.background(env)
        ship(env, letterhead={"pdf": file_id, "pages": "all", "offset_top": 40})
        out = await env.printer.render(ALICE, "Facture", await invoice(env))
        assert out["background"] is True

    async def test_the_reserved_zone_keeps_the_content_out(self, env):
        file_id = await self.background(env)
        ship(env, letterhead={"pdf": file_id, "offset_top": 40, "offset_bottom": 15})
        out = await env.printer.render(ALICE, "Facture", await invoice(env))
        assert "@page{margin-top:60mm;margin-bottom:35mm}" in out["html"]  # 20 de marge + 40, 20 + 15

    async def test_without_a_background_the_html_does_not_change_by_one_character(self, env):
        ship(env)
        name = await invoice(env)
        plain = (await env.printer.render(ALICE, "Facture", name))["html"]
        assert "margin-top" not in plain and (await env.printer.render(ALICE, "Facture", name))["background"] is False

    async def test_a_background_of_another_tenant_is_ignored(self, env):
        hana = make_ctx("globex", "hana", permissions=ALL)
        ship(env, letterhead={"pdf": await self.background(env, ctx=hana)})
        out = await env.printer.render(ALICE, "Facture", await invoice(env))
        assert out["background"] is False


class TestTheOverrideTarget:
    def test_a_template_is_targeted_by_its_name(self):
        """Une seule orthographe : `<DocType>:<nom>`, pour qu'un second gabarit ne demande pas de reprise de données."""
        assert target_of("Facture", "standard") == "Facture:standard"
        assert target_of("Facture", "duplicata") == "Facture:duplicata"


class TestALinkPrintsItsTitle:
    """« Société Durand », jamais « c99464b32801 » — ce qu'un premier PDF de bout en bout a révélé.

    La convention est celle du reste du moteur (`document.py` la dit en ces termes pour un refus de suppression),
    et sans elle un document imprimé est illisible : c'est un défaut de correction, pas un confort.
    """

    CLIENT = {
        "schema_version": 1, "name": "Client", "module": "sales", "title_field": "nom",
        "fields": [{"fieldname": "nom", "fieldtype": "Text", "required": True}],
    }
    ARTICLE = {
        "schema_version": 1, "name": "Article", "module": "sales", "title_field": "libelle",
        "fields": [{"fieldname": "libelle", "fieldtype": "Text", "required": True}],
    }
    LIGNE = {
        "schema_version": 1, "name": "Ligne Titree", "istable": True,
        "fields": [{"fieldname": "article", "fieldtype": "Link", "options": "Article"},
                   {"fieldname": "qte", "fieldtype": "Number"}],
    }
    PIECE = {
        "schema_version": 1, "name": "Piece", "module": "sales",
        "fields": [{"fieldname": "client", "fieldtype": "Link", "options": "Client"},
                   {"fieldname": "lignes", "fieldtype": "Table", "options": "Ligne Titree"}],
    }
    TPL = {
        "schema_version": 1, "doctype": "Piece", "name": "standard",
        "blocks": [{"type": "fields", "fields": ["client"]},
                   {"type": "table", "field": "lignes", "columns": [{"field": "article"}, {"field": "qte"}]}],
    }

    @pytest.fixture
    def env2(self, db, tables):
        reg = DocTypeRegistry()
        for raw in (self.CLIENT, self.ARTICLE, self.LIGNE, self.PIECE):
            reg.register(parse_definition(raw))
        reg.validate_links()
        docs = DocumentService(reg, db, tables, Log())
        files = FileService(docs, MemoryStore(), F.UrlSigner("s" * 32))
        docs.files = files
        from xcore_doctype.printing import parse_template

        template = parse_template(self.TPL)
        reg._print_templates[("Piece", "standard")] = (template, "<test>", "sales")
        return type("E", (), {"reg": reg, "docs": docs, "printer": PrintService(docs, files)})

    GRANTS = [f"sales:{n}:{a}" for n in ("client", "article", "piece") for a in ("read", "write")]

    async def piece(self, env2, ctx):
        client = await env2.docs.create(ctx, "Client", {"nom": "Société Durand & Fils"})
        article = await env2.docs.create(ctx, "Article", {"libelle": "Chaise en hêtre"})
        doc = await env2.docs.create(ctx, "Piece", {"client": client["name"], "lignes": [{"article": article["name"], "qte": 2.0}]})
        return doc, client, article

    async def test_the_title_is_printed_not_the_identifier(self, env2):
        ctx = make_ctx("acme", "alice", permissions=self.GRANTS)
        doc, client, article = await self.piece(env2, ctx)
        html = (await env2.printer.render(ctx, "Piece", doc["name"]))["html"]
        assert "Société Durand &amp; Fils" in html and "Chaise en hêtre" in html
        # Et l'identifiant n'y est plus : c'est le point du correctif.
        assert client["name"] not in html and article["name"] not in html

    async def test_an_unreadable_target_keeps_its_identifier(self, env2):
        """Imprimer ne contourne aucun droit (§6) : un titre n'est pas une fuite qu'on s'autorise parce qu'il est pratique."""
        ctx = make_ctx("acme", "alice", permissions=self.GRANTS)
        doc, client, _ = await self.piece(env2, ctx)
        partial = make_ctx("acme", "alice", permissions=["sales:piece:read"])  # ne lit NI Client NI Article
        html = (await env2.printer.render(partial, "Piece", doc["name"]))["html"]
        assert client["name"] in html and "Société Durand" not in html

    async def test_a_type_without_a_title_field_keeps_its_name(self, env2):
        """Un code devise ou un code pays EST son nom lisible : rien à résoudre."""
        env2.reg.register(parse_definition({"schema_version": 1, "name": "Devise", "module": "sales",
                                            "naming": {"field": "code"},
                                            "fields": [{"fieldname": "code", "fieldtype": "Text", "required": True, "unique": True}]}))
        assert env2.reg.get("Devise").definition.title_field is None


class TestTheEditorsServerSide:
    """Ce que l'éditeur de gabarits (§10) demande au serveur : ouvrir, enregistrer, aperçu d'un brouillon."""

    async def test_a_reader_opens_it_READ_ONLY_instead_of_being_refused(self, env):
        """Comme l'éditeur d'automate : tout lecteur du type l'ouvre, seul qui écrit tout y change quelque chose."""
        reader = make_ctx("acme", "rita", permissions=["sales:facture:read"])
        out = await env.printer.override(reader, "Facture")
        assert out["editable"] is False and out["template"]["blocks"]
        assert out["source"] == SOURCE_GENERATED  # rien de livré : le fabriqué sert de point de départ

    async def test_whoever_writes_the_whole_type_may_edit(self, env):
        out = await env.printer.override(ALICE, "Facture")
        assert out["editable"] is True

    async def test_a_partial_writer_may_NOT_save(self, env):
        """Un gabarit rend des documents que son éditeur n'a pas forcément le droit de LIRE : c'est la raison du verrou.

        Il faut un type dont l'écriture est bornée par `if_owner` — la convention, elle, donne un périmètre
        total, et c'est bien ainsi : le verrou ne gêne pas le cas ordinaire.
        """
        from xcore_doctype.errors import ValidationFailed

        env.reg.register(parse_definition({
            "schema_version": 1, "name": "Note Privee", "module": "sales",
            "permissions": {"read": ["sales:note_privee:read"],
                            "write": [{"grant": "sales:note_privee:write", "if_owner": True}]},
            "fields": [{"fieldname": "texte", "fieldtype": "Text", "required": True}],
        }))
        mine = make_ctx("acme", "alice", permissions=["sales:note_privee:read", "sales:note_privee:write"])
        assert (await env.printer.override(mine, "Note Privee"))["editable"] is False
        with pytest.raises(ValidationFailed) as e:
            await env.printer.save_override(mine, "Note Privee", {"name": "standard", "blocks": [{"type": "fields", "fields": ["texte"]}]})
        assert e.value.code == "print_template_reserved"

    async def test_saving_then_printing_uses_it(self, env):
        body = {k: v for k, v in TEMPLATE.items() if k != "doctype"}
        body = {**body, "label": "La facture de la maison", "blocks": [{"type": "title", "text": "MAISON {{ name }}"}]}
        await env.printer.save_override(ALICE, "Facture", body)
        name = await invoice(env)
        assert "MAISON" in (await env.printer.render(ALICE, "Facture", name))["html"]
        reopened = await env.printer.override(ALICE, "Facture")
        assert reopened["source"] == SOURCE_ORG and reopened["template"]["label"] == "La facture de la maison"

    async def test_removing_it_gives_the_plugins_template_back(self, env):
        ship(env, label="Celui du plugin")
        await env.printer.save_override(ALICE, "Facture", {k: v for k, v in TEMPLATE.items() if k != "doctype"})
        assert (await env.printer.override(ALICE, "Facture"))["source"] == SOURCE_ORG
        await env.printer.delete_override(ALICE, "Facture", "standard")
        assert (await env.printer.override(ALICE, "Facture"))["source"] == SOURCE_PLUGIN

    async def test_a_draft_is_previewed_without_being_saved(self, env):
        """Ce qui distingue cet éditeur : on regarde la facture, pas une mise en page abstraite."""
        name = await invoice(env)
        draft = {"name": "standard", "blocks": [{"type": "title", "text": "BROUILLON {{ name }}"}]}
        out = await env.printer.preview(ALICE, "Facture", name, draft)
        assert "BROUILLON" in out["html"] and name in out["html"]
        # Rien n'a été enregistré : le gabarit servi reste le fabriqué.
        assert (await env.printer.override(ALICE, "Facture"))["source"] == SOURCE_GENERATED

    async def test_a_draft_passes_the_SAME_checks_as_a_save(self, env):
        """Un gabarit non enregistré n'est pas une porte : ni champ interdit, ni URL externe (§6, §8)."""
        from xcore_doctype.errors import ValidationFailed

        name = await invoice(env)
        with pytest.raises(ValidationFailed):
            await env.printer.preview(ALICE, "Facture", name, {"name": "x", "blocks": [{"type": "fields", "fields": ["secret"]}]})
        with pytest.raises(ValidationFailed):
            await env.printer.preview(ALICE, "Facture", name, {"name": "x", "blocks": [{"type": "fields", "fields": ["absent"]}]})

    async def test_previewing_needs_only_the_right_to_READ_the_document(self, env):
        """Voir ce qu'on obtiendrait est une lecture ; enregistrer, lui, exige le périmètre total."""
        name = await invoice(env)
        reader = make_ctx("acme", "rita", permissions=["sales:facture:read"])
        out = await env.printer.preview(reader, "Facture", name, {"name": "x", "blocks": [{"type": "title", "text": "vu"}]})
        assert "vu" in out["html"]

    async def test_an_example_document_is_offered_or_honestly_absent(self, env):
        """Sans document, un gabarit s'édite sans aperçu — plutôt que pas du tout."""
        assert (await env.printer.example(ALICE, "Facture"))["name"] is None
        name = await invoice(env)
        assert (await env.printer.example(ALICE, "Facture"))["name"] == name
