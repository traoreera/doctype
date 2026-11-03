"""Le contrôle de licence d'xlicense (`platform/xlicense/src/middleware.py`) ne coupe jamais la PLATEFORME.

Mesuré dans un vrai navigateur (`ui/e2e/platform.e2e.mjs`) : le flux temps réel d'xpulse (`/app/xpulse/stream`) n'était pas dans les
préfixes exclus. Une organisation sans licence active (essai fini, impayé) recevait donc 402 sur le flux, et le Desk perdait la cloche
et les documents en direct, alors que le moteur et le Desk eux-mêmes restent servis. xpulse est un plugin de plateforme, jamais vendu
(`seed.PLATFORM_PLUGINS`) : il est exclu comme le moteur, dans les deux listes (le manifeste livré et la liste par défaut).

Un module vendu, lui, reste gardé : la même organisation reçoit 402 ailleurs.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import yaml
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from xcore_doctype import platform_plugins_dir
from xcore_doctype.platform.xlicense.src.middleware import DEFAULT_EXCLUDED_PREFIXES, LicenseMiddleware

SHIPPED = yaml.safe_load((Path(platform_plugins_dir()) / "xlicense" / "plugin.yaml").read_text())["config"]


class _User(BaseHTTPMiddleware):
    """Ce que pose le backend d'xauth après vérification du jeton : l'utilisateur et son organisation."""

    async def dispatch(self, request, call_next):
        request.state.user = {"sub": "bob", "tenant_id": "acme"}
        return await call_next(request)


def _app(config: dict | None) -> Starlette:
    async def emit(name, data):
        if name == "xlicense.config":
            return [config] if config is not None else []
        return [{"state": "expired", "id": "lic-1", "modules": []}]  # la licence de l'organisation n'est plus utilisable

    async def ok(request):
        return PlainTextResponse("ok")

    app = Starlette(routes=[Route("/app/{rest:path}", ok)])
    app.add_middleware(LicenseMiddleware, emit=emit)
    app.add_middleware(_User)  # ajouté en DERNIER = exécuté en premier
    return app


@pytest.mark.parametrize("config", [SHIPPED, None], ids=["manifeste livré", "liste par défaut"])
async def test_the_real_time_stream_is_platform_and_a_sold_module_stays_gated(config):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_app(config)), base_url="http://t") as client:
        stream = await client.get("/app/xpulse/stream", params={"channels": "notification"})
        assert stream.status_code == 200, stream.text  # le temps réel suit le Desk, licence ou pas
        engine = await client.get("/app/xcore-doctype/list/Customer")
        assert engine.status_code == 200
        sold = await client.get("/app/caisse/session")
        assert sold.status_code == 402 and sold.json()["error"] == "license_required"  # un module vendu reste gardé


def test_both_lists_name_the_real_time_plugin():
    assert any(p.rstrip("/*") == "/app/xpulse" for p in SHIPPED["excluded_prefixes"])
    assert "/app/xpulse" in DEFAULT_EXCLUDED_PREFIXES
