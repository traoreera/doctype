"""Le Desk servi par un vrai Xcore : public pour ses fichiers, mais l'API derrière reste protégée."""

from __future__ import annotations

import pytest

from tests.integration.conftest import boot_host


@pytest.fixture
def dist(tmp_path):
    d = tmp_path / "built-desk"
    (d / "assets").mkdir(parents=True)
    (d / "index.html").write_text("<!doctype html><title>Desk</title>")
    (d / "assets" / "app-abc.js").write_text("export {}")
    return d


@pytest.fixture
async def dh(tmp_path, dist):
    async with boot_host(tmp_path, doctype_config={"desk": {"dist_dir": str(dist), "login_url": "/app/auth/login"}}) as h:
        yield h


async def public(h, path):
    """Sans jeton ni en-tête de tenant : comme un navigateur qui charge l'application."""
    return await h.client.get(f"{h.base}{path}")


async def test_the_desk_loads_without_any_credential(dh):
    r = await public(dh, "/desk/")
    assert r.status_code == 200 and "<title>Desk</title>" in r.text and "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert (await public(dh, "/desk/assets/app-abc.js")).status_code == 200


async def test_the_desk_learns_the_api_prefix_from_where_xcore_mounted_it(dh):
    assert (await public(dh, "/desk/config.json")).json() == {"api_base": "/app/xcore-doctype", "login_url": "/app/auth/login"}


async def test_while_the_api_behind_it_still_demands_an_identity(dh):
    for path in ("/meta", "/meta/Customer", "/list/Customer", "/search/Customer", "/doc/Customer/x"):
        assert (await public(dh, path)).status_code == 401, path


async def test_the_desk_gives_no_access_to_documents_or_api_data(dh):
    """Même sous /desk/, aucun chemin ne ressemble à l'API : seul le contenu du build est servi."""
    # (« /desk/../meta » est absent : le client HTTP le réduit à « /meta » avant l'envoi, ce serait l'API.)
    for path in ("/desk/meta", "/desk/list/Customer", "/desk/%2e%2e/meta", "/desk/%2e%2e%2fmeta"):
        r = await public(dh, path)
        assert r.status_code == 404 and "Customer" not in r.text, path


async def test_desk_routes_survive_a_reload_and_vanish_on_unload(dh):
    assert (await dh.ipc("POST", "/xcore-doctype/reload")).status_code == 200
    assert (await public(dh, "/desk/")).status_code == 200
    assert (await public(dh, "/desk/config.json")).status_code == 200
    assert (await dh.ipc("DELETE", "/xcore-doctype/unload")).status_code == 200
    assert (await public(dh, "/desk/")).status_code == 404


async def test_the_desk_can_be_switched_off(tmp_path):
    async with boot_host(tmp_path, doctype_config={"desk": {"enabled": False}}) as h:
        assert (await public(h, "/desk/")).status_code == 404
        assert (await h.http("GET", "/meta", token=h.token())).status_code == 200  # l'API, elle, reste là


async def test_a_missing_build_says_so_instead_of_failing_mysteriously(tmp_path):
    async with boot_host(tmp_path, doctype_config={"desk": {"dist_dir": str(tmp_path / "nowhere")}}) as h:
        r = await public(h, "/desk/")
        assert r.status_code == 503 and r.json()["detail"]["code"] == "desk_not_built"


async def test_bad_desk_settings_stop_the_plugin_from_loading(tmp_path):
    async with boot_host(tmp_path, doctype_config={"desk": {"login_url": "javascript:alert(1)"}}) as h:
        assert (await h.client.get(f"{h.base}/desk/")).status_code == 404  # plugin non chargé : aucune route
        plugins = (await h.ipc("GET", "/status")).json()["plugins"]
        assert not any(p["name"] == "xcore-doctype" and p["state"] == "ready" for p in plugins)
