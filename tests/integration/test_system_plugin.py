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
            "translations/en.yaml": (system_plugin_dir() / "translations" / "en.yaml").read_text(encoding="utf-8"),
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
        assert r.json() == {"date_format": "JJ/MM/AAAA", "language": "fr"}  # les seuls défauts ; rien d'autre n'est inventé
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
        assert (await h.http("GET", "/preferences", token=user)).json() == {"organization_name": "ACME", "brand_color": "#2a78d6", "date_format": "AAAA-MM-JJ", "language": "fr"}
        other = h.token(sub="gus", tenant="globex", permissions=[])
        assert (await h.http("GET", "/preferences", token=other, tenant="globex")).json() == {"date_format": "JJ/MM/AAAA", "language": "fr"}  # pas celui d'acme


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
        assert set((await h.http("GET", "/preferences", token=admin)).json()) <= {"organization_name", "brand_color", "date_format", "language", "logo", "my_language"}


async def test_without_the_system_plugin_preferences_are_simply_empty(tmp_path):
    async with boot_host(tmp_path) as h:
        r = await h.http("GET", "/preferences", token=h.token(permissions=[]))
        assert r.status_code == 200 and r.json() == {}


def test_the_shipped_plugin_is_valid_and_ships_with_the_package():
    from xcore_doctype.cli import check

    lines, findings = check([system_plugin_dir() / "doctypes"], "system")
    # System Settings, Notification Rule et sa table de destinataires, User Permission
    assert "4 DocType(s) valides" in lines[0] and not [f for f in findings if f.level == "error"]
    dev_link = ROOT / "app" / "system"  # lien de confort de l'hôte de développement : présent seulement sur le poste du développeur
    if dev_link.exists() or dev_link.is_symlink():
        assert dev_link.resolve() == system_plugin_dir().resolve()


async def test_the_organisation_background_prints_under_every_document_for_whoever_can_read_it(tmp_path):
    """Le papier à en-tête de l'entreprise = UN PDF déposé dans Paramètres › Général (process/impression.md §14) :
    toutes les impressions l'emploient, même un gabarit LIVRÉ qui ne dit rien d'un fond.

    Le point qui compte : il est lu en contexte SYSTÈME, donc un lecteur SANS aucun grant sur les réglages l'obtient
    quand même. Autrement un commercial qui lit les factures imprimerait sans en-tête — l'en-tête est de la
    présentation d'entreprise, pas un document métier (le précédent de `preferences.py`).
    """
    import yaml as _yaml

    invoice = {
        "schema_version": 1, "name": "Bill", "module": "shop",
        "fields": [{"fieldname": "customer", "fieldtype": "Text", "required": True}],
    }
    template = {"schema_version": 1, "doctype": "Bill", "name": "standard", "label": "Facture",
                "blocks": [{"type": "fields", "fields": ["customer"]}]}
    shop = (
        MAIN_WITH_PRINT,
        {"doctypes/bill.yaml": _yaml.safe_dump(invoice), "print/standard.yaml": _yaml.safe_dump(template)},
    )
    async with boot_host(tmp_path, inline_plugins={**SYSTEM, "shop": shop}) as h:
        admin = h.token(sub="ada", permissions=[*ADMIN, "shop:bill:read", "shop:bill:write"])
        up = await h.http("POST", "/upload/System Settings/print_background", token=admin,
                          files={"file": ("papier.pdf", b"%PDF-1.4 papier", "application/pdf")})
        assert up.status_code == 201, up.text
        settled = await h.http("PUT", "/doc/System Settings/System Settings", token=admin, json={
            "version": 0, "data": {"print_background": up.json()["id"], "print_offset_top": 30}})
        assert settled.status_code == 200, settled.text
        bill = await h.http("POST", "/doc/Bill", token=admin, json={"data": {"customer": "Durand"}})
        name = bill.json()["name"]

        # Un lecteur SANS grant sur les réglages : le fond s'applique quand même, avec sa zone réservée.
        reader = h.token(sub="rita", permissions=["shop:bill:read"])
        printed = await h.http("GET", f"/print/Bill/{name}", token=reader)
        assert printed.status_code == 200, printed.text
        assert printed.json()["background"] is True and "@page{margin-top:50mm" in printed.json()["html"]
        assert (await h.http("GET", f"/print/Bill/{name}?letterhead=none", token=reader)).json()["background"] is False
        # Une autre organisation n'a pas ce fond.
        other = h.token(sub="gus", tenant="globex", permissions=["shop:bill:read", "shop:bill:write"])
        theirs = await h.http("POST", "/doc/Bill", token=other, tenant="globex", json={"data": {"customer": "G"}})
        out = await h.http("GET", f"/print/Bill/{theirs.json()['name']}", token=other, tenant="globex")
        assert out.json()["background"] is False


async def test_the_administration_workspace_no_longer_offers_a_letter_head(tmp_path):
    """`Letter Head` a été retiré (§14) : le papier à en-tête est le fond PDF des réglages."""
    code, files = SYSTEM["system"]
    shipped = {**files}
    for kind, name in (("workspaces", "system"), ("doctypes", "user_permission")):
        shipped[f"{kind}/{name}.yaml"] = (system_plugin_dir() / kind / f"{name}.yaml").read_text(encoding="utf-8")
    async with boot_host(tmp_path, inline_plugins={"system": (code, shipped)}) as h:
        admin = h.token(sub="ada", permissions=[*ADMIN, "system:user_permission:read"])
        spaces = (await h.http("GET", "/workspaces", token=admin)).json()
        system = next(w for w in spaces if w["name"] == "system")
        assert "Letter Head" not in [b.get("doctype") for b in system["blocks"]]
        assert (await h.http("GET", "/meta/Letter Head", token=admin)).status_code == 404


async def test_labels_follow_accept_language_and_each_user_keeps_a_language(tmp_path):
    """i18n : le plugin `system` est écrit en français et livre son catalogue anglais ; `/meta` suit `Accept-Language` (les VALEURS
    ne bougent pas, leurs libellés s'ajoutent) ; chacun choisit sa langue (`PUT /preferences/language`), seulement une langue du Desk."""
    async with boot_host(tmp_path, inline_plugins=SYSTEM) as h:
        admin = h.token(permissions=ADMIN)
        url = h.base + "/meta/System Settings"
        english = (await h.client.get(url, headers={**h.headers(admin, "acme"), "Accept-Language": "en-GB,en;q=0.9"})).json()
        french = (await h.client.get(url, headers={**h.headers(admin, "acme"), "Accept-Language": "fr-FR"})).json()
        assert (english["label"], french["label"]) == ("General settings", "Paramètres généraux")
        fields = {f["fieldname"]: f for f in english["fields"]}
        assert fields["language"]["label"] == "Language" and fields["language"]["options"] == ["fr", "en"]
        assert fields["language"]["option_labels"] == {"fr": "French", "en": "English"}
        assert [s["section"] for s in english["layout"]] == ["Organisation", "Regional", "Printing"]

        bob = h.token(sub="bob", permissions=[])
        r = await h.http("PUT", "/preferences/language", token=bob, json={"language": "en"})
        assert r.status_code == 200 and r.json() == {"my_language": "en"}
        assert (await h.http("GET", "/preferences", token=bob)).json()["my_language"] == "en"
        assert "my_language" not in (await h.http("GET", "/preferences", token=admin)).json()  # celle de bob, pas celle des autres
        assert (await h.http("PUT", "/preferences/language", token=bob, json={"language": "de"})).status_code == 422
        await h.http("PUT", "/preferences/language", token=bob, json={"language": None})
        assert "my_language" not in (await h.http("GET", "/preferences", token=bob)).json()
