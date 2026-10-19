"""RequestContext : tenant lié à l'identité (D17) et contexte explicite (D20), critères 4 et 5 de 14 bis.8."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from xcore_doctype.context import ContextSettings, RequestContext, _bind_tenant
from xcore_doctype.errors import ConfigurationError, ContextError

S = ContextSettings()  # défauts : liaison exigée, claim obligatoire


def request(tenant: str | None, headers: dict | None = None):
    state = SimpleNamespace() if tenant is None else SimpleNamespace(tenant_id=tenant)
    return SimpleNamespace(state=state, headers=headers or {})


class TestTenantBinding:
    def test_claim_matches(self):
        assert _bind_tenant("acme", {"sub": "a", "tenant_id": "acme"}, S) == "acme"

    def test_claim_read_from_user_dict(self):
        assert _bind_tenant("acme", {"sub": "a", "user": {"tenant_id": "acme"}}, S) == "acme"

    def test_mismatch_refused(self):
        with pytest.raises(ContextError) as e:
            _bind_tenant("globex", {"sub": "a", "tenant_id": "acme"}, S)
        assert (e.value.code, e.value.status) == ("tenant_mismatch", 403)

    def test_cross_tenant_grant_allows_header_tenant(self):
        p = {"sub": "a", "tenant_id": "acme", "permissions": ["doctype:cross_tenant"]}
        assert _bind_tenant("globex", p, S) == "globex"

    def test_a_role_with_the_name_of_the_grant_does_not_count(self):
        """Un grant vit dans `permissions`. Les noms de rôle sont libres chez auth : ils ne peuvent pas en jouer le rôle."""
        p = {"sub": "a", "tenant_id": "acme", "roles": ["doctype:cross_tenant"]}
        with pytest.raises(ContextError) as e:
            _bind_tenant("globex", p, S)
        assert e.value.code == "tenant_mismatch"

    def test_cross_tenant_grant_is_configurable_for_the_platform_admin_of_xauth(self):
        s = ContextSettings(cross_tenant_grant="admin:*")
        assert _bind_tenant("globex", {"sub": "a", "tenant_id": "acme", "permissions": ["admin:*"]}, s) == "globex"

    def test_missing_claim_refused_when_required(self):
        with pytest.raises(ContextError) as e:
            _bind_tenant("acme", {"sub": "a"}, S)
        assert e.value.code == "tenant_claim_missing"

    def test_missing_claim_tolerated_when_not_required(self):
        s = ContextSettings(require_tenant_claim=False)
        assert _bind_tenant("acme", {"sub": "a"}, s) == "acme"

    def test_binding_disabled_trusts_header(self):
        s = ContextSettings(enforce_tenant_binding=False)
        assert _bind_tenant("acme", {"sub": "a", "tenant_id": "globex"}, s) == "acme"

    @pytest.mark.parametrize("bad", ["", "public", "information_schema", "pg_catalog", "a b", "x" * 64, "a;b", "*"])
    def test_invalid_or_reserved_tenant(self, bad):
        with pytest.raises(ContextError) as e:
            _bind_tenant(bad, {"sub": "a", "tenant_id": bad}, ContextSettings(enforce_tenant_binding=False))
        assert (e.value.code, e.value.status) == ("invalid_tenant", 400)


class TestFromHttp:
    P = {"sub": "alice", "tenant_id": "acme"}

    def test_builds_context(self):
        ctx = RequestContext.from_http(request("acme", {"x-request-id": "r1"}), self.P, S)
        assert (ctx.tenant_id, ctx.user_id, ctx.source, ctx.request_id) == ("acme", "alice", "http", "r1")

    def test_fails_closed_without_tenant_middleware(self):
        """`Xcore.setup(app)` oublié : pas de repli silencieux sur « default »."""
        with pytest.raises(ContextError) as e:
            RequestContext.from_http(request(None), self.P, S)
        assert (e.value.code, e.value.status) == ("tenant_middleware_missing", 500)

    def test_without_binding_missing_middleware_means_default(self):
        s = ContextSettings(enforce_tenant_binding=False)
        assert RequestContext.from_http(request(None), self.P, s).tenant_id == "default"


class TestIdentity:
    def test_no_principal_refused(self):
        with pytest.raises(ContextError) as e:
            RequestContext("acme", None, "http").require_identity()
        assert (e.value.code, e.value.status) == ("unauthenticated", 401)

    def test_principal_without_sub_refused(self):
        with pytest.raises(ContextError):
            RequestContext("acme", {"roles": ["x"]}, "http").require_identity()

    def test_system_context_needs_no_user_but_a_reason(self):
        ctx = RequestContext.system("acme", "nightly-cleanup")
        ctx.require_identity()
        assert ctx.is_system and ctx.user_id is None and ctx.reason == "nightly-cleanup"
        with pytest.raises(ContextError):
            RequestContext.system("acme", "  ")

    def test_system_context_validates_tenant(self):
        with pytest.raises(ContextError):
            RequestContext.system("public", "x")

    def test_context_is_immutable(self):
        ctx = RequestContext("acme", {"sub": "a"}, "http")
        with pytest.raises(AttributeError):
            ctx.tenant_id = "globex"  # type: ignore[misc]


class TestPrincipalAccessors:
    """`permissions` (grants) est ce que lisent les règles de DocType ; `roles` est informatif."""

    def ctx(self, **principal):
        return RequestContext("acme", {"sub": "a", **principal}, "http")

    def test_permissions_and_roles_are_read_from_the_matching_claim(self):
        c = self.ctx(permissions=["sales:customer:read", "sales:customer:write"], roles=["Vendeur"])
        assert c.permissions == {"sales:customer:read", "sales:customer:write"} and c.roles == {"Vendeur"}

    @pytest.mark.parametrize("bad", ["sales:customer:read", {"sales:customer:read": True}, [["a:b"]], None, 5, True])
    def test_malformed_claims_grant_nothing(self, bad):
        """Une chaîne unique n'est pas une liste : sinon « abc » deviendrait {a, b, c}."""
        c = self.ctx(permissions=bad, roles=bad)
        assert c.permissions == frozenset() and c.roles == frozenset()

    def test_non_string_items_are_dropped(self):
        assert self.ctx(permissions=["a:b", 3, None, ["x"]]).permissions == {"a:b"}

    def test_a_role_is_never_a_permission_nor_the_reverse(self):
        c = self.ctx(permissions=["Admin"], roles=["sales:customer:read"])
        assert "sales:customer:read" not in c.permissions and "Admin" not in c.roles

    def test_no_principal(self):
        c = RequestContext("acme", None, "http")
        assert c.permissions == frozenset() and c.roles == frozenset()


class TestSettings:
    def test_defaults_fail_closed(self):
        assert ContextSettings.from_plugin_config({}).enforce_tenant_binding is True

    def test_reads_doctype_block_and_ignores_unrelated_keys(self):
        s = ContextSettings.from_plugin_config({"doctype": {"tenant_claim": "org", "doctypes_dirs": ["x"]}})
        assert s.tenant_claim == "org"

    @pytest.mark.parametrize("block", [{"enforce_tenant_binding": "false"}, {"tenant_claim": ""}, {"require_tenant_claim": 0}])
    def test_rejects_wrong_types(self, block):
        """Un `"false"` YAML entre guillemets ne doit pas désactiver la sécurité par accident."""
        with pytest.raises(ConfigurationError):
            ContextSettings.from_plugin_config({"doctype": block})
