"""Le registre accepte les DocTypes d'un plugin propriétaire (`module == plugin`), tout ou rien, et les rend à son départ."""

from __future__ import annotations

import pytest
import yaml

from tests.conftest import make_ctx
from xcore_doctype.document import DocumentService
from xcore_doctype.errors import InvalidDefinition
from xcore_doctype.grants import GrantCatalog
from xcore_doctype.registry import DocTypeChannel, DocTypeRegistry
from xcore_doctype.schema import parse_definition


def dt(name, module="sales", **extra):
    return {"schema_version": 1, "name": name, "module": module, "fields": [{"fieldname": "x", "fieldtype": "Text"}], **extra}


def write(directory, **files):
    directory.mkdir(parents=True, exist_ok=True)
    for name, doc in files.items():
        (directory / f"{name}.yaml").write_text(yaml.safe_dump(doc))
    return directory


class Log:
    def __init__(self):
        self.lines = []

    def info(self, msg, **kw):
        self.lines.append((msg, kw))


@pytest.fixture
def reg():
    return DocTypeRegistry()


class TestOwnership:
    def test_the_module_must_be_the_plugins_name(self, reg):
        reg.register(parse_definition(dt("Order")), owner="sales")
        assert reg.owner_of("Order") == "sales"
        with pytest.raises(InvalidDefinition, match=r"`module` doit être 'sales'.*'stock'"):
            reg.register(parse_definition(dt("Bin", module="stock")), owner="sales")
        with pytest.raises(InvalidDefinition, match="None"):
            reg.register(parse_definition({"schema_version": 1, "name": "Free", "fields": [{"fieldname": "x", "fieldtype": "Text"}]}), owner="sales")
        assert reg.names() == ["Order"]

    def test_the_operators_own_doctypes_have_no_owner_and_any_module(self, reg):
        reg.register(parse_definition(dt("Mine", module="whatever")))
        reg.register(parse_definition({"schema_version": 1, "name": "NoModule", "fields": [{"fieldname": "x", "fieldtype": "Text"}]}))
        assert reg.owner_of("Mine") is None

    def test_a_child_doctype_belongs_to_the_plugin_that_brings_it(self, reg, tmp_path):
        d = write(tmp_path / "d", line={"schema_version": 1, "name": "Line", "istable": True, "fields": [{"fieldname": "q", "fieldtype": "Number"}]},
                  parent=dt("Doc", fields=[{"fieldname": "lines", "fieldtype": "Table", "options": "Line"}]))
        assert sorted(reg.load_dir(d, owner="sales")) == ["Doc", "Line"]
        assert (reg.owner_of("Line"), reg.owner_of("Doc")) == ("sales", "sales")

    @pytest.mark.parametrize("owner", ["my-sales", "Sales", "sales.core", "", "a b"])
    def test_a_plugin_whose_name_cannot_be_a_grant_namespace_owns_nothing(self, reg, tmp_path, owner):
        with pytest.raises(InvalidDefinition, match="ne peut pas servir de `module`"):
            reg.load_dir(write(tmp_path / "d", a=dt("A")), owner=owner)
        assert reg.names() == []

    def test_a_name_already_taken_is_refused_whoever_owns_it(self, reg, tmp_path):
        reg.register(parse_definition(dt("Order")))  # celui de l'opérateur
        with pytest.raises(InvalidDefinition, match="déjà défini"):
            reg.load_dir(write(tmp_path / "d", a=dt("Order")), owner="sales")
        reg.load_dir(write(tmp_path / "e", a=dt("Mine")), owner="sales")
        with pytest.raises(InvalidDefinition, match="déjà défini"):
            reg.load_dir(write(tmp_path / "f", a=dt("Mine", module="other")), owner="other")
        assert reg.owner_of("Mine") == "sales"


class TestAllOrNothing:
    def test_a_bad_file_leaves_none_of_its_neighbours(self, reg, tmp_path):
        reg.register(parse_definition(dt("Existing", module="ops")))
        d = write(tmp_path / "d", a_ok=dt("Good"), b_bad=dt("Bad", module="stock"))
        with pytest.raises(InvalidDefinition):
            reg.load_dir(d, owner="sales")
        assert reg.names() == ["Existing"]
        assert reg.owner_of("Good") is None

    def test_an_unreadable_file_registers_nothing(self, reg, tmp_path):
        d = write(tmp_path / "d", a_ok=dt("Good"))
        (d / "z.yaml").write_text("fields: [unclosed")
        with pytest.raises(InvalidDefinition, match="illisible"):
            reg.load_dir(d, owner="sales")
        assert reg.names() == []

    def test_a_missing_directory_is_refused(self, reg, tmp_path):
        with pytest.raises(InvalidDefinition, match="introuvable"):
            reg.load_dir(tmp_path / "nope", owner="sales")


class TestSubdirectories:
    """`load_dir` descend dans les sous-dossiers : un jeu de DocTypes se range par module pour la lecture."""

    def test_a_doctype_in_a_subdirectory_is_loaded(self, reg, tmp_path):
        d = write(tmp_path / "d", top=dt("Top"))
        write(d / "accounts", account=dt("Account"))
        write(d / "stock" / "deep", item=dt("Item"))
        assert sorted(reg.load_dir(d, owner="sales")) == ["Account", "Item", "Top"]

    def test_a_parent_cites_a_child_of_another_subdirectory(self, reg, tmp_path):
        """Le tri « enfants d'abord » porte sur TOUT l'arbre : l'ordre des sous-dossiers n'a donc pas à être trouvé.

        C'est ce qui permet à deux modules de se citer mutuellement (une facture de vente dont la ligne vit dans
        le module des achats, et l'inverse) : aucun ordre de dossiers ne pourrait satisfaire un tel cycle.
        """
        d = write(tmp_path / "d")
        # Le parent est dans le dossier qui passe EN PREMIER par ordre alphabétique, l'enfant dans le dernier.
        write(d / "a_selling", invoice=dt("Invoice", fields=[{"fieldname": "items", "fieldtype": "Table", "options": "Invoice Line"}]))
        write(d / "z_lines", line=dt("Invoice Line", module=None, istable=True))
        assert sorted(reg.load_dir(d, owner="sales")) == ["Invoice", "Invoice Line"]

    def test_a_bad_file_in_a_subdirectory_leaves_none_of_the_tree(self, reg, tmp_path):
        d = write(tmp_path / "d", top=dt("Top"))
        write(d / "broken", bad=dt("Bad", module="stock"))
        with pytest.raises(InvalidDefinition):
            reg.load_dir(d, owner="sales")
        assert reg.names() == []


class TestUnregister:
    def test_a_plugin_takes_back_only_its_own(self, reg, tmp_path):
        reg.register(parse_definition(dt("Ops", module="ops")))
        reg.load_dir(write(tmp_path / "s", a=dt("A"), b=dt("B")), owner="sales")
        reg.load_dir(write(tmp_path / "t", c=dt("C", module="stock")), owner="stock")
        assert reg.unregister_owner("sales") == ["A", "B"]
        assert reg.names() == ["C", "Ops"]
        assert reg.unregister_owner("sales") == []  # idempotent
        assert reg.unregister_owner("unknown") == []

    def test_clear_forgets_the_owners_too(self, reg, tmp_path):
        reg.load_dir(write(tmp_path / "s", a=dt("A")), owner="sales")
        reg.clear()
        assert reg.owner_of("A") is None and reg.names() == []


class TestChannel:
    def test_it_loads_a_plugins_folder_and_says_so(self, reg, tmp_path):
        log = Log()
        channel = DocTypeChannel(reg, log)
        assert channel.load_dir("sales", write(tmp_path / "d", a=dt("A"))) == ["A"]
        assert log.lines == [("doctypes registered by plugin", {"plugin": "sales", "doctypes": ["A"]})]

    def test_an_unknown_link_target_takes_the_whole_folder_back(self, reg, tmp_path):
        link = dt("Order", fields=[{"fieldname": "client", "fieldtype": "Link", "options": "Client"}])
        channel = DocTypeChannel(reg)
        with pytest.raises(InvalidDefinition, match="DocType cible inconnu 'Client'"):
            channel.load_dir("sales", write(tmp_path / "d", order=link, other=dt("Fine")))
        assert reg.names() == []

    def test_a_link_to_another_plugins_doctype_works_once_that_plugin_is_loaded(self, reg, tmp_path):
        channel = DocTypeChannel(reg)
        channel.load_dir("stock", write(tmp_path / "s", item=dt("Item", module="stock")))
        order = dt("Order", fields=[{"fieldname": "item", "fieldtype": "Link", "options": "Item"}])
        assert channel.load_dir("sales", write(tmp_path / "d", order=order)) == ["Order"]

    def test_another_plugins_dangling_link_is_not_blamed_on_the_newcomer(self, reg, tmp_path):
        channel = DocTypeChannel(reg)
        channel.load_dir("stock", write(tmp_path / "s", item=dt("Item", module="stock")))
        orders = dt("Order", fields=[{"fieldname": "item", "fieldtype": "Link", "options": "Item"}])
        channel.load_dir("sales", write(tmp_path / "d", order=orders))
        channel.unregister_owner("stock")  # `Order.item` pointe maintenant dans le vide
        assert channel.load_dir("billing", write(tmp_path / "b", bill=dt("Bill", module="billing"))) == ["Bill"]

    def test_the_channel_exposes_no_way_to_read_or_edit_the_registry(self, reg):
        public = {n for n in dir(DocTypeChannel(reg)) if not n.startswith("_")}
        # `load_print_templates` : des gabarits d'impression livrés par un plugin (process/impression.md §2). Le canal
        # ne sait toujours que DÉPOSER et RETIRER — jamais lire ni modifier le registre.
        assert public == {"load_dir", "load_workspaces", "load_print_templates", "load_extensions", "load_desk",
                          "unregister_owner", "settled"}


class TestLiveRegistry:
    """Le service est construit au `on_load` d'xcore-doctype, les plugins arrivent après : ils voient le même registre."""

    async def test_doctypes_added_after_the_service_exists_are_served_and_their_grants_listed(self, db, tables, tmp_path):
        reg = DocTypeRegistry()
        svc = DocumentService(reg, db, tables, Log())
        catalog = GrantCatalog(reg)
        assert svc.list_meta(make_ctx(permissions=[])) == [] and catalog.cited() == []

        DocTypeChannel(reg).load_dir("sales", write(tmp_path / "d", order=dt("Order")))
        ctx = make_ctx(permissions=["sales:order:read", "sales:order:write", "sales:order:delete"])
        assert [m["name"] for m in svc.list_meta(ctx)] == ["Order"]
        assert "sales:order:read" in catalog.cited()
        assert [g["name"] for g in catalog.for_plugin("sales")] == ["sales:order:delete", "sales:order:read", "sales:order:write"]

        doc = await svc.create(ctx, "Order", {"x": "kept"})
        reg.unregister_owner("sales")
        assert svc.list_meta(ctx) == []  # plus de DocType...
        DocTypeChannel(reg).load_dir("sales", tmp_path / "d")
        assert (await svc.get(ctx, "Order", doc["name"]))["data"]["x"] == "kept"  # ...mais le document était resté en base
