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


async def test_the_desk_s_modules_page_reads_its_catalogue_and_takes_a_free_module(tmp_path, monkeypatch):
    """Ce que l'écran « Mes modules » appelle (`ui/src/lib/xlicense.ts`), sur un vrai hôte : `GET /me/modules` rend le catalogue et
    `can_manage` ; `POST /me/modules/{code}` prend un module gratuit (il apparaît pris) et REFUSE un payant (402 `payment_required`,
    c'est la caisse qui l'active) ; `DELETE` le rend."""
    keys(tmp_path, monkeypatch)
    async with boot_host(tmp_path, link_plugins=PLATFORM) as h:
        t = h.token(permissions=["admin:*"])  # dev-auth n'a pas `xauth.tenant_access` : l'administrateur de la plateforme passe sans
        hd, base = h.headers(t, "acme"), f"{h.prefix}/xlicense"
        # Ici, seuls des plugins de plateforme sont chargés (jamais vendables) : on met en rayon comme l'administrateur le ferait.
        for body in ({"code": "crm", "label": "CRM", "free": True},
                     {"code": "caisse", "label": "Caisse", "free": False, "price": 20, "currency": "XOF", "period": "month", "stripe_price_id": "price_x"}):
            r = await h.client.put(f"{base}/modules/{body['code']}", headers=hd, json=body)
            assert r.status_code == 200, r.text
        r = await h.client.get(f"{base}/me/modules", headers=hd)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["can_manage"] is True
        modules = {m["code"]: m for m in body["modules"]}
        assert {"code", "label", "free", "price", "currency", "period", "taken", "source", "usable", "sellable", "expires_at"} <= set(modules["crm"])
        assert modules["caisse"]["sellable"] and not modules["caisse"]["free"]

        r = await h.client.post(f"{base}/me/modules/caisse", headers=hd)
        assert r.status_code == 402 and r.json()["detail"]["code"] == "payment_required", r.text  # payant : seulement par la caisse
        r = await h.client.post(f"{base}/me/modules/crm", headers=hd)
        assert r.status_code == 200, r.text
        again = {m["code"]: m for m in (await h.client.get(f"{base}/me/modules", headers=hd)).json()["modules"]}
        assert again["crm"]["taken"] is True and again["crm"]["source"] == "free"
        assert again["caisse"]["taken"] is False
        r = await h.client.delete(f"{base}/me/modules/crm", headers=hd)
        assert r.status_code == 200, r.text
        assert not {m["code"]: m for m in (await h.client.get(f"{base}/me/modules", headers=hd)).json()["modules"]}["crm"]["taken"]


async def test_config_json_announces_real_time_only_when_xpulse_serves_it(tmp_path, monkeypatch):
    """Le Desk ne demande le flux que si `config.json` l'annonce : sans xpulse, un hôte répondait 404 au flux à chaque page (une erreur
    de console permanente, mesurée dans le scénario Chromium de la caisse d'erp). L'annonce exige le moteur qui publie ET xpulse chargé."""
    keys(tmp_path, monkeypatch)
    async with boot_host(tmp_path / "avec", link_plugins=PLATFORM) as h:
        r = await h.client.get(f"{h.base}/desk/config.json")
        assert r.status_code == 200 and r.json()["realtime"] is True, r.text
    async with boot_host(tmp_path / "sans") as h:  # ext.pubsub est là (hermetic_services), xpulse non
        r = await h.client.get(f"{h.base}/desk/config.json")
        assert r.status_code == 200 and r.json()["realtime"] is False, r.text
