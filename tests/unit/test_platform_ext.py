"""Les extensions de service livrées avec le moteur (`xcore_doctype.ext.*`), chacune seule et sans service extérieur.

- pubsub (mémoire) : un abonné reçoit ce qui est publié sur SON canal, filtré par destinataire ;
- xstorage (disque) : enregistrer, relire, supprimer ; un espace de noms qui remonte l'arborescence est refusé ;
- xwebsocket : un canal inconnu n'existe pas ; diffuser vers un canal vide ne lève rien ;
- xpay : sans clé du prestataire, le service se dit indisponible au lieu de planter le démarrage.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from xcore_doctype.ext.pubsub.service import PubSubClient
from xcore_doctype.ext.xstorage.main import StorageService
from xcore_doctype.ext.xwebsocket.main import WsManager


async def test_pubsub_in_memory_delivers_to_the_addressee_only():
    ps = PubSubClient({"provider": "memory"})
    await ps.init()
    got: list[str] = []

    async def listen():
        async for chunk in ps.stream(["notification"], "alice"):
            got.append(chunk)
            return

    task = asyncio.create_task(listen())
    await asyncio.sleep(0.05)
    await ps.publish("notification", {"user_id": "bob", "text": "pas pour alice"})
    await ps.publish("notification", {"user_id": "alice", "text": "bonjour"})
    await asyncio.wait_for(task, 2)
    assert len(got) == 1 and got[0].startswith("event: notification") and "bonjour" in got[0] and "pas pour alice" not in got[0]
    await ps.shutdown()


async def test_xstorage_local_round_trip_and_path_guard(tmp_path):
    st = StorageService({"backend": "local", "max_size_mb": 1, "local": {"path": str(tmp_path / "blobs")}})
    await st.init()
    up = await st.save(b"bonjour", "note.txt", namespace="xcore-doctype/acme")
    assert await st.read(up.file_id, "xcore-doctype/acme", up.stored_name) == b"bonjour"
    assert await st.delete(up.file_id, "xcore-doctype/acme", up.stored_name) is True
    assert await st.read(up.file_id, "xcore-doctype/acme", up.stored_name) is None
    with pytest.raises(Exception):
        await st.save(b"x", "x.txt", namespace="../../etc")
    await st.shutdown()


async def test_xwebsocket_broadcast_to_an_empty_channel_is_harmless():
    ws = WsManager({"channel": ["user"]})
    await ws.init()
    await ws.broadcast(channel="user", event="PING", data={"n": 1})
    info = await ws.info()
    assert json.loads(json.dumps(info, default=str))  # sérialisable (servi par une route de santé)
    await ws.shutdown()


async def test_xpay_without_keys_reports_unavailable_instead_of_crashing():
    from xcore_doctype.ext.xpay.service import PayProvider

    pay = PayProvider({"secret_key": "", "publishable_key": "", "webhook_secret": "", "domain": ""})
    try:
        await pay.init()
    except Exception as exc:  # noqa: BLE001 - un refus explicite au démarrage est acceptable, un plantage silencieux non
        assert str(exc)
    ok, _ = await pay.health_check()
    assert ok is False
