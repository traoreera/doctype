"""Le Desk dans un vrai navigateur, face à un vrai serveur xcore : `ui/e2e/desk.e2e.mjs`.

Ce test lance un hôte uvicorn dans un dossier temporaire (base SQLite vierge, tenant neuf), fabrique un jeton de
développement, puis exécute le scénario Playwright de `ui/e2e/`. Il vérifie la sortie de phase 2 : « le DocType Client se
gère entièrement depuis l'UI ».

Il est IGNORÉ (avec la raison) quand il manque un maillon : Node, `pnpm install` dans `ui/`, le build (`pnpm build`) ou un
Chromium installé. `XCORE_DOCTYPE_SKIP_E2E=1` le désactive, `XCORE_DOCTYPE_E2E_SHOTS=<dossier>` garde les captures d'écran.
"""

from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx
import pytest
import yaml

import xcore_doctype
from tests.support import FIXTURES, install_dev_auth, mint_token

ROOT = Path(__file__).resolve().parents[2]
UI = ROOT / "ui"
DIST = Path(xcore_doctype.__file__).parent / "ui" / "dist"
PLUGIN_DIR = Path(xcore_doctype.__file__).parent / "plugin_dir"
SECRET = "e2e-browser-secret"
PREFIX = "/app/xcore-doctype"
GRANTS = (
    [f"sales:{t}:{a}" for t in ("customer", "country", "invoice") for a in ("read", "write", "delete")]
    + [f"stock:{t}:{a}" for t in ("item", "warehouse", "stock_entry") for a in ("read", "write", "delete")]
    + ["stock:stock_entry:submit", "stock:stock_entry:cancel", "stock:stock_ledger_entry:read", "stock:bin:read"]
)


def _chromium_missing() -> str | None:
    node = shutil.which("node")
    if node is None:
        return "node est introuvable"
    if not (UI / "node_modules" / "playwright-core").is_dir():
        return "dépendances du Desk absentes (pnpm install dans ui/)"
    if not (DIST / "index.html").is_file():
        return "Desk non construit (pnpm build dans ui/)"
    probe = (
        "import { chromium } from 'playwright-core'; import { existsSync } from 'node:fs';"
        "process.exit(process.env.CHROME ? (existsSync(process.env.CHROME) ? 0 : 1) : existsSync(chromium.executablePath()) ? 0 : 1)"
    )
    ok = subprocess.run([node, "--input-type=module", "-e", probe], cwd=UI, capture_output=True).returncode == 0
    return None if ok else "aucun Chromium installé pour Playwright (npx playwright-core install chromium, ou CHROME=<exécutable>)"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def host(tmp_path):
    """Un hôte xcore réel, sur un port libre, avec le plugin, dev-auth et les DocTypes d'exemple."""
    config = yaml.safe_load((ROOT / "integration.yaml").read_text())
    config["services"]["databases"]["default"]["url"] = f"sqlite+aiosqlite:///{tmp_path / 'e2e.db'}"
    config["tenancy"] = {"enabled": True, "isolate_db": False}
    config["app"]["server"]["reload"] = False
    (tmp_path / "integration.yaml").write_text(yaml.safe_dump(config))
    # Le point d'entrée de l'hôte : écrit sur place (le `main.py` de la racine est local au développement, hors dépôt).
    (tmp_path / "main.py").write_text(
        "from contextlib import asynccontextmanager\n\nfrom fastapi import FastAPI\nfrom xcore import Xcore\n\n"
        'xcore = Xcore(config_path="integration.yaml")\n\n\n'
        "@asynccontextmanager\nasync def lifespan(app: FastAPI):\n    await xcore.boot(app)\n    yield\n    await xcore.shutdown()\n\n\n"
        'app = FastAPI(title=".", version="0.1.0", lifespan=lifespan)\n\n'
        "xcore.setup(app)\n\n\n@app.get(\"/health\")\nasync def health():\n    return {\"status\": \"ok\"}\n"
    )
    (tmp_path / "app").mkdir()
    # Une COPIE du plugin : ce scénario se connecte par jeton collé (plugin de test `auth`, HMAC), pas par xauth : `login_url` y est retiré.
    shutil.copytree(PLUGIN_DIR, tmp_path / "app" / "xcore-doctype", ignore=shutil.ignore_patterns("__pycache__"))
    manifest = tmp_path / "app" / "xcore-doctype" / "plugin.yaml"
    manifest.write_text(re.sub(r"^(\s*)login_url:.*$", r"\1login_url: null", manifest.read_text(), flags=re.M))
    install_dev_auth(tmp_path / "app" / "auth", link=True)  # le plugin de TEST, nommé `auth` : les plugins du dépôt le `requires`
    (tmp_path / "app" / "stock").symlink_to(FIXTURES / "stock")  # le stock : articles, entrepôts, mouvements soumettables
    (tmp_path / "app" / "sales").symlink_to(FIXTURES / "sales")  # le plugin métier : il apporte les DocTypes et le hook du total

    port = _free_port()
    log = (tmp_path / "host.log").open("w")
    env = {**os.environ, "XCORE_DEV_AUTH_SECRET": SECRET}
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=tmp_path, env=env, stdout=log, stderr=subprocess.STDOUT,
    )
    base = f"http://127.0.0.1:{port}{PREFIX}"
    try:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                pytest.fail(f"l'hôte s'est arrêté au démarrage :\n{(tmp_path / 'host.log').read_text()[-2000:]}")
            try:
                if httpx.get(f"{base}/desk/config.json", timeout=1).status_code == 200:
                    break
            except httpx.TransportError:
                pass
            time.sleep(0.3)
        else:
            pytest.fail("l'hôte n'a pas répondu en 60 s")
        yield base
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()


@pytest.mark.skipif(os.environ.get("XCORE_DOCTYPE_SKIP_E2E") == "1", reason="XCORE_DOCTYPE_SKIP_E2E=1")
def test_the_customer_doctype_is_managed_entirely_from_the_desk(host):
    missing = _chromium_missing()
    if missing:
        pytest.skip(missing)
    tenant = f"e2e-{uuid.uuid4().hex[:8]}"  # un tenant neuf : le scénario part d'une liste vide quoi qu'il arrive
    token = mint_token(
        SECRET,
        {"sub": "alice", "tenant_id": tenant, "permissions": GRANTS, "user": {"email": "alice@example.com", "tenant_id": tenant}},
        ttl=3600,
    )
    env = {
        **os.environ,
        "DESK_URL": f"{host}/desk/",
        "DESK_API": host,
        "DESK_TOKEN": token,
        "DESK_TENANT": tenant,
        "SHOTS_DIR": os.environ.get("XCORE_DOCTYPE_E2E_SHOTS", ""),
    }
    run = subprocess.run(["node", "e2e/desk.e2e.mjs"], cwd=UI, env=env, capture_output=True, text=True, timeout=300)
    assert run.returncode == 0, f"scénario du Desk en échec :\n{run.stdout}\n{run.stderr}"
