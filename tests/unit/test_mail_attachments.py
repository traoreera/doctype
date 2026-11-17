"""`ext.email` (xmailler) : une pièce jointe PART avec son message (traoreera/doctype#82).

`EmailMessage` déclarait `attachments` et rien ne les lisait : ni `EmailService.send` (aucun paramètre) ni `smtp.send_message` (un
`alternative` texte + HTML, jamais plus). L'ERP refusait donc d'envoyer une facture par courriel plutôt que d'envoyer un message vide.
Ce que ces tests défendent :
- sans pièce jointe, le message reste un `alternative` strict (pas de `mixed` superflu) ;
- avec, une enveloppe `mixed` porte le corps puis chaque pièce, octet pour octet, sous son nom et son type ;
- `send`, `send_template`, `send_bulk` et `queue` transportent les pièces de la même façon ;
- une pièce invalide ou trop lourde est refusée AVANT tout envoi et toute mise en file ;
- la file de réessai lit des réglages que la configuration déclare (elle les lisait sans qu'ils existent).

Le correctif vit dans `vendor/adaptations/xmailler` (posé par `scripts/sync_vendor.py` sur la copie de l'amont) tant que l'amont
`xcore-team/xmailler` ne l'a pas repris : le dernier test échoue dès que l'amont bouge sous cette adaptation.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib
from email import message_from_bytes
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if not (ROOT / "extensions" / "xmailler" / "main.py").is_file():  # un clone sans `make vendor-sync`
    pytest.skip("extensions/xmailler absent : lancez `make vendor-sync` (sous-module vendor/xmailler)", allow_module_level=True)

config = importlib.import_module("extensions.xmailler.config")
smtp = importlib.import_module("extensions.xmailler.smtp")
main = importlib.import_module("extensions.xmailler.main")

PDF = b"%PDF-1.7\n" + bytes(range(256)) * 4
INVOICE = {"filename": "SI-2026-00001.pdf", "content": PDF, "mimetype": "application/pdf"}
CFG = config.EmailConfig(smtp_host="smtp.acme.test", from_address="compta@acme.test", from_name="Acme")


def parsed(msg):
    """Le message tel qu'un client de messagerie le relit : sérialisé, puis analysé."""
    return message_from_bytes(smtp.build_mime(CFG, msg).as_bytes())


class TestTheMimeMessage:
    def test_without_attachment_it_stays_a_strict_alternative(self):
        mime = parsed(config.EmailMessage(to="a@b.test", subject="Bonjour", body="<p>Salut</p>", is_html=True))
        assert mime.get_content_type() == "multipart/alternative"
        assert [p.get_content_type() for p in mime.get_payload()] == ["text/plain", "text/html"]

    def test_with_one_it_is_a_mixed_envelope_around_the_body(self):
        mime = parsed(config.EmailMessage(to="a@b.test", subject="Votre facture", body="<p>Ci-joint</p>", is_html=True, attachments=[INVOICE]))
        assert mime.get_content_type() == "multipart/mixed"
        body, attached = mime.get_payload()
        assert body.get_content_type() == "multipart/alternative"
        assert [p.get_content_type() for p in body.get_payload()] == ["text/plain", "text/html"]
        assert attached.get_content_type() == "application/pdf"
        assert attached.get_content_disposition() == "attachment" and attached.get_filename() == "SI-2026-00001.pdf"
        assert attached.get_payload(decode=True) == PDF  # octet pour octet
        assert mime["To"] == "a@b.test" and mime["Subject"] == "Votre facture" and "compta@acme.test" in mime["From"]

    def test_several_attachments_keep_their_order_names_and_types(self):
        files = [INVOICE, {"filename": "Reçu n°2.txt", "content": b"pay\xc3\xa9", "mimetype": "text/plain"}, {"filename": "x.bin", "content": b"\x00\x01"}]
        clean = config.check_attachments(files, 10_000)
        mime = parsed(config.EmailMessage(to="a@b.test", subject="s", body="b", attachments=clean))
        got = mime.get_payload()[1:]
        assert [p.get_filename() for p in got] == ["SI-2026-00001.pdf", "Reçu n°2.txt", "x.bin"]  # un nom accentué survit à l'en-tête
        assert [p.get_content_type() for p in got] == ["application/pdf", "text/plain", "application/octet-stream"]
        assert [p.get_payload(decode=True) for p in got] == [PDF, b"pay\xc3\xa9", b"\x00\x01"]

    def test_a_filename_cannot_inject_a_header(self):
        bad = {"filename": "a.pdf\r\nBcc: voleur@evil.test", "content": b"x", "mimetype": "application/pdf"}
        with pytest.raises(ValueError, match="pièce jointe"):
            smtp.build_mime(CFG, config.EmailMessage(to="a@b.test", subject="s", body="b", attachments=[bad]))


class TestAttachmentsAreCheckedBeforeAnything:
    @pytest.mark.parametrize("files, why", [
        ("facture.pdf", "une liste"),
        (["facture.pdf"], "un objet"),
        ([{"content": b"x"}], "nom de fichier invalide"),
        ([{"filename": "../etc/passwd", "content": b"x"}], "nom de fichier invalide"),
        ([{"filename": "a\nb.pdf", "content": b"x"}], "nom de fichier invalide"),
        ([{"filename": "a.pdf", "content": "du texte"}], "octets"),
        ([{"filename": "a.pdf", "content": "/tmp/a.pdf"}], "octets"),  # jamais un chemin : le service ne lit rien sur le disque
        ([{"filename": "a.pdf", "content": b"x", "mimetype": "pdf"}], "type MIME invalide"),
        ([{"filename": "a.pdf", "content": b"x", "mimetype": "text/plain\r\nX: y"}], "type MIME invalide"),
    ])
    def test_a_malformed_attachment_is_named_and_refused(self, files, why):
        with pytest.raises(ValueError, match=why):
            config.check_attachments(files, 1000)

    def test_the_total_weight_is_bounded(self):
        two = [{"filename": "a.bin", "content": b"x" * 600}, {"filename": "b.bin", "content": b"x" * 600}]
        assert len(config.check_attachments(two, 1200)) == 2
        with pytest.raises(ValueError, match="trop lourdes : 1200 octets, 1199 au plus"):
            config.check_attachments(two, 1199)

    def test_nothing_is_nothing(self):
        assert config.check_attachments(None, 10) == [] and config.check_attachments([], 0) == []

    def test_the_bound_is_a_setting(self):
        assert config.EmailConfig.from_dict({}).max_attachment_bytes == 10 * 1024 * 1024
        assert config.EmailConfig.from_dict({"max_attachment_bytes": 2048}).max_attachment_bytes == 2048


@pytest.fixture
def service(monkeypatch):
    """Le service, avec un SMTP qui retient ce qu'on lui confie au lieu de l'envoyer."""
    sent: list = []

    async def capture(cfg, msg):
        sent.append(msg)

    monkeypatch.setattr(smtp, "send_message", capture)
    svc = main.EmailService({"smtp_host": "smtp.acme.test", "max_attachment_bytes": 100_000})
    svc._smtp_available = True
    svc._queue = asyncio.Queue(maxsize=5)
    return svc, sent


class TestEveryDoorCarriesTheAttachment:
    async def test_send(self, service):
        svc, sent = service
        assert await svc.send("a@b.test", "Votre facture", "Ci-joint", attachments=[INVOICE]) is True
        assert sent[0].attachments == [INVOICE]
        assert await svc.send("a@b.test", "Sans rien", "Bonjour") is True and sent[1].attachments == []

    async def test_send_template(self, service):
        svc, sent = service
        svc.add_template("facture", "<p>Facture {numero}</p>")
        assert await svc.send_template("a@b.test", "facture", {"numero": "SI-2026-00001"}, attachments=[INVOICE]) is True
        assert sent[0].is_html is True and sent[0].attachments == [INVOICE]

    async def test_send_bulk(self, service):
        svc, sent = service
        out = await svc.send_bulk([{"to": "a@b.test", "subject": "1", "body": "b", "attachments": [INVOICE]}, {"to": "c@d.test", "subject": "2", "body": "b"}])
        assert out == {"sent": 2, "failed": 0, "total": 2}
        assert {m.subject: len(m.attachments) for m in sent} == {"1": 1, "2": 0}

    async def test_queue(self, service):
        svc, _ = service
        assert svc.queue("a@b.test", "Votre facture", "Ci-joint", cc=["c@d.test"], attachments=[INVOICE]) is True
        queued = svc._queue.get_nowait()
        assert queued.attachments == [INVOICE] and queued.cc == ["c@d.test"]

    async def test_a_refused_attachment_sends_and_queues_nothing(self, service):
        svc, sent = service
        heavy = [{"filename": "grand-livre.pdf", "content": b"x" * 100_001, "mimetype": "application/pdf"}]
        with pytest.raises(ValueError, match="trop lourdes"):
            await svc.send("a@b.test", "s", "b", attachments=heavy)
        with pytest.raises(ValueError, match="trop lourdes"):
            svc.queue("a@b.test", "s", "b", attachments=heavy)
        assert sent == [] and svc._queue.empty()

    async def test_the_caller_keeps_its_own_list(self, service):
        """Le message porte une COPIE vérifiée : l'appelant peut réemployer ou vider sa liste sans toucher au message en file."""
        svc, _ = service
        mine = [dict(INVOICE)]
        svc.queue("a@b.test", "s", "b", attachments=mine)
        mine[0]["content"] = b"autre chose"
        assert svc._queue.get_nowait().attachments[0]["content"] == PDF


class TestTheRetryQueueReadsDeclaredSettings:
    def test_the_settings_and_the_counter_exist(self):
        cfg = config.EmailConfig.from_dict({"requeue_delay": 5, "max_requeues": 2})
        assert (cfg.requeue_delay, cfg.max_requeues) == (5, 2)
        assert (config.EmailConfig().requeue_delay, config.EmailConfig().max_requeues) == (30, 6)
        assert config.EmailMessage(to="a@b.test", subject="s", body="b").requeues == 0

    async def test_a_failed_queued_message_is_scheduled_again_with_its_attachment(self, service, monkeypatch):
        svc, _ = service
        svc._cfg.requeue_delay = 0
        msg = config.EmailMessage(to="a@b.test", subject="s", body="b", attachments=[INVOICE])
        svc._schedule_requeue(msg)  # levait AttributeError : `requeue_delay` et `requeues` n'étaient déclarés nulle part
        await asyncio.gather(*svc._retry_tasks)
        again = svc._queue.get_nowait()
        assert again is msg and again.requeues == 1 and again.attachments == [INVOICE]


# L'amont sur lequel l'adaptation a été écrite (vendor/xmailler à 7d1fede). Quand l'amont change l'un de ces fichiers, l'adaptation —
# qui le REMPLACE en entier — cacherait sa nouveauté : ce test le dit, il faut alors reporter le changement (ou retirer l'adaptation
# si l'amont a repris les pièces jointes) et mettre ces empreintes à jour.
UPSTREAM = {
    "config.py": "568cb9c119a5dc316d6e16342503cac7ad25efbe217872a6dad028638963aeb7",
    "main.py": "feb47526759bb7b3c40a2ad00eaa6f4d0a4c74252c8d60f0511788b39959a7cb",
    "smtp.py": "baaa611d6c94dd5e8e5e8dd2715380c9c372d229b72897f9d3f27310591d766e",
}


def test_the_adaptation_still_sits_on_the_upstream_it_was_written_for():
    vendor = ROOT / "vendor" / "xmailler"
    if not (vendor / "main.py").is_file():
        pytest.skip("vendor/xmailler absent (git submodule update --init)")
    moved = [name for name, digest in UPSTREAM.items() if hashlib.sha256((vendor / name).read_bytes()).hexdigest() != digest]
    assert not moved, f"l'amont xmailler a changé {', '.join(moved)} : reportez-le dans vendor/adaptations/xmailler, puis mettez UPSTREAM à jour"
    for name in UPSTREAM:
        assert (ROOT / "extensions" / "xmailler" / name).read_bytes() == (ROOT / "vendor" / "adaptations" / "xmailler" / name).read_bytes(), f"{name} : relancez `make vendor-sync`"
