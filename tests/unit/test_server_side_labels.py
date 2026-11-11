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


async def test_une_rafale_d_impressions_ne_depasse_jamais_le_plafond(monkeypatch) -> None:
    """Dix impressions lancées ensemble n'ouvrent pas dix moteurs de rendu.

    Les faux rendus RESTENT dans le moteur (ils attendent que le test les libère) au lieu de durer un temps fixe : sans cela, le
    test ne constatait que le chevauchement que l'ordonnanceur avait bien voulu produire, et il passait même sans plafond. Ici
    l'égalité est stricte — sans le sémaphore, l'exécuteur en laisse entrer au moins cinq.
    """
    entered, lock, release = 0, threading.Lock(), threading.Event()

    def fake(html: str) -> bytes:
        nonlocal entered
        with lock:
            entered += 1
        release.wait(10)
        return b"%PDF-1.7"

    monkeypatch.setattr(engine, "to_pdf", fake)
    burst = [asyncio.create_task(engine.to_pdf_async("<p>x</p>")) for _ in range(10)]
    try:
        for _ in range(500):  # attendre que le plafond soit atteint
            with lock:
                if entered >= engine.MAX_PARALLEL_RENDERS:
                    break
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.05)  # laisser à un rendu de trop le temps d'entrer, s'il n'y a pas de plafond
        with lock:
            assert entered == engine.MAX_PARALLEL_RENDERS
    finally:
        release.set()
    assert await asyncio.gather(*burst) == [b"%PDF-1.7"] * 10


async def test_une_attente_abandonnee_ne_rend_pas_sa_place_avant_la_fin_du_rendu(monkeypatch) -> None:
    """Annuler un `asyncio.to_thread` n'arrête pas le fil : si la place était rendue à l'abandon, le rendu continuerait HORS du
    plafond, et une rafale de requêtes interrompues (onglets fermés, délais dépassés) saturerait le processus.

    Mesuré par le COMPORTEMENT, pas en lisant le sémaphore : un rendu abandonné puis `MAX_PARALLEL_RENDERS` autres lancés, et il
    ne doit y en avoir que `MAX_PARALLEL_RENDERS` dans le moteur, pas un de plus.
    """
    entered, lock, release = 0, threading.Lock(), threading.Event()

    def fake(html: str) -> bytes:
        nonlocal entered
        with lock:
            entered += 1
        release.wait(10)
        return b"%PDF-1.7"

    monkeypatch.setattr(engine, "to_pdf", fake)
    abandoned = asyncio.create_task(engine.to_pdf_async("<p>x</p>"))
    others: list[asyncio.Task[bytes]] = []
    try:
        for _ in range(500):
            with lock:
                if entered:
                    break
            await asyncio.sleep(0.01)
        abandoned.cancel()
        with pytest.raises(asyncio.CancelledError):
            await abandoned

        others = [asyncio.create_task(engine.to_pdf_async("<p>y</p>")) for _ in range(engine.MAX_PARALLEL_RENDERS)]
        await asyncio.sleep(0.1)
        with lock:
            # Le rendu abandonné tient toujours sa place : les suivants n'en ont que MAX − 1 à se partager.
            assert entered == engine.MAX_PARALLEL_RENDERS
    finally:
        release.set()
    assert await asyncio.gather(*others) == [b"%PDF-1.7"] * engine.MAX_PARALLEL_RENDERS


async def test_un_semaphore_par_boucle_car_il_se_lie_a_celle_qui_l_attend(monkeypatch) -> None:
    """Un seul sémaphore de processus levait `RuntimeError` dès qu'une seconde boucle s'en servait — ce que font les tests (une
    boucle par fonction) et tout outil qui appelle `asyncio.run` deux fois."""
    monkeypatch.setattr(engine, "to_pdf", lambda html: b"%PDF-1.7")

    def run_once() -> bytes:
        return asyncio.run(engine.to_pdf_async("<p>x</p>"))

    assert await asyncio.to_thread(run_once) == b"%PDF-1.7"
    assert await asyncio.to_thread(run_once) == b"%PDF-1.7"  # une autre boucle : son propre sémaphore
    assert await engine.to_pdf_async("<p>x</p>") == b"%PDF-1.7"  # celle du test, intacte
