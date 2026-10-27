"""Ce que la revue adversariale de D26-D28 a trouvé (process/extensions.md) : chaque test reproduit un défaut confirmé, puis prouve sa correction."""

from __future__ import annotations

import pytest
import yaml

from tests.conftest import make_ctx
from xcore_doctype.document import DocumentService
from xcore_doctype.errors import InvalidDefinition, ValidationFailed
from xcore_doctype.registry import DocTypeChannel, DocTypeRegistry
from xcore_doctype.schema import parse_definition
from xcore_doctype.sealing import MASK, PREFIX, Sealer
from xcore_doctype.workspaces import parse_workspace

KEY = "k" * 32


class Log:
    def __init__(self):
        self.errors = []

    def info(self, *a, **k): ...

    def warning(self, *a, **k): ...

    def error(self, msg, **k):
        self.errors.append((msg, k))


def dt(name, module="shop", **extra):
    return {"schema_version": 1, "name": name, "module": module, "fields": [{"fieldname": "x", "fieldtype": "Text"}], **extra}


def write(directory, **files):
    directory.mkdir(parents=True, exist_ok=True)
    for name, doc in files.items():
        (directory / f"{name}.yaml").write_text(yaml.safe_dump(doc, allow_unicode=True))
    return directory


def shop(*, workspace_blocks=None):
    reg = DocTypeRegistry()
    for d in (dt("Order"), dt("Return")):
        reg.register(parse_definition(d), owner="shop")
    reg._workspaces["shop"] = parse_workspace({
        "schema_version": 1, "name": "shop", "label": "Boutique",
        "blocks": workspace_blocks or [{"type": "header", "text": "Ventes"}, {"type": "shortcut", "doctype": "Order"},
                                       {"type": "extension", "component": "shop.board", "props": {"stage": "won"}}],
    })
    reg._workspace_owners["shop"] = "shop"
    return reg


ORDER_RW = make_ctx("acme", "alice", permissions=["shop:order:read", "shop:order:write"])
RETURN_RW = make_ctx("acme", "bob", permissions=["shop:return:read", "shop:return:write"])
NOBODY = make_ctx("acme", "eve", permissions=[])


# ── Blocs d'extension (D28) ─────────────────────────────────────────────────────────────────────────────────────────

class TestExtensionBlocks:
    @pytest.mark.parametrize("key", ["innerHTML", "onClick", "textContent", "Stage", "_x", "a-b"])
    def test_props_are_settings_never_dom_attributes(self, key):
        with pytest.raises(InvalidDefinition, match="clés invalides"):
            parse_workspace({"schema_version": 1, "name": "shop", "label": "B",
                             "blocks": [{"type": "extension", "component": "shop.board", "props": {key: "x"}}]})

    def test_an_extension_block_alone_never_makes_a_workspace_visible(self, db, tables):
        svc = DocumentService(shop(workspace_blocks=[{"type": "header", "text": "Tarifs internes"},
                                                    {"type": "paragraph", "text": "Remise max 40 %"},
                                                    {"type": "extension", "component": "shop.board", "props": {"budget": "120000"}}]),
                              db, tables, Log())
        assert svc.list_workspaces(NOBODY) == []
        assert svc.list_workspaces(ORDER_RW) == []  # il lit Order, mais l'espace ne montre rien qu'il lise

    def test_beside_readable_content_it_is_served_with_its_section(self, db, tables):
        (ws,) = DocumentService(shop(), db, tables, Log()).list_workspaces(ORDER_RW)
        assert [b["type"] for b in ws["blocks"]] == ["header", "shortcut", "extension"]

    async def test_an_override_moves_removes_or_resizes_an_extension_block_but_never_adds_or_changes_one(self, db, tables):
        svc = DocumentService(shop(), db, tables, Log())
        moved = {"label": "Boutique", "blocks": [{"type": "extension", "component": "shop.board", "props": {"stage": "won"}, "cols": 12},
                            {"type": "shortcut", "doctype": "Order"}]}
        await svc.save_workspace_override(ORDER_RW, "shop", moved)
        await svc.save_workspace_override(ORDER_RW, "shop", {"label": "Boutique", "blocks": [{"type": "shortcut", "doctype": "Order"}]})  # retiré
        forged = {"type": "extension", "component": "shop.board", "props": {"stage": "<meta http-equiv=refresh>"}}
        for scope in ("org", "user"):
            for blocks in ([{"type": "shortcut", "doctype": "Return"}, forged],
                           [{"type": "shortcut", "doctype": "Return"}, {"type": "extension", "component": "sales.pipeline"}]):
                with pytest.raises(ValidationFailed) as e:
                    await svc.save_workspace_override(RETURN_RW, "shop", {"label": "Boutique", "blocks": blocks}, scope)
                assert e.value.code == "extension_block_not_offered"


# ── Un type déchargé depuis (le plugin d'une contribution est parti) ──────────────────────────────────────────────────

class TestUnloadedTypes:
    async def test_a_saved_layout_citing_a_departed_plugins_type_never_breaks_the_workspaces(self, db, tables, tmp_path):
        reg = shop(workspace_blocks=[{"type": "shortcut", "doctype": "Order"}])
        reg.load_dir(write(tmp_path / "crm", lead=dt("Lead", module="crm")), owner="crm")
        reg.load_workspaces(write(tmp_path / "ws", c={"schema_version": 1, "contributes_to": "shop",
                                                       "blocks": [{"type": "shortcut", "doctype": "Lead"}]}), owner="crm")
        svc = DocumentService(reg, db, tables, Log())
        # Alice ne voit pas Lead : `keep_hidden` le garde dans l'habillage qu'elle enregistre pour toute l'organisation.
        await svc.save_workspace_override(ORDER_RW, "shop", {"label": "Boutique", "blocks": [{"type": "shortcut", "doctype": "Order", "label": "Commandes"}]})
        reg.unregister_owner("crm")
        (ws,) = await svc.fetch_workspaces(ORDER_RW)  # avant : 404 doctype_not_found pour toute l'organisation
        assert [b.get("label") for b in ws["blocks"]] == ["Commandes"]
        await svc.save_workspace_override(ORDER_RW, "shop", {"label": "Boutique", "blocks": [{"type": "shortcut", "doctype": "Order"}]})  # encore enregistrable


# ── Secrets : une valeur chiffrée ne sort jamais, même sous un champ que la définition ne connaît plus ─────────────────

async def test_a_sealed_value_under_a_field_the_definition_no_longer_has_is_still_masked(db, tables):
    with_secret = dt("Account", fields=[{"fieldname": "x", "fieldtype": "Text"}, {"fieldname": "token", "fieldtype": "Password"}])
    reg = DocTypeRegistry()
    reg.register(parse_definition(with_secret), owner="shop")
    svc = DocumentService(reg, db, tables, Log(), sealer=Sealer(KEY))
    ctx = make_ctx("acme", "alice", permissions=["shop:account:read", "shop:account:write"])
    made = await svc.create(ctx, "Account", {"x": "a", "token": "s3cr3t"})
    reg.unregister_owner("shop")
    reg.register(parse_definition(dt("Account")), owner="shop")  # le champ Password a disparu de la définition
    got = await svc.get(ctx, "Account", made["name"])
    assert got["data"]["token"] == MASK and not str(got["data"]).count(PREFIX)
    listed = await svc.list(ctx, "Account")
    assert listed["items"][0]["data"]["token"] == MASK


# ── Règles des extensions de DocType (D26) ──────────────────────────────────────────────────────────────────────────

class TestExtensionRules:
    def reg(self, tmp_path, item=None):
        r = DocTypeRegistry()
        r.load_dir(write(tmp_path / "stock", item=item or {"schema_version": 1, "name": "Item", "module": "stock", "fields": [
            {"fieldname": "item_name", "fieldtype": "Text"}, {"fieldname": "qty", "fieldtype": "Int"}],
            "layout": [{"section": "Article", "columns": [["item_name"], ["qty"]]}]}), owner="stock")
        return r

    def test_a_hidden_field_joins_a_doctype_with_a_layout_without_a_section(self, tmp_path):
        r = self.reg(tmp_path)
        r.load_extensions(write(tmp_path / "x", a={"schema_version": 1, "doctype": "Item",
                                                   "fields": [{"fieldname": "sales_sync", "fieldtype": "Text", "hidden": True}]}), owner="sales")
        d = r.get("Item").definition
        assert "sales_sync" in d.field_map and [s.section for s in d.layout] == ["Article"]

    @pytest.mark.parametrize(
        "field, fragment",
        [
            ({"fieldname": "sales_ref", "fieldtype": "Text", "mandatory_depends_on": "qty > 0"}, "`mandatory_depends_on`"),
            ({"fieldname": "sales_ref", "fieldtype": "Text", "in_list_view": True}, "`in_list_view`"),
            ({"fieldname": "sales_token", "fieldtype": "Password"}, "Password"),
        ],
    )
    def test_what_would_change_the_owners_behaviour_is_refused(self, tmp_path, field, fragment):
        with pytest.raises(InvalidDefinition, match=fragment):
            self.reg(tmp_path).load_extensions(write(tmp_path / "x", a={"schema_version": 1, "doctype": "Item", "fields": [field]}), owner="sales")

    @pytest.mark.parametrize(
        "comeback, owner, fragment",
        [
            ({"schema_version": 1, "name": "Item", "istable": True, "fields": [{"fieldname": "item_name", "fieldtype": "Text"}]}, "stock", "DocType de lignes"),
            ({"schema_version": 1, "name": "Item", "module": "stock", "source": "remote", "fields": [{"fieldname": "item_name", "fieldtype": "Text"}]}, "stock", "virtuel"),
            ({"schema_version": 1, "name": "Item", "module": "sales", "fields": [{"fieldname": "item_name", "fieldtype": "Text"}]}, "sales", "appartient déjà"),
        ],
    )
    def test_a_target_that_comes_back_unextendable_fails_its_load_naming_the_extension(self, tmp_path, comeback, owner, fragment):
        r = self.reg(tmp_path)
        r.load_extensions(write(tmp_path / "x", a={"schema_version": 1, "doctype": "Item",
                                                   "fields": [{"fieldname": "sales_note", "fieldtype": "Text"}]}), owner="sales")
        r.unregister_owner("stock")
        with pytest.raises(InvalidDefinition, match=f"étendu par sales.*{fragment}"):
            r.load_dir(write(tmp_path / "back", item=comeback), owner=owner)
        assert "Item" not in r.names()


# ── Contributions d'espace (D27) ────────────────────────────────────────────────────────────────────────────────────

class TestContributionTargets:
    def test_a_module_that_only_has_settings_has_no_workspace_to_contribute_to(self, tmp_path):
        r = DocTypeRegistry()
        r.load_dir(write(tmp_path / "system", s=dt("System Settings", module="system", single=True)), owner="system")
        r.load_dir(write(tmp_path / "crm", lead=dt("Lead", module="crm")), owner="crm")
        with pytest.raises(InvalidDefinition, match="module inconnu 'system'"):
            r.load_workspaces(write(tmp_path / "ws", c={"schema_version": 1, "contributes_to": "system",
                                                         "blocks": [{"type": "shortcut", "doctype": "Lead"}]}), owner="crm")

    async def test_a_workspace_bigger_than_one_file_with_its_contributions_can_still_be_customised(self, db, tables, tmp_path):
        r = shop(workspace_blocks=[{"type": "shortcut", "doctype": "Order"}] * 80)
        r.load_dir(write(tmp_path / "crm", lead=dt("Lead", module="crm")), owner="crm")
        r.load_workspaces(write(tmp_path / "ws", c={"schema_version": 1, "contributes_to": "shop",
                                                     "blocks": [{"type": "shortcut", "doctype": "Lead"}] * 60}), owner="crm")
        svc = DocumentService(r, db, tables, Log())
        writer = make_ctx("acme", "alice", permissions=["shop:order:read", "shop:order:write", "crm:lead:read", "crm:lead:write"])
        body = {"label": "Boutique", "blocks": [{"type": "shortcut", "doctype": "Lead"}] * 60 + [{"type": "shortcut", "doctype": "Order"}] * 80}
        saved = await svc.save_workspace_override(writer, "shop", body)  # 140 blocs : au-delà d'un fichier (100), en deçà d'un espace composé
        assert len(saved["config"]["blocks"]) == 140

    def test_a_single_file_is_still_bounded(self, tmp_path):
        r = shop()
        with pytest.raises(InvalidDefinition, match="au plus 100 blocs"):
            r.load_workspaces(write(tmp_path / "ws", crm={"schema_version": 1, "name": "crm", "label": "CRM",
                                                           "blocks": [{"type": "header", "text": "x"}] * 101}), owner="crm")


# ── Le contrôle `unique` rétroactif des DocTypes de plugin a VRAIMENT lieu (il n'était jamais attendu) ───────────────

async def test_a_plugin_doctype_made_unique_over_existing_duplicates_is_withdrawn(db, tables, tmp_path):
    reg = DocTypeRegistry()
    log = Log()
    svc = DocumentService(reg, db, tables, log)
    channel = DocTypeChannel(reg, log, unique_check=svc.ensure_unique_clean)
    plain = dt("Badge", fields=[{"fieldname": "code", "fieldtype": "Text"}])
    channel.load_dir("shop", write(tmp_path / "v1", b=plain))
    ctx = make_ctx("acme", "alice", permissions=["shop:badge:read", "shop:badge:write"])
    await svc.create(ctx, "Badge", {"code": "A"})
    await svc.create(ctx, "Badge", {"code": "A"})
    channel.unregister_owner("shop")
    channel.load_dir("shop", write(tmp_path / "v2", b=dt("Badge", fields=[{"fieldname": "code", "fieldtype": "Text", "unique": True}])))
    await channel.settled()
    assert "Badge" not in reg.names()
    assert log.errors and log.errors[-1][0] == "doctypes withdrawn: retroactive unique check failed"
    assert "rétroactif" in log.errors[-1][1]["reason"]


async def test_the_operators_unique_check_reports_existing_duplicates(db, tables):
    """`doc.check_unique` (et le contrôle au chargement) relisaient `DOCUMENT_TABLE.c`, un NOM de table : ils n'avaient jamais tourné."""
    reg = DocTypeRegistry()
    reg.register(parse_definition(dt("Badge", fields=[{"fieldname": "code", "fieldtype": "Text"}])), owner="shop")
    svc = DocumentService(reg, db, tables, Log())
    ctx = make_ctx("acme", "alice", permissions=["shop:badge:read", "shop:badge:write"])
    a = await svc.create(ctx, "Badge", {"code": "A"})
    b = await svc.create(ctx, "Badge", {"code": "A"})
    await svc.create(ctx, "Badge", {"code": "B"})
    reg.unregister_owner("shop")
    reg.register(parse_definition(dt("Badge", fields=[{"fieldname": "code", "fieldtype": "Text", "unique": True}])), owner="shop")
    (conflict,) = await svc.check_unique()
    assert (conflict["doctype"], conflict["field"], conflict["value"], conflict["tenant"]) == ("Badge", "code", "A", "acme")
    assert conflict["names"] == sorted([a["name"], b["name"]])
    with pytest.raises(InvalidDefinition, match="rétroactif"):
        await svc.ensure_unique_clean(reg.get("Badge"))
