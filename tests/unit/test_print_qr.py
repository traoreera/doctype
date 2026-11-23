"""Le bloc d'impression `qr` : la valeur d'un champ texte imprimée en QR code (`qrcodes.py`).

Le cas qui l'a demandé : la facture certifiée par le boîtier MCF/SECeF de la DGI (Burkina Faso), qui porte un QR obligatoire dont
le CONTENU est fourni par le boîtier (`BFSECEF01;…`). Le gabarit ne compose donc rien : il dit quel champ, quelle taille, où.
"""

from __future__ import annotations

import base64
import re
import struct
import zlib

import pytest

from xcore_doctype import qrcodes
from xcore_doctype.errors import InvalidDefinition
from xcore_doctype.printing import parse_template, render_html, template_fields_problems
from xcore_doctype.schema import parse_definition

INVOICE = parse_definition({
    "schema_version": 1, "name": "Facture", "module": "ventes",
    "fields": [{"fieldname": "client", "fieldtype": "Text"}, {"fieldname": "qr_dgi", "fieldtype": "LongText"},
               {"fieldname": "total", "fieldtype": "Currency", "decimals": 2}],
})
SECEF = "BFSECEF01;DT02200401;FV;20261123120000;SECEF-0001;118000"


def qr_template(**block):
    return parse_template({"schema_version": 1, "doctype": "Facture",
                           "blocks": [{"type": "title", "text": "Facture {{ name }}"}, {"type": "qr", "field": "qr_dgi", **block}]})


def decode_png(uri: str) -> tuple[int, list[bytes]]:
    """Relit le PNG que le moteur écrit : (côté en pixels, lignes de pixels). Un PNG faux serait perdu SANS BRUIT par le moteur PDF."""
    raw = base64.b64decode(uri.removeprefix("data:image/png;base64,"))
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"
    pos, chunks = 8, {}
    while pos < len(raw):
        (length,) = struct.unpack(">I", raw[pos:pos + 4])
        kind, payload = raw[pos + 4:pos + 8], raw[pos + 8:pos + 8 + length]
        (crc,) = struct.unpack(">I", raw[pos + 8 + length:pos + 12 + length])
        assert crc == zlib.crc32(kind + payload) & 0xFFFFFFFF, kind
        chunks[kind] = payload
        pos += 12 + length
    width, height, depth, color = struct.unpack(">IIBB", chunks[b"IHDR"][:10])
    assert (width, depth, color) == (height, 8, 0)
    pixels = zlib.decompress(chunks[b"IDAT"])
    rows = [pixels[i * (width + 1):(i + 1) * (width + 1)] for i in range(height)]
    assert all(r[0] == 0 for r in rows)  # filtre « aucun » sur chaque ligne
    return width, [r[1:] for r in rows]




class TestTheImageIsTheMatrix:
    def test_each_pixel_is_its_module(self):
        modules = qrcodes.matrix(SECEF)
        side, rows = decode_png(qrcodes.data_uri(SECEF))
        assert side == len(modules) * qrcodes.SCALE
        for y, row in enumerate(modules):
            for x, dark in enumerate(row):
                assert rows[y * qrcodes.SCALE][x * qrcodes.SCALE] == (0 if dark else 255)

    def test_it_has_the_quiet_zone_and_the_three_finder_patterns(self):
        m = qrcodes.matrix(SECEF)
        b, n = qrcodes.BORDER, len(qrcodes.matrix(SECEF)) - 2 * qrcodes.BORDER
        assert not any(m[0]) and not any(r[0] for r in m)  # zone calme
        finder = [[(y in (0, 6) or x in (0, 6) or (2 <= x <= 4 and 2 <= y <= 4)) for x in range(7)] for y in range(7)]
        for oy, ox in ((b, b), (b, b + n - 7), (b + n - 7, b)):
            assert [row[ox:ox + 7] for row in m[oy:oy + 7]] == finder

    def test_it_is_error_correction_m_as_the_dgi_asks(self):
        # Même texte, même version : M (15 %) donne une matrice plus grande que L et plus petite que H.
        import qrcode
        from qrcode.constants import ERROR_CORRECT_M

        qr = qrcode.QRCode(error_correction=ERROR_CORRECT_M, border=qrcodes.BORDER)
        qr.add_data(SECEF)
        qr.make(fit=True)
        assert qrcodes.matrix(SECEF) == [[bool(c) for c in r] for r in qr.get_matrix()]

    def test_a_text_too_long_for_a_readable_qr_is_refused(self):
        with pytest.raises(ValueError):
            qrcodes.data_uri("x" * (qrcodes.MAX_QR_TEXT + 1))


class TestTheBlock:
    def test_it_prints_the_fields_value_at_its_size(self):
        html = render_html(qr_template(size=25, align="right", label="Code SECeF"), INVOICE, {"qr_dgi": SECEF}, name="FA-1")
        img = re.search(r'<div class="qr" style="text-align:right"><img src="(data:image/png;base64,[^"]+)" alt="QR" '
                        r'style="width:25mm;height:25mm"><div class="qr-label">Code SECeF</div></div>', html)
        assert img is not None
        assert img.group(1) == qrcodes.data_uri(SECEF)

    def test_an_empty_field_prints_nothing(self):
        """Une facture pas encore certifiée : pas de QR, et surtout pas un QR vide qu'un contrôleur scannerait."""
        for data in ({}, {"qr_dgi": ""}, {"qr_dgi": "   "}):
            assert "<img" not in render_html(qr_template(), INVOICE, data, name="FA-1")

    def test_the_label_is_escaped(self):
        html = render_html(qr_template(label="<b>x</b>"), INVOICE, {"qr_dgi": SECEF}, name="FA-1")
        assert "&lt;b&gt;x&lt;/b&gt;" in html and "<b>x" not in html

    def test_it_is_refused_on_a_field_that_is_not_text(self):
        problems = template_fields_problems(parse_template({"schema_version": 1, "doctype": "Facture",
                                                            "blocks": [{"type": "qr", "field": "total"}]}), INVOICE, lambda _: None)
        assert any("total" in p and "Currency" in p for p in problems)
        assert template_fields_problems(qr_template(), INVOICE, lambda _: None) == []

    @pytest.mark.parametrize("block", [{"size": 10}, {"size": 81}, {"align": "justify"}, {"text": "BFSECEF01;…"}])
    def test_its_vocabulary_is_closed(self, block):
        with pytest.raises(InvalidDefinition):
            qr_template(**block)

    def test_a_free_document_embeds_it(self):
        """Un gabarit libre (`content`, celui d'erpprint et de l'éditeur « Personnaliser l'impression ») l'incorpore comme un
        tableau ou des totaux."""
        free = parse_template({"schema_version": 1, "doctype": "Facture",
                               "content": '<p>Facture certifiée</p><div data-block=\'{"type": "qr", "field": "qr_dgi", "size": 20}\'></div>'})
        assert template_fields_problems(free, INVOICE, lambda _: None) == []
        html = render_html(free, INVOICE, {"qr_dgi": SECEF}, name="FA-1")
        assert qrcodes.data_uri(SECEF) in html and "width:20mm" in html
