"""Imprimer contre une vraie base : les droits, le choix du gabarit, le papier à en-tête (process/impression.md §6/§7).

Le rendu HTML est testé à part, sur des dictionnaires (`test_printing.py`). Ce qui se joue ICI, et qui exige la
base : qu'imprimer ne soit QUE lire, que trois niveaux de gabarits se résolvent dans le bon ordre, et qu'un
papier à en-tête absent n'empêche jamais une facture de sortir.
"""

from __future__ import annotations

import struct
import zlib

import pytest

from tests.conftest import make_ctx
from xcore_doctype import files as F
from xcore_doctype.document import DocumentService
from xcore_doctype.errors import NotFound, PermissionDenied, ValidationFailed
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
# Le `Letter Head` du plugin `system` arrive à l'étape 6 de §11 ; le MÉCANISME se prouve ici avec sa forme (§5).
LETTER_HEAD = {
    "schema_version": 1, "name": "Letter Head", "module": "system",
    "fields": [
        {"fieldname": "image", "fieldtype": "Image"},
        {"fieldname": "footer_text", "fieldtype": "LongText"},
        {"fieldname": "is_default", "fieldtype": "Boolean"},
        {"fieldname": "disabled", "fieldtype": "Boolean"},
        # Le FOND préimprimé de §5 : un PDF téléversé, donc un champ `Attach` (une image ne reçoit pas un PDF).
        {"fieldname": "background", "fieldtype": "Attach"},
    ],
    "naming": "prompt",
}

TEMPLATE = {
    "schema_version": 1, "doctype": "Facture", "name": "standard", "label": "Facture",
    "blocks": [{"type": "title", "text": "Facture {{ name }}"}, {"type": "fields", "fields": ["client"]}],
}

ALL = ["sales:facture:read", "sales:facture:write", "system:letter_head:read", "system:letter_head:write",
       "sales:reglages:read", "sales:reglages:write"]
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
    for raw in (LINE, INVOICE, SETTINGS, LETTER_HEAD):
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


class TestTheLetterhead:
    """§5 : un `Letter Head` par son nom, son image INCORPORÉE (§6), et son absence n'est jamais une erreur."""

    async def head(self, env, name="Entête", image=None, **extra):
        data = {"is_default": False, "disabled": False, **extra}
        if image is not None:
            data["image"] = image
        return await env.docs.create(ALICE, "Letter Head", data, name=name)

    async def image(self, env, content=None, content_type=None):
        meta = await env.files.upload(ALICE, "Letter Head", "image", content=content or png(),
                                      filename="entete.png", content_type=content_type)
        return meta["id"]

    async def test_the_image_is_embedded_never_a_url(self, env):
        """Le moteur n'a pas d'accès réseau : c'est NOUS qui lisons le fichier et l'incorporons."""
        ship(env, letterhead={"name": "Entête"})
        await self.head(env, image=await self.image(env))
        out = await env.printer.render(ALICE, "Facture", await invoice(env))
        assert "src=\"data:image/png;base64," in out["html"] and "http" not in out["html"].split("<body>")[1]

    async def test_the_footer_text_is_printed_and_escaped(self, env):
        ship(env, letterhead={"name": "Entête"})
        await self.head(env, footer_text="SARL <Durand> — RCS 1234")
        out = await env.printer.render(ALICE, "Facture", await invoice(env))
        assert "SARL &lt;Durand&gt; — RCS 1234" in out["html"]

    async def test_default_takes_the_one_marked_by_default(self, env):
        ship(env, letterhead={"name": "default"})
        await self.head(env, name="Ancien", footer_text="PAS CELUI-LÀ")
        await self.head(env, name="Actuel", footer_text="CELUI-LÀ", is_default=True)
        out = await env.printer.render(ALICE, "Facture", await invoice(env))
        assert "CELUI-LÀ" in out["html"] and "PAS CELUI-LÀ" not in out["html"]

    async def test_a_disabled_letterhead_prints_without_one(self, env):
        ship(env, letterhead={"name": "Entête"})
        await self.head(env, footer_text="désactivé", disabled=True)
        out = await env.printer.render(ALICE, "Facture", await invoice(env))
        assert "désactivé" not in out["html"]

    async def test_a_missing_letterhead_is_never_an_error(self, env):
        """Délibéré : un en-tête supprimé ne doit pas rendre une facture inimprimable."""
        ship(env, letterhead={"name": "Jamais créé"})
        out = await env.printer.render(ALICE, "Facture", await invoice(env))
        assert "Durand" in out["html"]

    async def test_an_absent_letter_head_doctype_is_never_an_error(self, env):
        """Le plugin `system` n'est pas forcément chargé : l'impression continue sans en-tête."""
        env.reg.unregister_owner("system")
        ship(env, letterhead={"name": "default"})
        assert "Durand" in (await env.printer.render(ALICE, "Facture", await invoice(env)))["html"]

    async def test_the_request_replaces_what_the_template_says(self, env):
        ship(env, letterhead={"name": "Entête"})
        await self.head(env, footer_text="DU GABARIT")
        await self.head(env, name="Autre", footer_text="DE LA REQUÊTE")
        out = await env.printer.render(ALICE, "Facture", await invoice(env), letterhead="Autre")
        assert "DE LA REQUÊTE" in out["html"] and "DU GABARIT" not in out["html"]

    async def test_none_prints_without_any_letterhead(self, env):
        ship(env, letterhead={"name": "Entête"})
        await self.head(env, footer_text="PAS CETTE FOIS")
        out = await env.printer.render(ALICE, "Facture", await invoice(env), letterhead="none")
        assert "PAS CETTE FOIS" not in out["html"]

    async def test_a_reader_with_no_grant_on_letter_head_still_gets_it(self, env):
        """C'est le précédent de `preferences.py` : l'en-tête est de la présentation d'entreprise, lue en contexte système."""
        ship(env, letterhead={"name": "Entête"})
        await self.head(env, footer_text="RCS 1234")
        reader = make_ctx("acme", "rita", permissions=["sales:facture:read"])
        assert "RCS 1234" in (await env.printer.render(reader, "Facture", await invoice(env)))["html"]

    async def test_another_tenants_letterhead_is_invisible(self, env):
        ship(env, letterhead={"name": "Entête"})
        await self.head(env, footer_text="CHEZ ACME")
        hana = make_ctx("globex", "hana", permissions=ALL)
        await env.docs.create(hana, "Facture", {"client": "G"})
        out = await env.printer.render(hana, "Facture", (await env.docs.list(hana, "Facture"))["items"][0]["name"])
        assert "CHEZ ACME" not in out["html"]

    async def test_a_file_of_another_tenant_is_never_read(self, env):
        """Deux verrous, et le premier est le plus fort : le moteur REFUSE de poser le fichier d'un autre tenant sur
        un document (`_check_files`), donc le cas ne peut même pas exister ; et la lecture est de toute façon bornée
        au tenant (`FileService.content`), ce qui tient aussi pour l'identifiant d'un gabarit livré."""
        hana = make_ctx("globex", "hana", permissions=ALL)
        meta = await env.files.upload(hana, "Letter Head", "image", content=png(), filename="g.png", content_type=None)
        with pytest.raises(ValidationFailed):
            await self.head(env, image=meta["id"])
        assert await env.files.content("acme", meta["id"], reason="test") is None
        assert await env.printer._embedded(ALICE, meta["id"]) is None
        assert await env.printer._embedded(ALICE, "0" * 32) is None

    async def test_an_svg_is_never_embedded(self, env):
        """Un SVG porte du script : refusé au téléversement d'un champ Image, et refusé ici aussi."""
        from xcore_doctype.printing import letterhead_markup

        with pytest.raises(ValueError, match="incorporée"):
            letterhead_markup("https://exemple.invalid/logo.svg")


class TestTheOnlyFieldsRead:
    async def test_a_field_added_to_letter_head_never_leaks(self, env):
        """La liste est FERMÉE (`LETTERHEAD_FIELDS`) : un réglage sensible ajouté plus tard ne sort pas par l'impression."""
        from xcore_doctype import print_service

        assert set(print_service.LETTERHEAD_FIELDS) == {"image", "footer_text", "is_default", "disabled"}


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

    async def background(self, env, ctx=ALICE, content=b"%PDF-1.4 faux"):
        meta = await env.files.upload(ctx, "Letter Head", "background", content=content, filename="fond.pdf",
                                      content_type="application/pdf")
        return meta["id"]

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
