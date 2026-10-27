"""Définition et validation d'écriture (process/doctype.md §9, §7.1)."""

from __future__ import annotations

import pytest

from xcore_doctype.builder import CompiledDocType
from xcore_doctype.errors import InvalidDefinition, ValidationFailed
from xcore_doctype.schema import parse_definition


def definition(**over):
    base = {"schema_version": 1, "name": "T", "fields": [{"fieldname": "a", "fieldtype": "Text"}]}
    return {**base, **over}


class TestDefinition:
    def test_valid(self, customer):
        assert customer.definition.name == "Customer"

    @pytest.mark.parametrize(
        "bad",
        [
            # Ce que le moteur n'applique pas encore est refusé, jamais ignoré en silence.
            definition(fields=[{"fieldname": "a", "fieldtype": "Text", "depends_on": "b == 1"}]),  # b n'existe pas
            definition(naming="CUST-.####"),
            # Noms réservés (colonnes système) et doublons.
            *[definition(fields=[{"fieldname": n, "fieldtype": "Text"}]) for n in
              ("name", "owner", "creation", "modified", "version", "tenant_id", "doctype")],
            definition(fields=[{"fieldname": "a", "fieldtype": "Text"}, {"fieldname": "a", "fieldtype": "Number"}]),
            # Cohérence type / options / défaut.
            definition(fields=[{"fieldname": "a", "fieldtype": "Select"}]),
            definition(fields=[{"fieldname": "a", "fieldtype": "Select", "options": ["x", "x"]}]),
            definition(fields=[{"fieldname": "a", "fieldtype": "Link"}]),
            definition(fields=[{"fieldname": "a", "fieldtype": "Text", "options": ["x"]}]),
            definition(fields=[{"fieldname": "a", "fieldtype": "Number", "default": "zéro"}]),
            definition(fields=[{"fieldname": "a", "fieldtype": "Boolean", "default": 1}]),
            definition(fields=[{"fieldname": "a", "fieldtype": "Select", "options": ["x"], "default": "y"}]),
            definition(fields=[{"fieldname": "a", "fieldtype": "Date", "default": "17/10/2026"}]),
            # Identifiants : le nom de champ finit dans une expression SQL JSON.
            definition(fields=[{"fieldname": "A", "fieldtype": "Text"}]),
            definition(fields=[{"fieldname": "a'; DROP TABLE x;--", "fieldtype": "Text"}]),
            definition(fields=[]),
            definition(schema_version=2),
        ],
    )
    def test_rejected(self, bad):
        with pytest.raises(InvalidDefinition):
            parse_definition(bad)


def with_perm(permissions, *, module=None, **over):
    fields = [{"fieldname": "a", "fieldtype": "Text"}, {"fieldname": "n", "fieldtype": "Number"},
              {"fieldname": "c", "fieldtype": "Link", "options": "C"}]
    extra = {"module": module} if module else {}
    return definition(fields=fields, permissions=permissions, **extra, **over)


MATCH = {"field": "c", "principal_attr": "country"}
ACTIONS = ("read", "write", "create", "delete")


def grants_of(d, action):
    return [e.grant for e in d.rules_for(action)]


class TestConventionalPermissions:
    """Sans bloc `permissions`, les grants se déduisent de `module` et du nom du DocType."""

    def test_default_grants(self):
        d = parse_definition(definition(name="Customer", module="xsales"))
        assert {a: grants_of(d, a) for a in ACTIONS} == {
            "read": ["xsales:customer:read"],
            "write": ["xsales:customer:write"],
            "create": ["xsales:customer:write"],  # comme « Créer/modifier » chez xauth
            "delete": ["xsales:customer:delete"],
        }

    def test_name_with_spaces_becomes_a_snake_case_segment(self):
        d = parse_definition(definition(name="Sales Invoice-Line", module="xsales"))
        assert grants_of(d, "read") == ["xsales:sales_invoice_line:read"]

    def test_without_module_nobody_gets_anything(self):
        d = parse_definition(definition())
        assert all(d.rules_for(a) == () for a in ACTIONS)

    @pytest.mark.parametrize("module", ["Sales", "x-sales", "a b", "x:y", "", "é", "a" * 64])
    def test_module_must_be_a_valid_grant_namespace(self, module):
        with pytest.raises(InvalidDefinition):
            parse_definition(definition(module=module))

    def test_the_conventional_grants_are_valid_grant_names(self):
        d = parse_definition(definition(name="Z", module="m"))
        assert set(d.grant_specs()) == {"m:z:read", "m:z:write", "m:z:delete"}


class TestExplicitPermissions:
    def test_an_entry_is_a_string_or_an_object(self):
        d = parse_definition(with_perm({"read": ["sales:customer:read", {"grant": "sales:customer:own", "if_owner": True}]}))
        plain, own = d.rules_for("read")
        assert (plain.grant, plain.if_owner, own.grant, own.if_owner) == ("sales:customer:read", False, "sales:customer:own", True)

    def test_an_action_that_is_not_listed_falls_back_to_the_convention(self):
        d = parse_definition(with_perm({"read": ["x:y:z"]}, module="xsales", name="Customer"))
        assert grants_of(d, "read") == ["x:y:z"]  # remplacé, pas fusionné
        assert grants_of(d, "write") == ["xsales:customer:write"] and grants_of(d, "delete") == ["xsales:customer:delete"]

    def test_an_empty_list_closes_the_action_for_everybody(self):
        d = parse_definition(with_perm({"delete": []}, module="xsales", name="Customer"))
        assert d.rules_for("delete") == () and grants_of(d, "read") == ["xsales:customer:read"]

    def test_without_module_an_unlisted_action_is_closed(self):
        d = parse_definition(with_perm({"read": ["x:y:read"]}))
        assert grants_of(d, "read") == ["x:y:read"] and d.rules_for("write") == () and d.rules_for("create") == ()

    @pytest.mark.parametrize("grant", ["a:b", "sales:customer:read", "x_1:y_2:z_3:w", "stock:item:move"])
    def test_grant_names_follow_the_auth_convention(self, grant):
        parse_definition(with_perm({"read": [grant]}))

    @pytest.mark.parametrize(
        "permissions",
        [
            [{"grant": "a:b:c", "read": True}],  # l'ancienne forme en liste de lignes
            [],
            {"purge": ["a:b:c"]},  # action inconnue
            {"read": "a:b:c"},  # une chaîne, pas une liste
            {"read": [{"role": "Sales Manager"}]},  # les rôles ne sont pas pris en charge
            {"read": ["role:admin"]},  # ni sous la forme `role:`, qui serait lue comme un grant
            {"read": ["Sales Manager"]},
            {"read": ["sales"]},  # un seul segment
            {"read": ["Sales:Customer:Read"]},  # majuscules
            {"read": ["sales:customer:*"]},  # pas de joker
            {"read": ["admin:*"]},
            {"read": ["sales::read"]},
            {"read": [""]},
            {"read": [3]},
            {"read": [{"grant": "a:b:c", "if_owner": "yes"}]},  # pas de coercion
            {"read": [{"grant": "a:b:c", "tenant_grantable": "no"}]},
            {"read": [{"grant": "a:b:c", "unknown": 1}]},
            {"read": [{"if_owner": True}]},  # grant manquant
            {"read": ["a:b:c", "a:b:c"]},  # doublon
        ],
    )
    def test_rejected(self, permissions):
        with pytest.raises(InvalidDefinition):
            parse_definition(with_perm(permissions))

    def test_the_old_row_form_is_explained(self):
        with pytest.raises(InvalidDefinition, match="l'ancienne liste de lignes"):
            parse_definition(with_perm([{"grant": "a:b:c", "read": True}]))

    def test_roles_are_explained(self):
        with pytest.raises(InvalidDefinition, match="rôles ne sont pas pris en charge"):
            parse_definition(with_perm({"read": ["role:admin"]}))

    def test_the_same_grant_may_appear_under_several_actions(self):
        d = parse_definition(with_perm({"read": ["a:b:own"], "write": [{"grant": "a:b:own", "if_owner": True}]}))
        assert grants_of(d, "read") == grants_of(d, "write") == ["a:b:own"]


class TestGrantSpecs:
    """Ce que le plugin propriétaire transmet à `rbac.declare` (nom, description, groupe, délégable)."""

    def specs(self, **over):
        return parse_definition(definition(name="Customer", label="Client", module="xsales", **over)).grant_specs()

    def test_conventional_grants_are_described_in_the_style_of_the_manifests(self):
        s = self.specs()
        assert (s["xsales:customer:read"].description, s["xsales:customer:write"].description,
                s["xsales:customer:delete"].description) == ("Consulter Client", "Créer/modifier Client", "Supprimer Client")
        assert {x.group for x in s.values()} == {"xsales-customer"} and all(x.tenant_grantable for x in s.values())

    def test_explicit_metadata_wins(self):
        s = self.specs(permissions={"delete": [{"grant": "xsales:customer:delete", "tenant_grantable": False, "description": "Suppression définitive"}]})
        assert s["xsales:customer:delete"].tenant_grantable is False
        assert s["xsales:customer:delete"].description == "Suppression définitive"
        assert s["xsales:customer:read"].tenant_grantable is True  # les autres gardent le défaut

    def test_a_grant_without_description_gets_a_readable_default(self):
        s = self.specs(permissions={"read": [{"grant": "xsales:customer:own", "if_owner": True}]})
        assert s["xsales:customer:own"].description == "Client : xsales:customer:own"

    def test_contradictory_metadata_for_one_grant_is_refused(self):
        with pytest.raises(InvalidDefinition, match="contradictoire"):
            self.specs(permissions={"read": [{"grant": "a:b:c", "tenant_grantable": True}],
                                    "write": [{"grant": "a:b:c", "tenant_grantable": False}]})

    def test_repeating_the_same_metadata_is_fine(self):
        m = {"grant": "a:b:c", "tenant_grantable": False}
        self.specs(permissions={"read": [m], "write": [m]})

    def test_declaration_matches_what_auth_reads(self):
        """`PluginGrantService.reconcile_plugin_grants` lit name, description, group et tenant_grantable."""
        d = self.specs()["xsales:customer:read"].as_declaration()
        # `default_roles` accompagne un grant DÉLÉGABLE : auth le verse alors aussi au rôle du propriétaire d'une organisation.
        assert set(d) == {"name", "description", "group", "tenant_grantable", "default_roles"} and d["name"] == "xsales:customer:read"
        assert d["default_roles"] == ["tenant_admin"]


class TestFrozenMatch:
    """`match` est gelé tant qu'aucun backend ne fournit d'attribut ou de scope par utilisateur (process/phase1.md §7)."""

    def test_rejected_by_default_with_an_explanation(self):
        with pytest.raises(InvalidDefinition, match="gelé"):
            parse_definition(with_perm({"read": [{"grant": "a:b:c", "match": MATCH}]}))

    def test_the_machinery_is_kept_and_can_be_enabled(self, monkeypatch):
        from xcore_doctype import schema

        monkeypatch.setattr(schema, "MATCH_ENABLED", True)
        d = parse_definition(with_perm({"read": [{"grant": "a:b:c", "match": MATCH}]}))
        assert d.rules_for("read")[0].match.field == "c"

    @pytest.mark.parametrize(
        "match",
        [
            {"field": "zz", "principal_attr": "x"},  # champ inconnu
            {"field": "n", "principal_attr": "x"},  # Number : pas textuel
            {"field": "a"},
            {"field": "a", "principal_attr": "x; DROP"},
        ],
    )
    def test_invalid_matches_are_still_rejected_when_enabled(self, monkeypatch, match):
        from xcore_doctype import schema

        monkeypatch.setattr(schema, "MATCH_ENABLED", True)
        with pytest.raises(InvalidDefinition):
            parse_definition(with_perm({"read": [{"grant": "a:b:c", "match": match}]}))


class TestWriteValidation:
    def test_defaults_applied_and_none_omitted(self, customer):
        assert customer.validate_data({"customer_name": "Acme"}) == {"customer_name": "Acme", "credit_limit": 0}

    def test_all_types(self, customer):
        data = {
            "customer_name": "Acme",
            "type": "Entreprise",
            "credit_limit": 12.5,
            "vip": True,
            "since": "2026-10-17",
            "country": "FR",
        }
        assert customer.validate_data(data) == data

    @pytest.mark.parametrize(
        "payload, field",
        [
            ({}, "customer_name"),  # requis
            ({"customer_name": None}, "customer_name"),  # null refusé sur un requis
            ({"customer_name": "A", "type": "Autre"}, "type"),  # hors options
            ({"customer_name": "A", "credit_limit": "12"}, "credit_limit"),  # pas de coercion str -> nombre
            ({"customer_name": "A", "credit_limit": True}, "credit_limit"),  # bool n'est pas un nombre
            *[({"customer_name": "A", "credit_limit": v}, "credit_limit") for v in (float("nan"), float("inf"), float("-inf"))],
            ({"customer_name": "A", "vip": 1}, "vip"),  # pas de coercion int -> bool
            ({"customer_name": "A", "vip": "true"}, "vip"),
            ({"customer_name": "A", "since": "20261017"}, "since"),  # ISO étendu uniquement
            ({"customer_name": "A", "since": "2026-02-30"}, "since"),
            ({"customer_name": "A", "since": 1760000000}, "since"),  # pas de timestamp
            ({"customer_name": "A", "country": ""}, "country"),
            # Champs système et inconnus : refusés (extra="forbid").
            *[({"customer_name": "A", k: "x"}, k) for k in ("owner", "tenant_id", "version", "name", "unknown")],
        ],
    )
    def test_rejected(self, customer, payload, field):
        with pytest.raises(ValidationFailed) as e:
            customer.validate_data(payload)
        assert any(d["field"].split(".")[0] == field for d in e.value.details)

    def test_error_details_never_echo_input(self, customer):
        with pytest.raises(ValidationFailed) as e:
            customer.validate_data({"customer_name": "A", "credit_limit": "secret-value"})
        assert "secret-value" not in repr(e.value.details)

    def test_payload_must_be_object(self, customer):
        with pytest.raises(ValidationFailed):
            customer.validate_data(["customer_name"])

    def test_filter_coercion_uses_field_type(self, customer):
        assert customer.coerce_filter("credit_limit", 5) == 5
        with pytest.raises(ValidationFailed):
            customer.coerce_filter("credit_limit", "5")
        with pytest.raises(ValidationFailed):
            customer.coerce_filter("type", "Autre")


class TestNumber:
    def test_one_error_per_bad_number_not_one_per_union_member(self, customer):
        with pytest.raises(ValidationFailed) as e:
            customer.validate_data({"customer_name": "A", "credit_limit": "12"})
        assert [d["field"] for d in e.value.details] == ["credit_limit"]

    @pytest.mark.parametrize("value", [0, -3, 5000, 12.5, 1e300])
    def test_ints_stay_ints_and_floats_stay_floats(self, customer, value):
        out = customer.validate_data({"customer_name": "A", "credit_limit": value})["credit_limit"]
        assert out == value and type(out) is type(value)

    @pytest.mark.parametrize("bad", [float("nan"), float("inf")])
    def test_non_finite_rejected_as_filter_too(self, customer, bad):
        with pytest.raises(ValidationFailed):
            customer.coerce_filter("credit_limit", bad)


class TestConditionsAndLayout:
    F = [{"fieldname": "a", "fieldtype": "Text"}, {"fieldname": "n", "fieldtype": "Number"},
         {"fieldname": "h", "fieldtype": "Text", "hidden": True}, {"fieldname": "z", "fieldtype": "Boolean"}]

    def d(self, **over):
        return parse_definition(definition(fields=over.pop("fields", self.F), **over))

    def test_conditions_are_accepted_and_checked_against_the_fields(self):
        f = [{"fieldname": "a", "fieldtype": "Text", "depends_on": "n > 1 and z", "mandatory_depends_on": "n == 2",
              "read_only_depends_on": "not z"}, *self.F[1:]]
        assert self.d(fields=f).field_map["a"].depends_on == "n > 1 and z"

    @pytest.mark.parametrize("src, why", [("zz == 1", "inconnu"), ("n >", "valeur"), ("n.x", "caractère"), ("__import__('os')", "fin de condition"), ("", "vide")])
    def test_bad_conditions_are_rejected_with_the_field_and_the_reason(self, src, why):
        for kind in ("depends_on", "mandatory_depends_on", "read_only_depends_on"):
            f = [{"fieldname": "a", "fieldtype": "Text", kind: src}, *self.F[1:]]
            with pytest.raises(InvalidDefinition, match=f"a.{kind}.*{why}"):
                self.d(fields=f)

    @pytest.mark.parametrize("name", ["and", "or", "not", "in", "true", "false", "null", "none"])
    def test_condition_keywords_are_reserved_field_names(self, name):
        with pytest.raises(InvalidDefinition):
            self.d(fields=[{"fieldname": name, "fieldtype": "Text"}])

    def test_default_layout_is_one_block_of_visible_fields_in_order(self):
        assert self.d().effective_layout() == [{"section": None, "collapsible": False, "collapsed": False, "columns": [["a", "n", "z"]]}]

    def test_declared_sections_then_the_fields_nobody_placed(self):
        lay = self.d(layout=[{"section": "Général", "columns": [["a"], ["n"]]}]).effective_layout()
        assert [(s["section"], s["columns"]) for s in lay] == [("Général", [["a"], ["n"]]), (None, [["z"]])]

    def test_hidden_fields_never_appear_in_the_layout_nor_the_list(self):
        d = self.d()
        assert all("h" not in col for s in d.effective_layout() for col in s["columns"]) and "h" not in d.list_columns()

    @pytest.mark.parametrize(
        "layout",
        [[{"columns": [["zz"]]}], [{"columns": [["h"]]}], [{"columns": [["a"], ["a"]]}], [{"columns": [["a"]]}, {"columns": [["a"]]}],
         [{"collapsed": True, "columns": [["a"]]}], [{"columns": [[]]}], [{"columns": []}], [{"columns": [["a"]] * 5}],
         [{"section": "", "columns": [["a"]]}], [{"columns": [["A"]]}], [{"unknown": 1, "columns": [["a"]]}], "a", [["a"]]],
    )
    def test_bad_layouts_are_rejected(self, layout):
        with pytest.raises(InvalidDefinition):
            self.d(layout=layout)

    def test_list_columns(self):
        assert self.d().list_columns() == ["a", "n", "z"]  # aucun champ marqué : les premiers visibles
        many = [{"fieldname": f"f{i}", "fieldtype": "Text"} for i in range(7)]
        assert self.d(fields=many).list_columns() == ["f0", "f1", "f2", "f3"]
        flagged = [{**f, "in_list_view": f["fieldname"] in ("f5", "f2")} for f in many]
        assert self.d(fields=flagged).list_columns() == ["f2", "f5"]  # ordre de déclaration

    def test_title_field_must_exist_and_be_textual(self):
        assert self.d(title_field="a").title_field == "a"
        for bad in ("zz", "n", "z"):
            with pytest.raises(InvalidDefinition):
                self.d(title_field=bad)

    def test_date_default_must_be_canonical_like_a_written_value(self):
        for bad in ("20261017", "2026-1-5", "17/10/2026"):
            with pytest.raises(InvalidDefinition):
                self.d(fields=[{"fieldname": "d", "fieldtype": "Date", "default": bad}])
        self.d(fields=[{"fieldname": "d", "fieldtype": "Date", "default": "2026-10-17"}])

