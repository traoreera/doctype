"""Documents uniques (« Paramètres ») : un seul enregistrement par organisation, nommé comme son type, ouvert vide de valeurs tant qu'on ne l'a pas enregistré."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from tests.conftest import make_ctx
from xcore_doctype.document import DocumentService
from xcore_doctype.errors import InvalidDefinition, NotFound, PermissionDenied, ValidationFailed, VersionConflict
from xcore_doctype.registry import DocTypeRegistry
from xcore_doctype.schema import parse_definition

SETTINGS = {
    "schema_version": 1,
    "name": "Stock Settings",
    "label": "Paramètres du stock",
    "module": "stock",
    "single": True,
    "fields": [
        {"fieldname": "allow_negative", "fieldtype": "Boolean", "default": False},
        {"fieldname": "default_uom", "fieldtype": "Select", "options": ["pcs", "kg"], "default": "pcs"},
        {"fieldname": "threshold", "fieldtype": "Decimal", "decimals": 3, "default": 5},
        {"fieldname": "opened", "fieldtype": "Date", "default": "today"},
        {"fieldname": "contact", "fieldtype": "Text", "format": "email"},
    ],
    "hooks": {"validate": ["stock.check_settings"]},
}
ADMIN = make_ctx("acme", "ada", permissions=["stock:stock_settings:read", "stock:stock_settings:write"])
READER = make_ctx("acme", "rita", permissions=["stock:stock_settings:read"])
OTHER = make_ctx("globex", "gus", permissions=["stock:stock_settings:read", "stock:stock_settings:write"])
NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)


class _Log:
    def info(self, *a, **k): ...
    def error(self, *a, **k): ...


@pytest.fixture
def svc(db, tables):
    from xcore_doctype.hooks import HookRegistry

    hooks = HookRegistry()
    calls = []

    async def check(event):
        calls.append(event.event)

    hooks.register("stock.check_settings", check, owner="stock")
    reg = DocTypeRegistry()
    reg.register(parse_definition({k: v for k, v in SETTINGS.items()}))
    s = DocumentService(reg, db, tables, _Log(), hooks=hooks, clock=lambda: NOW)
    s.calls = calls
    return s


class TestDefinition:
    @pytest.mark.parametrize(
        "patch, fragment",
        [({"submittable": True}, "single"), ({"naming": {"series": "S-{#####}"}}, "nommé comme son type"), ({"istable": True}, "single")],
    )
    def test_what_makes_no_sense_for_a_single_is_refused(self, patch, fragment):
        with pytest.raises(InvalidDefinition, match=fragment):
            parse_definition({**SETTINGS, "hooks": {}, **patch})

    def test_a_global_setting_must_be_a_single(self):
        ok = parse_definition({**SETTINGS, "hooks": {}, "settings_group": "general"})
        assert ok.settings_group == "general"
        plain = {k: v for k, v in SETTINGS.items() if k not in ("single", "hooks")}
        with pytest.raises(InvalidDefinition, match="settings_group"):
            parse_definition({**plain, "settings_group": "general"})
        with pytest.raises(InvalidDefinition):  # un seul groupe existe : « Général »
            parse_definition({**SETTINGS, "hooks": {}, "settings_group": "autre"})

    def test_no_kanban_calendar_gantt_or_tree(self):
        with pytest.raises(InvalidDefinition, match="un seul document"):
            parse_definition({**SETTINGS, "hooks": {}, "views": {"calendar": {"start": "opened"}}})


class TestOpening:
    async def test_it_opens_with_its_defaults_before_anything_is_saved(self, svc):
        doc = await svc.get(ADMIN, "Stock Settings", "Stock Settings")
        assert doc["version"] == 0 and doc["name"] == "Stock Settings"
        assert doc["data"] == {"allow_negative": False, "default_uom": "pcs", "threshold": "5.000", "opened": "2026-10-18"}
        assert (await svc.list(ADMIN, "Stock Settings"))["total"] == 0  # rien n'existe encore en base

    async def test_it_needs_the_right_to_read(self, svc):
        with pytest.raises(PermissionDenied):
            await svc.get(make_ctx("acme", "x", permissions=[]), "Stock Settings", "Stock Settings")

    async def test_only_its_own_name_opens(self, svc):
        with pytest.raises(NotFound):
            await svc.get(ADMIN, "Stock Settings", "autre")

    async def test_the_meta_says_it_is_single_and_closes_delete(self, svc):
        meta = svc.get_meta(ADMIN, "Stock Settings")
        assert meta["single"] is True and meta["rights"]["delete"] == "none" and meta["rights"]["write"] == "all"
        summary = svc.list_meta(ADMIN)[0]
        assert summary["single"] is True and summary["create"] is False  # jamais de « Nouveau » pour un réglage
        assert "settings_group" not in summary  # un réglage de module n'est pas un réglage global


class TestSaving:
    async def test_the_first_save_creates_it_under_the_name_of_its_type(self, svc):
        saved = await svc.update(ADMIN, "Stock Settings", "Stock Settings", {"allow_negative": True, "contact": "a@b.fr"}, 0)
        assert saved["name"] == "Stock Settings" and saved["version"] == 1 and saved["owner"] == "ada"
        assert saved["data"]["allow_negative"] is True
        assert (await svc.list(ADMIN, "Stock Settings"))["total"] == 1
        assert svc.calls == ["validate"]  # les hooks tournent comme pour tout document

    async def test_then_it_updates_with_the_version(self, svc):
        first = await svc.update(ADMIN, "Stock Settings", "Stock Settings", {"allow_negative": False}, 0)
        second = await svc.update(ADMIN, "Stock Settings", "Stock Settings", {"allow_negative": True}, first["version"])
        assert second["version"] == 2
        with pytest.raises(VersionConflict):
            await svc.update(ADMIN, "Stock Settings", "Stock Settings", {"allow_negative": False}, 1)

    async def test_a_second_first_save_is_a_conflict_not_a_duplicate(self, svc):
        await svc.update(ADMIN, "Stock Settings", "Stock Settings", {}, 0)
        with pytest.raises(VersionConflict):
            await svc.update(ADMIN, "Stock Settings", "Stock Settings", {"allow_negative": True}, 0)  # un autre onglet avait la version 0

    async def test_two_simultaneous_first_saves_make_one_document(self, svc):
        results = await asyncio.gather(
            *(svc.update(ADMIN, "Stock Settings", "Stock Settings", {"default_uom": u}, 0) for u in ("pcs", "kg")), return_exceptions=True
        )
        assert sum(not isinstance(r, Exception) for r in results) == 1
        assert [type(r) for r in results if isinstance(r, Exception)] == [VersionConflict]
        assert (await svc.list(ADMIN, "Stock Settings"))["total"] == 1

    async def test_it_is_validated_like_any_document(self, svc):
        with pytest.raises(ValidationFailed) as e:
            await svc.update(ADMIN, "Stock Settings", "Stock Settings", {"contact": "pas un courriel"}, 0)
        assert e.value.details[0]["field"] == "contact"
        assert (await svc.list(ADMIN, "Stock Settings"))["total"] == 0

    async def test_a_reader_cannot_save(self, svc):
        with pytest.raises(PermissionDenied):
            await svc.update(READER, "Stock Settings", "Stock Settings", {}, 0)

    async def test_it_cannot_be_created_or_deleted_the_ordinary_way(self, svc):
        with pytest.raises(ValidationFailed) as e:
            await svc.create(ADMIN, "Stock Settings", {})
        assert e.value.code == "single_document"
        await svc.update(ADMIN, "Stock Settings", "Stock Settings", {}, 0)
        with pytest.raises(ValidationFailed) as e:
            await svc.delete(ADMIN, "Stock Settings", "Stock Settings")
        assert e.value.code == "single_document"
        assert (await svc.list(ADMIN, "Stock Settings"))["total"] == 1

    async def test_only_its_own_name_saves(self, svc):
        with pytest.raises(NotFound):
            await svc.update(ADMIN, "Stock Settings", "autre", {}, 0)

    async def test_each_tenant_has_its_own(self, svc):
        await svc.update(ADMIN, "Stock Settings", "Stock Settings", {"default_uom": "kg"}, 0)
        other = await svc.get(OTHER, "Stock Settings", "Stock Settings")
        assert other["version"] == 0 and other["data"]["default_uom"] == "pcs"  # les défauts, pas les réglages de l'autre organisation
        await svc.update(OTHER, "Stock Settings", "Stock Settings", {"default_uom": "pcs"}, 0)
        assert (await svc.get(ADMIN, "Stock Settings", "Stock Settings"))["data"]["default_uom"] == "kg"

    async def test_history_records_the_changes(self, svc):
        first = await svc.update(ADMIN, "Stock Settings", "Stock Settings", {"allow_negative": False}, 0)
        await svc.update(ADMIN, "Stock Settings", "Stock Settings", {"allow_negative": True}, first["version"])
        history = await svc.history(ADMIN, "Stock Settings", "Stock Settings")
        assert [h["action"] for h in history["items"]] == ["update", "create"]
