"""Le plugin `system` : réglages globaux (`System Settings`) et préférences d'affichage servies à tout utilisateur identifié."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.integration.conftest import boot_host
from xcore_doctype import system_plugin_dir

ROOT = Path(__file__).resolve().parents[2]
SYSTEM = {
    "system": (
        (system_plugin_dir() / "src" / "main.py").read_text(encoding="utf-8"),
        {
            "doctypes/system_settings.yaml": (system_plugin_dir() / "doctypes" / "system_settings.yaml").read_text(encoding="utf-8"),
        },
    )
}
ADMIN = ["system:system_settings:read", "system:system_settings:write"]
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


async def test_a_new_organisation_gets_the_defaults_and_anybody_identified_reads_them(tmp_path):
    async with boot_host(tmp_path, inline_plugins=SYSTEM) as h:
        nobody = h.token(sub="bob", permissions=[])  # aucun grant : un simple utilisateur
        r = await h.http("GET", "/preferences", token=nobody)
        assert r.status_code == 200, r.text
        assert r.json() == {"date_format": "JJ/MM/AAAA"}  # le seul défaut ; rien d'autre n'est inventé
        # Mais les réglages eux-mêmes restent réservés : lire le document demande le grant.
        assert (await h.http("GET", "/doc/System Settings/System Settings", token=nobody)).status_code in (403, 404)
        assert (await h.http("GET", "/doc/System Settings/System Settings", token=h.token(permissions=ADMIN))).status_code == 200
        # L'administrateur le trouve dans la liste des types, marqué comme réglage GLOBAL (rubrique « Général » des Paramètres).
        listed = {d["name"]: d for d in (await h.http("GET", "/meta", token=h.token(permissions=ADMIN))).json()}
        assert listed["System Settings"]["single"] is True and listed["System Settings"]["settings_group"] == "general"


async def test_what_an_administrator_saves_is_what_everybody_sees_in_their_own_organisation_only(tmp_path):
    async with boot_host(tmp_path, inline_plugins=SYSTEM) as h:
        admin = h.token(permissions=ADMIN)
        r = await h.http("PUT", "/doc/System Settings/System Settings", token=admin, json={"version": 0, "data": {"organization_name": "ACME", "brand_color": "#2a78d6", "date_format": "AAAA-MM-JJ"}})
        assert r.status_code == 200, r.text
        user = h.token(sub="bob", permissions=[])
        assert (await h.http("GET", "/preferences", token=user)).json() == {"organization_name": "ACME", "brand_color": "#2a78d6", "date_format": "AAAA-MM-JJ"}
        other = h.token(sub="gus", tenant="globex", permissions=[])
        assert (await h.http("GET", "/preferences", token=other, tenant="globex")).json() == {"date_format": "JJ/MM/AAAA"}  # pas celui d'acme


async def test_the_logo_is_served_to_everybody_through_a_signed_link(tmp_path):
    async with boot_host(tmp_path, inline_plugins=SYSTEM) as h:
        admin = h.token(permissions=ADMIN)
        up = await h.http("POST", "/upload/System Settings/logo", token=admin, files={"file": ("logo.png", PNG, "image/png")})
        assert up.status_code == 201, up.text
        file_id = up.json()["id"]
        assert (await h.http("PUT", "/doc/System Settings/System Settings", token=admin, json={"version": 0, "data": {"logo": file_id}})).status_code == 200
        logo = (await h.http("GET", "/preferences", token=h.token(sub="bob", permissions=[]))).json()["logo"]
        assert logo["content_type"] == "image/png" and f"files/acme/{file_id}?exp=" in logo["path"]
        served = await h.http("GET", "/" + logo["path"], token=None)
        assert served.status_code == 200 and served.content == PNG


async def test_preferences_need_an_identity_and_only_ever_expose_the_closed_list(tmp_path):
    async with boot_host(tmp_path, inline_plugins=SYSTEM) as h:
        assert (await h.http("GET", "/preferences", token=None)).status_code in (401, 403, 422)
        admin = h.token(permissions=ADMIN)
        await h.http("PUT", "/doc/System Settings/System Settings", token=admin, json={"version": 0, "data": {"organization_name": "ACME"}})
        assert set((await h.http("GET", "/preferences", token=admin)).json()) <= {"organization_name", "brand_color", "date_format", "logo"}


async def test_without_the_system_plugin_preferences_are_simply_empty(tmp_path):
    async with boot_host(tmp_path) as h:
        r = await h.http("GET", "/preferences", token=h.token(permissions=[]))
        assert r.status_code == 200 and r.json() == {}


def test_the_shipped_plugin_is_valid_and_ships_with_the_package():
    from xcore_doctype.cli import check

    lines, findings = check([system_plugin_dir() / "doctypes"], "system")
    assert "1 DocType(s) valides" in lines[0] and not [f for f in findings if f.level == "error"]
    assert (ROOT / "app" / "system").resolve() == system_plugin_dir().resolve()
