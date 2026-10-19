"""Espaces de travail (palier 70 %, phase G) : déclarés par les plugins, FILTRÉS par les droits de celui qui regarde."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tests.conftest import make_ctx
from xcore_doctype.document import DocumentService
from xcore_doctype.errors import InvalidDefinition
from xcore_doctype.registry import DocTypeChannel, DocTypeRegistry
from xcore_doctype.schema import parse_definition
from xcore_doctype.workspaces import module_label, parse_workspace

ROOT = Path(__file__).resolve().parents[2]


class Log:
    def info(self, *a, **k): ...
    def error(self, *a, **k): ...


def dt(name, module="shop", **extra):
    return {"schema_version": 1, "name": name, "module": module, "fields": [{"fieldname": "x", "fieldtype": "Text"}], **extra}


def ws(**over):
    base = {
        "schema_version": 1,
        "name": "shop",
        "label": "Boutique",
        "icon": "shop",
        "blocks": [
            {"type": "header", "text": "Ventes"},
            {"type": "shortcut", "doctype": "Order", "new": True},
            {"type": "shortcut", "doctype": "Return", "label": "Retours"},
            {"type": "header", "text": "Catalogue"},
            {"type": "shortcut", "doctype": "Product", "new": True},
        ],
    }
    return {**base, **over}


def registry(*, with_workspace=True, extra=()):
    reg = DocTypeRegistry()
    for d in (dt("Order", icon="receipt"), dt("Return"), dt("Product"), *extra):
        reg.register(parse_definition(d))
    if with_workspace:
        reg._workspaces["shop"] = parse_workspace(ws())  # chargé par load_workspaces dans les tests de chargement
    return reg


def service(reg, db, tables):
    return DocumentService(reg, db, tables, Log())


def spaces(svc, perms):
    return svc.list_workspaces(make_ctx("acme", "alice", permissions=perms))


ALL = [f"shop:{t}:{a}" for t in ("order", "return", "product") for a in ("read", "write")]


class TestDefinition:
    def test_a_valid_workspace_parses(self):
        d = parse_workspace(ws())
        assert (d.name, d.label, d.order, len(d.blocks)) == ("shop", "Boutique", 100, 5) and d.doctypes() == ["Order", "Return", "Product"]

    @pytest.mark.parametrize(
        "patch, fragment",
        [
            ({"blocks": []}, "blocks"),
            ({"blocks": [{"type": "chart", "doctype": "x"}]}, "blocks"),  # une liste fermée de blocs
            ({"blocks": [{"type": "shortcut", "doctype": "Order", "script": "x"}]}, "script"),  # rien d'exécutable, rien d'inconnu
            ({"blocks": [{"type": "header", "text": ""}]}, "text"),
            ({"icon": "pi pi-x"}, "icon"),  # pas de classe CSS libre
            ({"icon": "Shop"}, "icon"),
            ({"name": "Shop"}, "name"),
            ({"order": -1}, "order"),
            ({"unknown": 1}, "unknown"),
            ({"schema_version": 2}, "schema_version"),
        ],
    )
    def test_an_invalid_workspace_is_refused_with_its_path(self, patch, fragment):
        with pytest.raises(InvalidDefinition, match=fragment):
            parse_workspace(ws(**patch))

    def test_it_must_be_an_object(self):
        with pytest.raises(InvalidDefinition, match="objet"):
            parse_workspace(["x"])

    def test_module_labels(self):
        assert [module_label(m) for m in ("sales", "stock_control", "", None)] == ["Sales", "Stock control", "Autres", "Autres"]

    def test_a_doctype_icon_is_a_bare_primeicons_name(self):
        assert parse_definition(dt("A", icon="receipt")).icon == "receipt"
        with pytest.raises(InvalidDefinition, match="icon"):
            parse_definition(dt("A", icon="pi pi-receipt"))
        with pytest.raises(InvalidDefinition, match="icon"):
            parse_definition({"schema_version": 1, "name": "A", "istable": True, "icon": "box", "fields": [{"fieldname": "x", "fieldtype": "Text"}]})


class TestLoading:
    def write(self, tmp_path, **files):
        d = tmp_path / "workspaces"
        d.mkdir(parents=True)
        for name, doc in files.items():
            (d / f"{name}.yaml").write_text(yaml.safe_dump(doc))
        return d

    def test_a_plugin_loads_its_workspace_for_its_own_module(self, tmp_path):
        reg = registry(with_workspace=False)
        assert reg.load_workspaces(self.write(tmp_path, shop=ws()), owner="shop") == ["shop"]
        assert [w.label for w in reg.workspaces()] == ["Boutique"]

    def test_a_workspace_name_is_taken_once(self, tmp_path):
        reg = registry(with_workspace=False)
        reg.load_workspaces(self.write(tmp_path / "a", shop=ws()), owner="shop")
        with pytest.raises(InvalidDefinition, match="déjà défini"):
            reg.load_workspaces(self.write(tmp_path / "b", shop=ws()), owner="shop")

    def test_the_name_must_be_the_plugin(self, tmp_path):
        reg = registry(with_workspace=False)
        with pytest.raises(InvalidDefinition, match="doit être 'other'"):
            reg.load_workspaces(self.write(tmp_path, shop=ws()), owner="other")

    def test_a_shortcut_to_an_unknown_doctype_is_a_load_error(self, tmp_path):
        reg = registry(with_workspace=False)
        bad = ws(blocks=[{"type": "shortcut", "doctype": "Ghost"}])
        with pytest.raises(InvalidDefinition, match="Ghost"):
            reg.load_workspaces(self.write(tmp_path, shop=bad), owner="shop")
        assert reg.workspaces() == []  # tout ou rien

    def test_a_shortcut_to_a_child_doctype_is_refused(self, tmp_path):
        reg = registry(with_workspace=False)
        reg.register(parse_definition({"schema_version": 1, "name": "Row", "istable": True, "fields": [{"fieldname": "x", "fieldtype": "Text"}]}))
        with pytest.raises(InvalidDefinition, match="enfant"):
            reg.load_workspaces(self.write(tmp_path, shop=ws(blocks=[{"type": "shortcut", "doctype": "Row"}])), owner="shop")

    def test_a_missing_folder_and_a_broken_file_are_load_errors(self, tmp_path):
        reg = registry(with_workspace=False)
        with pytest.raises(InvalidDefinition, match="introuvable"):
            reg.load_workspaces(tmp_path / "nope", owner="shop")
        d = tmp_path / "w"
        d.mkdir()
        (d / "a.yaml").write_text("a: [unclosed")
        with pytest.raises(InvalidDefinition, match="illisible"):
            reg.load_workspaces(d, owner="shop")

    def test_the_channel_loads_and_unregistering_the_owner_removes_them(self, tmp_path):
        reg = registry(with_workspace=False)
        channel = DocTypeChannel(reg, Log())
        channel.load_workspaces("shop", self.write(tmp_path, shop=ws()))
        assert len(reg.workspaces()) == 1
        channel.unregister_owner("shop")
        assert reg.workspaces() == []

    def test_the_shipped_plugins_workspaces_are_valid(self):
        reg = DocTypeRegistry()
        for plugin in ("stock", "sales"):
            reg.load_dir(ROOT / "app" / plugin / "doctypes", owner=plugin)
        reg.validate_links()
        for plugin in ("stock", "sales"):
            assert reg.load_workspaces(ROOT / "app" / plugin / "workspaces", owner=plugin) == [plugin]
        assert [w.name for w in reg.workspaces()] == ["sales", "stock"]  # `order` : Ventes (10) avant Stock (20)


class TestFiltering:
    def test_a_user_sees_only_what_they_can_read_and_create(self, db, tables):
        svc = service(registry(), db, tables)
        (space,) = spaces(svc, ["shop:order:read", "shop:order:write", "shop:product:read"])
        assert (space["name"], space["label"], space["icon"]) == ("shop", "Boutique", "shop")
        assert [(b["type"], b.get("doctype") or b.get("text"), b.get("create")) for b in space["blocks"]] == [
            ("header", "Ventes", None),
            ("shortcut", "Order", True),  # lecture ET création
            ("header", "Catalogue", None),
            ("shortcut", "Product", False),  # lisible, mais « new » ne s'offre pas sans droit de créer
        ]

    def test_a_section_without_a_visible_shortcut_disappears(self, db, tables):
        svc = service(registry(), db, tables)
        (space,) = spaces(svc, ["shop:product:read"])
        assert [b.get("doctype") or b["text"] for b in space["blocks"]] == ["Catalogue", "Product"]  # « Ventes » n'a plus rien à montrer

    def test_a_user_with_no_right_sees_no_workspace(self, db, tables):
        assert spaces(service(registry(), db, tables), []) == []

    def test_a_label_overrides_the_doctype_label_and_the_icon_comes_from_the_doctype(self, db, tables):
        svc = service(registry(), db, tables)
        (space,) = spaces(svc, ALL)
        blocks = {b["doctype"]: b for b in space["blocks"] if b["type"] == "shortcut"}
        assert blocks["Return"]["label"] == "Retours" and blocks["Order"]["label"] == "Order"
        assert blocks["Order"]["icon"] == "receipt" and blocks["Return"]["icon"] is None

    def test_a_module_without_a_workspace_gets_a_made_one(self, db, tables):
        reg = registry(with_workspace=False, extra=(dt("Sku", module="warehouse_ops"), dt("Bay", module="warehouse_ops")))
        svc = service(reg, db, tables)
        out = spaces(svc, ALL + ["warehouse_ops:sku:read", "warehouse_ops:bay:read", "warehouse_ops:bay:write"])
        by_name = {s["name"]: s for s in out}
        made = by_name["warehouse_ops"]
        assert made["label"] == "Warehouse ops" and made["icon"] is None
        shortcuts = [b for b in made["blocks"] if b["type"] == "shortcut"]
        assert [(b["doctype"], b["create"]) for b in shortcuts] == [("Bay", True), ("Sku", False)]  # alphabétique ; « + » selon les droits

    def test_a_setting_is_never_a_shortcut_of_an_application(self, db, tables):
        # Tous les réglages vivent dans la page Paramètres du Desk : ni un espace généré, ni un raccourci écrit à la main ne les montre.
        conf = dt("Shop Settings", single=True)
        reg = registry(with_workspace=False, extra=(conf, dt("Sku", module="warehouse_ops"), dt("Warehouse Settings", module="warehouse_ops", single=True), dt("Only Settings", module="solo", single=True)))
        reg._workspaces["shop"] = parse_workspace(ws(blocks=[{"type": "shortcut", "doctype": "Order"}, {"type": "shortcut", "doctype": "Shop Settings"}]))
        perms = ALL + ["shop:shop_settings:read", "warehouse_ops:sku:read", "warehouse_ops:warehouse_settings:read", "solo:only_settings:read"]
        by_name = {s["name"]: s for s in spaces(service(reg, db, tables), perms)}
        assert [b["doctype"] for b in by_name["shop"]["blocks"] if b["type"] == "shortcut"] == ["Order"]
        made = by_name["warehouse_ops"]
        assert [b["doctype"] for b in made["blocks"] if b["type"] in ("shortcut", "number_card")] == ["Sku", "Sku"]
        assert "solo" not in by_name  # un module qui n'a que des réglages n'a pas d'espace : il est dans Paramètres

    def test_a_made_workspace_opens_on_a_dashboard_not_on_a_list_of_links(self, db, tables):
        reg = registry(with_workspace=False, extra=(dt("Sku", module="warehouse_ops"), dt("Bay", module="warehouse_ops")))
        made = {s["name"]: s for s in spaces(service(reg, db, tables), ALL + ["warehouse_ops:sku:read", "warehouse_ops:bay:read"])}["warehouse_ops"]
        kinds = [b["type"] for b in made["blocks"]]
        assert kinds[:4] == ["header", "number_card", "number_card", "quick_list"]  # l'aperçu d'abord : un chiffre par type, puis les derniers documents
        assert kinds[4:] == ["header", "shortcut", "shortcut"]
        cards = [b for b in made["blocks"] if b["type"] == "number_card"]
        assert [(c["doctype"], c["function"]) for c in cards] == [("Bay", "count"), ("Sku", "count")]
        assert made["blocks"][3]["doctype"] == "Bay"  # les derniers documents du premier type

    def test_a_made_dashboard_only_shows_what_the_caller_may_read(self, db, tables):
        reg = registry(with_workspace=False, extra=(dt("Sku", module="warehouse_ops"), dt("Bay", module="warehouse_ops")))
        made = {s["name"]: s for s in spaces(service(reg, db, tables), ["warehouse_ops:sku:read"])}["warehouse_ops"]
        assert {b["doctype"] for b in made["blocks"] if "doctype" in b} == {"Sku"}

    def test_a_declared_workspace_is_not_duplicated_by_a_made_one(self, db, tables):
        svc = service(registry(), db, tables)
        assert [s["name"] for s in spaces(svc, ALL)] == ["shop"]

    def test_the_order_then_the_label_set_the_sequence(self, db, tables):
        reg = registry(extra=(dt("Zed", module="zz"), dt("Abe", module="aa")))
        reg._workspaces["aa"] = parse_workspace(ws(name="aa", label="Alpha", order=50, blocks=[{"type": "shortcut", "doctype": "Abe"}]))
        reg._workspaces["zz"] = parse_workspace(ws(name="zz", label="Zulu", order=5, blocks=[{"type": "shortcut", "doctype": "Zed"}]))
        out = spaces(service(reg, db, tables), ALL + ["aa:abe:read", "zz:zed:read"])
        assert [s["label"] for s in out] == ["Zulu", "Alpha", "Boutique"]  # 5, 50, 100

    def test_children_never_appear(self, db, tables):
        reg = registry(with_workspace=False)
        reg.register(parse_definition({"schema_version": 1, "name": "Row", "istable": True, "fields": [{"fieldname": "x", "fieldtype": "Text"}]}))
        names = [b["doctype"] for s in spaces(service(reg, db, tables), ALL) for b in s["blocks"] if "doctype" in b]
        assert "Row" not in names

    def test_the_summaries_carry_the_icon(self, db, tables):
        svc = service(registry(), db, tables)
        got = {m["name"]: m["icon"] for m in svc.list_meta(make_ctx("acme", "a", permissions=ALL))}
        assert got["Order"] == "receipt" and got["Product"] is None
