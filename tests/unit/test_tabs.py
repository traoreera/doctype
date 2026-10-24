"""Onglets de fiche (programme groupe 1, élément 2) : un `tab` par section, les mêmes règles pour le YAML et pour l'habillage d'une
organisation, et rien de changé pour une disposition sans onglet."""

from __future__ import annotations

import pytest
import yaml

from tests.conftest import make_ctx
from xcore_doctype.document import DocumentService
from xcore_doctype.errors import InvalidDefinition, ValidationFailed
from xcore_doctype.overrides import apply_form_override, validate_form_override
from xcore_doctype.registry import DocTypeRegistry
from xcore_doctype.schema import MAX_TABS, complete_layout, layout_tab_problems, parse_definition

FIELDS = [
    {"fieldname": "title", "fieldtype": "Text"},
    {"fieldname": "customer", "fieldtype": "Text"},
    {"fieldname": "amount", "fieldtype": "Int"},
    {"fieldname": "account", "fieldtype": "Text"},
    {"fieldname": "note", "fieldtype": "Text"},
    {"fieldname": "secret_flag", "fieldtype": "Boolean", "hidden": True},
]
TABBED = [
    {"tab": "Détails", "section": "Client", "columns": [["title", "customer"]]},
    {"tab": "Détails", "section": "Montant", "columns": [["amount"]]},
    {"tab": "Comptabilité", "columns": [["account"]]},
]


def invoice(layout=TABBED, **extra):
    return {"schema_version": 1, "name": "Invoice", "module": "sales", "fields": FIELDS, "layout": layout} | extra


class TestDefinition:
    def test_sections_carry_their_tab_and_what_the_layout_forgets_closes_the_first_tab(self):
        out = parse_definition(invoice()).effective_layout()
        assert [(s.get("tab"), s["section"], s["columns"]) for s in out] == [
            ("Détails", "Client", [["title", "customer"]]),
            ("Détails", "Montant", [["amount"]]),
            ("Détails", None, [["note"]]),  # `note` n'est placé nulle part : sous les yeux, dans l'onglet qui s'ouvre
            ("Comptabilité", None, [["account"]]),
        ]

    def test_a_layout_without_tabs_keeps_exactly_its_former_shape(self):
        out = parse_definition(invoice([{"section": "Client", "columns": [["title"]]}])).effective_layout()
        assert all("tab" not in s for s in out)
        assert out[-1] == {"section": None, "collapsible": False, "collapsed": False, "columns": [["customer", "amount", "account", "note"]]}

    @pytest.mark.parametrize(
        ("layout", "fragment"),
        [
            ([{"tab": "A", "columns": [["title"]]}, {"columns": [["amount"]]}], "CHAQUE section"),
            ([{"columns": [["title"]]}, {"tab": "A", "columns": [["amount"]]}], "CHAQUE section"),
            ([{"tab": "A", "columns": [["title"]]}, {"tab": "B", "columns": [["amount"]]}, {"tab": "A", "columns": [["account"]]}], "doivent se suivre"),
            ([{"tab": "Seul", "columns": [["title"]]}], None),
            ([{"tab": "", "columns": [["title"]]}], "at least 1"),
            ([{"tab": "   ", "columns": [["title"]]}], "at least 1"),  # des blancs ne nomment pas un onglet
            ([{"tab": "x" * 61, "columns": [["title"]]}], "at most 60"),
        ],
    )
    def test_tab_rules_are_load_errors(self, layout, fragment):
        if fragment is None:
            parse_definition(invoice(layout))  # contrôle : un onglet seul est permis
            return
        with pytest.raises(InvalidDefinition) as e:
            parse_definition(invoice(layout))
        assert fragment in str(e.value)

    def test_at_most_max_tabs(self):
        names = [f"f{i}" for i in range(MAX_TABS + 1)]
        fields = [{"fieldname": n, "fieldtype": "Text"} for n in names]
        layout = [{"tab": f"Onglet {i}", "columns": [[n]]} for i, n in enumerate(names)]
        with pytest.raises(InvalidDefinition) as e:
            parse_definition({"schema_version": 1, "name": "Many", "module": "x", "fields": fields, "layout": layout})
        assert f"au plus {MAX_TABS} onglets" in str(e.value)
        parse_definition({"schema_version": 1, "name": "Many", "module": "x", "fields": fields, "layout": layout[:MAX_TABS]})  # pile la limite

    def test_the_rule_function_is_the_one_both_sides_use(self):
        assert layout_tab_problems([None, None]) == []
        assert layout_tab_problems(["A", "A", "B"]) == []
        assert layout_tab_problems(["A", "B", "A"]) == ["les sections de l'onglet 'A' doivent se suivre"]
        assert "section(s) 1" in layout_tab_problems(["A", None])[0]

    def test_completing_an_empty_layout_gives_one_untabbed_block(self):
        assert complete_layout([], ["a", "b"]) == [{"section": None, "collapsible": False, "collapsed": False, "columns": [["a", "b"]]}]


class TestOrganisationLayout:
    """L'habillage de formulaire d'une organisation : des onglets aussi, sous les MÊMES règles, et la même complétion."""

    @pytest.fixture
    def compiled(self):
        reg = DocTypeRegistry()
        reg.register(parse_definition(invoice([{"section": "Tout", "columns": [["title", "customer", "amount", "account", "note"]]}])))
        return reg.get("Invoice")

    def test_an_organisation_may_put_an_untabbed_form_into_tabs(self, compiled):
        got = validate_form_override(compiled, {"layout": [
            {"tab": "Vente", "section": "Client", "columns": [["title", "customer"]]},
            {"tab": "Compta", "columns": [["account"]]},
        ]})
        assert [s.get("tab") for s in got["layout"]] == ["Vente", "Compta"]
        meta = {"fields": [{"fieldname": f["fieldname"], "hidden": f.get("hidden", False)} for f in FIELDS], "layout": []}
        apply_form_override(meta, got)
        assert [(s.get("tab"), s["columns"]) for s in meta["layout"]] == [
            ("Vente", [["title", "customer"]]),
            ("Vente", [["amount", "note"]]),  # ce que l'habillage ne place pas ferme son PREMIER onglet
            ("Compta", [["account"]]),
        ]

    @pytest.mark.parametrize(
        ("layout", "fragment"),
        [
            ([{"tab": "A", "columns": [["title"]]}, {"columns": [["amount"]]}], "CHAQUE section"),
            ([{"tab": "A", "columns": [["title"]]}, {"tab": "B", "columns": [["amount"]]}, {"tab": "A", "columns": [["note"]]}], "doivent se suivre"),
            ([{"tab": 3, "columns": [["title"]]}], "nom d'onglet"),
            ([{"tab": "", "columns": [["title"]]}], "nom d'onglet"),
            ([{"tab": "  ", "columns": [["title"]]}], "nom d'onglet"),
            ([{"tab": "y" * 61, "columns": [["title"]]}], "nom d'onglet"),
        ],
    )
    def test_the_same_rules_refuse_a_bad_organisation_layout(self, compiled, layout, fragment):
        with pytest.raises(ValidationFailed) as e:
            validate_form_override(compiled, {"layout": layout})
        assert fragment in str(e.value)

    def test_an_organisation_may_place_a_table_too(self):
        """Placer est de l'affichage : une table peut aller dans l'onglet « Lignes » de l'organisation, une seule fois, comme tout champ."""
        reg = DocTypeRegistry()
        reg.register(parse_definition({"schema_version": 1, "name": "Line", "istable": True, "fields": [{"fieldname": "qty", "fieldtype": "Number"}]}))
        reg.register(parse_definition(invoice(fields=FIELDS + [{"fieldname": "items", "fieldtype": "Table", "options": "Line"}])))
        got = validate_form_override(reg.get("Invoice"), {"layout": [{"tab": "Tête", "columns": [["title"]]}, {"tab": "Lignes", "columns": [["items"]]}]})
        assert got["layout"][1]["columns"] == [["items"]]
        with pytest.raises(ValidationFailed, match="placé deux fois"):
            validate_form_override(reg.get("Invoice"), {"layout": [{"tab": "A", "columns": [["items"]]}, {"tab": "B", "columns": [["items"]]}]})

    def test_saving_the_definition_s_own_layout_stores_none_so_later_tabs_still_show(self):
        """Le Desk propose la disposition courante quand l'organisation n'en a pas : l'enregistrer telle quelle (pour changer un libellé) ne
        doit pas la FIGER, sinon les onglets que le plugin ajoute ensuite n'apparaîtraient jamais pour elle."""
        reg = DocTypeRegistry()
        reg.register(parse_definition(invoice([{"section": "Client", "columns": [["title"]]}])))
        current = reg.get("Invoice").definition.effective_layout()
        got = validate_form_override(reg.get("Invoice"), {"layout": current, "fields": {"title": {"label": "Objet"}}})
        assert "layout" not in got and got["fields"] == {"title": {"label": "Objet"}}
        later = DocTypeRegistry()
        later.register(parse_definition(invoice()))  # la version suivante du plugin range la fiche en onglets
        meta = {"fields": [{"fieldname": f["fieldname"], "hidden": f.get("hidden", False)} for f in FIELDS], "layout": later.get("Invoice").definition.effective_layout()}
        apply_form_override(meta, got)
        assert {s.get("tab") for s in meta["layout"]} == {"Détails", "Comptabilité"}

    def test_an_untabbed_organisation_layout_on_a_tabbed_form_drops_the_tabs(self):
        reg = DocTypeRegistry()
        reg.register(parse_definition(invoice()))
        got = validate_form_override(reg.get("Invoice"), {"layout": [{"section": "Tout", "columns": [["title"]]}]})
        meta = {"fields": [{"fieldname": f["fieldname"], "hidden": f.get("hidden", False)} for f in FIELDS], "layout": reg.get("Invoice").definition.effective_layout()}
        apply_form_override(meta, got)
        assert all("tab" not in s for s in meta["layout"])  # l'organisation a choisi une fiche sans onglets : elle gagne


class TestMeta:
    async def test_meta_sends_the_tabs_and_an_organisation_layout_with_its_own(self, db, tables):
        reg = DocTypeRegistry()
        reg.register(parse_definition(invoice()))
        svc = DocumentService(reg, db, tables, _Log())
        ctx = make_ctx("acme", "ada", permissions=["sales:invoice:read", "sales:invoice:write"])
        meta = svc.get_meta(ctx, "Invoice")
        assert [s.get("tab") for s in meta["layout"]] == ["Détails", "Détails", "Détails", "Comptabilité"]
        await svc.save_form_override(ctx, "Invoice", {"layout": [{"tab": "Un", "columns": [["title"]]}, {"tab": "Deux", "columns": [["account"]]}]})
        override = await svc.form_override(ctx, "Invoice")
        meta = svc.get_meta(ctx, "Invoice", override=override["config"])
        assert [(s.get("tab"), s["columns"]) for s in meta["layout"]] == [
            ("Un", [["title"]]), ("Un", [["customer", "amount", "note"]]), ("Deux", [["account"]]),
        ]


class TestExtensions:
    """D26 : la section qu'une extension ajoute à une fiche à onglets doit avoir un onglet, sinon la fusion échouerait."""

    @staticmethod
    def load(tmp_path, layout, label=None):
        reg = DocTypeRegistry()
        d = tmp_path / "sales"
        d.mkdir()
        (d / "invoice.yaml").write_text(yaml.safe_dump(invoice(layout), allow_unicode=True))
        reg.load_dir(d, owner="sales")
        e = tmp_path / "crm"
        e.mkdir()
        ext = {"schema_version": 1, "doctype": "Invoice", "fields": [{"fieldname": "crm_lead", "fieldtype": "Text"}]}
        if label is not None:
            ext["label"] = label
        (e / "invoice.yaml").write_text(yaml.safe_dump(ext, allow_unicode=True))
        reg.load_extensions(e, owner="crm")
        return reg.get("Invoice").definition.effective_layout()

    def test_an_extension_gets_its_own_tab(self, tmp_path):
        out = self.load(tmp_path, TABBED, label="Suivi commercial")
        assert (out[-1].get("tab"), out[-1]["section"], out[-1]["columns"]) == ("Suivi commercial", "Suivi commercial", [["crm_lead"]])

    def test_a_label_already_used_by_an_earlier_tab_joins_the_last_tab(self, tmp_path):
        out = self.load(tmp_path, TABBED, label="Détails")
        assert out[-1].get("tab") == "Comptabilité"

    def test_a_long_label_is_cut_to_a_tab_name(self, tmp_path):
        out = self.load(tmp_path, TABBED, label="L" * 100)
        assert out[-1].get("tab") == "L" * 60 and out[-1]["section"] == "L" * 100

    def test_a_form_that_already_has_the_most_tabs_takes_the_extension_in_its_last_tab(self, tmp_path):
        """Douze onglets déjà : un treizième ferait échouer la fusion, donc le chargement du plugin PROPRIÉTAIRE. La section rejoint le dernier."""
        fields = [{"fieldname": f"f{i}", "fieldtype": "Text"} for i in range(MAX_TABS)]
        layout = [{"tab": f"Onglet {i}", "columns": [[f"f{i}"]]} for i in range(MAX_TABS)]
        reg = DocTypeRegistry()
        d = tmp_path / "sales"
        d.mkdir()
        (d / "invoice.yaml").write_text(yaml.safe_dump({"schema_version": 1, "name": "Invoice", "module": "sales", "fields": fields, "layout": layout}, allow_unicode=True))
        reg.load_dir(d, owner="sales")
        e = tmp_path / "crm"
        e.mkdir()
        (e / "invoice.yaml").write_text(yaml.safe_dump({"schema_version": 1, "doctype": "Invoice", "label": "CRM", "fields": [{"fieldname": "crm_lead", "fieldtype": "Text"}]}))
        reg.load_extensions(e, owner="crm")
        out = reg.get("Invoice").definition.effective_layout()
        assert out[-1].get("tab") == f"Onglet {MAX_TABS - 1}" and out[-1]["columns"] == [["crm_lead"]]
        reg.unregister_owner("sales")
        reg.load_dir(d, owner="sales")  # le propriétaire se recharge : l'extension se réapplique, la fusion tient toujours
        assert reg.get("Invoice").definition.effective_layout()[-1]["columns"] == [["crm_lead"]]

    def test_an_untabbed_form_stays_untabbed(self, tmp_path):
        out = self.load(tmp_path, [{"section": "Client", "columns": [["title"]]}], label="CRM")
        assert all("tab" not in s for s in out)


class _Log:
    def info(self, *a, **k): ...

    def warning(self, *a, **k): ...

    def error(self, *a, **k): ...
