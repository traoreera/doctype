"""Les routes publiques du Desk : fichiers statiques et configuration, rien d'autre (src/xcore_doctype/api/desk.py)."""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from xcore_doctype.api.desk import DeskSettings, build_desk_router
from xcore_doctype.errors import ConfigurationError

PREFIX = "/app/xcore-doctype"


@pytest.fixture
def dist(tmp_path):
    d = tmp_path / "dist"
    (d / "assets").mkdir(parents=True)
    (d / "index.html").write_text("<!doctype html><title>Desk</title><script type=module src=./assets/app-1a2b3c.js></script>")
    (d / "assets" / "app-1a2b3c.js").write_text("console.log('desk')")
    (d / "assets" / "index-9f.css").write_text("body{margin:0}")
    (d / "assets" / "inter.woff2").write_bytes(b"wOF2\x00\x01")
    (d / "assets" / "blob.bin").write_bytes(b"\x00\x01")
    (d / "favicon.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>")
    (tmp_path / "secret.txt").write_text("TOP SECRET")  # hors du build : ne doit jamais sortir
    return d


def client_for(dist, prefix=PREFIX, **settings):
    app = FastAPI()
    app.include_router(build_desk_router(DeskSettings(dist_dir=dist, **settings)), prefix=prefix)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t"), app


@pytest.fixture
async def c(dist):
    client, _ = client_for(dist)
    async with client:
        yield client


class TestIndex:
    async def test_served_with_a_strict_policy(self, c):
        r = await c.get(f"{PREFIX}/desk/")
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
        assert "<title>Desk</title>" in r.text
        csp = r.headers["content-security-policy"]
        for directive in ("default-src 'self'", "script-src 'self'", "object-src 'none'", "frame-ancestors 'none'", "base-uri 'self'", "form-action 'self'"):
            assert directive in csp, directive
        assert "unsafe-eval" not in csp and "script-src 'self' 'unsafe-inline'" not in csp  # pas de script en ligne
        assert r.headers["x-frame-options"] == "DENY" and r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["cache-control"] == "no-cache" and r.headers["referrer-policy"] == "no-referrer"

    async def test_the_slashless_url_redirects_relatively(self, c):
        r = await c.get(f"{PREFIX}/desk", follow_redirects=False)
        assert r.status_code == 307 and r.headers["location"] == "desk/"  # reste juste sous n'importe quel préfixe

    async def test_the_policy_allows_a_cross_origin_login_server_only_when_configured(self, dist):
        client, _ = client_for(dist, login_url="https://auth.example.com/login")
        async with client:
            csp = (await client.get(f"{PREFIX}/desk/")).headers["content-security-policy"]
        assert "connect-src 'self' https://auth.example.com" in csp
        client, _ = client_for(dist, login_url="/app/auth/login")
        async with client:
            assert "connect-src 'self';" in (await client.get(f"{PREFIX}/desk/")).headers["content-security-policy"]


class TestFiles:
    async def test_hashed_assets_are_cached_forever(self, c):
        r = await c.get(f"{PREFIX}/desk/assets/app-1a2b3c.js")
        assert r.status_code == 200 and r.text == "console.log('desk')"
        assert r.headers["content-type"].startswith("text/javascript")
        assert r.headers["cache-control"] == "public, max-age=31536000, immutable"

    @pytest.mark.parametrize(
        "path, media",
        [("assets/index-9f.css", "text/css"), ("assets/inter.woff2", "font/woff2"), ("favicon.svg", "image/svg+xml"), ("assets/blob.bin", "application/octet-stream")],
    )
    async def test_media_types_do_not_depend_on_the_system(self, c, path, media):
        r = await c.get(f"{PREFIX}/desk/{path}")
        assert r.status_code == 200 and r.headers["content-type"].startswith(media) and r.headers["x-content-type-options"] == "nosniff"

    async def test_files_outside_assets_are_not_cached_forever(self, c):
        assert (await c.get(f"{PREFIX}/desk/favicon.svg")).headers["cache-control"] == "no-cache"

    @pytest.mark.parametrize(
        "path",
        [
            "../secret.txt",
            "%2e%2e/secret.txt",
            "..%2fsecret.txt",
            "%2e%2e%2fsecret.txt",
            "assets/../../secret.txt",
            "assets/%2e%2e/%2e%2e/secret.txt",
            "..%5csecret.txt",
            "/etc/passwd",
            "%2fetc%2fpasswd",
            "assets/../../../etc/passwd",
            "assets%00.js",
            "assets/app-1a2b3c.js%00.png",
            "%00",
            "assets/",
            "assets",
            "nope.js",
            "x" * 5000,
        ],
    )
    async def test_nothing_outside_the_build_is_ever_served(self, c, path):
        r = await c.get(f"{PREFIX}/desk/{path}")
        assert r.status_code == 404, (path, r.status_code, r.text[:80])
        assert "TOP SECRET" not in r.text and "root:" not in r.text

    async def test_a_symlink_that_leaves_the_build_is_refused(self, c, dist, tmp_path):
        (dist / "assets" / "link.txt").symlink_to(tmp_path / "secret.txt")
        r = await c.get(f"{PREFIX}/desk/assets/link.txt")
        assert r.status_code == 404 and "TOP SECRET" not in r.text

    async def test_a_symlink_inside_the_build_is_fine(self, c, dist):
        (dist / "assets" / "alias.js").symlink_to(dist / "assets" / "app-1a2b3c.js")
        assert (await c.get(f"{PREFIX}/desk/assets/alias.js")).status_code == 200

    @pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH"])
    async def test_read_only(self, c, method):
        for path in ("desk/", "desk/assets/app-1a2b3c.js", "desk/config.json"):
            assert (await c.request(method, f"{PREFIX}/{path}", json={})).status_code == 405


class TestConfig:
    async def test_it_tells_the_desk_where_the_api_is_from_the_request_itself(self, c):
        r = await c.get(f"{PREFIX}/desk/config.json")
        assert r.json() == {"api_base": PREFIX, "login_url": None, "extensions": []}
        assert r.headers["cache-control"] == "no-store"

    @pytest.mark.parametrize("prefix", ["/plugin/xcore-doctype", "/a/b/c/d", "/x"])
    async def test_it_follows_whatever_prefix_xcore_mounts_the_plugin_under(self, dist, prefix):
        client, _ = client_for(dist, prefix=prefix)
        async with client:
            assert (await client.get(f"{prefix}/desk/config.json")).json()["api_base"] == prefix

    async def test_login_url(self, dist):
        client, _ = client_for(dist, login_url="/app/auth/login")
        async with client:
            assert (await client.get(f"{PREFIX}/desk/config.json")).json()["login_url"] == "/app/auth/login"

    @pytest.mark.parametrize(
        "prefix, login",
        [("/app/xcore-doctype", "/app/auth/login"), ("/plugin/xcore-doctype", "/plugin/auth/login"), ("/a/b/c/xcore-doctype", "/a/b/c/auth/login")],
    )
    async def test_the_auth_sentinel_follows_the_prefix(self, dist, prefix, login):
        """« auth » ne fige pas `/app` : le Desk déduit `<préfixe>/auth/login` du préfixe réel sous lequel il est servi."""
        client, _ = client_for(dist, prefix=prefix, login_url="auth")
        async with client:
            assert (await client.get(f"{prefix}/desk/config.json")).json()["login_url"] == login

    async def test_it_contains_nothing_secret(self, c):
        assert set((await c.get(f"{PREFIX}/desk/config.json")).json()) == {"api_base", "login_url", "extensions"}


class TestNotBuilt:
    async def test_a_clear_message_when_the_build_is_missing(self, tmp_path):
        client, _ = client_for(tmp_path / "absent")
        async with client:
            for path in ("desk/", "desk/assets/app.js"):
                r = await client.get(f"{PREFIX}/{path}")
                assert r.status_code == 503 and r.json()["detail"]["code"] == "desk_not_built" and "pnpm build" in r.json()["detail"]["msg"]
            assert (await client.get(f"{PREFIX}/desk/config.json")).status_code == 200  # la config ne dépend pas du build


class TestSchema:
    async def test_desk_routes_stay_out_of_the_api_documentation(self, dist):
        _, app = client_for(dist)
        assert not [p for p in app.openapi()["paths"] if "desk" in p]


class TestSettings:
    def parse(self, block):
        return DeskSettings.from_plugin_config({"doctype": {"desk": block}})

    def test_defaults(self):
        s = DeskSettings.from_plugin_config({})
        assert (s.enabled, s.login_url) == (True, None) and s.dist_dir.name == "dist"

    def test_values(self):
        s = self.parse({"enabled": False, "login_url": "https://a.example/login", "dist_dir": "~/desk"})
        assert (s.enabled, s.login_url) == (False, "https://a.example/login") and s.dist_dir.name == "desk"

    @pytest.mark.parametrize("url", ["/app/auth/login", "https://a.example/x", "http://localhost:8000/login", "auth"])
    def test_valid_login_urls(self, url):
        assert self.parse({"login_url": url}).login_url == url

    @pytest.mark.parametrize(
        "block",
        [
            {"login_url": "app/auth/login"},  # ni chemin ni URL
            {"login_url": "//evil.example/login"},  # schéma implicite : hors du site
            {"login_url": "javascript:alert(1)"},
            {"login_url": "ftp://a/b"},
            {"login_url": "/a b"},
            {"login_url": "/a\nb"},
            {"login_url": "/" + "x" * 300},
            {"login_url": 5},
            {"enabled": "yes"},
            {"enabled": 1},
            {"dist_dir": ""},
            {"dist_dir": 5},
            {"unknown": 1},
            "oups",
            ["a"],
        ],
    )
    def test_rejected(self, block):
        with pytest.raises(ConfigurationError):
            self.parse(block)


# ── D28 : les extensions JavaScript des plugins ────────────────────────────────────────────────────────────────────

from xcore_doctype.desk_extensions import DeskExtensions  # noqa: E402
from xcore_doctype.errors import InvalidDefinition  # noqa: E402


@pytest.fixture
def ext_dir(tmp_path):
    d = tmp_path / "plugins" / "sales" / "desk"
    (d / "chunks").mkdir(parents=True)
    (d / "index.js").write_text("export default { name: 'sales', apiVersion: 1, setup() {} }")
    (d / "chunks" / "panel.js").write_text("export const x = 1")
    (d / "style.css").write_text(".x{}")
    (d / "page.html").write_text("<script>alert(1)</script>")
    (d / "notes.unknown").write_text("?")
    (tmp_path / "plugins" / "sales" / "secret.py").write_text("KEY = 'x'")
    return d


def ext_client(dist, extensions, **settings):
    app = FastAPI()
    app.include_router(build_desk_router(DeskSettings(dist_dir=dist, **settings), extensions), prefix=PREFIX)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


class TestExtensions:
    def test_settings_name_the_allowed_plugins(self):
        assert DeskSettings.from_plugin_config({}).extensions == ()
        assert DeskSettings.from_plugin_config({"doctype": {"desk": {"extensions": ["sales", "*"]}}}).extensions == ("sales", "*")
        for bad in ("sales", ["Sales"], ["../x"], [1]):
            with pytest.raises(ConfigurationError, match="extensions"):
                DeskSettings.from_plugin_config({"doctype": {"desk": {"extensions": bad}}})

    def test_registration_rules(self, ext_dir, tmp_path):
        exts = DeskExtensions(["sales"])
        assert exts.register("sales", ext_dir) is True
        with pytest.raises(InvalidDefinition, match="déjà"):
            exts.register("sales", ext_dir)
        with pytest.raises(InvalidDefinition, match="index.js"):
            exts.register("stock", tmp_path)
        with pytest.raises(InvalidDefinition, match="nom"):
            exts.register("my-plugin", ext_dir)
        assert exts.register("hr", ext_dir) is False  # pas autorisée : déclarée, jamais servie
        assert [e.name for e in exts.served()] == ["sales"] and exts.get("hr") is None

    def test_the_version_follows_the_content(self, ext_dir):
        a = DeskExtensions(["*"])
        a.register("sales", ext_dir)
        (ext_dir / "chunks" / "panel.js").write_text("export const x = 2")
        b = DeskExtensions(["*"])
        b.register("sales", ext_dir)
        assert a.get("sales").version != b.get("sales").version
        assert b.get("sales").url == f"ext/sales/index.js?v={b.get('sales').version}"

    async def test_only_allowed_extensions_are_listed_and_served(self, dist, ext_dir):
        exts = DeskExtensions(["sales"])
        exts.register("sales", ext_dir)
        exts.register("hr", ext_dir)
        async with ext_client(dist, exts) as c:
            config = (await c.get(f"{PREFIX}/desk/config.json")).json()
            assert config["extensions"] == [{"name": "sales", "url": exts.get("sales").url}]
            r = await c.get(f"{PREFIX}/desk/ext/sales/index.js")
            assert r.status_code == 200 and r.headers["content-type"].startswith("text/javascript")
            assert r.headers["cache-control"] == "no-cache" and r.headers["x-content-type-options"] == "nosniff"
            assert (await c.get(f"{PREFIX}/desk/ext/sales/chunks/panel.js")).status_code == 200
            assert (await c.get(f"{PREFIX}/desk/ext/sales/style.css")).headers["content-type"].startswith("text/css")
            for path in ("hr/index.js", "ghost/index.js", "sales/page.html", "sales/notes.unknown", "sales/../secret.py",
                         "sales/%2e%2e/secret.py", "sales/missing.js"):
                assert (await c.get(f"{PREFIX}/desk/ext/{path}")).status_code == 404, path

    async def test_a_symlink_out_of_the_extension_is_refused(self, dist, ext_dir, tmp_path):
        (ext_dir / "leak.js").symlink_to(tmp_path / "plugins" / "sales" / "secret.py")
        exts = DeskExtensions(["*"])
        exts.register("sales", ext_dir)
        async with ext_client(dist, exts) as c:
            assert (await c.get(f"{PREFIX}/desk/ext/sales/leak.js")).status_code == 404

    async def test_the_import_map_is_allowed_by_its_hash_only(self, dist):
        import base64
        import hashlib

        body = '{"imports":{"vue":"./shared/vue.js"}}'
        (dist / "index.html").write_text(f'<!doctype html><script type="importmap">{body}</script><title>Desk</title>')
        async with ext_client(dist, DeskExtensions()) as c:
            csp = (await c.get(f"{PREFIX}/desk/")).headers["content-security-policy"]
        digest = base64.b64encode(hashlib.sha256(body.encode()).digest()).decode()
        assert f"script-src 'self' 'sha256-{digest}';" in csp
        assert "unsafe-inline'" not in csp.split("style-src")[0]
