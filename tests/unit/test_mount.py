"""`mount_desk` : le Desk servi par une application FastAPI quelconque, avec les protections du plugin.

Ce que ces tests défendent, chacun par une mutation qui le casse :
- `index.html` part sous la CSP stricte (empreinte de l'import map, jamais `'unsafe-inline'`) et `X-Frame-Options` ;
- `config.json` dit ce que l'opérateur a configuré, sous la clé `extensions` que le Desk lit (vide : le montage n'en sert pas) ;
- monté à la racine, il ne masque AUCUNE route que l'application déclare après lui ;
- traversée refusée, et le client ne choisit pas le dossier servi ;
- une API sur une autre origine est nommée dans `connect-src` ; une adresse invalide est refusée au montage.
"""

from __future__ import annotations

import base64
import hashlib

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from xcore_doctype import Config, mount_desk
from xcore_doctype.errors import ConfigurationError

IMPORT_MAP = '{"imports":{"vue":"./shared/vue.js"}}'


@pytest.fixture
def dist(tmp_path):
    d = tmp_path / "dist"
    (d / "assets").mkdir(parents=True)
    (d / "shared").mkdir()
    (d / "index.html").write_text(f'<!doctype html><script type="importmap">{IMPORT_MAP}</script><div id="app"></div>')
    (d / "assets" / "index-abc.js").write_text("console.log(1)")
    (d / "shared" / "vue.js").write_text("export default 1")
    (tmp_path / "secret.txt").write_text("secret")
    return d


def client(dist, after=True, **config) -> TestClient:
    app = FastAPI()
    mount_desk(app, Config(dist_dir=dist, **config))
    if after:  # une route déclarée APRÈS le Desk : elle doit rester joignable
        @app.get("/api/ping")
        def ping():
            return {"ok": True}

        @app.get("/reports/{name}")
        def report(name: str):
            return {"report": name}
    return TestClient(app)


def test_the_page_carries_the_strict_policy(dist):
    r = client(dist).get("/")
    assert r.status_code == 200 and "importmap" in r.text
    csp = r.headers["content-security-policy"]
    digest = base64.b64encode(hashlib.sha256(IMPORT_MAP.encode()).digest()).decode()
    assert f"'sha256-{digest}'" in csp and "unsafe-inline" not in csp.split("style-src")[0]
    assert r.headers["x-frame-options"] == "DENY" and r.headers["x-content-type-options"] == "nosniff"
    assert client(dist).get("/index.html").headers["content-security-policy"] == csp


def test_config_says_what_the_operator_configured(dist):
    body = client(dist, api_base="/api/doc/", login_url="/auth/login", developer=True).get("/config.json").json()
    assert body == {"api_base": "/api/doc", "login_url": "/auth/login", "extensions": [], "developer": True}


def test_mounted_at_the_root_it_hides_no_later_route(dist):
    c = client(dist)
    assert c.get("/api/ping").json() == {"ok": True}
    assert c.get("/reports/x").json() == {"report": "x"}
    assert c.get("/nothing-here").status_code == 404


def test_build_files_are_served_with_their_cache(dist):
    c = client(dist)
    assert c.get("/assets/index-abc.js").headers["cache-control"].endswith("immutable")
    assert c.get("/shared/vue.js").headers["cache-control"] == "no-cache"


@pytest.mark.parametrize("url", ["/assets/../../secret.txt", "/assets/%2e%2e/%2e%2e/secret.txt", "/assets/vue.js?folder=shared"])
def test_nothing_outside_the_build_and_no_folder_choice(dist, url):
    r = client(dist).get(url)
    assert r.status_code == 404 and "secret" not in r.text


def test_under_a_path_it_redirects_and_derives_nothing(dist):
    c = client(dist, path="/erp")
    r = c.get("/erp", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == "erp/"
    assert c.get("/erp/").status_code == 200
    assert c.get("/erp/config.json").json()["api_base"] == "/app/xcore-doctype"


def test_another_origin_for_the_api_is_allowed_to_connect(dist):
    csp = client(dist, api_base="https://api.example.com/app/xcore-doctype", login_url="https://auth.example.com/app/auth/login").get("/").headers["content-security-policy"]
    connect = next(p for p in csp.split("; ") if p.startswith("connect-src"))
    assert connect == "connect-src 'self' https://auth.example.com https://api.example.com"


@pytest.mark.parametrize("config", [{"api_base": "//evil.example"}, {"api_base": "javascript:alert(1)"}, {"login_url": "ftp://x"}, {"path": "erp"}])
def test_bad_addresses_are_refused_at_mount(dist, config):
    with pytest.raises(ConfigurationError):
        client(dist, after=False, **config)


def test_an_unbuilt_desk_says_so(tmp_path):
    r = client(tmp_path / "missing").get("/")
    assert r.status_code == 503 and r.json()["detail"]["code"] == "desk_not_built"
