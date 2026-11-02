"""Les traductions des définitions (`translations.py`) : `/meta`, la liste des types et les espaces de travail dans la langue de la requête.

Ce que ces tests défendent, chacun par une mutation qui le casse :
- un libellé se traduit par son TEXTE SOURCE, une clé de contexte (« Type|texte ») gagne, un texte inconnu reste tel quel ;
- les VALEURS ne bougent jamais (options, états, noms) : seuls leurs libellés s'ajoutent (`option_labels`, `state_labels`) ;
- le libellé que l'ORGANISATION a choisi (son habillage) gagne sur la traduction ;
- les lignes d'une table, les sections et les onglets suivent ;
- un plugin qui part emporte son catalogue ; un catalogue fautif est une erreur de CHARGEMENT ;
- la langue vient de l'en-tête `Accept-Language` ; la préférence personnelle n'accepte que les langues du Desk.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from starlette.requests import Request

from tests.conftest import make_ctx
from xcore_doctype.context import ContextSettings, RequestContext
from xcore_doctype.document import DocumentService
from xcore_doctype.errors import InvalidDefinition, ValidationFailed
from xcore_doctype.preferences import Preferences
from xcore_doctype.registry import DocTypeRegistry
from xcore_doctype.schema import parse_definition
from xcore_doctype.translations import Translations, parse_language

LINE = {"schema_version": 1, "name": "Entry Line", "istable": True, "fields": [{"fieldname": "qty", "fieldtype": "Decimal", "decimals": 3, "label": "Quantity"}]}
ENTRY = {
    "schema_version": 1, "name": "Stock Entry", "module": "stock", "label": "Stock Entry",
    "fields": [
        {"fieldname": "purpose", "fieldtype": "Select", "label": "Purpose", "options": ["Receipt", "Issue"], "default": "Receipt"},
        {"fieldname": "posting_date", "fieldtype": "Date", "label": "Posting Date", "description": "The day"},
        {"fieldname": "items", "fieldtype": "Table", "options": "Entry Line", "label": "Items"},
    ],
    "layout": [{"section": "Details", "tab": "Main", "columns": [["purpose", "posting_date"]]}, {"section": "Lines", "tab": "Items", "columns": [["items"]]}],
    "workflow": {
        "states": [{"name": "Draft"}, {"name": "Approved"}],
        "transitions": [{"action": "Approve", "from": "Draft", "to": "Approved"}],
    },
}
FR = """
messages:
  Stock Entry: Mouvement de stock
  A movement: Un mouvement
  Purpose: Objet
  Receipt: Réception
  Posting Date: Date de comptabilisation
  Quantity: Quantité
  Items: Lignes
  Details: Détails
  Main: Principal
  Draft: Brouillon
  Approve: Approuver
  Stock: Stock
context:
  "Stock Entry|Purpose": Nature du mouvement
"""
GRANTS = [f"stock:stock_entry:{a}" for a in ("read", "write", "workflow_approve")]
ALICE = make_ctx("acme", "alice", permissions=GRANTS)
FRENCH = replace(ALICE, language="fr")


class Log:
    def info(self, *a, **k): ...
    def warning(self, *a, **k): ...
    def error(self, *a, **k): ...


@pytest.fixture
def svc(db, tables, tmp_path):
    reg = DocTypeRegistry()
    for d in (LINE, ENTRY):
        reg.register(parse_definition(d))
    reg.validate_links()
    s = DocumentService(reg, db, tables, Log())
    (tmp_path / "stock").mkdir()
    (tmp_path / "stock" / "fr.yaml").write_text(FR, encoding="utf-8")
    s.translations.load_dir("stock", tmp_path / "stock")
    return s


async def test_meta_speaks_the_request_s_language(svc):
    meta = await svc.fetch_meta(FRENCH, "Stock Entry")
    assert meta["label"] == "Mouvement de stock"
    fields = {f["fieldname"]: f for f in meta["fields"]}
    assert fields["purpose"]["label"] == "Nature du mouvement"  # la clé de contexte gagne sur « Purpose → Objet »
    assert fields["posting_date"]["label"] == "Date de comptabilisation"
    assert fields["posting_date"]["description"] == "The day"  # inconnu : tel quel, jamais vide
    assert [(s["section"], s["tab"]) for s in meta["layout"]] == [("Détails", "Principal"), ("Lines", "Lignes")]
    assert meta["children"]["Entry Line"]["fields"][0]["label"] == "Quantité"


async def test_values_never_move_only_their_labels(svc):
    meta = await svc.fetch_meta(FRENCH, "Stock Entry")
    purpose = next(f for f in meta["fields"] if f["fieldname"] == "purpose")
    assert purpose["options"] == ["Receipt", "Issue"] and purpose["default"] == "Receipt"
    assert purpose["option_labels"] == {"Receipt": "Réception"}  # « Issue » n'est pas traduit : pas de libellé inventé
    assert [s["name"] for s in meta["workflow"]["states"]] == ["Draft", "Approved"]
    assert meta["state_labels"] == {"Draft": "Brouillon"} and meta["action_labels"] == {"Approve": "Approuver"}


async def test_without_a_language_or_catalog_nothing_changes(svc):
    for ctx in (ALICE, replace(ALICE, language="de")):
        meta = await svc.fetch_meta(ctx, "Stock Entry")
        assert meta["label"] == "Stock Entry" and "state_labels" not in meta
        assert all("option_labels" not in f for f in meta["fields"])


async def test_the_organisation_s_own_label_wins(svc):
    # « Items » est dans le catalogue (« Lignes ») : c'est l'organisation qui l'a écrit, il ne se traduit pas.
    await svc.save_form_override(ALICE, "Stock Entry", {"fields": {"posting_date": {"label": "Items"}}})
    fields = {f["fieldname"]: f for f in (await svc.fetch_meta(FRENCH, "Stock Entry"))["fields"]}
    assert fields["posting_date"]["label"] == "Items"
    assert fields["purpose"]["label"] == "Nature du mouvement"


async def test_the_type_list_and_the_workspaces_follow(svc):
    assert [m["label"] for m in svc.list_meta(FRENCH)] == ["Mouvement de stock"]
    spaces = svc.list_workspaces(FRENCH)
    shortcuts = [b for ws in spaces for b in ws["blocks"] if b.get("doctype") == "Stock Entry" and b.get("label")]
    assert shortcuts and all(b["label"] == "Mouvement de stock" for b in shortcuts)


def test_a_plugin_that_leaves_takes_its_catalog(tmp_path):
    t = Translations()
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "a" / "fr.yaml").write_text("messages: {Save: Enregistrer A}", encoding="utf-8")
    (tmp_path / "b" / "fr.yaml").write_text("messages: {Save: Enregistrer B, Open: Ouvrir}", encoding="utf-8")
    t.load_dir("b", tmp_path / "b")
    t.load_dir("a", tmp_path / "a")
    assert (t.text("fr", "Save"), t.text("fr", "Open")) == ("Enregistrer A", "Ouvrir")  # le premier par NOM gagne, pas le dernier chargé
    t.unload("a")
    assert t.text("fr", "Save") == "Enregistrer B"
    t.unload("b")
    assert t.text("fr", "Open") == "Open" and t.languages() == []


@pytest.mark.parametrize("name, body, why", [
    ("french.yaml", "messages: {}", "nomme d'après sa langue"),
    ("fr.yaml", "- a list", "messages"),
    ("fr.yaml", "messages: {Save: ''}", "non vides"),
    ("fr.yaml", "context: {Save: Enregistrer}", "DocType|texte"),
    ("fr.yaml", "labels: {}", "rien d'autre"),
    ("fr.yaml", "messages: [", "YAML invalide"),
])
def test_a_broken_catalog_is_a_load_error(tmp_path, name, body, why):
    (tmp_path / name).write_text(body, encoding="utf-8")
    with pytest.raises(InvalidDefinition, match=why):
        Translations().load_dir("p", tmp_path)


@pytest.mark.parametrize("header, lang", [
    ("fr-FR,fr;q=0.9,en;q=0.8", "fr"), ("en", "en"), ("EN-us", "en"), ("*", None), ("", None), (None, None), ("français", None),
])
def test_the_language_comes_from_accept_language(header, lang):
    assert parse_language(header) == lang


def test_the_http_context_carries_it():
    headers = [(b"accept-language", b"en-GB,en;q=0.9")]
    request = Request({"type": "http", "headers": headers, "state": {"tenant_id": "acme"}})
    ctx = RequestContext.from_http(request, {"sub": "alice", "tenant_id": "acme"}, ContextSettings())
    assert ctx.language == "en"


async def test_one_s_own_language_is_kept_and_only_the_desk_s(svc):
    prefs = Preferences(svc, None)
    assert await prefs.get(ALICE) == {}
    assert await prefs.set_my_language(ALICE, "en") == {"my_language": "en"}
    assert await prefs.get(ALICE) == {"my_language": "en"}
    assert await prefs.my_language(make_ctx("acme", "bob", permissions=GRANTS)) is None  # chacun la sienne
    assert await prefs.my_language(make_ctx("globex", "alice", permissions=GRANTS)) is None  # et par organisation
    with pytest.raises(ValidationFailed):
        await prefs.set_my_language(ALICE, "de")
    await prefs.set_my_language(ALICE, None)
    assert await prefs.get(ALICE) == {}
