"""Le gabarit comme DOCUMENT LIBRE, façon traitement de texte (process/impression.md §12).

Deux étages, comme le reste de l'impression : le PUR (`print_document`, `printing.render_html`) sur des
dictionnaires, et contre une vraie base ce qui touche aux fichiers — une image de gabarit ne s'imprime que si
elle a été déposée POUR un gabarit de ce type.
"""

from __future__ import annotations

import json

import pytest

from tests.unit.test_print_service import ALICE, BOB, STRANGER, env, invoice, png  # noqa: F401 — `env` est une fixture
from xcore_doctype import print_document as D
from xcore_doctype.errors import InvalidDefinition, PermissionDenied, ValidationFailed
from xcore_doctype.printing import parse_template, render_html, template_fields_problems
from xcore_doctype.schema import parse_definition

LINE = parse_definition({
    "schema_version": 1, "name": "Ligne", "istable": True,
    "fields": [{"fieldname": "article", "fieldtype": "Text"}, {"fieldname": "montant", "fieldtype": "Decimal", "decimals": 2}],
})
DEF = parse_definition({
    "schema_version": 1, "name": "Facture", "module": "sales", "label": "Facture de vente",
    "fields": [
        {"fieldname": "client", "fieldtype": "Text"},
        {"fieldname": "secret", "fieldtype": "Password"},
        {"fieldname": "lignes", "fieldtype": "Table", "options": "Ligne"},
        {"fieldname": "total", "fieldtype": "Decimal", "decimals": 2},
    ],
})
TABLE = {"type": "table", "field": "lignes", "columns": [{"field": "article"}, {"field": "montant", "align": "right"}]}
IMG = "a" * 32


def doc(content: str, **extra):
    return parse_template({"schema_version": 1, "doctype": "Facture", "content": content, **extra})


def html(content: str, data=None, images=None) -> str:
    return render_html(doc(content), DEF, data or {"client": "Durand <& fils>", "total": "40.00",
                                                   "lignes": [{"article": "Chaise", "montant": "40.00"}]},
                       name="FA-1", children={"lignes": LINE}, images=images)


def problems_of(content: str) -> list[str]:
    return D.parse(content).problems


class TestTheFormIsOneOrTheOther:
    def test_blocks_or_content_exactly(self):
        with pytest.raises(InvalidDefinition, match="exactement"):
            parse_template({"schema_version": 1, "doctype": "Facture"})
        with pytest.raises(InvalidDefinition, match="exactement"):
            parse_template({"schema_version": 1, "doctype": "Facture", "content": "<p>x</p>", "blocks": [{"type": "spacer"}]})

    def test_a_block_template_still_prints_as_before(self):
        t = parse_template({"schema_version": 1, "doctype": "Facture", "blocks": [{"type": "title", "text": "T"}]})
        out = render_html(t, DEF, {}, name="FA-1")
        assert "<h1>T</h1>" in out and 'class="doc"' not in out


class TestTheClosedSubset:
    """Refusé, jamais nettoyé : un gabarit ne doit pas dire autre chose que ce qu'on a écrit."""

    @pytest.mark.parametrize("bad", [
        "<script>alert(1)</script>",
        '<p onclick="x()">a</p>',
        '<a href="https://evil.example">a</a>',
        '<img src="https://evil.example/x.png">',
        '<img data-file="../../etc/passwd">',
        '<p style="background-image:url(https://evil.example)">a</p>',
        '<p style="position:fixed">a</p>',
        '<span style="font-size:400pt">a</span>',
        '<p style="color:expression(alert(1))">a</p>',
        '<iframe></iframe>',
        "<!-- caché --><p>a</p>",
        '<span data-value="doc.Client">x</span>',
        '<span data-value="actor">x</span>',
        '<div data-block="{}"></div>',
        '<div data-block=\'{"type":"title","text":"x"}\'></div>',
        "<div>du texte libre dans un div</div>",
        '<img data-file="' + IMG + '" style="left:10mm">',  # une position hors du mode « devant le texte »
        '<td colspan="99">a</td>',
    ])
    def test_anything_outside_is_a_fault(self, bad):
        assert problems_of(bad), bad

    def test_the_editor_output_is_accepted(self):
        ok = (
            '<h1 style="text-align: center">Facture <span data-value="name">N°</span></h1>'
            '<p style="text-align: right"><strong>Client :</strong> <span data-value="doc.client">Client</span></p>'
            '<p><span style="color: #1a56db; font-size: 14pt">bleu</span> <em>i</em> <u>u</u> <s>s</s></p>'
            '<table data-borders="none" style="min-width: 50px"><colgroup><col style="min-width: 25px"></colgroup>'
            '<tbody><tr><td colspan="1" rowspan="1" colwidth="120"><p>a</p></td></tr></tbody></table>'
            '<ul><li><p>un</p></li></ul><hr>'
            f'<p><img data-file="{IMG}" data-wrap="front" style="width: 40mm; left: 120mm; top: 5mm"></p>'
            f"<div data-block='{json.dumps(TABLE)}'></div><div data-page-break=\"\"></div>"
        )
        assert problems_of(ok) == []

    def test_every_fault_is_said_not_only_the_first(self):
        assert len(problems_of('<p onclick="x" style="position:fixed"><iframe></iframe></p>')) >= 3

    def test_size_is_bounded(self):
        assert problems_of("<p>" + "a" * D.MAX_CONTENT + "</p>")


class TestTheRenderNeverCopiesTheInput:
    def test_text_is_escaped(self):
        out = html("<p>&lt;b&gt;pas du gras&lt;/b&gt;</p>")
        assert "&lt;b&gt;pas du gras&lt;/b&gt;" in out and "<b>pas" not in out

    def test_a_value_is_the_documents_escaped(self):
        out = html('<p><span data-value="doc.client">Client</span> · <span data-value="name">N</span></p>')
        assert "Durand &lt;&amp; fils&gt;" in out and "FA-1" in out
        assert "Client</span>" not in out  # le libellé de l'éditeur ne s'imprime pas : la valeur le remplace

    def test_styles_are_rebuilt_from_checked_pairs(self):
        out = html('<p style="text-align: CENTER ;  color:#ABCDEF">x</p>')
        assert '<p style="text-align:center;color:#abcdef">x</p>' in out

    def test_an_embedded_table_is_the_existing_renderer(self):
        out = html(f"<div data-block='{json.dumps({**TABLE, 'subtotal': ['montant'], 'group_by': 'article'})}'></div>")
        assert 'class="lines' in out and "Chaise" in out and "40.00" in out

    def test_a_page_break(self):
        assert '<div class="pb"></div>' in html('<p>a</p><div data-page-break=""></div><p>b</p>')

    def test_layout_tables_say_their_borders(self):
        out = html('<table data-borders="none"><tbody><tr><td><p>a</p></td></tr></tbody></table>')
        assert '<table class="lay none">' in out


class TestImages:
    def test_an_image_is_embedded_by_the_caller_never_by_url(self):
        out = html(f'<p><img data-file="{IMG}" data-wrap="right" style="width:30mm"></p>', images={IMG: "data:image/png;base64,AAAA"})
        assert '<img class="im right" src="data:image/png;base64,AAAA" alt="" style="width:30mm">' in out

    def test_an_unresolved_image_is_simply_absent(self):
        out = html(f'<p>a<img data-file="{IMG}">b</p>', images={})
        assert "<img" not in out and "<p>ab</p>" in out

    def test_in_front_of_the_text_is_placed_in_mm_from_the_printable_area(self):
        out = html(f'<p><img data-file="{IMG}" data-wrap="front" style="width:40mm;left:120mm;top:5mm"></p>', images={IMG: "data:image/png;base64,AA"})
        assert 'class="im front"' in out and "left:120mm;top:5mm" in out
        assert out.index('<div class="doc">') < out.index("im front")  # dans le conteneur positionné
        assert "div.doc{position:relative;" in out and "img.im.front{position:absolute" in out

    def test_the_letterhead_is_inside_the_positioned_wrapper(self):
        t = doc("<p>a</p>")
        out = render_html(t, DEF, {}, name="FA-1", letterhead_html='<div class=lh>LH</div>')
        assert out.index('<div class="doc">') < out.index("LH")


class TestLoadTimeRefusals:
    def check(self, content):
        return template_fields_problems(doc(content), DEF, lambda f: LINE if f == "lignes" else None)

    def test_a_value_must_be_a_printable_field(self):
        assert self.check('<span data-value="doc.inconnu">x</span>')
        assert self.check('<span data-value="doc.secret">x</span>')
        assert self.check('<span data-value="doc.lignes">x</span>')
        assert self.check('<span data-value="doc.client">x</span>') == []

    def test_an_embedded_block_is_checked_like_a_block(self):
        bad = {**TABLE, "columns": [{"field": "nope"}]}
        assert self.check(f"<div data-block='{json.dumps(bad)}'></div>")


# ── Contre une vraie base : les images ─────────────────────────────────────


async def _template_with(env, file_id: str) -> dict:
    return {"name": "standard", "content": f'<p><img data-file="{file_id}" data-wrap="inline" style="width:20mm"></p><p>x</p>'}


class TestPrintImages:
    async def test_an_image_uploaded_for_this_type_is_printed(self, env):
        name = await invoice(env)
        up = await env.printer.upload_image(ALICE, "Facture", content=png(), filename="logo.png", content_type="image/png")
        out = await env.printer.preview(ALICE, "Facture", name, await _template_with(env, up["id"]))
        assert "data:image/png;base64," in out["html"]

    async def test_another_file_of_the_tenant_is_never_printed(self, env):
        """L'identifiant vient du gabarit, donc d'un client : un fichier d'un CHAMP (une photo d'un document) ne sort pas."""
        name = await invoice(env)
        # Une pièce jointe d'une facture : le MÊME type, mais pas une image de gabarit (le contrôle du champ, seul).
        other = await env.files.save_attachment(ALICE, "Facture", name, content=png(), filename="x.png", content_type="image/png")
        out = await env.printer.preview(ALICE, "Facture", name, await _template_with(env, other["file_id"]))
        assert "data:image" not in out["html"]

    async def test_an_image_of_another_type_is_never_printed(self, env):
        name = await invoice(env)
        up = await env.files.save_print_image(ALICE, "Letter Head", content=png(), filename="x.png", content_type="image/png")
        out = await env.printer.preview(ALICE, "Facture", name, await _template_with(env, up["id"]))
        assert "data:image" not in out["html"]

    async def test_another_tenants_image_is_never_printed(self, env):
        from tests.conftest import make_ctx

        other = make_ctx("globex", "zoe", permissions=list(ALICE.principal["permissions"]))
        up = await env.printer.upload_image(other, "Facture", content=png(), filename="x.png", content_type="image/png")
        name = await invoice(env)
        out = await env.printer.preview(ALICE, "Facture", name, await _template_with(env, up["id"]))
        assert "data:image" not in out["html"]

    async def test_uploading_needs_the_template_right(self, env):
        with pytest.raises(PermissionDenied):
            await env.printer.upload_image(STRANGER, "Facture", content=png(), filename="x.png", content_type="image/png")
        # Un écrivain PARTIEL (`if_owner`) non plus : le verrou de l'enregistrement du gabarit.
        env.reg.register(parse_definition({
            "schema_version": 1, "name": "Note Privee", "module": "sales",
            "permissions": {"read": ["sales:note_privee:read"], "write": [{"grant": "sales:note_privee:write", "if_owner": True}]},
            "fields": [{"fieldname": "texte", "fieldtype": "Text"}],
        }))
        from tests.conftest import make_ctx

        mine = make_ctx("acme", "alice", permissions=["sales:note_privee:read", "sales:note_privee:write"])
        with pytest.raises(ValidationFailed) as e:
            await env.printer.upload_image(mine, "Note Privee", content=png(), filename="x.png", content_type="image/png")
        assert e.value.code == "print_template_reserved"

    async def test_only_an_image_is_accepted(self, env):
        with pytest.raises(Exception) as e:
            await env.printer.upload_image(ALICE, "Facture", content=b"<svg onload=alert(1)>", filename="x.svg", content_type="image/svg+xml")
        assert getattr(e.value, "code", "") == "file_type_not_allowed"

    async def test_a_reader_of_the_type_sees_the_images_in_the_editor(self, env):
        up = await env.printer.upload_image(ALICE, "Facture", content=png(), filename="logo.png", content_type="image/png")
        assert up["id"] in await env.files.links(BOB, [up["id"]])  # BOB lit le type
        assert up["id"] not in await env.files.links(STRANGER, [up["id"]])

    async def test_a_saved_free_document_prints(self, env):
        name = await invoice(env)
        await env.printer.save_override(ALICE, "Facture", {"name": "standard", "content": '<p>Client : <span data-value="doc.client">c</span></p>'})
        out = await env.printer.render(ALICE, "Facture", name)
        assert "Client : Durand" in out["html"]

    async def test_a_broken_document_is_refused_at_save(self, env):
        with pytest.raises(ValidationFailed):
            await env.printer.save_override(ALICE, "Facture", {"name": "standard", "content": "<script>x</script>"})


STYLE_VECTORS = json.loads((__import__("pathlib").Path(__file__).resolve().parent.parent / "vectors" / "print_style_cases.json").read_text())["cases"]


@pytest.mark.parametrize("case", STYLE_VECTORS, ids=[f"{c['tag']}:{c['style']}" for c in STYLE_VECTORS])
def test_the_style_vectors_shared_with_the_desk(case):
    """Les mêmes cas que `ui/tests/printdoc.spec.ts` : le Desk garde exactement ce que le serveur accepte."""
    faults: list[str] = []
    kept = D._parse_style(case["tag"], case["style"], faults.append)
    assert (not faults) == case["ok"], faults
    assert D._style(kept) == case["kept"]
