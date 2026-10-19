"""Catalogue des grants : la source unique que le plugin propriétaire transmet à `rbac.declare` (doc/phase1.md §7)."""

from __future__ import annotations

import pytest

from xcore_doctype.grants import GrantCatalog
from xcore_doctype.registry import DocTypeRegistry
from xcore_doctype.schema import parse_definition


def doctype(name, module=None, label=None, permissions=None):
    raw = {"schema_version": 1, "name": name, "fields": [{"fieldname": "a", "fieldtype": "Text"}]}
    raw.update({k: v for k, v in (("module", module), ("label", label), ("permissions", permissions)) if v is not None})
    return parse_definition(raw)


def catalog(*defs):
    reg = DocTypeRegistry()
    for d in defs:
        reg.register(d)
    return GrantCatalog(reg)


def names(declarations):
    return [d["name"] for d in declarations]


class TestForPlugin:
    def test_returns_the_conventional_grants_of_the_plugins_doctypes_in_the_format_of_rbac_declare(self):
        c = catalog(doctype("Customer", module="xsales", label="Client"))
        decl = c.for_plugin("xsales")
        assert decl == [
            {"name": "xsales:customer:delete", "description": "Supprimer Client", "group": "xsales-customer", "tenant_grantable": True},
            {"name": "xsales:customer:read", "description": "Consulter Client", "group": "xsales-customer", "tenant_grantable": True},
            {"name": "xsales:customer:write", "description": "Créer/modifier Client", "group": "xsales-customer", "tenant_grantable": True},
        ]

    def test_only_the_namespace_of_the_asking_plugin(self):
        c = catalog(doctype("Customer", module="xsales"), doctype("Item", module="xstock"))
        assert {n.split(":")[0] for n in names(c.for_plugin("xsales"))} == {"xsales"}
        assert {n.split(":")[0] for n in names(c.for_plugin("xstock"))} == {"xstock"}
        assert c.for_plugin("unknown") == []

    def test_grants_of_a_foreign_namespace_are_cited_but_not_declarable_by_the_owner(self):
        """Un DocType de `xsales` qui cite `xstock:item:read` : ce grant appartient au plugin xstock."""
        c = catalog(doctype("Order", module="xsales", permissions={"read": ["xsales:order:read", "xstock:item:read"]}))
        assert "xstock:item:read" in c.cited()
        assert "xstock:item:read" not in names(c.for_plugin("xsales"))
        assert names(c.for_plugin("xstock")) == ["xstock:item:read"]  # c'est xstock qui le déclarerait

    def test_a_doctype_without_module_declares_nothing_by_convention(self):
        c = catalog(doctype("Free"))
        assert c.cited() == [] and c.for_plugin("xsales") == []

    def test_explicit_grants_are_included(self):
        c = catalog(doctype("Customer", module="xsales", permissions={
            "read": ["xsales:customer:read", {"grant": "xsales:customer:own", "if_owner": True, "description": "Ses clients"}],
            "delete": [{"grant": "xsales:customer:delete", "tenant_grantable": False}],
        }))
        by_name = {d["name"]: d for d in c.for_plugin("xsales")}
        assert by_name["xsales:customer:own"]["description"] == "Ses clients"
        assert by_name["xsales:customer:delete"]["tenant_grantable"] is False
        assert by_name["xsales:customer:write"]["tenant_grantable"] is True  # convention conservée pour write

    def test_output_is_sorted_and_stable(self):
        a = catalog(doctype("B", module="m"), doctype("A", module="m")).for_plugin("m")
        b = catalog(doctype("A", module="m"), doctype("B", module="m")).for_plugin("m")
        assert a == b and names(a) == sorted(names(a))


class TestSharedGrants:
    def test_a_grant_cited_by_two_doctypes_is_listed_once(self):
        shared = {"read": ["xsales:shared:read"]}
        c = catalog(doctype("A", module="xsales", permissions=shared), doctype("B", module="xsales", permissions=shared))
        assert names(c.for_plugin("xsales")).count("xsales:shared:read") == 1

    def test_the_most_restrictive_delegation_wins(self):
        open_ = doctype("A", module="xsales", permissions={"read": ["xsales:shared:read"]})
        closed = doctype("B", module="xsales", permissions={"read": [{"grant": "xsales:shared:read", "tenant_grantable": False}]})
        for order in ((open_, closed), (closed, open_)):
            spec = {d["name"]: d for d in catalog(*order).for_plugin("xsales")}["xsales:shared:read"]
            assert spec["tenant_grantable"] is False

    def test_the_first_doctype_alphabetically_provides_the_description(self):
        c = catalog(doctype("Zeta", module="m", label="Z", permissions={"read": ["m:s:read"]}),
                    doctype("Alpha", module="m", label="A", permissions={"read": ["m:s:read"]}))
        assert {d["name"]: d for d in c.for_plugin("m")}["m:s:read"]["description"] == "A : m:s:read"


class TestWiringWithAuth:
    """Ce que `PluginGrantService.reconcile_plugin_grants` d'orchestra-backend/app/auth exige d'une déclaration."""

    @staticmethod
    def is_valid_grant(plugin, name):  # recopié de xauth : services/rbac/grants.py
        import re

        reserved = {"admin", "rbac", "auth", "tenants", "user", "license", "plugins"}
        if plugin in reserved or not name or not re.match(r"^[a-z0-9_]+:[a-z0-9_]+(:[a-z0-9_]+)*$", name):
            return False
        return name.split(":", 1)[0] == plugin

    @pytest.mark.parametrize("module", ["xsales", "stock", "a1_b2"])
    def test_every_declared_grant_would_be_accepted_by_xauth(self, module):
        c = catalog(
            doctype("Sales Invoice-Line", module=module),
            doctype("Customer", module=module, permissions={"read": [f"{module}:customer:own"]}),
        )
        decl = c.for_plugin(module)
        assert decl and all(self.is_valid_grant(module, d["name"]) for d in decl)

    @pytest.mark.parametrize("reserved", ["admin", "user", "rbac", "auth", "tenants", "license", "plugins"])
    def test_a_module_in_a_reserved_namespace_is_refused_at_load(self, reserved):
        """xauth refuserait la déclaration en silence : on échoue au chargement plutôt qu'avec un `rbac.declare` vide."""
        from xcore_doctype.errors import InvalidDefinition

        with pytest.raises(InvalidDefinition, match="namespace réservé"):
            doctype("Thing", module=reserved)

    def test_explicit_grants_in_a_reserved_namespace_are_only_cited(self):
        """Un DocType peut exiger `user:list` (grant de la plateforme) : il ne le déclare pas, il le vérifie."""
        c = catalog(doctype("Account", module="xsales", permissions={"read": ["user:list"]}))
        assert "user:list" in c.cited() and "user:list" not in names(c.for_plugin("xsales"))
