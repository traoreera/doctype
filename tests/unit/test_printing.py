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
    footer_markup,
    generated_template,
    letterhead_markup,
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

    def test_a_letterhead_is_a_name_or_a_pdf_never_both(self):
        with pytest.raises(InvalidDefinition, match="OU"):
            template(letterhead={"name": "default", "pdf": "a" * 32})

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


class TestTheLetterheadMarkup:
    """Le papier à en-tête est rendu ICI, mais ses OCTETS sont lus par le service : c'est la frontière du module pur (§5/§6)."""

    def test_an_embedded_image_is_placed_at_the_top(self):
        uri = "data:image/png;base64,iVBORw0KGgo="
        out = render_html(template(), INVOICE, DATA, name="FA-1", children=CHILDREN,
                          letterhead_html=letterhead_markup(uri))
        assert f'<div class=lh><img src="{uri}" alt=""></div>' in out
        assert out.index("class=lh") < out.index("<h1>")  # en tête du flux, donc sur la première page

    @pytest.mark.parametrize("bad", [
        "https://exemple.invalid/logo.png",  # une URL distante : exactement la faille que §6 ferme
        "data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=",  # un SVG porte du script
        "data:text/html;base64,PGI+",
        'data:image/png;base64,abc" onerror="alert(1)',  # une tentative de sortir de l'attribut
        "javascript:alert(1)",
    ])
    def test_anything_that_is_not_an_embedded_image_is_refused(self, bad):
        with pytest.raises(ValueError, match="incorporée"):
            letterhead_markup(bad)

    def test_no_image_is_no_markup(self):
        assert letterhead_markup(None) == "" and letterhead_markup("") == ""

    def test_the_footer_is_escaped_and_comes_last(self):
        out = render_html(template(), INVOICE, DATA, name="FA-1", children=CHILDREN,
                          footer_html=footer_markup("SARL <Durand> & Fils"))
        assert "SARL &lt;Durand&gt; &amp; Fils" in out and "<Durand>" not in out
        assert out.index("class=lf") > out.index("<h1>")

    def test_no_footer_is_no_markup(self):
        assert footer_markup(None) == "" and footer_markup("") == ""


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
