"""L'impression de bout en bout, par l'API et par l'IPC (process/impression.md §7).

Un vrai hôte xcore, un vrai plugin qui LIVRE un gabarit dans son dossier `print/`, et les trois routes. Ce que
les tests unitaires ne peuvent pas montrer : que le gabarit d'un plugin arrive bien par le canal, que la réponse
PDF est une vraie réponse binaire avec ses en-têtes, et que les actions IPC rendent la même chose que l'HTTP.
"""

from __future__ import annotations

import base64

import yaml

from tests.integration.conftest import boot_host

# Un shim qui charge ses DocTypes PUIS ses gabarits d'impression : l'ordre de §2 (un gabarit vise un type chargé).
MAIN = '''
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

LINE = {
    "schema_version": 1, "name": "Bill Line", "istable": True,
    "fields": [{"fieldname": "item", "fieldtype": "Text"}, {"fieldname": "amount", "fieldtype": "Decimal", "decimals": 2}],
}
BILL = {
    "schema_version": 1, "name": "Bill", "module": "shop", "label": "Facture",
    "fields": [
        {"fieldname": "customer", "fieldtype": "Text"},
        {"fieldname": "items", "fieldtype": "Table", "options": "Bill Line"},
        {"fieldname": "total", "fieldtype": "Decimal", "decimals": 2},
    ],
}
NOTE = {"schema_version": 1, "name": "Note", "module": "shop", "fields": [{"fieldname": "body", "fieldtype": "Text"}]}

STANDARD = {
    "schema_version": 1, "doctype": "Bill", "name": "standard", "label": "Facture",
    "blocks": [
        {"type": "title", "text": "Facture {{ name }}"},
        {"type": "fields", "fields": ["customer"]},
        {"type": "table", "field": "items", "columns": [{"field": "item"}, {"field": "amount", "align": "right"}]},
        {"type": "totals", "fields": ["total"]},
    ],
}
DUPLICATE = {**STANDARD, "name": "duplicata", "label": "Duplicata",
             "blocks": [{"type": "title", "text": "DUPLICATA {{ name }}"}]}

GRANTS = [f"shop:{n}:{a}" for n in ("bill", "note") for a in ("read", "write")]


def plugins():
    files = {f"doctypes/{d['name'].lower().replace(' ', '_')}.yaml": yaml.safe_dump(d) for d in (LINE, BILL, NOTE)}
    files |= {f"print/{t['name']}.yaml": yaml.safe_dump(t) for t in (STANDARD, DUPLICATE)}
    return {"shop": (MAIN, files)}


async def bill(h, token):
    r = await h.http("POST", "/doc/Bill", token=token,
                     json={"data": {"customer": "Durand", "items": [{"item": "Chaise", "amount": "40.00"}], "total": "40.00"}})
    assert r.status_code == 201, r.text
    return r.json()["name"]


async def test_a_plugin_ships_its_print_templates_and_the_desk_lists_them(tmp_path):
    async with boot_host(tmp_path, inline_plugins=plugins()) as h:
        ada = h.token(sub="ada", permissions=GRANTS)
        r = await h.http("GET", "/print-templates/Bill", token=ada)
        assert r.status_code == 200, r.text
        body = r.json()
        assert [(t["name"], t["label"], t["source"]) for t in body["templates"]] == [
            ("duplicata", "Duplicata", "plugin"), ("standard", "Facture", "plugin")]
        assert body["default"] == "standard" and isinstance(body["pdf"], bool)


async def test_a_type_without_a_template_still_gets_one(tmp_path):
    """§2 : aucun document n'est inimprimable — le gabarit est FABRIQUÉ depuis la définition."""
    async with boot_host(tmp_path, inline_plugins=plugins()) as h:
        ada = h.token(sub="ada", permissions=GRANTS)
        r = await h.http("GET", "/print-templates/Note", token=ada)
        assert [t["source"] for t in r.json()["templates"]] == ["fabriqué"]
        made = await h.http("POST", "/doc/Note", token=ada, json={"data": {"body": "à imprimer"}})
        out = await h.http("GET", f"/print/Note/{made.json()['name']}", token=ada)
        assert out.status_code == 200 and "à imprimer" in out.json()["html"]


async def test_the_preview_renders_the_document(tmp_path):
    async with boot_host(tmp_path, inline_plugins=plugins()) as h:
        ada = h.token(sub="ada", permissions=GRANTS)
        name = await bill(h, ada)
        r = await h.http("GET", f"/print/Bill/{name}", token=ada)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["template"] == "standard" and body["background"] is False
        html = body["html"]
        assert f"Facture {name}" in html and "Durand" in html and "Chaise" in html and "40.00" in html

        chosen = await h.http("GET", f"/print/Bill/{name}?template=duplicata", token=ada)
        assert f"DUPLICATA {name}" in chosen.json()["html"]
        missing = await h.http("GET", f"/print/Bill/{name}?template=inexistant", token=ada)
        assert missing.status_code == 404 and missing.json()["detail"]["code"] == "print_template_not_found"


async def test_printing_is_reading_and_nothing_else(tmp_path):
    """§6 : aucun grant d'impression. Qui lit imprime ; qui ne lit pas n'imprime pas."""
    async with boot_host(tmp_path, inline_plugins=plugins()) as h:
        ada = h.token(sub="ada", permissions=GRANTS)
        name = await bill(h, ada)
        reader = h.token(sub="rita", permissions=["shop:bill:read"])  # lecture SEULE : il imprime
        assert (await h.http("GET", f"/print/Bill/{name}", token=reader)).status_code == 200
        nobody = h.token(sub="sam", permissions=[])
        assert (await h.http("GET", f"/print/Bill/{name}", token=nobody)).status_code == 404
        assert (await h.http("GET", "/print-templates/Bill", token=nobody)).status_code == 403
        assert (await h.http("GET", f"/print/Bill/{name}", token=None)).status_code == 401  # aucune identité


async def test_the_pdf_route_answers_bytes_or_names_the_missing_package(tmp_path):
    async with boot_host(tmp_path, inline_plugins=plugins()) as h:
        from xcore_doctype import pdf as engine

        ada = h.token(sub="ada", permissions=GRANTS)
        name = await bill(h, ada)
        r = await h.http("GET", f"/print/Bill/{name}/pdf", token=ada)
        if engine.engine_available():
            assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
            assert r.content.startswith(b"%PDF") and "attachment" in r.headers["content-disposition"]
            assert r.headers["cache-control"] == "private, no-store" and r.headers["x-content-type-options"] == "nosniff"
        else:
            assert r.status_code == 503 and r.json()["detail"]["code"] == "pdf_engine_missing"
            assert "plutoprint" in r.json()["detail"]["msg"]


async def test_the_ipc_actions_mirror_the_routes(tmp_path):
    async with boot_host(tmp_path, inline_plugins=plugins()) as h:
        from xcore_doctype import pdf as engine

        ada = h.token(sub="ada", permissions=GRANTS)
        name = await bill(h, ada)
        listed = await h.action("doc.print.templates", {"doctype": "Bill"}, token=ada)
        assert [t["name"] for t in listed["templates"]] == ["duplicata", "standard"]
        shown = await h.action("doc.print", {"doctype": "Bill", "name": name}, token=ada)
        assert "Durand" in shown["html"]
        if engine.engine_available():
            # L'IPC transporte du JSON : le PDF voyage en base64, jamais en octets bruts.
            made = await h.action("doc.print.pdf", {"doctype": "Bill", "name": name}, token=ada)
            content = base64.b64decode(made["pdf_base64"])
            assert content.startswith(b"%PDF") and made["size"] == len(content)
            assert made["filename"].endswith(".pdf") and made["content_type"] == "application/pdf"
