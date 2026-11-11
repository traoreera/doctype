"""Deux services que le moteur devait rendre et ne rendait pas : traduire côté SERVEUR, et imprimer sans bloquer la boucle.

- `doctype_registry.translate` / `.doctype_label` (traoreera/doctype#68) : le catalogue chargé ne servait qu'au Desk. Un plugin qui
  écrit un libellé dans un état imprimé relisait les `translations/*.yaml` lui-même (`erp/schema.py::label_fr`, avec le format du
  catalogue, les clés de contexte et la langue codés en dur). Un seul lecteur, les mêmes règles que `/meta`.
- `pdf.to_pdf_async` (traoreera/doctype#65) : `to_pdf` calcule en C pendant des secondes sur un document long. Appelé dans une route
  `async`, il faisait attendre toutes les requêtes des autres utilisateurs ; chaque appelant l'enveloppait lui-même dans un thread
  (et le livre de paie de l'ERP avait oublié de le faire). Le rendu part maintenant dans un thread, et au plus
  `MAX_PARALLEL_RENDERS` à la fois : une rafale fait la queue au lieu d'ouvrir un moteur de rendu par requête.
"""

from __future__ import annotations

import asyncio
import threading

import pytest

from xcore_doctype import pdf as engine
from xcore_doctype.registry import DocTypeChannel, DocTypeRegistry
from xcore_doctype.schema import parse_definition
from xcore_doctype.translations import Translations

INVOICE = {
    "schema_version": 1, "name": "Sales Invoice", "module": "accounts", "label": "Sales Invoice",
    "fields": [{"fieldname": "title", "fieldtype": "Text", "label": "Title", "required": True}],
}
FR = """
messages:
  Sales Invoice: Facture de vente
  Service: Prestation
context:
  Sales Invoice|Title: Objet
"""


@pytest.fixture
def channel(tmp_path) -> DocTypeChannel:
    (tmp_path / "fr.yaml").write_text(FR, encoding="utf-8")
    registry = DocTypeRegistry()
    registry.register(parse_definition(INVOICE))
    ch = DocTypeChannel(registry, translations=Translations())
    ch.load_translations("accounts", tmp_path)
    return ch


def test_un_plugin_traduit_un_libelle_par_le_catalogue_charge(channel: DocTypeChannel) -> None:
    assert channel.translate("Sales Invoice", lang="fr") == "Facture de vente"
    assert channel.translate("Service", lang="fr") == "Prestation"


def test_une_cle_de_contexte_gagne_sur_le_texte_seul(channel: DocTypeChannel) -> None:
    assert channel.translate("Title", lang="fr", context="Sales Invoice") == "Objet"
    assert channel.translate("Title", lang="fr") == "Title"  # hors contexte : inconnu, donc tel quel


def test_un_texte_sans_traduction_sort_tel_quel_jamais_vide(channel: DocTypeChannel) -> None:
    assert channel.translate("Credit Note", lang="fr") == "Credit Note"
    assert channel.translate("Sales Invoice", lang="es") == "Sales Invoice"  # langue sans catalogue
    assert channel.translate(None, lang="fr") is None  # autre chose qu'un texte : inchangé


def test_le_libelle_d_un_doctype_est_celui_qu_un_etat_imprime(channel: DocTypeChannel) -> None:
    """La NATURE d'une pièce dans un grand livre : « Facture de vente », pas « Sales Invoice »."""
    assert channel.doctype_label("Sales Invoice", lang="fr") == "Facture de vente"
    assert channel.doctype_label("Sales Invoice", lang="en") == "Sales Invoice"
    assert channel.doctype_label("Inconnu", lang="fr") == "Inconnu"  # un type non chargé : son nom


def test_les_langues_chargees_se_listent(channel: DocTypeChannel) -> None:
    assert channel.languages() == ["fr"]


# ── Le PDF hors de la boucle ───────────────────────────────────────────────────────────────────


async def test_le_rendu_pdf_quitte_la_boucle_d_evenements(monkeypatch) -> None:
    """Le rendu ne doit PAS s'exécuter sur le fil de la boucle : c'est tout l'objet du ticket."""
    where: list[str] = []

    def fake(html: str) -> bytes:
        where.append(threading.current_thread().name)
        return b"%PDF-1.7 " + html.encode()

    monkeypatch.setattr(engine, "to_pdf", fake)
    out = await engine.to_pdf_async("<p>x</p>")
    assert out.startswith(b"%PDF-1.7")
    assert where and where[0] != threading.main_thread().name


async def test_une_rafale_d_impressions_fait_la_queue(monkeypatch) -> None:
    """Au plus `MAX_PARALLEL_RENDERS` rendus à la fois : dix impressions lancées ensemble n'ouvrent pas dix moteurs de rendu."""
    monkeypatch.setattr(engine, "_slots", None)  # un sémaphore par boucle : celui d'un autre test ne vaut rien ici
    live, peak, lock = 0, 0, threading.Lock()

    def fake(html: str) -> bytes:
        nonlocal live, peak
        with lock:
            live += 1
            peak = max(peak, live)
        threading.Event().wait(0.02)
        with lock:
            live -= 1
        return b"%PDF-1.7"

    monkeypatch.setattr(engine, "to_pdf", fake)
    await asyncio.gather(*(engine.to_pdf_async("<p>x</p>") for _ in range(10)))
    assert 0 < peak <= engine.MAX_PARALLEL_RENDERS
