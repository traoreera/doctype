"""Impression : le vocabulaire des gabarits et le rendu HTML (process/impression.md).

Aucune base, aucun droit, aucun moteur PDF : `printing.py` reçoit un DocType, des données et un gabarit, il
rend une chaîne. C'est ce découpage qui rend le rendu testable à la ligne près.
"""

from __future__ import annotations

import pytest
import yaml

from xcore_doctype.registry import DocTypeRegistry

from xcore_doctype.errors import InvalidDefinition
from xcore_doctype.printing import (
    Formats,
    generated_template,
    PRINT_SPECIALS,
    parse_template,
    render_html,
    reserved_margins_css,
    template_fields_problems,
)
from xcore_doctype.schema import parse_definition


def f(fieldname, fieldtype="Text", **kw):
    return {"fieldname": fieldname, "fieldtype": fieldtype, **kw}


LINE = parse_definition({
    "schema_version": 1, "name": "Facture Ligne", "istable": True,
    "fields": [f("article"), f("entrepot"), f("qte", "Decimal", decimals=3),
               f("pu", "Currency", decimals=2), f("montant", "Currency", decimals=2), f("secret", "Password")],
})
INVOICE = parse_definition({
    "schema_version": 1, "name": "Facture", "module": "ventes", "submittable": True,
    "fields": [f("client", required=True), f("date_piece", "Date"), f("note", "LongText"),
               f("lignes", "Table", options="Facture Ligne"),
               f("total", "Currency", decimals=2), f("tva", "Currency", decimals=2),
               f("total_ttc", "Currency", decimals=2),
               f("mot_de_passe", "Password"), f("piece_jointe", "Attach")],
})
CHILDREN = {"lignes": LINE}


def template(**extra):
    base = {
        "schema_version": 1, "doctype": "Facture",
        "blocks": [
            {"type": "title", "text": "Facture {{ name }}"},
            {"type": "fields", "columns": 2, "fields": ["client", "date_piece"]},
            {"type": "table", "field": "lignes",
             "columns": [{"field": "article"}, {"field": "qte", "align": "right"},
                         {"field": "montant", "align": "right", "width": 25}]},
            {"type": "totals", "fields": ["total", "tva", "total_ttc"]},
        ],
    }
    base.update(extra)
    return parse_template(base)


DATA = {
    "client": "Acme SA", "date_piece": "2026-10-27",
    "total": "1000.00", "tva": "200.00", "total_ttc": "1200.00",
    "lignes": [
        {"article": "ITEM-1", "entrepot": "Central", "qte": "2.000", "pu": "250.00", "montant": "500.00"},
        {"article": "ITEM-2", "entrepot": "Central", "qte": "1.000", "pu": "300.00", "montant": "300.00"},
        {"article": "ITEM-3", "entrepot": "Atelier", "qte": "1.000", "pu": "200.00", "montant": "200.00"},
    ],
}


def html(tpl=None, data=None):
    return render_html(tpl or template(), INVOICE, data if data is not None else DATA, name="FA-2026-00001", children=CHILDREN)


class TestTheVocabularyIsClosed:
    def test_an_unknown_block_is_refused(self):
        with pytest.raises(InvalidDefinition):
            parse_template({"schema_version": 1, "doctype": "Facture", "blocks": [{"type": "iframe", "src": "x"}]})

    def test_a_template_needs_at_least_one_block(self):
        with pytest.raises(InvalidDefinition):
            parse_template({"schema_version": 1, "doctype": "Facture", "blocks": []})

    def test_column_widths_cannot_exceed_the_table(self):
        with pytest.raises(InvalidDefinition, match="largeurs"):
            template(blocks=[{"type": "table", "field": "lignes",
                              "columns": [{"field": "article", "width": 60}, {"field": "qte", "width": 60}]}])

    def test_a_subtotal_without_a_group_is_refused(self):
        """Un sous-total se calcule PAR GROUPE : sans `group_by` il ne veut rien dire."""
        with pytest.raises(InvalidDefinition, match="group_by"):
            template(blocks=[{"type": "table", "field": "lignes", "subtotal": ["montant"],
                              "columns": [{"field": "montant"}]}])

    def test_a_subtotal_on_an_unlisted_column_is_refused(self):
        with pytest.raises(InvalidDefinition, match="columns"):
            template(blocks=[{"type": "table", "field": "lignes", "group_by": "entrepot", "subtotal": ["pu"],
                              "columns": [{"field": "article"}]}])

    def test_a_named_letterhead_of_before_is_forgotten_not_refused(self):
        """Le `Letter Head` nommé n'existe plus (§14) : un gabarit qui le citait reste LISIBLE, le nom est oublié."""
        assert template(letterhead={"name": "default"}).letterhead.pdf is None
        assert template(letterhead={"name": "x", "pdf": "a" * 32}).letterhead.pdf == "a" * 32

    def test_a_background_pdf_is_a_file_id_never_a_url(self):
        """Le point de sécurité du §6 : jamais une URL, jamais un chemin."""
        with pytest.raises(InvalidDefinition):
            template(letterhead={"pdf": "https://example.invalid/entete.pdf"})

    def test_a_negative_margin_is_refused(self):
        with pytest.raises(InvalidDefinition):
            template(page={"top": -5})


class TestWhatIsRefusedAtLoadTime:
    """§8 : une faute de gabarit échoue au chargement, jamais à la première impression."""

    def check(self, tpl):
        return template_fields_problems(tpl, INVOICE, CHILDREN.get)

    def test_a_valid_template_has_no_problem(self):
        assert self.check(template()) == []

    def test_an_unknown_field_is_named(self):
        got = self.check(template(blocks=[{"type": "fields", "fields": ["nope"]}]))
        assert len(got) == 1 and "nope" in got[0]

    @pytest.mark.parametrize("field", ["mot_de_passe", "piece_jointe"])
    def test_a_secret_and_a_file_never_print(self, field):
        got = self.check(template(blocks=[{"type": "fields", "fields": [field]}]))
        assert len(got) == 1 and "ne s'imprime pas" in got[0]

    def test_a_table_is_refused_in_a_fields_block(self):
        got = self.check(template(blocks=[{"type": "fields", "fields": ["lignes"]}]))
        assert len(got) == 1 and "propre bloc" in got[0]

    def test_a_total_must_be_a_number(self):
        got = self.check(template(blocks=[{"type": "totals", "fields": ["client"]}]))
        assert len(got) == 1 and "attendu" in got[0]

    def test_a_table_block_needs_a_table_field(self):
        got = self.check(template(blocks=[{"type": "table", "field": "client", "columns": [{"field": "article"}]}]))
        assert len(got) == 1 and "n'est pas un champ Table" in got[0]

    def test_an_unknown_column_is_named(self):
        got = self.check(template(blocks=[{"type": "table", "field": "lignes", "columns": [{"field": "nope"}]}]))
        assert len(got) == 1 and "nope" in got[0]

    def test_a_secret_column_never_prints(self):
        got = self.check(template(blocks=[{"type": "table", "field": "lignes", "columns": [{"field": "secret"}]}]))
        assert len(got) == 1 and "ne s'imprime pas" in got[0]

    def test_a_group_by_must_be_groupable(self):
        got = self.check(template(blocks=[{"type": "table", "field": "lignes", "group_by": "qte",
                                           "columns": [{"field": "article"}]}]))
        assert len(got) == 1 and "ne regroupe pas" in got[0]

    def test_an_unknown_substitution_is_refused(self):
        got = self.check(template(blocks=[{"type": "title", "text": "Facture {{ doc.nope }}"}]))
        assert len(got) == 1 and "champ inconnu" in got[0]

    def test_a_secret_cannot_be_quoted_in_a_text(self):
        got = self.check(template(blocks=[{"type": "text", "text": "{{ doc.mot_de_passe }}"}]))
        assert len(got) == 1

    def test_a_variable_of_the_notification_rules_is_refused_here(self):
        """`event`, `actor`, `link` existent pour une notification, pas sur un document imprimé — et la
        substitution laisserait le jeton TEL QUEL, donc visible sur la facture."""
        got = self.check(template(blocks=[{"type": "title", "text": "Facture {{ actor }}"}]))
        assert len(got) == 1 and all(s in got[0] for s in PRINT_SPECIALS)


class TestTheRenderedDocument:
    def test_the_substitution_fills_the_document_number(self):
        assert "<h1>Facture FA-2026-00001</h1>" in html()

    def test_a_field_value_appears_with_its_label(self):
        out = html()
        assert "Acme SA" in out and "2026-10-27" in out

    def test_every_line_is_printed(self):
        out = html()
        assert all(f"ITEM-{i}" in out for i in (1, 2, 3))

    def test_the_table_header_repeats_across_pages(self):
        """Mesuré contre le moteur (11 pages, en-tête partout) : côté HTML, c'est `table-header-group`."""
        assert "display:table-header-group" in html()

    def test_a_decimal_keeps_its_canonical_string(self):
        """Jamais de passage par un flottant : « 2.000 » reste « 2.000 », pas « 2.0 »."""
        assert ">2.000<" in html()

    def test_no_css_grid_is_ever_emitted(self):
        """Le moteur ne le connaît pas : la contrainte est tenue par construction."""
        out = html()
        assert "display:grid" not in out and "grid-template" not in out

    def test_a_value_is_escaped(self):
        out = html(data={**DATA, "client": '<script>alert("x")</script>'})
        assert "<script>" not in out and "&lt;script&gt;" in out

    def test_an_empty_table_prints_its_header_and_no_row(self):
        out = html(data={**DATA, "lignes": []})
        assert "<tbody></tbody>" in out and "ITEM-1" not in out

    def test_a_missing_value_prints_nothing_rather_than_none(self):
        out = html(data={"client": "Acme SA", "lignes": []})
        assert "None" not in out


class TestSubtotalsAreExact:
    def grouped(self):
        return template(blocks=[{
            "type": "table", "field": "lignes", "group_by": "entrepot",
            "subtotal": ["qte", "montant"],
            "columns": [{"field": "article"}, {"field": "qte", "align": "right"}, {"field": "montant", "align": "right"}],
        }])

    def test_each_group_gets_its_subtotal(self):
        out = html(self.grouped())
        # Central : 2.000 + 1.000 = 3.000 et 500.00 + 300.00 = 800.00 ; Atelier : 1.000 et 200.00
        assert ">3.000<" in out and ">800.00<" in out
        assert ">1.000<" in out and ">200.00<" in out

    def test_a_subtotal_keeps_the_scale_of_its_field(self):
        """La somme sort à l'échelle déclarée : trois décimales pour une quantité, deux pour un montant."""
        out = html(self.grouped())
        assert ">3.000<" in out and ">3.0<" not in out

    def test_a_subtotal_is_exact_not_floating(self):
        data = {**DATA, "lignes": [
            {"article": "A", "entrepot": "C", "qte": "0.100", "montant": "0.10"},
            {"article": "B", "entrepot": "C", "qte": "0.200", "montant": "0.20"},
        ]}
        out = html(self.grouped(), data)
        assert ">0.300<" in out  # jamais 0.30000000000000004
        assert ">0.30<" in out

    def test_an_unreadable_value_does_not_break_the_print(self):
        data = {**DATA, "lignes": [{"article": "A", "entrepot": "C", "qte": "pas un nombre", "montant": "100.00"}]}
        out = html(self.grouped(), data)
        assert ">100.00<" in out  # la colonne lisible est quand même totalisée

    def test_groups_keep_the_order_the_user_typed(self):
        out = html(self.grouped())
        assert out.index("Central") < out.index("Atelier")


class TestTheGeneratedTemplate:
    """Aucun document n'est jamais inimprimable : à défaut de gabarit, le moteur en fabrique un (§2)."""

    def made(self, definition=INVOICE, children=CHILDREN):
        return generated_template(definition, children)

    def test_it_is_valid_by_construction(self):
        assert template_fields_problems(self.made(), INVOICE, CHILDREN.get) == []

    def test_it_prints_the_document_number(self):
        out = render_html(self.made(), INVOICE, DATA, name="FA-1", children=CHILDREN)
        assert "FA-1" in out

    def test_it_never_prints_a_secret_or_a_file(self):
        """Il choisit par TYPE, puisque le schéma n'a aucune propriété d'impression (§4)."""
        placed = [n for b in self.made().blocks for n in getattr(b, "fields", [])]
        assert "mot_de_passe" not in placed and "piece_jointe" not in placed

    def test_the_amounts_become_the_totals_block(self):
        totals = [b for b in self.made().blocks if b.type == "totals"]
        assert totals and set(totals[0].fields) == {"total", "tva", "total_ttc"}

    def test_a_table_becomes_a_table_block_with_its_printable_columns(self):
        tables = [b for b in self.made().blocks if b.type == "table"]
        assert len(tables) == 1 and tables[0].field == "lignes"
        assert "secret" not in [c.field for c in tables[0].columns]

    def test_a_numeric_column_is_aligned_right(self):
        table = next(b for b in self.made().blocks if b.type == "table")
        assert next(c for c in table.columns if c.field == "montant").align == "right"

    def test_it_follows_the_layout_when_there_is_one(self):
        """C'est l'ordre que l'utilisateur connaît déjà de sa fiche."""
        definition = parse_definition({
            "schema_version": 1, "name": "T", "module": "m",
            "fields": [f("a"), f("b"), f("c")],
            "layout": [{"section": "S", "columns": [["c", "a"]]}],
        })
        fields = next(b for b in generated_template(definition).blocks if b.type == "fields")
        assert fields.fields == ["c", "a"]  # « b » n'est pas placé, donc pas imprimé

    def test_a_hidden_field_is_not_printed(self):
        definition = parse_definition({"schema_version": 1, "name": "T", "module": "m",
                                       "fields": [f("a"), f("secret_interne", hidden=True)]})
        placed = [n for b in generated_template(definition).blocks for n in getattr(b, "fields", [])]
        assert placed == ["a"]


class TestTemplatesShippedByAPlugin:
    """Le chargement des gabarits livrés (§2) : tout ou rien, et les fautes sortent ICI."""

    def shipped(self, tmp_path, **tpl):
        d = tmp_path / "print"
        d.mkdir(exist_ok=True)
        base = {"schema_version": 1, "doctype": "Facture",
                "blocks": [{"type": "title", "text": "Facture {{ name }}"}]}
        base.update(tpl)
        (d / f"{base.get('name', 'standard')}.yaml").write_text(yaml.safe_dump(base, allow_unicode=True), encoding="utf-8")
        return d

    def reg(self):
        r = DocTypeRegistry()
        r.register(LINE)
        r.register(INVOICE)
        return r

    def test_a_template_is_loaded_and_listed(self, tmp_path):
        r = self.reg()
        assert r.load_print_templates(self.shipped(tmp_path), owner="ventes") == ["Facture:standard"]
        assert [t.name for t in r.print_templates("Facture")] == ["standard"]
        assert r.print_template("Facture", "standard") is not None

    def test_it_goes_away_with_its_plugin(self, tmp_path):
        r = self.reg()
        r.load_print_templates(self.shipped(tmp_path), owner="ventes")
        r.unregister_owner("ventes")
        assert r.print_templates("Facture") == []

    def test_an_unknown_doctype_is_refused(self, tmp_path):
        r = self.reg()
        with pytest.raises(InvalidDefinition, match="inconnu"):
            r.load_print_templates(self.shipped(tmp_path, doctype="Nope"), owner="ventes")

    def test_a_child_doctype_cannot_be_printed(self, tmp_path):
        """Une ligne n'est pas un document : elle vit dans le JSON de son parent."""
        r = self.reg()
        with pytest.raises(InvalidDefinition, match="enfant"):
            r.load_print_templates(self.shipped(tmp_path, doctype="Facture Ligne"), owner="ventes")

    def test_two_plugins_cannot_ship_the_same_template(self, tmp_path):
        r = self.reg()
        r.load_print_templates(self.shipped(tmp_path), owner="ventes")
        with pytest.raises(InvalidDefinition, match="déjà livré"):
            r.load_print_templates(self.shipped(tmp_path), owner="autre")

    def test_a_bad_block_fails_the_whole_load(self, tmp_path):
        """Tout ou nothing : un dossier à moitié chargé laisserait un gabarit sans ses voisins."""
        d = self.shipped(tmp_path)
        (d / "casse.yaml").write_text(yaml.safe_dump(
            {"schema_version": 1, "doctype": "Facture", "name": "casse",
             "blocks": [{"type": "fields", "fields": ["nope"]}]}), encoding="utf-8")
        r = self.reg()
        with pytest.raises(InvalidDefinition, match="nope"):
            r.load_print_templates(d, owner="ventes")
        assert r.print_templates("Facture") == []


class TestTheReservedMargins:
    """Un fond PDF occupe des zones : le contenu s'y interdit, en S'AJOUTANT aux marges du gabarit (§5)."""

    def test_the_offsets_add_to_the_templates_margins(self):
        page = template(page={"top": 20, "bottom": 20}).page
        assert reserved_margins_css(page, top=45, bottom=25) == "@page{margin-top:65mm;margin-bottom:45mm}"

    def test_nothing_to_reserve_changes_nothing(self):
        """Le HTML d'un aperçu ordinaire ne doit pas changer d'un caractère quand il n'y a pas de fond."""
        page = template().page
        assert reserved_margins_css(page) == "" and reserved_margins_css(page, top=0, bottom=0) == ""

    def test_it_comes_after_the_stylesheet_so_it_wins(self):
        """La cascade CSS fait la règle : la dernière déclaration gagne, sans que le code ait à défaire la première."""
        out = render_html(template(), INVOICE, DATA, name="FA-1", children=CHILDREN,
                          css_extra="@page{margin-top:65mm}")
        assert out.index("@page{size:") < out.index("@page{margin-top:65mm}")


class TestCssExtraIsCssNotHtml:
    """`css_extra` arrive dans un <style>, un élément de TEXTE BRUT : les entités n'y sont pas relues, donc l'échapper en HTML
    ne protégeait de rien et cassait le CSS. Seule la suite qui fermerait la balise est refusée (`_checked_css`)."""

    def css(self, extra: str) -> str:
        out = render_html(template(), INVOICE, DATA, name="FA-1", children=CHILDREN, css_extra=extra)
        return out[out.index("<style>") + len("<style>"):out.index("</style>")]

    @pytest.mark.parametrize("extra", [
        '@page{@bottom-left{content:"Durand & Fils"}}',  # le « & » d'une raison sociale : « &amp; » s'imprimait tel quel
        ".doc > table{width:100%}",                      # un sélecteur enfant
        "@media (width < 600px){p{font-size:9pt}}",       # une requête de média par intervalle
        "div::after{content:'<'}",
    ])
    def test_the_css_arrives_verbatim(self, extra):
        assert extra in self.css(extra)

    def test_the_engines_own_reserved_margins_still_pass(self):
        assert "@page{margin-top:65mm}" in self.css("@page{margin-top:65mm}")

    @pytest.mark.parametrize("extra", ["p{}</style><script>x()</script>", "p{}</STYLE ", "p{}</style/"])
    def test_what_would_close_the_stylesheet_is_refused(self, extra):
        """Refusé, jamais nettoyé : c'est la faute de l'appelant, et un CSS n'a aucune raison de contenir `</style`."""
        with pytest.raises(ValueError, match="style"):
            self.css(extra)


# ── Nombres et dates selon la langue (traoreera/doctype#62) ───────────────────────────────────────────────────────────────────────────

NBSP = " "
FR = Formats("fr", "JJ/MM/AAAA")
BIG = {**DATA, "total": "1234567.50", "tva": "-200.00", "total_ttc": "1234367.50",
       "lignes": [{"article": "A", "entrepot": "C", "qte": "1500.000", "montant": "1000.00"},
                  {"article": "B", "entrepot": "C", "qte": "0.250", "montant": "2500.10"}]}


def shown(tpl=None, data=None, formats=FR):
    return render_html(tpl or template(), INVOICE, data if data is not None else BIG, name="FA-1", children=CHILDREN, formats=formats)


class TestNumbersAndDatesReadInTheOrganisationsLanguage:
    def test_a_number_is_only_rewritten_never_recomputed(self):
        assert FR.number("1234567.50") == f"1{NBSP}234{NBSP}567,50"
        assert FR.number("-999.5") == "-999,5" and FR.number("1000") == f"1{NBSP}000" and FR.number("0.001") == "0,001"
        assert Formats("en").number("1234567.50") == "1,234,567.50"
        assert FR.number("123456789012345678.123456789") == f"123{NBSP}456{NBSP}789{NBSP}012{NBSP}345{NBSP}678,123456789"  # aucun flottant
        assert FR.number("ABC-12") == "ABC-12" and FR.number("1e5") == "1e5"  # ce qui n'est pas un nombre du moteur reste tel quel

    def test_a_date_follows_the_organisations_date_format(self):
        assert FR.date("2026-11-10") == "10/11/2026"
        assert Formats("en", "MM/JJ/AAAA").date("2026-11-10") == "11/10/2026"
        assert Formats("fr", "AAAA-MM-JJ").date("2026-11-10") == "2026-11-10"
        assert Formats("en").date("2026-11-10") == "10/11/2026"  # sans réglage : le jour d'abord, dans les deux langues du Desk
        assert FR.date("hier") == "hier"

    def test_an_instant_is_read_in_the_organisations_timezone(self):
        from zoneinfo import ZoneInfo

        assert Formats("fr", None, ZoneInfo("Africa/Abidjan")).instant("2026-10-18T23:30:00Z") == "18/10/2026 23:30"
        assert Formats("fr", None, ZoneInfo("Pacific/Auckland")).instant("2026-10-18T23:30:00Z") == "19/10/2026 12:30"
        assert FR.instant("2026-10-18T23:30:00Z") == "18/10/2026 23:30"  # sans fuseau : UTC
        assert FR.instant("pas une heure") == "pas une heure"

    def test_fields_totals_and_lines_are_written_for_a_reader(self):
        out = shown()
        assert "27/10/2026" in out and "2026-10-27" not in out
        assert f">1{NBSP}234{NBSP}567,50<" in out and ">-200,00<" in out
        assert f">1{NBSP}500,000<" in out and f">2{NBSP}500,10<" in out and ">0,250<" in out

    def test_a_subtotal_is_summed_exactly_then_written_the_same_way(self):
        tpl = template(blocks=[{"type": "table", "field": "lignes", "group_by": "entrepot", "subtotal": ["qte", "montant"],
                                "columns": [{"field": "article"}, {"field": "qte"}, {"field": "montant"}]}])
        out = shown(tpl)
        assert f">1{NBSP}500,250<" in out and f">3{NBSP}500,10<" in out

    def test_the_type_decides_never_the_look_of_the_value(self):
        """Un Text « 2026-10-27 » ou « 75001 » n'est ni une date ni un nombre : seul le TYPE du champ fait formater."""
        out = shown(data={**BIG, "client": "2026-10-27", "note": "75001"})
        assert "<br>2026-10-27</td>" in out  # le client, un Text : tel quel
        assert "<br>27/10/2026</td>" in out  # la date de pièce, une Date : écrite pour un lecteur

    def test_a_text_block_quotes_the_same_readable_values(self):
        tpl = template(blocks=[{"type": "text", "text": "Le {{ doc.date_piece }}, total {{ doc.total }}"}])
        assert f"Le 27/10/2026, total 1{NBSP}234{NBSP}567,50" in shown(tpl)

    def test_a_template_or_a_column_can_ask_for_the_stored_form(self):
        raw_all = shown(template(raw_values=True))
        assert ">1234567.50<" in raw_all and "2026-10-27" in raw_all
        tpl = template(blocks=[{"type": "table", "field": "lignes", "group_by": "entrepot", "subtotal": ["qte", "montant"],
                                "columns": [{"field": "article"}, {"field": "qte", "raw": True}, {"field": "montant"}]}])
        out = shown(tpl)
        assert ">1500.000<" in out and ">1500.250<" in out  # la colonne brute, sous-total compris
        assert f">2{NBSP}500,10<" in out  # sa voisine suit la langue

    def test_without_formats_nothing_changes(self):
        assert ">1234567.50<" in shown(formats=None)

    def test_an_amount_keeps_its_currency_code_after_the_readable_number(self):
        from xcore_doctype.printing import _cell

        assert _cell("1200.00", INVOICE.field_map["total"], None, "EUR", FR) == f"1{NBSP}200,00 EUR"

    def test_a_free_document_formats_its_inserted_values_too(self):
        tpl = parse_template({"schema_version": 1, "doctype": "Facture",
                              "content": '<p>Le <span data-value="doc.date_piece"></span> : <span data-value="doc.total"></span></p>'})
        assert f"<p>Le 27/10/2026 : 1{NBSP}234{NBSP}567,50</p>" in shown(tpl)


# ── Gabarits : ligne de total en évidence, hauteur de cellule, bloc insécable (traoreera/doctype#64) ─────────────────────────────────

BALANCE_LINE = parse_definition({
    "schema_version": 1, "name": "Balance Ligne", "istable": True,
    "fields": [f("libelle"), f("montant", "Currency", decimals=2), f("est_total", "Boolean")],
})
BALANCE = parse_definition({
    "schema_version": 1, "name": "Balance", "module": "compta",
    "fields": [f("societe"), f("lignes", "Table", options="Balance Ligne")],
})


def balance(**block):
    return parse_template({"schema_version": 1, "doctype": "Balance", "blocks": [
        {"type": "table", "field": "lignes", "columns": [{"field": "libelle"}, {"field": "montant"}], **block}]})


class TestATotalLineStandsOut:
    ROWS = {"lignes": [{"libelle": "Capital", "montant": "100.00"}, {"libelle": "Total classe 1", "montant": "100.00", "est_total": True},
                       {"libelle": "Stocks", "montant": "5.00", "est_total": False}]}

    def test_only_the_rows_whose_flag_is_true_are_emphasised(self):
        out = render_html(balance(emphasis="est_total"), BALANCE, self.ROWS, name="B1", children={"lignes": BALANCE_LINE})
        assert out.count('<tr class="em">') == 1
        assert '<tr class="em"><td class=left>Total classe 1</td>' in out
        assert "table.lines tr.em td{font-weight:bold" in out

    def test_without_the_option_no_row_is_emphasised(self):
        assert '<tr class="em">' not in render_html(balance(), BALANCE, self.ROWS, name="B1", children={"lignes": BALANCE_LINE})

    @pytest.mark.parametrize("field, why", [("absent", "n'est pas un champ"), ("libelle", "il faut un Boolean")])
    def test_a_wrong_flag_is_a_load_error(self, field, why):
        problems = template_fields_problems(balance(emphasis=field), BALANCE, {"lignes": BALANCE_LINE}.get)
        assert len(problems) == 1 and "emphasis" in problems[0] and why in problems[0]


class TestAFreeDocumentReservesRoomAndKeepsBlocksWhole:
    def doc(self, content):
        return parse_template({"schema_version": 1, "doctype": "Balance", "content": content})

    def test_a_cell_height_and_an_unbreakable_block_are_kept_as_written(self):
        tpl = self.doc('<table style="page-break-inside: avoid"><tr><td style="height: 25mm">Le comptable</td></tr></table>'
                       '<p style="page-break-inside: avoid">Bon pour accord</p><div data-page-break></div><p>Passif</p>')
        out = render_html(tpl, BALANCE, {}, name="B1")
        assert '<table style="page-break-inside:avoid" class="lay bordered">' in out
        assert '<td style="height:25mm">Le comptable</td>' in out
        assert '<p style="page-break-inside:avoid">Bon pour accord</p><div class="pb"></div><p>Passif</p>' in out

    @pytest.mark.parametrize("content", [
        '<table><tr><td style="height: 900mm">x</td></tr></table>',  # une hauteur est BORNÉE
        '<p style="page-break-inside: auto">x</p>',  # un seul mot permis
        '<h1 style="page-break-inside: avoid">x</h1>',  # seulement sur un paragraphe ou un tableau
        '<p style="height: 20mm">x</p>',  # la hauteur ne se réserve que dans une cellule (ou sur une image)
    ])
    def test_anything_else_is_still_refused(self, content):
        with pytest.raises(InvalidDefinition):
            self.doc(content)


# ── En-tête et pied de page, numéros de page (traoreera/doctype#63) ──────────────────────────────────────────────────────────────────


def banded(**page):
    return template(page=page)


class TestHeaderAndFooterOnEveryPage:
    def test_a_footer_becomes_page_margin_boxes_with_the_engines_counters(self):
        out = html(banded(footer={"left": "{{ doc.client }} — {{ doctype }} {{ name }}", "right": "Page {page} / {pages}"}))
        assert "@page{@bottom-left{content:" in out and "@bottom-right{content:" in out
        right = out.split("@bottom-right{content:")[1].split(";")[0]
        assert right == '"Page\\000020" counter(page) "\\000020\\00002f\\000020" counter(pages)'
        left = out.split("@bottom-left{content:")[1].split(";")[0]
        assert left.startswith('"Acme\\000020SA\\000020\\002014\\000020Facture\\000020FA') and "counter(" not in left

    def test_a_header_goes_in_the_top_margin_and_zones_are_independent(self):
        out = html(banded(header={"center": "Relevé d'un tiers"}))
        assert "@top-center{content:" in out and "@top-left" not in out and "@bottom" not in out
        # L'espace après un échappement n'est PAS avalée : chaque caractère a ses six chiffres, sans séparateur.
        assert '"Relev\\0000e9\\000020d\\000027un\\000020tiers"' in out

    def test_nothing_of_a_value_escapes_the_css_string_or_the_style_tag(self):
        nasty = 'Durand & Fils "</style><script>alert(1)</script> \\ } @import url(x)'
        out = html(banded(footer={"left": "{{ doc.client }}"}), {**DATA, "client": nasty})
        box = out.split("@bottom-left{content:")[1].split(";font-family")[0]
        assert box.count('"') == 2  # la chaîne s'ouvre et se ferme, rien ne la referme avant
        assert not any(c in box[1:-1] for c in '<>&{}@();" ')
        assert out.count("</style>") == 1 and "<script>alert" not in out

    def test_numbers_and_dates_read_like_the_rest_of_the_document(self):
        out = render_html(banded(footer={"left": "Du {{ doc.date_piece }}, total {{ doc.total }}"}), INVOICE, BIG, name="FA-1", children=CHILDREN, formats=FR)
        left = out.split("@bottom-left{content:")[1].split(";")[0]
        assert "27\\00002f10\\00002f2026" in left and "1\\0000a0234\\0000a0567\\00002c50" in left

    def test_without_bands_the_stylesheet_is_what_it_was(self):
        assert "@top-" not in html() and "@bottom-" not in html()

    @pytest.mark.parametrize("page, why", [
        ({"footer": {}}, "au moins une zone"),
        ({"footer": {"left": "a\nb"}}, "une seule ligne"),
        ({"footer": {"left": "x" * 201}}, "200"),
        ({"footer": {"left": "Page {page}"}, "bottom": 5}, "marge `bottom` d'au moins 8 mm"),
        ({"header": {"left": "x"}, "top": 0}, "marge `top`"),
        ({"footer": {"middle": "x"}}, "middle"),
    ])
    def test_a_malformed_band_is_refused(self, page, why):
        with pytest.raises(InvalidDefinition, match=why):
            banded(**page)

    @pytest.mark.parametrize("text, why", [
        ("{{ doc.inconnu }}", "champ inconnu"),
        ("{{ doc.mot_de_passe }}", "ne se cite pas"),
        ("{{ actor }}", "n'existe pas sur un document imprimé"),
    ])
    def test_a_wrong_substitution_is_a_load_error(self, text, why):
        problems = template_fields_problems(banded(footer={"right": text}), INVOICE, CHILDREN.get)
        assert problems and all("page.footer.right" in p for p in problems) and any(why in p for p in problems)

    def test_a_free_document_takes_bands_too(self):
        tpl = parse_template({"schema_version": 1, "doctype": "Facture", "content": "<p>Grand livre</p>",
                              "page": {"footer": {"right": "{page}/{pages}"}}})
        assert '@bottom-right{content:counter(page) "\\00002f" counter(pages)' in render_html(tpl, INVOICE, DATA, name="GL")
