"""Le test FUMIGÈNE du moteur PDF (process/impression.md §1, §3, étape 4 de §11).

Tout le reste de l'impression se teste sans binaire : `test_printing.py` sur des dictionnaires,
`test_print_service.py` contre une base. Ce fichier est le SEUL qui demande les paquets optionnels, et il
s'ignore avec une raison quand ils sont absents — c'est le prix de la dépendance optionnelle, et il est
délibéré : sans eux l'aperçu HTML fonctionne et seule la route PDF répond 503.

Il ne vérifie pas « que ça marche » en général mais les **quatre comportements dont la conception dépend**
(§0), parce que chacun pourrait changer à une version près du moteur :

1. le `@page` de NOTRE feuille de style gouverne le format et les marges (sinon `printing.py` ne serait plus
   la seule source de la mise en page, et il faudrait régler la géométrie deux fois) ;
2. l'en-tête d'un tableau se répète à chaque page, par le CSS paginé et non par notre code ;
3. une ressource EXTERNE n'est pas récupérée, et le document se rend quand même sans elle ;
4. une image INCORPORÉE (`data:`) s'imprime malgré ce refus — le verrou et le logo cohabitent.

Et le cinquième, pour `pypdf` : un fond PDF passe SOUS le contenu.

On lance le moteur sur le HTML que notre propre générateur produit, jamais sur un HTML écrit à la main pour
l'occasion : c'est la chaîne complète qui doit tenir, pas ses morceaux.
"""

from __future__ import annotations

import http.server
import io
import threading

import pytest

from xcore_doctype import pdf as engine
from xcore_doctype.printing import footer_markup, letterhead_markup, parse_template, render_html
from xcore_doctype.schema import parse_definition

pytestmark = pytest.mark.skipif(
    not engine.engine_available(),
    reason="plutoprint absent (dépendance OPTIONNELLE) : `poetry install --extras pdf` pour lancer ce fichier",
)

LINE = parse_definition({
    "schema_version": 1, "name": "Ligne", "istable": True,
    "fields": [{"fieldname": "article", "fieldtype": "Text"}, {"fieldname": "entrepot", "fieldtype": "Text"},
               {"fieldname": "qte", "fieldtype": "Decimal", "decimals": 3},
               {"fieldname": "montant", "fieldtype": "Currency", "decimals": 2}],
})
INVOICE = parse_definition({
    "schema_version": 1, "name": "Facture", "module": "ventes", "label": "Facture",
    "fields": [{"fieldname": "client", "fieldtype": "Text"}, {"fieldname": "date_piece", "fieldtype": "Date"},
               {"fieldname": "lignes", "fieldtype": "Table", "options": "Ligne"},
               {"fieldname": "total", "fieldtype": "Currency", "decimals": 2}],
})
CHILDREN = {"lignes": LINE}


# Un PNG 8x8 rouge VALIDE, construit puis encodé (le premier essai, tapé à la main, était refusé par libpng et
# l'image était silencieusement perdue : d'où la vérification par l'XObject du PDF, et non par sa taille).
RED_PNG = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAgAAAAICAIAAABLbSncAAAAEUlEQVR4nGO4"
           "IyeHFTEMLQkAid1GAXiz9RcAAAAASUVORK5CYII=")


def invoice_html(*, lines: int = 3, page: dict | None = None, extra_blocks: list[dict] | None = None, **render) -> str:
    """Le HTML que NOTRE générateur produit pour une facture. C'est lui qu'on imprime, pas une facsimilé."""
    blocks = [
        {"type": "title", "text": "Facture {{ name }}"},
        {"type": "fields", "columns": 2, "fields": ["client", "date_piece"]},
        {"type": "table", "field": "lignes", "style": "bordered", "repeat_header": True,
         "group_by": "entrepot", "subtotal": ["qte", "montant"],
         "columns": [{"field": "article", "width": 45}, {"field": "qte", "align": "right", "width": 15},
                     {"field": "montant", "align": "right", "width": 25, "label": "Montant"}]},
        {"type": "totals", "fields": ["total"]},
    ]
    blocks += extra_blocks or []
    template = parse_template({"schema_version": 1, "doctype": "Facture", "blocks": blocks,
                               **({"page": page} if page else {})})
    rows = [{"article": f"Article {i}", "entrepot": f"Dépôt {i % 3}", "qte": f"{i}.000",
             "montant": f"{i * 15:.2f}"} for i in range(1, lines + 1)]
    data = {"client": "Société Durand & Fils", "date_piece": "2026-10-28", "lignes": rows, "total": "1234.50"}
    return render_html(template, INVOICE, data, name="FA-2026-00001", children=CHILDREN, **render)


def pages(pdf: bytes) -> list[str]:
    """Le texte de chaque page du PDF produit. `pypdf` sert ici d'OUTIL DE MESURE, pas de code testé."""
    pypdf = pytest.importorskip("pypdf", reason="pypdf absent : il lit le PDF pour vérifier ce qu'il contient")
    return [p.extract_text() for p in pypdf.PdfReader(io.BytesIO(pdf)).pages]


def images(pdf: bytes) -> list[tuple[int, int]]:
    """La taille des images INCORPORÉES dans la première page, lues dans le `/XObject` du PDF.

    Par le dictionnaire et non par `page.images`, qui exige Pillow : on ne fait pas entrer une dépendance de
    plus dans l'environnement de test pour mesurer la présence d'une image.
    """
    pypdf = pytest.importorskip("pypdf")
    page = pypdf.PdfReader(io.BytesIO(pdf)).pages[0]
    xobjects = page.get("/Resources", {}).get("/XObject")
    if xobjects is None:
        return []
    return [(int(o["/Width"]), int(o["/Height"]))
            for o in (x.get_object() for x in xobjects.values()) if o.get("/Subtype") == "/Image"]


def mm(pdf: bytes) -> tuple[float, float]:
    pypdf = pytest.importorskip("pypdf")
    box = pypdf.PdfReader(io.BytesIO(pdf)).pages[0].mediabox
    return round(float(box.width) / 72 * 25.4, 1), round(float(box.height) / 72 * 25.4, 1)


class TestItProducesAPdf:
    def test_our_own_html_prints(self):
        pdf = engine.to_pdf(invoice_html())
        assert pdf.startswith(b"%PDF") and len(pdf) > 1000
        text = "".join(pages(pdf))
        assert "FA-2026-00001" in text and "Durand" in text and "Article 1" in text

    def test_the_exact_amounts_reach_the_paper(self):
        """Un montant est une chaîne canonique de bout en bout : aucun flottant ne s'est glissé dans le rendu."""
        text = "".join(pages(engine.to_pdf(invoice_html(lines=2))))
        assert "15.00" in text and "30.00" in text and "1234.50" in text


class TestOurStylesheetGovernsTheGeometry:
    """§0 : si le `@page` du gabarit n'était PAS suivi, il faudrait régler la géométrie une seconde fois côté moteur."""

    @pytest.mark.parametrize("page,expected", [
        ({"size": "A4"}, (210.0, 297.0)),
        ({"size": "A5"}, (148.0, 210.0)),
        ({"size": "A4", "orientation": "landscape"}, (297.0, 210.0)),
        ({"size": "Letter"}, (215.9, 279.4)),
    ])
    def test_the_templates_page_size_is_the_pdfs_page_size(self, page, expected):
        assert mm(engine.to_pdf(invoice_html(page=page))) == expected

    def test_the_margins_are_followed(self):
        """Mesuré par leur EFFET — une marge large tient moins de lignes, donc plus de pages."""
        narrow = len(pages(engine.to_pdf(invoice_html(lines=120, page={"top": 5, "bottom": 5}))))
        wide = len(pages(engine.to_pdf(invoice_html(lines=120, page={"top": 60, "bottom": 60}))))
        assert wide > narrow

    def test_the_reserved_margins_of_a_pdf_background_push_the_content(self):
        """Ce que `reserved_margins_css` promet : `css_extra` passe après la feuille de style, donc il gagne."""
        plain = len(pages(engine.to_pdf(invoice_html(lines=120))))
        reserved = len(pages(engine.to_pdf(invoice_html(lines=120, css_extra="@page{margin-top:80mm;margin-bottom:60mm}"))))
        assert reserved > plain


class TestThePaginatedCss:
    def test_a_table_header_repeats_on_every_page_it_spans(self):
        """Et SEULEMENT sur celles-là : le `page_break` final ouvre une page sans tableau, qui ne doit pas l'avoir."""
        pdf = engine.to_pdf(invoice_html(lines=140, extra_blocks=[
            {"type": "page_break"}, {"type": "title", "text": "Conditions générales"}]))
        text = pages(pdf)
        assert len(text) > 2, "il faut plusieurs pages pour que la question se pose"
        assert "Conditions générales" in text[-1] and "Montant" not in text[-1]
        assert all("Montant" in p for p in text[:-1])

    def test_an_explicit_page_break_opens_a_page_for_what_FOLLOWS_it(self):
        """Et rien d'autre : un saut en DERNIER bloc n'ouvre aucune page (mesuré) — il n'y a rien à y mettre.
        C'est le bon comportement, et il vaut d'être écrit : un gabarit qui finit par `page_break` ne produit
        pas une page blanche à la fin d'une facture."""
        without = pages(engine.to_pdf(invoice_html()))
        trailing = pages(engine.to_pdf(invoice_html(extra_blocks=[{"type": "page_break"}])))
        followed = pages(engine.to_pdf(invoice_html(extra_blocks=[
            {"type": "page_break"}, {"type": "title", "text": "Conditions générales"}])))
        assert len(trailing) == len(without)
        assert len(followed) == len(without) + 1 and "Conditions générales" in followed[-1]

    def test_the_subtotals_and_groups_are_there(self):
        text = "".join(pages(engine.to_pdf(invoice_html(lines=9))))
        assert text.count("Dépôt") >= 3  # trois groupes, dans l'ordre de saisie


class TestTheRendererHasNoNetwork:
    """§6, le point de sécurité central : un moteur qui suit les URL d'un gabarit serait une faille SSRF.

    Et ce n'est PAS une précaution de confort. Mesuré le 28 oct. : le binaire de PlutoPrint embarque libcurl et
    toute sa pile TLS (openssl, krb5, nghttp2, libpsl…), et SANS notre récupérateur il effectue réellement la
    requête — un serveur local la reçoit. Le refus au CHARGEMENT d'un gabarit est un contrôle statique sur ce
    que le gabarit DÉCLARE ; le récupérateur, lui, voit chaque URL que le HTML contient au moment du rendu.
    C'est donc lui la vraie barrière, et c'est pourquoi il est vérifié ici contre un serveur qui compte.
    """

    def test_the_default_fetcher_WOULD_reach_the_network_but_ours_does_not(self):
        """La preuve que le verrou porte : un serveur sur 127.0.0.1 compte ce qui lui arrive.

        Rien ne sort de la machine — on ne sonde jamais un hôte distant pour vérifier une règle de sécurité.
        """
        plutoprint = pytest.importorskip("plutoprint")
        received: list[str] = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 - l'API de http.server
                received.append(self.path)
                self.send_response(200)
                self.send_header("Content-Type", "text/css")
                self.end_headers()
                self.wfile.write(b"body{color:red}")

            def log_message(self, *a):  # le serveur ne doit pas polluer la sortie des tests
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_address[1]}/exfiltration.css"
            html = f'<html><head><link rel=stylesheet href="{url}"></head><body><p>x</p></body></html>'

            # Le moteur NU : il suit l'URL. C'est ce fait qui rend notre récupérateur indispensable.
            book = plutoprint.Book()
            book.load_html(html)
            book.write_to_pdf_stream(io.BytesIO())
            assert received == ["/exfiltration.css"], "le moteur n'a pas suivi l'URL : la mesure a changé, relire §6"

            # Notre chemin : AUCUNE requête, et le document sort quand même.
            received.clear()
            assert engine.to_pdf(html).startswith(b"%PDF")
            assert received == []
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_an_external_resource_is_not_fetched_and_the_document_still_renders(self):
        html = invoice_html().replace("<body>", '<body><img src="https://exemple.invalid/pixel.png">')
        pdf = engine.to_pdf(html)  # notre `fetch_url` lève ; le moteur avertit et continue
        assert pdf.startswith(b"%PDF") and "FA-2026-00001" in "".join(pages(pdf))

    @pytest.mark.parametrize("url", [
        "https://exemple.invalid/feuille.css",
        "http://169.254.169.254/latest/meta-data/",  # l'adresse de métadonnées d'un nuage : la cible classique
        "file:///etc/passwd",
    ])
    def test_nothing_is_followed_whatever_the_scheme(self, url):
        html = invoice_html().replace("<body>", f'<body><img src="{url}">')
        assert engine.to_pdf(html).startswith(b"%PDF")

    def test_an_embedded_image_prints_despite_that_refusal(self):
        """Le fait qui n'était PAS acquis : une URL `data:` n'atteint pas le récupérateur, donc le logo du
        papier à en-tête s'imprime alors que tout le réseau est fermé.

        Vérifié par l'XObject image du PDF, et pas par sa taille : un PNG invalide est perdu SILENCIEUSEMENT
        (PlutoPrint avertit et continue), et le PDF amputé pèse alors le même poids à quelques octets près.
        C'est arrivé en écrivant ce test — la mesure par la taille l'avait laissé passer."""
        with_image = engine.to_pdf(invoice_html(letterhead_html=letterhead_markup(RED_PNG),
                                                footer_html=footer_markup("SARL — RCS Paris 123")))
        assert with_image.startswith(b"%PDF")
        assert "RCS Paris 123" in "".join(pages(with_image))
        assert images(with_image) == [(8, 8)]
        assert images(engine.to_pdf(invoice_html())) == []


class TestTheMissingPackageIsNamed:
    def test_without_plutoprint_it_is_a_503_naming_the_package(self, monkeypatch):
        monkeypatch.setattr(engine, "_module", lambda name: None)
        assert engine.engine_available() is False and engine.overlay_available() is False
        with pytest.raises(engine.PdfEngineMissing) as e:
            engine.to_pdf("<p>x</p>")
        assert (e.value.code, e.value.status) == ("pdf_engine_missing", 503) and "plutoprint" in e.value.message
        with pytest.raises(engine.PdfEngineMissing, match="pypdf"):
            engine.overlay(b"%PDF", b"%PDF", every_page=True)


class TestThePdfBackground:
    """§5 : l'entreprise fournit le PDF de son papier à en-tête, nos blocs s'impriment PAR-DESSUS."""

    def setup_method(self):
        if not engine.overlay_available():
            pytest.skip("pypdf absent (dépendance OPTIONNELLE du fond PDF) : `poetry install --extras pdf`")

    def background(self) -> bytes:
        """Un faux papier à en-tête : du texte qu'on retrouvera sous le contenu."""
        return engine.to_pdf("<html><body><h1>PAPIER À EN-TÊTE</h1></body></html>")

    def test_the_background_is_under_the_content_on_every_page(self):
        content = engine.to_pdf(invoice_html(lines=90))
        merged = engine.overlay(content, self.background(), every_page=True)
        text = pages(merged)
        assert len(text) == len(pages(content))  # le fond n'ajoute pas de page
        assert all("PAPIER À EN-TÊTE" in p for p in text)
        assert "FA-2026-00001" in text[0]

    def test_first_page_only_leaves_the_others_untouched(self):
        content = engine.to_pdf(invoice_html(lines=90))
        text = pages(engine.overlay(content, self.background(), every_page=False))
        assert len(text) > 1 and "PAPIER À EN-TÊTE" in text[0]
        assert not any("PAPIER À EN-TÊTE" in p for p in text[1:])

    def test_a_multi_page_background_aligns_then_repeats_its_last_page(self):
        """La règle choisie : page à page tant qu'il en reste, puis la dernière se répète. Le cas réel est une
        page unique, et cette règle la couvre sans cas particulier."""
        two = engine.to_pdf("<html><body><p>FOND UN</p><p style='page-break-before:always'>FOND DEUX</p></body></html>")
        text = pages(engine.overlay(engine.to_pdf(invoice_html(lines=90)), two, every_page=True))
        assert "FOND UN" in text[0] and all("FOND DEUX" in p for p in text[1:])

    def test_an_empty_background_changes_nothing(self):
        content = engine.to_pdf(invoice_html())
        assert engine.overlay(content, b"", every_page=True) == content


class TestAFreeDocumentPlacesItsImagesExactly:
    """Le document libre (§12) : une image « devant le texte » sort là où l'éditeur l'a posée, au millimètre.

    Mesuré sur la matrice `cm` qui précède le `Do` de l'image dans le flux de la page : PlutoPrint écrit dans un
    repère retourné (origine en haut à gauche, en points). C'est cette mesure qui a trouvé le décalage de 4,2 mm
    — la marge du premier paragraphe qui traversait le conteneur — corrigé par le `padding-top` de la feuille.
    """

    def placed(self, content: str, page: dict) -> tuple[float, float, float]:
        import re

        pypdf = pytest.importorskip("pypdf")
        template = parse_template({"schema_version": 1, "doctype": "Facture", "content": content, "page": page})
        html = render_html(template, INVOICE, {"client": "Durand"}, name="FA-1", images={"a" * 32: RED_PNG})
        stream = pypdf.PdfReader(io.BytesIO(engine.to_pdf(html))).pages[0].get_contents().get_data().decode("latin1")
        found = re.search(r"([-\d.]+) 0 0 -([-\d.]+) ([-\d.]+) ([-\d.]+) cm\s*/\w+ gs /\w+ Do", stream)
        assert found, "aucune image placée dans la page"
        width, height, x, bottom = (float(g) for g in found.groups())
        pt = 25.4 / 72
        return round(x * pt, 1), round((bottom - height) * pt, 1), round(width * pt, 1)

    def test_in_front_of_the_text_lands_where_it_was_put(self):
        page = {"size": "A4", "top": 20, "bottom": 20, "left": 15, "right": 15}
        image = f'<img data-file="{"a" * 32}" data-wrap="front" style="width:40mm;left:120mm;top:30mm">'
        # Derrière un titre ET un paragraphe : leurs marges ne doivent rien changer à la position.
        x, y, w = self.placed(f"<h2>Titre</h2><p>Client {image}</p>", page)
        assert (x, y, w) == (135.0, 50.0, 40.0)  # 15 + 120, 20 + 30
