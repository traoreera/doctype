"""Critère de sortie de la phase 4 : le wheel s'installe et le plugin se charge dans une instance xcore NEUVE.

Ce test fait ce que ferait un utilisateur : il construit le wheel, l'installe dans un environnement vierge, crée une instance
avec l'outil d'xcore (`xcli init`), pose le plugin avec `xcore-doctype install`, corrige ce que la commande a signalé, puis démarre
l'instance et s'en sert (le Desk est servi depuis le wheel, une facture à lignes est numérotée et totalisée par le plugin `sales`).

Hors ligne, les DÉPENDANCES (xcore, FastAPI…) sont prises dans l'environnement de test via PYTHONPATH : seul le wheel est installé
(`--no-deps`). Avec `XCORE_DOCTYPE_INSTALL_DEPS=1` (la CI), le wheel est installé avec ses dépendances résolues sur l'index.

Ignoré, avec la raison, quand il manque `poetry`, le build du Desk (`make build-ui`) ou si XCORE_DOCTYPE_SKIP_DELIVERY=1.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import sysconfig
import time
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from tests.support import FIXTURES, install_dev_auth, mint_token

ROOT = Path(__file__).resolve().parents[2]
SECRET = "delivery-test-secret"
GRANTS = (
    [f"sales:{t}:{a}" for t in ("customer", "country", "invoice") for a in ("read", "write", "delete")]
    + [f"stock:{t}:{a}" for t in ("item", "warehouse", "stock_entry") for a in ("read", "write", "delete")]
    + ["stock:stock_entry:submit", "stock:stock_entry:cancel", "stock:stock_ledger_entry:read", "stock:bin:read"]
)

pytestmark = pytest.mark.skipif(os.environ.get("XCORE_DOCTYPE_SKIP_DELIVERY") == "1", reason="XCORE_DOCTYPE_SKIP_DELIVERY=1")


def run(cmd, *, cwd=None, env=None, timeout=300) -> subprocess.CompletedProcess:
    return subprocess.run([str(c) for c in cmd], cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)


def ok(proc: subprocess.CompletedProcess) -> str:
    assert proc.returncode == 0, f"{proc.args}\n--- stdout\n{proc.stdout}\n--- stderr\n{proc.stderr}"
    return proc.stdout


@pytest.fixture(scope="module")
def wheel(tmp_path_factory) -> Path:
    if shutil.which("poetry") is None:
        pytest.skip("poetry est introuvable")
    if not (ROOT / "src" / "xcore_doctype" / "ui" / "dist" / "index.html").is_file():
        pytest.skip("Desk non construit (make build-ui)")
    out = tmp_path_factory.mktemp("wheel")
    ok(run(["poetry", "build", "-f", "wheel", "-o", out], cwd=ROOT))
    (path,) = out.glob("*.whl")
    return path


@pytest.fixture(scope="module")
def venv(tmp_path_factory, wheel):
    """Un environnement vierge où seul le wheel est installé. Retourne (python, env d'exécution)."""
    d = tmp_path_factory.mktemp("venv")
    ok(run([sys.executable, "-m", "venv", d]))
    py = d / "bin" / "python"
    env = {**os.environ, "XCORE_DEV_AUTH_SECRET": SECRET}
    env.pop("VIRTUAL_ENV", None)
    if os.environ.get("XCORE_DOCTYPE_INSTALL_DEPS") == "1":
        ok(run([py, "-m", "pip", "install", "--quiet", wheel]))
    else:
        ok(run([py, "-m", "pip", "install", "--quiet", "--no-deps", "--no-index", wheel]))
        # Les dépendances de l'environnement de test, SANS son installation éditable de xcore-doctype (un .pth n'est pas lu
        # depuis PYTHONPATH : seul le paquet du wheel est importable).
        env["PYTHONPATH"] = os.pathsep.join(dict.fromkeys([sysconfig.get_paths()["purelib"], sysconfig.get_paths()["platlib"]]))
    return py, env


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class TestWheel:
    def test_it_contains_the_plugin_folder_the_built_desk_and_the_entry_points(self, wheel):
        with zipfile.ZipFile(wheel) as z:
            names = set(z.namelist())
            assert {"xcore_doctype/plugin_dir/plugin.yaml", "xcore_doctype/plugin_dir/src/main.py", "xcore_doctype/ui/dist/index.html"} <= names
            # Le plugin `system` (réglages globaux) voyage dans le wheel, DocType compris.
            assert {"xcore_doctype/system_plugin_dir/plugin.yaml", "xcore_doctype/system_plugin_dir/src/main.py", "xcore_doctype/system_plugin_dir/doctypes/system_settings.yaml"} <= names
            assert any(n.startswith("xcore_doctype/ui/dist/assets/") and n.endswith(".js") for n in names)
            # D28 : les deux modules que l'import map donne aux extensions, sous leurs noms FIXES.
            assert {"xcore_doctype/ui/dist/shared/vue.js", "xcore_doctype/ui/dist/shared/sdk.js"} <= names
            # Le modèle de `xcore-doctype new-desk-extension` voyage avec la commande.
            assert {"xcore_doctype/desk_template/index.js", "xcore_doctype/desk_template/README.md"} <= names
            assert not any("node_modules" in n or n.endswith(".map") for n in names)
            entry_points = next(z.read(n).decode() for n in names if n.endswith("entry_points.txt")).replace(" = ", "=")
        assert "[xcore.plugins]" in entry_points and "xcore-doctype=xcore_doctype:plugin_dir" in entry_points
        assert "xcore-doctype=xcore_doctype.cli:main" in entry_points

    def test_the_installed_package_is_the_wheel_and_not_the_sources(self, venv):
        py, env = venv
        where = ok(run([py, "-c", "import xcore_doctype; print(xcore_doctype.__file__)"], env=env)).strip()
        assert "site-packages" in where and str(ROOT / "src") not in where


@pytest.fixture(scope="module")
def instance(tmp_path_factory, venv) -> Path:
    """Une instance xcore créée par `xcli init`, puis le plugin posé par `xcore-doctype install`."""
    py, env = venv
    base = tmp_path_factory.mktemp("fresh")
    xcli = Path(sys.executable).parent / "xcli"
    if not xcli.exists():
        pytest.skip("xcli est introuvable dans l'environnement de test")
    ok(run([xcli, "init", "demo", "--db", "sqlite"], cwd=base))
    return base / "demo"


class TestFreshInstance:
    def test_install_names_what_a_new_xcore_instance_gets_wrong(self, venv, instance):
        py, env = venv
        out = ok(run([py.parent / "xcore-doctype", "install", "./app"], cwd=instance, env=env))
        assert (instance / "app" / "xcore-doctype" / "plugin.yaml").is_file()
        assert (instance / "app" / "xcore-doctype" / "src" / "main.py").is_file()
        for rel in ("plugin.yaml", "src/main.py", "doctypes/system_settings.yaml"):  # le plugin `system` est posé avec lui
            assert (instance / "app" / "system" / rel).is_file(), rel
        # Les deux pièges d'une instance `xcli init` : adaptateur SQLite synchrone, xcore.setup(app) absent.
        assert "adaptateur SYNCHRONE" in out and "xcore.setup(app)" in out
        assert "2 point(s) à corriger" in out

    def test_doctor_agrees_then_passes_once_the_instance_is_fixed(self, venv, instance):
        py, env = venv
        tool = py.parent / "xcore-doctype"
        assert run([tool, "doctor"], cwd=instance, env=env).returncode == 1

        config = instance / "integration.yaml"
        config.write_text(config.read_text().replace("type: sqlite\n", "type: sqlite+aio\n"))
        main = instance / "main.py"
        main.write_text(main.read_text().replace('@app.get("/health")', 'xcore.setup(app)\n\n\n@app.get("/health")'))
        assert run([tool, "doctor"], cwd=instance, env=env).returncode == 0

    def test_the_sample_business_plugin_passes_check_from_the_installed_package(self, venv, instance):
        py, env = venv
        tool = py.parent / "xcore-doctype"
        # `sales` cite l'article du plugin `stock` : sans lui, la cible de son champ Link est inconnue.
        alone = run([tool, "check", FIXTURES / "sales" / "doctypes", "--owner", "sales"], cwd=instance, env=env)
        assert alone.returncode == 1 and "Item" in alone.stderr
        out = ok(run([tool, "check", FIXTURES / "sales" / "doctypes", "--owner", "sales", "--with", FIXTURES / "stock" / "doctypes"], cwd=instance, env=env))
        assert "4 DocType(s) valides" in out and "sales.invoice_totals" in out
        out = ok(run([tool, "check", FIXTURES / "stock" / "doctypes", "--owner", "stock"], cwd=instance, env=env))
        assert "6 DocType(s) valides" in out and "stock.post_entry" in out and "stock:stock_entry:submit" in out


@pytest.fixture(scope="module")
def running(venv, instance, request):
    """L'instance corrigée, avec dev-auth et le plugin métier `sales`, démarrée par uvicorn."""
    py, env = venv
    # Les tests précédents du module l'ont préparée ; si on est exécuté seul, on la prépare ici.
    tool = py.parent / "xcore-doctype"
    ok(run([tool, "install", "./app"], cwd=instance, env=env))
    config = instance / "integration.yaml"
    config.write_text(config.read_text().replace("type: sqlite\n", "type: sqlite+aio\n"))
    main = instance / "main.py"
    if "xcore.setup(app)" not in main.read_text():
        main.write_text(main.read_text().replace('@app.get("/health")', 'xcore.setup(app)\n\n\n@app.get("/health")'))
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    install_dev_auth(instance / "app" / "auth")  # le plugin de TEST : une instance neuve n'a pas xauth
    shutil.copytree(FIXTURES / "stock", instance / "app" / "stock", ignore=ignore, dirs_exist_ok=True)
    shutil.copytree(FIXTURES / "sales", instance / "app" / "sales", ignore=ignore, dirs_exist_ok=True)

    port = free_port()
    log = (instance / "uvicorn.log").open("w")
    proc = subprocess.Popen([py, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", str(port)], cwd=instance, env=env,
                            stdout=log, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}/app/xcore-doctype"
    try:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                pytest.fail(f"l'instance s'est arrêtée :\n{(instance / 'uvicorn.log').read_text()[-3000:]}")
            try:
                if httpx.get(f"{base}/desk/config.json", timeout=1).status_code == 200:
                    break
            except httpx.TransportError:
                pass
            time.sleep(0.3)
        else:
            pytest.fail(f"l'instance ne répond pas :\n{(instance / 'uvicorn.log').read_text()[-3000:]}")
        yield base
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()


class TestRunningInstance:
    def test_the_desk_is_served_from_the_installed_wheel(self, running):
        index = httpx.get(f"{running}/desk/")
        assert index.status_code == 200 and "<title>Desk</title>" in index.text
        assert "script-src 'self'" in index.headers["content-security-policy"]
        asset = next(part for part in index.text.split('"') if part.startswith("./assets/") and part.endswith(".js"))
        js = httpx.get(f"{running}/desk/{asset[2:]}")
        assert js.status_code == 200 and js.headers["content-type"].startswith("text/javascript")
        assert httpx.get(f"{running}/desk/config.json").json() == {"api_base": "/app/xcore-doctype", "login_url": "/app/auth/login", "extensions": []}  # xauth est l'authentification par défaut

    def test_the_api_needs_an_identity(self, running):
        assert httpx.get(f"{running}/meta").status_code == 401

    def test_a_business_plugin_brings_doctypes_and_an_invoice_is_numbered_and_totalled(self, running):
        token = mint_token(SECRET, {"sub": "alice", "permissions": GRANTS, "user": {"email": "a@b.c"}})  # pas de tenancy : tenant « default »
        headers = {"Authorization": f"Bearer {token}"}

        def call(method, path, **kw):
            r = httpx.request(method, f"{running}{path}", headers=headers, **kw)
            return r

        assert [m["name"] for m in call("GET", "/meta").json()] == [
            "Bin", "Country", "Customer", "Invoice", "Item", "Stock Entry", "Stock Ledger Entry", "Warehouse"]
        customer = call("POST", "/doc/Customer", json={"data": {"customer_name": "Acme"}}).json()
        item = call("POST", "/doc/Item", json={"data": {"item_name": "Vis", "uom": "pcs"}}).json()
        rows = [{"item": item["name"], "qty": 2, "rate": 5}, {"item": item["name"], "qty": 1, "rate": 0.5}]
        r = call("POST", "/doc/Invoice", json={"data": {"customer": customer["name"], "items": rows}})
        assert r.status_code == 201, r.text
        invoice = r.json()
        assert invoice["name"].endswith("-00001") and invoice["data"]["total"] == 10.5
        assert call("GET", f"/history/Invoice/{invoice['name']}").json()["total"] == 1
        assert call("DELETE", f"/doc/Item/{item['name']}").status_code == 409  # cité par une ligne

    def test_a_stock_movement_is_submitted_posted_and_cancelled_from_the_installed_package(self, running):
        """Documents soumettables et quantités décimales, de bout en bout, avec le code du wheel (palier 40 %, phase A)."""
        token = mint_token(SECRET, {"sub": "alice", "permissions": GRANTS, "user": {"email": "a@b.c"}})
        headers = {"Authorization": f"Bearer {token}"}

        def call(method, path, **kw):
            return httpx.request(method, f"{running}{path}", headers=headers, **kw)

        item = call("POST", "/doc/Item", json={"data": {"item_name": "Écrou", "uom": "pcs"}}).json()["name"]
        paris = call("POST", "/doc/Warehouse", json={"data": {"warehouse_name": "Paris"}}).json()["name"]
        day = datetime.now(UTC).date().isoformat()
        body = {"data": {"purpose": "Réception", "posting_date": day, "items": [{"item": item, "qty": "7.5", "target_warehouse": paris}]}}
        draft = call("POST", "/doc/Stock Entry", json=body).json()
        assert draft["docstatus"] == 0 and draft["data"]["items"][0]["qty"] == "7.500"

        done = call("POST", f"/doc/Stock Entry/{draft['name']}/submit", json={"version": draft["version"]})
        assert done.status_code == 200 and done.json()["docstatus"] == 1
        assert [b["data"]["qty"] for b in call("GET", "/list/Bin").json()["items"]] == ["7.500"]
        assert call("PUT", f"/doc/Stock Entry/{draft['name']}", json={"version": 2, "data": body["data"]}).status_code == 409  # figé

        cancelled = call("POST", f"/doc/Stock Entry/{draft['name']}/cancel", json={"version": 2})
        assert cancelled.status_code == 200 and cancelled.json()["docstatus"] == 2
        assert [b["data"]["qty"] for b in call("GET", "/list/Bin").json()["items"]] == ["0.000"]
        assert call("GET", "/list/Stock Ledger Entry").json()["total"] == 2  # l'écriture et son inverse
