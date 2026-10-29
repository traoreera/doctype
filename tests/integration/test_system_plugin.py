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

# Un plugin qui apporte ses DocTypes PUIS ses gabarits d'impression (process/impression.md §2) : l'ordre compte.
MAIN_WITH_PRINT = '''
from pathlib import Path
from xcore.sdk import TrustedBase, error

BASE = Path(__file__).resolve().parent.parent


class Plugin(TrustedBase):
    async def on_load(self):
        registry = self.get_service("doctype_registry")
        registry.load_dir(self.ctx.name, BASE / "doctypes")
        registry.load_print_templates(self.ctx.name, BASE / "print")

    async def handle(self, action, payload):
        return error("no actions", "unknown_action")
'''
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
    # System Settings, Notification Rule et sa table de destinataires, User Permission, Letter Head
    assert "5 DocType(s) valides" in lines[0] and not [f for f in findings if f.level == "error"]
    dev_link = ROOT / "app" / "system"  # lien de confort de l'hôte de développement : présent seulement sur le poste du développeur
    if dev_link.exists() or dev_link.is_symlink():
        assert dev_link.resolve() == system_plugin_dir().resolve()


LETTERHEAD = ["system:letter_head:read", "system:letter_head:write"]


def with_letterhead() -> dict:
    """Le plugin `system` tel qu'il est LIVRÉ, avec le papier à en-tête et l'espace « Administration »."""
    files = {
        f"doctypes/{n}.yaml": (system_plugin_dir() / "doctypes" / f"{n}.yaml").read_text(encoding="utf-8")
        for n in ("system_settings", "letter_head")
    }
    files["workspaces/system.yaml"] = (system_plugin_dir() / "workspaces" / "system.yaml").read_text(encoding="utf-8")
    return {"system": ((system_plugin_dir() / "src" / "main.py").read_text(encoding="utf-8"), files)}


async def test_a_letter_head_is_printed_on_a_document_by_whoever_can_only_read_it(tmp_path):
    """Le papier à en-tête livré (process/impression.md §5, étape 6) : son image et son pied s'impriment.

    Le point qui compte : il est lu en contexte SYSTÈME, donc un lecteur SANS aucun grant sur `Letter Head`
    l'obtient quand même. Autrement un commercial qui lit les factures imprimerait sans en-tête — l'en-tête est
    de la présentation d'entreprise, pas un document métier (le précédent de `preferences.py`).
    """
    import yaml as _yaml

    invoice = {
        "schema_version": 1, "name": "Bill", "module": "shop",
        "fields": [{"fieldname": "customer", "fieldtype": "Text", "required": True}],
    }
    template = {
        "schema_version": 1, "doctype": "Bill", "name": "standard", "label": "Facture",
        "letterhead": {"name": "default"},
        "blocks": [{"type": "fields", "fields": ["customer"]}],
    }
    shop = (
        MAIN_WITH_PRINT,
        {"doctypes/bill.yaml": _yaml.safe_dump(invoice), "print/standard.yaml": _yaml.safe_dump(template)},
    )
    async with boot_host(tmp_path, inline_plugins={**with_letterhead(), "shop": shop}) as h:
        admin = h.token(sub="ada", permissions=[*LETTERHEAD, "shop:bill:read", "shop:bill:write"])
        made = await h.http("POST", "/doc/Letter Head", token=admin, json={
            "data": {"is_default": True, "disabled": False, "footer_text": "SARL Durand — RCS Paris 123 456 789"},
            "name": "Papier 2026",
        })
        assert made.status_code == 201, made.text
        bill = await h.http("POST", "/doc/Bill", token=admin, json={"data": {"customer": "Durand"}})
        name = bill.json()["name"]

        # Un lecteur SANS grant sur `Letter Head` : il obtient quand même le pied de page.
        reader = h.token(sub="rita", permissions=["shop:bill:read"])
        printed = await h.http("GET", f"/print/Bill/{name}", token=reader)
        assert printed.status_code == 200, printed.text
        assert "RCS Paris 123 456 789" in printed.json()["html"]

        # Désactivé : on imprime SANS en-tête, et ce n'est pas une erreur.
        current = await h.http("GET", "/doc/Letter Head/Papier 2026", token=admin)
        off = await h.http("PUT", "/doc/Letter Head/Papier 2026", token=admin, json={
            "version": current.json()["version"],
            "data": {"is_default": True, "disabled": True, "footer_text": "SARL Durand — RCS Paris 123 456 789"},
        })
        assert off.status_code == 200, off.text
        again = await h.http("GET", f"/print/Bill/{name}", token=reader)
        assert again.status_code == 200 and "RCS Paris" not in again.json()["html"]


async def test_the_administration_workspace_offers_the_letter_head(tmp_path):
    async with boot_host(tmp_path, inline_plugins=with_letterhead()) as h:
        admin = h.token(sub="ada", permissions=[*LETTERHEAD, *ADMIN])
        spaces = (await h.http("GET", "/workspaces", token=admin)).json()
        system = next(w for w in spaces if w["name"] == "system")
        assert "Letter Head" in [b.get("doctype") for b in system["blocks"]]
