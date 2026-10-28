"""Le moteur PDF : la couche MINCE au-dessus de PlutoPrint et de pypdf (process/impression.md §1, §3).

Tout ce qui décide est dans `printing.py`, qui est pur. Ici il n'y a que deux gestes — rendre une chaîne HTML en
PDF, et superposer un fond — et un refus : **le rendu n'accède pas au réseau**.

Les deux paquets sont OPTIONNELS. Absents, l'aperçu HTML continue de fonctionner et seule la route PDF répond
`503 pdf_engine_missing` en nommant le paquet qui manque. C'est ce qui rend l'engagement réversible : rien
d'autre dans le moteur ne dépend de PlutoPrint.

Mesuré le 28 oct. 2026 sur plutoprint 0.23.0 / pypdf 6.21.3, et deux de ces faits comptent pour la conception :

- `@page{size:…;margin:…}` de notre feuille de style **est** suivi (format de page et marges) : `Book()` n'a
  donc aucun réglage de géométrie, et `printing.py` reste la SEULE source de la mise en page. Attention,
  `Book.get_page_size()` rend le défaut du livre et non la taille résolue — c'est `get_page_size_at(0)` et la
  `mediabox` du PDF qui disent vrai, et c'est sur elles que la mesure a été faite.
- une URL `data:` **ne passe pas** par le récupérateur de ressources : refuser TOUT dans `fetch_url` n'empêche
  donc pas une image incorporée de s'imprimer. Le verrou et l'image cohabitent, ce qui n'était pas acquis.
"""

from __future__ import annotations

import io
from typing import Any

from .errors import DoctypeError

MISSING = "pdf_engine_missing"


class PdfEngineMissing(DoctypeError):
    """Le paquet optionnel n'est pas installé : 503, et on NOMME ce qu'il faut installer."""

    def __init__(self, package: str) -> None:
        super().__init__(MISSING, f"L'impression PDF demande le paquet « {package} » (pip install {package})", status=503)


def engine_available() -> bool:
    """PlutoPrint est-il installé ? (L'aperçu HTML ne le demande pas.)"""
    return _module("plutoprint") is not None


def overlay_available() -> bool:
    """pypdf est-il installé ? Il ne sert QU'au fond PDF d'un papier à en-tête : son absence n'empêche pas les autres gabarits d'imprimer."""
    return _module("pypdf") is not None


def _module(name: str) -> Any:
    try:
        return __import__(name)
    except ImportError:  # pragma: no cover - dépend de l'environnement
        return None


def to_pdf(html: str) -> bytes:
    """Le HTML en PDF. Aucun accès réseau : notre récupérateur de ressources refuse TOUT (process/impression.md §6).

    C'est la ceinture en plus de la bretelle — les URL externes sont déjà refusées au chargement du gabarit. Un
    refus ici n'interrompt pas le rendu : PlutoPrint avertit et rend le document sans la ressource (mesuré), ce
    qui est le bon comportement pour une facture : elle sort, même amputée d'une image.
    """
    plutoprint = _module("plutoprint")
    if plutoprint is None:
        raise PdfEngineMissing("plutoprint")

    class _NoNetwork(plutoprint.ResourceFetcher):
        def fetch_url(self, url: str) -> Any:
            raise RuntimeError(f"ressource externe refusée à l'impression : {url[:200]}")

    book = plutoprint.Book()  # la géométrie vient du `@page` de notre feuille de style, mesuré
    book.custom_resource_fetcher = _NoNetwork()
    book.load_html(html)
    out = io.BytesIO()
    book.write_to_pdf_stream(out)
    return out.getvalue()


def overlay(content: bytes, background: bytes, *, every_page: bool) -> bytes:
    """Superpose `content` SUR `background` (le fond préimprimé de §5), page à page.

    `every_page` : le fond sur toutes les pages ou sur la première seulement. Un fond de plusieurs pages
    s'aligne page à page tant qu'il en reste, puis sa DERNIÈRE page se répète — le cas réel est une page unique,
    et cette règle la couvre sans cas particulier.
    """
    pypdf = _module("pypdf")
    if pypdf is None:
        raise PdfEngineMissing("pypdf")
    front = pypdf.PdfReader(io.BytesIO(content))
    back = pypdf.PdfReader(io.BytesIO(background))
    if not back.pages:
        return content
    writer = pypdf.PdfWriter()
    for i, page in enumerate(front.pages):
        if i and not every_page:
            writer.add_page(page)
            continue
        under = back.pages[min(i, len(back.pages) - 1)]
        merged = pypdf.PageObject.create_blank_page(width=under.mediabox.width, height=under.mediabox.height)
        merged.merge_page(under)  # le fond dessous...
        merged.merge_page(page)  # ...notre contenu dessus
        writer.add_page(merged)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()
