"""Les plugins de la PLATEFORME livrés par le paquet (`xcore_doctype/platform/` : xlicense, xpulse) se chargent sur un vrai hôte, à côté
du moteur, avec les extensions de service livrées elles aussi (`xcore_doctype.ext.*` : pubsub, xstorage, xwebsocket, xpay).

Sans Redis ni Stripe (`hermetic_services` met pubsub en mémoire ; xpay sans clés reste indisponible, sans empêcher le reste).
"""

from __future__ import annotations

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from xcore_doctype import platform_plugins_dir
from tests.integration.conftest import boot_host

PLATFORM = {"xlicense": platform_plugins_dir() / "xlicense", "xpulse": platform_plugins_dir() / "xpulse"}


def keys(tmp_path, monkeypatch):
    """La paire RSA des jetons de licence (sans elle, xlicense démarre DÉGRADÉ : ses routes en 503, ses écrans non montés)."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv, pub = tmp_path / "private.pem", tmp_path / "public.pem"
    priv.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    pub.write_bytes(key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
    monkeypatch.setenv("XLICENSE_PRIVATE_KEY_PATH", str(priv))
    monkeypatch.setenv("XLICENSE_PUBLIC_KEY_PATH", str(pub))


async def test_the_platform_plugins_load_beside_the_engine(tmp_path, monkeypatch):
    keys(tmp_path, monkeypatch)
    async with boot_host(tmp_path, operator_doctypes=False, link_plugins=PLATFORM) as h:
        t = h.token(permissions=["xlicense:module:read", "xlicense:tenant_module:read", "xlicense:license:read"])
        r = await h.http("GET", "/meta", token=t)
        assert r.status_code == 200, r.text
        names = {m["name"] for m in r.json()}
        assert {"Module", "Tenant Module", "License"} <= names  # les DocTypes d'xlicense, servis par le moteur
        h.app.openapi_schema = None
        paths = set(h.app.openapi()["paths"])
        assert any(p.startswith("/app/xlicense/") for p in paths), sorted(p for p in paths if "license" in p)
        assert any(p.startswith("/app/xpulse/") for p in paths), sorted(p for p in paths if "pulse" in p)
        assert any(e["plugin"] == "xlicense" for e in h.rbac_declared)


async def test_a_write_signals_its_type_on_the_organisation_channel_xpulse_serves(tmp_path, monkeypatch):
    """Bout à bout sur un vrai hôte : une écriture par l'API publie `doc.changed` sur le canal `tenant-<id>` d'`ext.pubsub` (celui
    qu'xpulse relaie en SSE à ses seuls membres), avec le TYPE seulement ; le canal d'une autre organisation ne reçoit rien."""
    import asyncio
    import json

    keys(tmp_path, monkeypatch)
    async with boot_host(tmp_path, link_plugins=PLATFORM) as h:
        pubsub = h.xcore.services.get("ext.pubsub")

        async def first(channel):
            async for chunk in pubsub.stream([channel], user_id="-", unfiltered_channels={channel}):
                return json.loads(chunk.split("data: ", 1)[1])

        mine, theirs = asyncio.create_task(first("tenant-acme")), asyncio.create_task(first("tenant-globex"))
        await asyncio.sleep(0.05)  # les deux abonnements sont posés avant l'écriture
        r = await h.http("POST", "/doc/Customer", token=h.token(), json={"data": {"customer_name": "Secrète SA", "type": "Particulier"}})
        assert r.status_code == 201, r.text
        signal = await asyncio.wait_for(mine, 5)
        assert signal == {"event_type": "doc.changed", "tenant_id": "acme", "doctype": "Customer"}  # ni nom ni valeur
        await asyncio.sleep(0.1)
        assert not theirs.done()
        theirs.cancel()
