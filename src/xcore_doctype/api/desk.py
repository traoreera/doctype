"""Sert le Desk (l'interface Vue de `ui/`) : fichiers statiques et configuration d'exécution.

Ces routes sont PUBLIQUES, à la différence de l'API : le navigateur doit pouvoir charger l'application avant que
l'utilisateur ait un jeton. Elles ne donnent donc jamais accès à une donnée, seulement à :

- `GET desk/`             : `index.html` du build (`src/xcore_doctype/ui/dist`, embarqué dans le wheel) ;
- `GET desk/<fichier>`    : un fichier du build, et rien d'autre (la traversée de chemin est refusée) ;
- `GET desk/config.json`  : où se trouve l'API et où se connecter. Dérivé de l'URL de la requête : le Desk n'a donc pas
                            à connaître le préfixe sous lequel xcore monte le plugin (`app.plugin_prefix`). Liste aussi les
                            extensions JavaScript que l'opérateur autorise (D28) ;
- `GET desk/ext/<plugin>/<fichier>` : un fichier de l'extension d'un plugin autorisé, avec la même garde qu'un fichier du build.

Le routeur du Desk est monté séparément de celui de l'API : la dépendance d'identité de l'API ne s'applique pas ici,
et une route ajoutée ici ne peut pas l'hériter par accident (voir `tests/integration/test_desk.py`).
"""

from __future__ import annotations

import base64
import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response

from ..desk_extensions import DeskExtensions
from ..errors import ConfigurationError

DEFAULT_DIST = Path(__file__).resolve().parent.parent / "ui" / "dist"
# Le dossier sous lequel xcore monte ce plugin : sert à situer le plugin `auth` sur le même préfixe que l'API.
_PLUGIN_NAME = "xcore-doctype"
# Valeur sentinelle de `login_url` : le Desk déduit l'adresse de connexion du préfixe réel de la requête (`<préfixe>/auth/login`),
# au lieu de figer `/app/auth/login` (faux dès que `app.plugin_prefix` n'est pas `/app`).
LOGIN_FROM_PREFIX = "auth"

# Types de média explicites : `mimetypes` dépend du système (woff2 manque souvent) et son registre est global.
_MEDIA = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
    ".ttf": "font/ttf",
    ".txt": "text/plain; charset=utf-8",
    ".map": "application/json",
}


@dataclass(frozen=True, slots=True)
class DeskSettings:
    """Bloc `doctype.desk` du manifeste."""

    enabled: bool = True
    # Où le Desk envoie email et mot de passe (xauth : `/app/auth/login`). None : le Desk demande un jeton collé (développement).
    login_url: str | None = None
    dist_dir: Path = DEFAULT_DIST
    # Les plugins dont l'extension JavaScript est servie (D28) : des noms, ou `*` pour tous. Vide : aucune (fermé par défaut).
    extensions: tuple[str, ...] = field(default=())

    @classmethod
    def from_plugin_config(cls, config: Mapping[str, Any]) -> "DeskSettings":
        block = ((config or {}).get("doctype") or {}).get("desk") or {}
        if not isinstance(block, Mapping):
            raise ConfigurationError("doctype.desk doit être un objet")
        unknown = sorted(set(block) - {"enabled", "login_url", "dist_dir", "extensions"})
        if unknown:
            raise ConfigurationError(f"doctype.desk: clé(s) inconnue(s) {unknown}")
        out: dict[str, Any] = {}
        if "enabled" in block:
            if not isinstance(block["enabled"], bool):
                raise ConfigurationError("doctype.desk.enabled doit être un booléen")
            out["enabled"] = block["enabled"]
        if block.get("login_url") is not None:
            url = block["login_url"]
            if url == LOGIN_FROM_PREFIX:
                out["login_url"] = url
            else:
                parts = urlsplit(url) if isinstance(url, str) else None
                relative = isinstance(url, str) and url.startswith("/") and not url.startswith("//")
                absolute = parts is not None and parts.scheme in ("http", "https") and bool(parts.netloc)
                if not (relative or absolute) or len(url) > 300 or any(c in url for c in "\r\n \t"):
                    raise ConfigurationError(f"doctype.desk.login_url doit être « {LOGIN_FROM_PREFIX} » (déduit du préfixe), un chemin (/app/auth/login) ou une URL http(s)")
                out["login_url"] = url
        if "dist_dir" in block:
            if not isinstance(block["dist_dir"], str) or not block["dist_dir"]:
                raise ConfigurationError("doctype.desk.dist_dir doit être un chemin")
            out["dist_dir"] = Path(block["dist_dir"]).expanduser()
        if block.get("extensions") is not None:
            names = block["extensions"]
            if not isinstance(names, list) or not all(isinstance(n, str) and (n == "*" or re.fullmatch(r"[a-z0-9_]+", n)) for n in names):
                raise ConfigurationError("doctype.desk.extensions doit être une liste de noms de plugins (ou [\"*\"] : tous)")
            out["extensions"] = tuple(names)
        return cls(**out)


def _csp(login_url: str | None, script_hashes: tuple[str, ...] = ()) -> str:
    """Politique stricte. Les styles en ligne restent permis : PrimeVue injecte ses feuilles de style à l'exécution.

    `script_hashes` : l'empreinte de l'import map d'`index.html` (D28), le seul script en ligne permis ; jamais `'unsafe-inline'`.
    """
    connect = ["'self'"]
    if login_url and login_url.startswith(("http://", "https://")):
        parts = urlsplit(login_url)
        connect.append(f"{parts.scheme}://{parts.netloc}")
    return "; ".join(
        [
            "default-src 'self'",
            " ".join(["script-src 'self'", *(f"'sha256-{h}'" for h in script_hashes)]),
            "style-src 'self' 'unsafe-inline'",
            "img-src 'self' data:",
            "font-src 'self' data:",
            f"connect-src {' '.join(connect)}",
            "object-src 'none'",
            "base-uri 'self'",
            "form-action 'self'",
            "frame-ancestors 'none'",
        ]
    )


def _login_url(configured: str | None, api_base: str) -> str | None:
    """Adresse de connexion servie au Desk. La sentinelle `auth` se déduit du préfixe réel de la requête (`/app/xcore-doctype` -> `/app/auth/login`)."""
    if configured != LOGIN_FROM_PREFIX:
        return configured
    prefix = api_base[: -len(f"/{_PLUGIN_NAME}")] if api_base.endswith(f"/{_PLUGIN_NAME}") else api_base
    return f"{prefix}/auth/login"


_IMPORT_MAP = re.compile(r'<script type="importmap">(.*?)</script>', re.DOTALL)


def inline_script_hashes(html: str) -> tuple[str, ...]:
    """Les empreintes SHA-256 (base64) des import maps d'une page : ce que la CSP doit nommer pour que le navigateur les accepte."""
    return tuple(base64.b64encode(hashlib.sha256(m.encode()).digest()).decode() for m in _IMPORT_MAP.findall(html))


def _safe_file(root: Path, relative: str) -> Path | None:
    """Le fichier `relative` sous `root`, ou `None` : traversée, lien symbolique qui sort, octet nul, absent."""
    try:
        target = (root / relative).resolve()
    except (ValueError, OSError):  # octet nul, chemin trop long…
        return None
    # `resolve()` suit les liens symboliques : un lien qui sort du dossier est refusé comme une traversée.
    return target if target.is_relative_to(root) and target.is_file() else None


def build_desk_router(settings: DeskSettings, extensions: DeskExtensions | None = None) -> APIRouter:
    dist = settings.dist_dir.resolve()
    extensions = extensions if extensions is not None else DeskExtensions()
    router = APIRouter(include_in_schema=False)
    common = {"X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer"}
    # L'empreinte de l'import map se relit quand `index.html` change (un nouveau build sous un serveur qui tourne).
    seen: dict[str, Any] = {"stamp": None, "hashes": ()}

    def html_headers(index: Path) -> dict[str, str]:
        stat = index.stat()
        stamp = (stat.st_mtime_ns, stat.st_size)
        if seen["stamp"] != stamp:
            seen["hashes"], seen["stamp"] = inline_script_hashes(index.read_text(encoding="utf-8")), stamp
        return {
            **common,
            "Content-Security-Policy": _csp(settings.login_url, seen["hashes"]),
            "X-Frame-Options": "DENY",
            "Cache-Control": "no-cache",
        }

    def not_built() -> Response:
        return JSONResponse(
            {"detail": {"code": "desk_not_built", "msg": "Le Desk n'est pas construit : lancez `pnpm build` dans ui/."}},
            status_code=503,
            headers={**common, "Cache-Control": "no-store"},
        )

    def missing() -> Response:
        return JSONResponse({"detail": {"code": "not_found", "msg": "Fichier introuvable"}}, status_code=404, headers=common)

    @router.get("/desk")
    async def desk_without_slash() -> Response:
        # Relatif : la redirection reste correcte quel que soit le préfixe sous lequel xcore monte le plugin.
        return RedirectResponse("desk/", status_code=307)

    @router.get("/desk/config.json")
    async def desk_config(request: Request) -> Response:
        path = request.url.path
        api_base = path[: -len("/desk/config.json")] if path.endswith("/desk/config.json") else ""
        return JSONResponse(
            {
                "api_base": api_base,
                "login_url": _login_url(settings.login_url, api_base),
                "extensions": [{"name": e.name, "url": e.url} for e in extensions.served()],
            },
            headers={**common, "Cache-Control": "no-store"},
        )

    @router.get("/desk/")
    async def desk_index() -> Response:
        index = dist / "index.html"
        if not index.is_file():
            return not_built()
        return FileResponse(index, media_type=_MEDIA[".html"], headers=html_headers(index))

    @router.get("/desk/ext/{plugin}/{asset_path:path}")
    async def desk_extension_file(plugin: str, asset_path: str) -> Response:
        # D28 : seulement l'extension d'un plugin que l'opérateur autorise ; inconnue et refusée répondent pareil.
        ext = extensions.get(plugin)
        target = _safe_file(ext.directory, asset_path) if ext is not None else None
        media = _MEDIA.get(target.suffix.lower()) if target is not None else None
        if target is None or media is None or media.startswith("text/html"):
            return missing()  # ni type inconnu (servi comme quoi ?), ni page HTML (elle échapperait à la CSP du Desk)
        return FileResponse(target, media_type=media, headers={**common, "Cache-Control": "no-cache"})

    @router.get("/desk/{asset_path:path}")
    async def desk_file(asset_path: str) -> Response:
        target = _safe_file(dist, asset_path)
        if target is None:
            return missing() if dist.is_dir() else not_built()
        hashed = target.parent.name == "assets"  # Vite nomme ces fichiers d'après leur contenu : cache sans limite
        headers = {**common, "Cache-Control": "public, max-age=31536000, immutable" if hashed else "no-cache"}
        media = _MEDIA.get(target.suffix.lower(), "application/octet-stream")
        return FileResponse(target, media_type=media, headers=headers)

    return router
