"""`xprint` : le plugin de plateforme qui livre, en YAML seul, les gabarits d'impression des pièces commerciales de l'ERP.

Ce que ces tests défendent :
- chaque gabarit se CHARGE contre le vrai jeu `doctypes/` (un champ renommé dans un DocType casserait ici, pas à la première facture) ;
- chaque gabarit se REND, sur un document vide comme sur un document rempli, et imprime ses libellés en français ;
- le chargeur ne prend que les types PRÉSENTS (un module absent n'est pas une faute) et laisse tomber le plugin sur un gabarit fautif ;
- `install` pose le dossier `print/` avec le plugin.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.support.erp_set import load_erp_set
from xcore_doctype import platform_plugins_dir, printing
from xcore_doctype.errors import InvalidDefinition
from xcore_doctype.registry import DocTypeChannel, DocTypeRegistry

ROOT = Path(__file__).resolve().parents[2]
ERP_DIR = ROOT / "doctypes"
XPRINT = platform_plugins_dir() / "xprint"
FOLDERS = sorted(p for p in (XPRINT / "print").iterdir() if p.is_dir())
TITLES = {
    "Sales Invoice": "FACTURE", "Quotation": "DEVIS", "Sales Order": "CONFIRMATION DE COMMANDE",
    "Delivery Note": "BON DE LIVRAISON", "Purchase Order": "BON DE COMMANDE", "Payment Entry": "REÇU DE PAIEMENT",
}


@pytest.fixture(scope="module")
def erp() -> DocTypeRegistry:
    if not ERP_DIR.is_dir():
        pytest.skip("doctypes/ absent")
    reg = DocTypeRegistry()
    load_erp_set(reg, ERP_DIR)
    for folder in FOLDERS:
        reg.load_print_templates(folder, owner="xprint")
    return reg


def test_one_folder_per_doctype_and_every_piece_is_there():
    assert [f.name for f in FOLDERS] == sorted(TITLES)
    for folder in FOLDERS:
        assert [p.name for p in folder.iterdir()] == ["standard.yaml"]


@pytest.mark.parametrize("doctype", sorted(TITLES))
def test_every_template_loads_against_the_erp_set(erp, doctype):
    templates = erp.print_templates(doctype)
    assert [t.name for t in templates] == ["standard"]
    assert templates[0].content is not None  # un document libre : ses libellés sont écrits en français, pas ceux du DocType


@pytest.mark.parametrize("doctype", sorted(TITLES))
def test_every_template_renders_an_empty_document_and_says_what_it_is(erp, doctype):
    compiled = erp.get(doctype)
    html = printing.render_html(erp.print_templates(doctype)[0], compiled.definition, {}, name="DOC-1", children=compiled.children_by_field)
    assert TITLES[doctype] in html and "DOC-1" in html


def test_the_invoice_prints_its_lines_totals_and_words(erp):
    compiled = erp.get("Sales Invoice")
    data = {
        "customer_name": "Établissements Koné", "posting_date": "2026-11-04",
        "items": [{"item_code": "VIS", "item_name": "Vis inox", "qty": "12.000", "uom": "Boîte", "rate": "4500.00", "amount": "54000.00"}],
        "taxes": [{"description": "TVA 18 %", "rate": 18, "tax_amount": "9720.00"}],
        "net_total": "54000.00", "total_taxes_and_charges": "9720.00", "grand_total": "63720.00",
        "in_words": "Soixante-trois mille sept cent vingt",
    }
    html = printing.render_html(erp.print_templates("Sales Invoice")[0], compiled.definition, data, name="SI-2026-00001",
                                children=compiled.children_by_field)
    for expected in ("Établissements Koné", "Vis inox", "54000.00", "TVA 18 %", "63720.00", "Soixante-trois mille", "Désignation", "Total TTC"):
        assert expected in html, expected
    assert 'class="lines accent' in html  # le style de la galerie


# ── Le chargeur (`src/main.py`) ──────────────────────────────────────────────────────────────────


def _plugin_class():
    spec = importlib.util.spec_from_file_location("xprint_main", XPRINT / "src" / "main.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module.Plugin


class Log:
    def __init__(self) -> None:
        self.lines: list[tuple[str, dict]] = []

    def info(self, msg, **fields):
        self.lines.append((msg, fields))

    warning = error = info


def _instance(channel: DocTypeChannel):
    cls = _plugin_class()  # une classe neuve par test : on peut lui poser le journal du test (une propriété dans TrustedBase)
    cls.logger = Log()
    plugin = cls.__new__(cls)
    plugin.__dict__.update(get_service=lambda name: channel, ctx=SimpleNamespace(name="xprint"))
    return plugin


async def test_only_the_doctypes_present_get_their_template(tmp_path):
    """Un module absent (ici : tout sauf la vente) n'est pas une faute : ses gabarits sont sautés, et le journal le dit."""
    if not ERP_DIR.is_dir():
        pytest.skip("doctypes/ absent")
    reg = DocTypeRegistry()
    load_erp_set(reg, ERP_DIR)
    present = {"Sales Invoice", "Quotation"}
    channel = DocTypeChannel(reg)
    channel.has = lambda doctype: doctype in present  # type: ignore[method-assign]
    plugin = _instance(channel)
    await plugin.on_load()
    assert {k[0] for k in reg._print_templates} == present
    msg, fields = plugin.logger.lines[-1]
    assert msg == "xprint loaded" and sorted(fields["skipped_doctypes"]) == sorted(set(TITLES) - present)
    await plugin.on_unload()
    assert reg._print_templates == {}  # retirés avec le plugin


async def test_a_broken_template_fails_the_plugin_instead_of_being_skipped(tmp_path, monkeypatch):
    """Un gabarit FAUTIF est un défaut à corriger : il fait échouer le chargement, jamais un silence."""
    reg = DocTypeRegistry()
    from xcore_doctype.schema import parse_definition

    reg.register(parse_definition({"schema_version": 1, "name": "Sales Invoice", "module": "accounts",
                                   "fields": [{"fieldname": "customer_name", "fieldtype": "Text"}]}))
    channel = DocTypeChannel(reg)
    plugin = _instance(channel)
    broken = tmp_path / "print" / "Sales Invoice"
    broken.mkdir(parents=True)
    (broken / "standard.yaml").write_text(
        "schema_version: 1\ndoctype: Sales Invoice\nname: standard\ncontent: '<p><span data-value=\"doc.nexiste_pas\">x</span></p>'\n"
    )
    monkeypatch.setitem(type(plugin).on_load.__globals__, "PRINT", tmp_path / "print")
    with pytest.raises(InvalidDefinition, match="nexiste_pas"):
        await plugin.on_load()


def test_the_channel_says_whether_a_doctype_is_loaded():
    reg = DocTypeRegistry()
    from xcore_doctype.schema import parse_definition

    reg.register(parse_definition({"schema_version": 1, "name": "Livre", "module": "biblio", "fields": [{"fieldname": "titre", "fieldtype": "Text"}]}))
    channel = DocTypeChannel(reg)
    assert channel.has("Livre") is True and channel.has("Emprunt") is False


def test_install_lays_the_templates_with_the_plugin(tmp_path):
    from xcore_doctype.cli import install

    install(tmp_path / "app")
    laid = sorted(p.parent.name for p in (tmp_path / "app" / "xprint" / "print").rglob("standard.yaml"))
    assert laid == sorted(TITLES)
