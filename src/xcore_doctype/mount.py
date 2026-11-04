"""Servir le Desk depuis une application FastAPI quelconque, hors du plugin (`mount_desk`).

Le plugin sert déjà le Desk sous `<préfixe>/xcore-doctype/desk/` (`api/desk.py`). `mount_desk` sert le MÊME build ailleurs —
à la racine d'un domaine, sous `/erp`… — avec les MÊMES protections, parce qu'il réutilise le même routeur :

- `index.html` sous une CSP stricte qui nomme l'empreinte de l'import map (D28), jamais `'unsafe-inline'`, et `X-Frame-Options` ;
- traversée de chemin et liens symboliques qui sortent du build refusés ; `assets/` en cache immuable ;
- `config.json` dit au Desk où est l'API et où se connecter ; une API ou une connexion sur une AUTRE origine est ajoutée à
  `connect-src` (il faut alors aussi autoriser cette origine en CORS côté API, ce que ce module ne fait pas).

Monté à la racine, le Desk ne pose PAS de route attrape-tout : seulement `/`, `/index.html`, `/config.json`, `/assets/…` et
`/shared/…`, pour ne jamais masquer une route que l'application déclare après lui (le Desk route par `#`, il n'a besoin de rien d'autre).
Les extensions JavaScript (D28) restent servies par le PLUGIN (elles dépendent des plugins chargés et de la liste blanche de son
manifeste) : le `config.json` d'ici renvoie à celui du plugin (`extensions_from`), où le Desk lit la liste et l'annonce du temps réel.
Sans ce renvoi, un Desk monté ailleurs ne chargeait plus aucune extension.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI

from .api.desk import DEFAULT_DIST, DeskSettings, build_desk_router
from .errors import ConfigurationError


@dataclass(frozen=True)
class Config:
    # Où le Desk est servi : `/` (racine) ou un chemin (`/erp`, qui redirige vers `/erp/`).
    path: str = "/"
    # L'API du plugin : un chemin sur la même origine (le cas simple, sans CORS) ou une URL http(s).
    api_base: str = "/app/xcore-doctype"
    # La connexion xauth : un chemin, une URL http(s), ou None (le Desk demande un jeton collé : développement).
    login_url: str | None = "/app/auth/login"
    # Le constructeur de DocTypes (mode développeur), fermé par défaut comme dans le manifeste.
    developer: bool = False
    # Le build du Desk ; None : celui du paquet (`src/xcore_doctype/ui/dist`, embarqué dans le wheel).
    dist_dir: Path | str | None = None


def _check_api_base(url: str) -> str:
    parts = urlsplit(url) if isinstance(url, str) else None
    relative = isinstance(url, str) and url.startswith("/") and not url.startswith("//")
    absolute = parts is not None and parts.scheme in ("http", "https") and bool(parts.netloc)
    if not (relative or absolute) or len(url) > 300 or any(c in url for c in "\r\n \t"):
        raise ConfigurationError("api_base doit être un chemin (/app/xcore-doctype) ou une URL http(s)")
    return url.rstrip("/")


def mount_desk(app: FastAPI, config: Config | None = None, **route: Any) -> None:
    """Ajoute à `app` les routes du Desk décrites par `config`. `route` va à `include_router` (`dependencies`, `tags`…)."""
    config = config or Config()
    # Les mêmes règles que le bloc `doctype.desk` du manifeste : une seule validation de `login_url` et du build.
    block = {"login_url": config.login_url, "developer_mode": config.developer, "dist_dir": str(config.dist_dir or DEFAULT_DIST)}
    settings = DeskSettings.from_plugin_config({"doctype": {"desk": block}})
    router = build_desk_router(settings, at=config.path, api_base=_check_api_base(config.api_base), catch_all=False, extensions_from=True)
    app.include_router(router, include_in_schema=False, **route)
