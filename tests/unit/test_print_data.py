"""Imprimer des données qui ne sont pas un document stocké : états, registres (traoreera/doctype#61, process/impression.md §15).

Une balance ou un livre de paie se CALCULENT : avant ce ticket ils ne passaient pas par l'impression du moteur, et l'ERP
fabriquait une définition de type à la volée, refaisait les contrôles de gabarit et écrivait le livre de paie en HTML à la
main. Ce qui se prouve ici : qu'un état sort de la MÊME chaîne qu'une pièce (trois niveaux de gabarit, langue de
l'organisation, contrôle du gabarit au chargement), sans devenir un DocType — ni stockage, ni droits, ni route de document.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tests.conftest import make_ctx
from tests.unit.test_print_service import MemoryStore, Log, SYSTEM_SETTINGS
from xcore_doctype import files as F
from xcore_doctype import pdf as engine
from xcore_doctype.context import RequestContext
from xcore_doctype.document import DocumentService
from xcore_doctype.errors import DoctypeError, InvalidDefinition, NotFound, PermissionDenied, ValidationFailed
from xcore_doctype.file_service import FileService
from xcore_doctype.print_data import MAX_DATA_ROWS, compile_source, data_problems
from xcore_doctype.print_service import EXAMPLE_NAME, SOURCE_GENERATED, SOURCE_ORG, SOURCE_PLUGIN, DataPrinters, PrintService
from xcore_doctype.registry import DocTypeChannel, DocTypeRegistry
from xcore_doctype.schema import parse_definition
from xcore_doctype.sealing import Sealer

DATA = {
    "company": "Kounandy SARL",
    "closing": "2026-11-30",
    "total_debit": "1234567.50",
    "lines": [
        {"account": "411000", "label": "Clients", "debit": "1234567.50", "credit": "0.00", "total": False},
        {"account": "", "label": "Total", "debit": "1234567.50", "credit": "0.00", "total": True},
    ],
}
BALANCE = {
    "schema_version": 1, "name": "accounts.balance", "label": "Balance générale", "grant": "accounts:report:write",
    "fields": [
        {"fieldname": "company", "fieldtype": "Text", "label": "Société"},
        {"fieldname": "closing", "fieldtype": "Date", "label": "Arrêtée au"},
        {"fieldname": "total_debit", "fieldtype": "Currency", "decimals": 2, "options": "XOF", "label": "Total débit"},
        {"fieldname": "lines", "fieldtype": "Table", "label": "Comptes", "columns": [
            {"fieldname": "account", "fieldtype": "Text", "label": "Compte"},
            {"fieldname": "label", "fieldtype": "Text", "label": "Intitulé"},
            {"fieldname": "debit", "fieldtype": "Decimal", "decimals": 2, "label": "Débit"},
            {"fieldname": "credit", "fieldtype": "Decimal", "decimals": 2, "label": "Crédit"},
            {"fieldname": "total", "fieldtype": "Boolean"},
        ]},
    ],
    "example": DATA,
}
TEMPLATE = {
    "schema_version": 1, "doctype": "accounts.balance", "name": "standard", "label": "Balance",
    "blocks": [
        {"type": "title", "text": "{{ name }} — {{ doc.company }}"},
        {"type": "table", "field": "lines", "emphasis": "total", "columns": [{"field": "account"}, {"field": "label"}, {"field": "debit"}, {"field": "credit"}]},
        {"type": "totals", "fields": ["total_debit"]},
    ],
}
OWNER = make_ctx("acme", "alice", permissions=["accounts:report:write"])
READER = make_ctx("acme", "bob", permissions=["accounts:report:read"])
OTHER_ORG = make_ctx("globex", "eve", permissions=["accounts:report:write"])


def write(folder: Path, name: str, raw: dict) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    return folder


@pytest.fixture
def env(db, tables, tmp_path):
    reg = DocTypeRegistry()
    reg.register(parse_definition(SYSTEM_SETTINGS))
    reg.validate_links()
    reg.load_print_sources(write(tmp_path / "print-sources", "balance.yaml", BALANCE), owner="accounts")
    docs = DocumentService(reg, db, tables, Log(), sealer=Sealer("k" * 32))
    files = FileService(docs, MemoryStore(), F.UrlSigner("s" * 32))
    docs.files = files
    return type("Env", (), {"reg": reg, "docs": docs, "printer": PrintService(docs, files), "tmp": tmp_path})


def ship(env, **extra):
    env.reg.load_print_templates(write(env.tmp / "print", "balance.yaml", {**TEMPLATE, **extra}), owner="accounts")


class TestAnEstateIsNotADocType:
    def test_it_is_registered_beside_the_doctypes_not_among_them(self, env):
        assert [s.name for s in env.reg.print_sources()] == ["accounts.balance"]
        assert "accounts.balance" not in env.reg.names()

    async def test_no_document_route_reaches_it(self, env):
        """Ni fiche, ni liste, ni impression « d'un document » : il n'y a rien de stocké à lire."""
        with pytest.raises(NotFound):
            await env.docs.list(OWNER, "accounts.balance")
        with pytest.raises(NotFound):
            await env.printer.render(OWNER, "accounts.balance", "x")

    def test_a_plugin_leaving_takes_its_estates_and_their_templates(self, env):
        ship(env)
        env.reg.unregister_owner("accounts")
        assert env.reg.print_sources() == [] and env.reg.print_templates("accounts.balance") == []


class TestTheSameChainAsAVoucher:
    async def test_without_any_template_the_generated_one_prints_everything(self, env):
        out = await env.printer.render_data(OWNER, "accounts.balance", DATA, title="Balance au 30/11/2026")
        html = out["html"]
        assert "Kounandy SARL" in html and "411000" in html and "Balance au 30/11/2026" in html
        assert out["template"] == "standard"

    async def test_the_shipped_template_wins_over_the_generated_one(self, env):
        ship(env)
        chosen = await env.printer.templates(OWNER, "accounts.balance")
        assert [(t["name"], t["source"]) for t in chosen["templates"]] == [("standard", SOURCE_PLUGIN)]
        html = (await env.printer.render_data(OWNER, "accounts.balance", DATA, title="Balance"))["html"]
        assert "Balance — Kounandy SARL" in html

    async def test_numbers_and_dates_follow_the_organisation_language(self, env):
        """La règle des pièces (#62) : « 1 234 567,50 », pas « 1234567.50 » — sous-totaux compris."""
        ship(env)
        html = (await env.printer.render_data(make_ctx("acme", "alice", language="fr"), "accounts.balance", DATA))["html"]
        assert "1 234 567,50" in html and "1234567.50" not in html
        assert "XOF" in html  # la devise d'un montant, comme sur une facture

    async def test_the_title_defaults_to_the_label(self, env):
        html = (await env.printer.render_data(OWNER, "accounts.balance", DATA))["html"]
        assert "Balance générale" in html

    async def test_values_are_escaped(self, env):
        html = (await env.printer.render_data(OWNER, "accounts.balance", {"company": "<script>alert(1)</script>"}))["html"]
        assert "<script>alert" not in html and "&lt;script&gt;" in html

    @pytest.mark.skipif(not engine.engine_available(), reason="moteur PDF absent (make install-pdf)")
    async def test_the_pdf_comes_out_with_a_file_name(self, env):
        content, filename = await env.printer.render_data_pdf(OWNER, "accounts.balance", DATA, title="novembre 2026")
        assert content.startswith(b"%PDF-") and filename == "Balance générale novembre 2026.pdf"

    async def test_without_the_engine_only_the_pdf_refuses(self, env, monkeypatch):
        monkeypatch.setattr(engine, "engine_available", lambda: False)
        assert (await env.printer.render_data(OWNER, "accounts.balance", DATA))["html"]
        if not _real_engine():
            with pytest.raises(DoctypeError) as e:
                await env.printer.render_data_pdf(OWNER, "accounts.balance", DATA)
            assert e.value.code == "pdf_engine_missing"


def _real_engine() -> bool:
    try:
        import plutoprint  # noqa: F401
    except ImportError:
        return False
    return True


class TestTheDataFollowTheSchema:
    async def test_an_unknown_key_is_refused_not_printed_as_an_empty_cell(self, env):
        with pytest.raises(ValidationFailed) as e:
            await env.printer.render_data(OWNER, "accounts.balance", {**DATA, "compagny": "x"})
        assert e.value.code == "print_data_invalid" and "compagny" in str(e.value)

    @pytest.mark.parametrize("bad, said", [
        ({"lines": {"account": "1"}}, "liste de lignes"),
        ({"lines": ["411"]}, "une ligne est un objet"),
        ({"lines": [{"acount": "1"}]}, "colonne inconnue"),
        ({"company": {"name": "x"}}, "valeur simple"),
        ("pas un objet", "objet"),
    ])
    async def test_malformed_data(self, env, bad, said):
        source = env.reg.print_source("accounts.balance")
        assert any(said in p for p in data_problems(source, bad))

    def test_too_many_rows_is_an_export_not_a_print(self, env):
        source = env.reg.print_source("accounts.balance")
        problems = data_problems(source, {"lines": [{}] * (MAX_DATA_ROWS + 1)})
        assert problems and str(MAX_DATA_ROWS) in problems[0]

    async def test_an_unknown_estate(self, env):
        with pytest.raises(NotFound) as e:
            await env.printer.render_data(OWNER, "accounts.ledger", {})
        assert e.value.code == "print_source_not_found"

    async def test_an_unknown_template(self, env):
        with pytest.raises(NotFound) as e:
            await env.printer.render_data(OWNER, "accounts.balance", DATA, template="duplicata")
        assert e.value.code == "print_template_not_found"


class TestMistakesAreLoadErrors:
    """La règle de `load_print_templates` : une faute sort au chargement, jamais à la première impression."""

    def test_a_template_citing_an_absent_field_is_refused_by_the_engine(self, env):
        with pytest.raises(InvalidDefinition, match="n'est pas un champ de accounts.balance"):
            ship(env, blocks=[{"type": "fields", "fields": ["compagny"]}])

    def test_a_template_citing_an_absent_column(self, env):
        with pytest.raises(InvalidDefinition, match="colonne 'solde'"):
            ship(env, blocks=[{"type": "table", "field": "lines", "columns": [{"field": "solde"}]}])

    def test_only_the_owner_ships_templates_for_its_estate(self, env):
        with pytest.raises(InvalidDefinition, match="appartient au plugin 'accounts'"):
            env.reg.load_print_templates(write(env.tmp / "p2", "b.yaml", TEMPLATE), owner="payroll")

    @pytest.mark.parametrize("change, said", [
        ({"name": "balance"}, "<plugin>.<état>"),
        ({"name": "payroll.balance"}, "doit commencer par le nom du plugin"),
        ({"name": "accounts.Balance"}, "<plugin>.<état>"),
        ({"fields": [{"fieldname": "client", "fieldtype": "Link", "options": "Customer"}]}, "fieldtype"),
        ({"fields": [{"fieldname": "secret", "fieldtype": "Password"}]}, "fieldtype"),
        ({"fields": [{"fieldname": "t", "fieldtype": "Table"}]}, "doit déclarer ses `columns`"),
        ({"fields": [{"fieldname": "a", "fieldtype": "Text", "columns": [{"fieldname": "b", "fieldtype": "Text"}]}]}, "n'est pas une table"),
        ({"fields": [{"fieldname": "a", "fieldtype": "Text"}, {"fieldname": "a", "fieldtype": "Text"}]}, "deux fois"),
        ({"fields": [{"fieldname": "m", "fieldtype": "Decimal"}]}, "decimals"),
        ({"example": {"nope": 1}}, "`example` ne suit pas le schéma"),
        ({"grant": "role:admin!"}, "grant"),
        ({"surprise": True}, "surprise"),
    ])
    def test_a_bad_schema(self, change, said):
        with pytest.raises(InvalidDefinition, match=said):
            compile_source({**BALANCE, **change}, owner="accounts")

    def test_two_estates_with_one_name(self, env):
        with pytest.raises(InvalidDefinition, match="déjà déclaré"):
            env.reg.register_print_source(BALANCE, owner="accounts")

    def test_a_folder_loads_all_or_nothing(self, env, tmp_path):
        folder = write(tmp_path / "more", "a.yaml", {**BALANCE, "name": "accounts.ledger"})
        write(folder, "b.yaml", {**BALANCE, "name": "accounts.BAD"})
        with pytest.raises(InvalidDefinition):
            env.reg.load_print_sources(folder, owner="accounts")
        assert env.reg.print_source("accounts.ledger") is None

    def test_the_channel_is_the_door_plugins_use(self, env):
        channel = DocTypeChannel(env.reg)
        assert channel.register_print_source("payroll", {**BALANCE, "name": "payroll.register", "grant": None}) == "payroll.register"
        assert env.reg.print_source("payroll.register").grant is None


class TestTheOrganisationEditsItsTemplate:
    """« Modifiables par organisation au Desk comme ceux d'une pièce » : mêmes routes, même validation, un grant pour verrou."""

    ORG = {"name": "standard", "blocks": [{"type": "title", "text": "ÉDITION ACME"}, {"type": "fields", "fields": ["company"]}]}

    async def test_the_editor_opens_with_the_fields_and_the_example(self, env):
        ship(env)
        opened = await env.printer.override(OWNER, "accounts.balance")
        assert opened["editable"] is True and opened["source"] == SOURCE_PLUGIN
        assert [f["fieldname"] for f in opened["data_source"]["fields"]] == ["company", "closing", "total_debit", "lines"]
        child = opened["data_source"]["fields"][3]["options"]
        assert [f["fieldname"] for f in opened["data_source"]["children"][child]["fields"]][:2] == ["account", "label"]
        assert opened["example"] == DATA
        assert (await env.printer.example(OWNER, "accounts.balance"))["name"] == EXAMPLE_NAME

    async def test_the_organisation_template_wins_and_stays_in_its_organisation(self, env):
        ship(env)
        await env.printer.save_override(OWNER, "accounts.balance", self.ORG)
        mine = await env.printer.render_data(OWNER, "accounts.balance", DATA)
        theirs = await env.printer.render_data(OTHER_ORG, "accounts.balance", DATA)
        assert "ÉDITION ACME" in mine["html"] and "ÉDITION ACME" not in theirs["html"]
        chosen = await env.printer.templates(OWNER, "accounts.balance")
        assert chosen["templates"][0]["source"] == SOURCE_ORG

    async def test_removing_it_gives_the_plugin_template_back(self, env):
        ship(env)
        await env.printer.save_override(OWNER, "accounts.balance", self.ORG)
        await env.printer.delete_override(OWNER, "accounts.balance")
        assert "Balance générale — Kounandy" in (await env.printer.render_data(OWNER, "accounts.balance", DATA))["html"]

    async def test_an_override_goes_through_the_same_check_as_a_shipped_template(self, env):
        with pytest.raises(ValidationFailed, match="n'est pas un champ"):
            await env.printer.save_override(OWNER, "accounts.balance", {"name": "standard", "blocks": [{"type": "fields", "fields": ["nope"]}]})

    async def test_the_preview_shows_the_example_data(self, env):
        out = await env.printer.preview(OWNER, "accounts.balance", EXAMPLE_NAME, self.ORG)
        assert "ÉDITION ACME" in out["html"] and "Kounandy SARL" in out["html"]

    @pytest.mark.parametrize("call", ["templates", "override", "example", "save", "delete", "preview", "upload"])
    async def test_without_the_grant_every_door_is_closed(self, env, call):
        doors = {
            "templates": lambda: env.printer.templates(READER, "accounts.balance"),
            "override": lambda: env.printer.override(READER, "accounts.balance"),
            "example": lambda: env.printer.example(READER, "accounts.balance"),
            "save": lambda: env.printer.save_override(READER, "accounts.balance", self.ORG),
            "delete": lambda: env.printer.delete_override(READER, "accounts.balance"),
            "preview": lambda: env.printer.preview(READER, "accounts.balance", EXAMPLE_NAME, self.ORG),
            "upload": lambda: env.printer.upload_image(READER, "accounts.balance", content=b"x", filename="a.png", content_type="image/png"),
        }
        with pytest.raises(PermissionDenied):
            await doors[call]()

    async def test_an_estate_without_a_grant_has_no_organisation_template(self, env):
        env.reg.register_print_source({**BALANCE, "name": "accounts.ledger", "grant": None}, owner="accounts")
        with pytest.raises(PermissionDenied):
            await env.printer.save_override(OWNER, "accounts.ledger", self.ORG)
        assert (await env.printer.render_data(OWNER, "accounts.ledger", DATA))["html"]  # il s'imprime quand même


class TestTheServicePluginsUse:
    async def test_a_job_prints_in_system_context(self, env):
        printers = DataPrinters(env.printer, settings=None)  # type: ignore[arg-type]
        printer = printers.system("acme", "balance mensuelle")
        assert isinstance(printer.context, RequestContext) and printer.context.is_system
        assert "Kounandy SARL" in await printer.html("accounts.balance", DATA, title="Balance")
