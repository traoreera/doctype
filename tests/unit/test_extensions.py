"""Extensions de DocType (D26, process/extensions.md) : un plugin ajoute des champs et des hooks au DocType d'un autre plugin."""

from __future__ import annotations

import pytest
import yaml

from tests.conftest import make_ctx
from xcore_doctype.document import DocumentService
from xcore_doctype.errors import DoctypeError, InvalidDefinition, ValidationFailed
from xcore_doctype.hooks import HookEvent, HookRegistry
from xcore_doctype.registry import DocTypeChannel, DocTypeRegistry

STOCK_GRANTS = ["stock:item:read", "stock:item:write", "stock:item:delete", "sales:customer:read", "sales:customer:write"]


def item(**extra):
    return {
        "schema_version": 1, "name": "Item", "module": "stock",
        "fields": [{"fieldname": "item_name", "fieldtype": "Text", "required": True}, {"fieldname": "qty", "fieldtype": "Int"}],
        **extra,
    }


def customer():
    return {"schema_version": 1, "name": "Customer", "module": "sales", "fields": [{"fieldname": "customer_name", "fieldtype": "Text"}]}


def ext(doctype="Item", **extra):
    return {"schema_version": 1, "doctype": doctype, **extra}


def write(directory, **files):
    directory.mkdir(parents=True, exist_ok=True)
    for name, doc in files.items():
        (directory / f"{name}.yaml").write_text(yaml.safe_dump(doc, allow_unicode=True))
    return directory


@pytest.fixture
def reg(tmp_path):
    r = DocTypeRegistry()
    r.load_dir(write(tmp_path / "stock", item=item()), owner="stock")
    r.load_dir(write(tmp_path / "sales", customer=customer()), owner="sales")
    return r


def fieldnames(reg, name="Item"):
    return [f.fieldname for f in reg.get(name).definition.fields]


class TestFields:
    def test_fields_are_appended_after_the_owners_and_reach_meta(self, reg, tmp_path):
        d = write(tmp_path / "x", item=ext(fields=[{"fieldname": "sales_customer", "fieldtype": "Link", "options": "Customer"}]))
        assert reg.load_extensions(d, owner="sales") == ["Item"]
        assert fieldnames(reg) == ["item_name", "qty", "sales_customer"]
        assert reg.extensions_of("Item") == [{"plugin": "sales", "label": None, "fields": ["sales_customer"], "hooks": []}]
        assert reg.owner_of("Item") == "stock"  # le DocType reste à son propriétaire (ses grants, son module)

    def test_a_layout_gets_one_more_section_named_after_the_extension(self, reg, tmp_path):
        reg.unregister_owner("stock")
        reg.load_dir(write(tmp_path / "s2", item=item(layout=[{"section": "Article", "columns": [["item_name"], ["qty"]]}])), owner="stock")
        reg.load_extensions(write(tmp_path / "x", item=ext(label="Ventes", fields=[{"fieldname": "sales_note", "fieldtype": "Text"}])), owner="sales")
        layout = reg.get("Item").definition.layout
        assert [s.section for s in layout] == ["Article", "Ventes"]
        assert layout[-1].columns == [["sales_note"]]

    @pytest.mark.parametrize(
        "field, why",
        [
            ({"fieldname": "note", "fieldtype": "Text"}, "commence par 'sales_'"),
            ({"fieldname": "stock_note", "fieldtype": "Text"}, "commence par 'sales_'"),
            ({"fieldname": "sales_note", "fieldtype": "Text", "required": True}, "`required`"),
            ({"fieldname": "sales_code", "fieldtype": "Text", "unique": True}, "`unique`"),
            ({"fieldname": "sales_code", "fieldtype": "Text", "set_only_once": True}, "`set_only_once`"),
        ],
    )
    def test_refused_fields(self, reg, tmp_path, field, why):
        with pytest.raises(InvalidDefinition, match=why):
            reg.load_extensions(write(tmp_path / "x", item=ext(fields=[field])), owner="sales")
        assert fieldnames(reg) == ["item_name", "qty"]

    def test_the_merge_is_checked_like_any_definition(self, reg, tmp_path):
        bad_condition = {"fieldname": "sales_note", "fieldtype": "Text", "depends_on": "nowhere == 1"}
        with pytest.raises(InvalidDefinition, match="Item"):
            reg.load_extensions(write(tmp_path / "x", item=ext(fields=[bad_condition])), owner="sales")
        with pytest.raises(InvalidDefinition, match="DocType cible inconnu 'Ghost'"):
            reg.load_extensions(write(tmp_path / "y", item=ext(fields=[{"fieldname": "sales_g", "fieldtype": "Link", "options": "Ghost"}])), owner="sales")
        assert fieldnames(reg) == ["item_name", "qty"]

    def test_a_condition_may_read_the_owners_fields(self, reg, tmp_path):
        f = {"fieldname": "sales_note", "fieldtype": "Text", "depends_on": "qty > 0"}
        reg.load_extensions(write(tmp_path / "x", item=ext(fields=[f])), owner="sales")
        assert "sales_note" in reg.get("Item").conditions


class TestTargets:
    @pytest.mark.parametrize(
        "target, owner, why",
        [
            ("Ghost", "sales", "inconnu"),
            ("Customer", "sales", "appartient déjà à ce plugin"),
        ],
    )
    def test_refused_targets(self, reg, tmp_path, target, owner, why):
        with pytest.raises(InvalidDefinition, match=why):
            reg.load_extensions(write(tmp_path / "x", a=ext(target, fields=[{"fieldname": f"{owner}_n", "fieldtype": "Text"}])), owner=owner)

    def test_a_child_doctype_is_not_extended(self, tmp_path):
        r = DocTypeRegistry()
        line = {"schema_version": 1, "name": "Line", "istable": True, "fields": [{"fieldname": "q", "fieldtype": "Int"}]}
        r.load_dir(write(tmp_path / "s", line=line), owner="stock")
        with pytest.raises(InvalidDefinition, match="DocType de lignes"):
            r.load_extensions(write(tmp_path / "x", a=ext("Line", fields=[{"fieldname": "sales_n", "fieldtype": "Text"}])), owner="sales")

    def test_one_extension_per_plugin_and_doctype(self, reg, tmp_path):
        d = write(tmp_path / "x", a=ext(fields=[{"fieldname": "sales_a", "fieldtype": "Text"}]), b=ext(fields=[{"fieldname": "sales_b", "fieldtype": "Text"}]))
        with pytest.raises(InvalidDefinition, match="déjà"):
            reg.load_extensions(d, owner="sales")
        assert fieldnames(reg) == ["item_name", "qty"]

    def test_an_empty_extension_is_refused(self, reg, tmp_path):
        with pytest.raises(InvalidDefinition, match="au moins un champ ou un hook"):
            reg.load_extensions(write(tmp_path / "x", a=ext()), owner="sales")

    def test_all_or_nothing(self, reg, tmp_path):
        reg.load_dir(write(tmp_path / "hr", e={"schema_version": 1, "name": "Employee", "module": "hr", "fields": [{"fieldname": "n", "fieldtype": "Text"}]}), owner="hr")
        d = write(tmp_path / "x", a=ext("Employee", fields=[{"fieldname": "sales_ok", "fieldtype": "Text"}]), b=ext(fields=[{"fieldname": "sales_bad", "fieldtype": "Link", "options": "Ghost"}]))
        with pytest.raises(InvalidDefinition):
            reg.load_extensions(d, owner="sales")
        assert fieldnames(reg, "Employee") == ["n"]
        assert reg.extensions_of("Employee") == []


class TestLifecycle:
    def test_two_plugins_extend_in_load_order_and_leave_independently(self, reg, tmp_path):
        reg.load_dir(write(tmp_path / "hr", e={"schema_version": 1, "name": "Employee", "module": "hr", "fields": [{"fieldname": "n", "fieldtype": "Text"}]}), owner="hr")
        reg.load_extensions(write(tmp_path / "a", a=ext(fields=[{"fieldname": "sales_a", "fieldtype": "Text"}])), owner="sales")
        reg.load_extensions(write(tmp_path / "b", a=ext(fields=[{"fieldname": "hr_b", "fieldtype": "Text"}])), owner="hr")
        assert fieldnames(reg) == ["item_name", "qty", "sales_a", "hr_b"]
        reg.unregister_owner("sales")
        assert fieldnames(reg) == ["item_name", "qty", "hr_b"]
        assert "Customer" not in reg.names()

    def test_extensions_come_back_when_the_owner_reloads(self, reg, tmp_path):
        reg.load_extensions(write(tmp_path / "x", a=ext(fields=[{"fieldname": "sales_a", "fieldtype": "Text"}])), owner="sales")
        reg.unregister_owner("stock")
        assert "Item" not in reg.names()
        reg.load_dir(write(tmp_path / "s2", item=item()), owner="stock")
        assert fieldnames(reg) == ["item_name", "qty", "sales_a"]

    def test_an_owner_that_now_collides_fails_to_load_naming_the_extension(self, reg, tmp_path):
        reg.load_extensions(write(tmp_path / "x", a=ext(fields=[{"fieldname": "sales_a", "fieldtype": "Text"}])), owner="sales")
        reg.unregister_owner("stock")
        clash = item(fields=[{"fieldname": "item_name", "fieldtype": "Text"}, {"fieldname": "sales_a", "fieldtype": "Int"}])
        with pytest.raises(InvalidDefinition, match="sales"):
            reg.load_dir(write(tmp_path / "s2", item=clash), owner="stock")
        assert "Item" not in reg.names()

    def test_the_channel_loads_and_logs(self, reg, tmp_path):
        lines = []

        class Log:
            def info(self, msg, **kw):
                lines.append((msg, kw))

        channel = DocTypeChannel(reg, Log())
        channel.load_extensions("sales", write(tmp_path / "x", a=ext(hooks={"before_save": ["sales.stamp"]})))
        assert lines[-1] == ("doctype extensions registered by plugin", {"plugin": "sales", "doctypes": ["Item"]})
        assert reg.get("Item").definition.hooks.before_save == ["sales.stamp"]
        channel.unregister_owner("sales")
        assert reg.get("Item").definition.hooks.before_save == []


# ── Le service de documents : les champs et les hooks ajoutés se comportent comme ceux d'origine ──────────────────────

class Log:
    def info(self, msg, **kw): ...

    def error(self, msg, **kw): ...

    def warning(self, msg, **kw): ...


@pytest.fixture
def env(db, tables, reg, tmp_path):
    hooks = HookRegistry()
    svc = DocumentService(reg, db, tables, Log(), hooks=hooks)
    return reg, hooks, svc, tmp_path


CTX = make_ctx("acme", "alice", permissions=STOCK_GRANTS)


async def test_an_added_field_is_stored_validated_and_linked(env):
    reg, _, svc, tmp = env
    reg.load_extensions(write(tmp / "x", a=ext(fields=[{"fieldname": "sales_customer", "fieldtype": "Link", "options": "Customer"}])), owner="sales")
    acme = await svc.create(CTX, "Customer", {"customer_name": "Acme"})
    made = await svc.create(CTX, "Item", {"item_name": "Vis", "sales_customer": acme["name"]})
    assert made["data"]["sales_customer"] == acme["name"]
    with pytest.raises(ValidationFailed):
        await svc.create(CTX, "Item", {"item_name": "Vis", "sales_customer": 12})  # type strict, comme un champ d'origine
    with pytest.raises(ValidationFailed) as e:
        await svc.create(CTX, "Item", {"item_name": "Vis", "sales_customer": "nope"})
    assert e.value.details[0]["type"] == "link_not_found"
    meta = svc.get_meta(CTX, "Item")
    assert meta["extended_by"] == [{"plugin": "sales", "label": "Sales", "fields": ["sales_customer"]}]
    assert "hooks" not in meta


async def test_an_added_hook_runs_after_the_owners_and_its_absence_refuses_the_save(env):
    reg, hooks, svc, tmp = env
    reg.unregister_owner("stock")
    reg.load_dir(write(tmp / "s2", item=item(hooks={"before_save": ["stock.first"]})), owner="stock")
    reg.load_extensions(write(tmp / "x", a=ext(fields=[{"fieldname": "sales_seen", "fieldtype": "Text"}], hooks={"before_save": ["sales.second"]})), owner="sales")
    calls = []

    async def first(event: HookEvent):
        calls.append("stock")

    async def second(event: HookEvent):
        calls.append("sales")
        event.data["sales_seen"] = "oui"

    hooks.register("stock.first", first, owner="stock")
    with pytest.raises(DoctypeError) as e:
        await svc.create(CTX, "Item", {"item_name": "Vis"})
    assert e.value.code == "hook_unavailable"  # jamais une règle d'extension silencieusement sautée
    hooks.register("sales.second", second, owner="sales")
    made = await svc.create(CTX, "Item", {"item_name": "Vis"})
    assert calls == ["stock", "sales"]
    assert made["data"]["sales_seen"] == "oui"
