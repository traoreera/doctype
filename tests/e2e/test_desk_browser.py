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
from tests.support import FIXTURES, hermetic_services, install_dev_auth, mint_token

ROOT = Path(__file__).resolve().parents[2]
UI = ROOT / "ui"
DIST = Path(xcore_doctype.__file__).parent / "ui" / "dist"
PLUGIN_DIR = Path(xcore_doctype.__file__).parent / "plugin_dir"
SECRET = "e2e-browser-secret"
PREFIX = "/app/xcore-doctype"
XFLOW_DIR = ROOT / "app" / "xflows"  # l'orchestrateur, hors dépôt (poste du développeur) : sans lui, le scénario des automatisations saute
XFLOW_GRANTS = ["xflow:workflows:read", "xflow:workflows:write", "xflow:runs:read", "xflow:runs:write"]
GRANTS = (
    [f"sales:{t}:{a}" for t in ("customer", "country", "invoice") for a in ("read", "write", "delete")]
    + [f"stock:{t}:{a}" for t in ("item", "warehouse", "stock_entry") for a in ("read", "write", "delete")]
    + ["stock:stock_entry:submit", "stock:stock_entry:cancel", "stock:stock_ledger_entry:read", "stock:bin:read"]
    + [f"biblio:{t}:{a}" for t in ("livre", "emprunt") for a in ("read", "write", "delete")]
    + [f"flux:demande:{a}" for a in ("read", "write", "delete", "workflow_soumettre", "workflow_approuver", "workflow_refuser", "workflow_reprendre")]
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
def host(tmp_path, request):
    """Un hôte xcore réel, sur un port libre, avec le plugin, dev-auth et les DocTypes d'exemple.

    Paramètre indirect `{"extensions": [...]}` : les plugins dont l'opérateur laisse servir l'extension du Desk (D28)."""
    extensions = getattr(request, "param", {}).get("extensions")
    developer = getattr(request, "param", {}).get("developer", False)
    xflow = getattr(request, "param", {}).get("xflow", False)
    if xflow:
        import importlib.util

        if not XFLOW_DIR.is_dir():
            pytest.skip("xflow (app/xflows) est absent de ce poste : scénario des automatisations sans objet")
        if importlib.util.find_spec("jmespath") is None:
            pytest.skip("jmespath (dépendance de xflow) n'est pas installé dans cet environnement")
    config = hermetic_services(yaml.safe_load((ROOT / "integration.yaml").read_text()))  # jamais de Redis dans un test
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
    if developer:
        data = yaml.safe_load(manifest.read_text())
        data.setdefault("doctype", {}).setdefault("desk", {})["developer_mode"] = True
        manifest.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False))
    if extensions is not None:
        data = yaml.safe_load(manifest.read_text())
        data.setdefault("doctype", {}).setdefault("desk", {})["extensions"] = extensions
        manifest.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False))
    if xflow:
        # Le plugin de test `auth` imite aussi `xauth.user.permissions` : xflow y relit, à chaque exécution, les grants de qui il représente.
        install_dev_auth(tmp_path / "app" / "auth")
        auth = yaml.safe_load((tmp_path / "app" / "auth" / "plugin.yaml").read_text())
        auth["allowed_callers"] = ["xcore-doctype", "xflow"]
        auth["permissions"] = [{"resource": "xauth.*", "actions": ["*"], "effect": "allow"}]
        auth["grants"] = {"*": {"alice": GRANTS + XFLOW_GRANTS}}
        (tmp_path / "app" / "auth" / "plugin.yaml").write_text(yaml.safe_dump(auth, allow_unicode=True))
        shutil.copytree(XFLOW_DIR, tmp_path / "app" / "xflow", ignore=shutil.ignore_patterns("__pycache__", "tests"))
        flow = yaml.safe_load((tmp_path / "app" / "xflow" / "plugin.yaml").read_text())
        flow["requires"] = ["auth"]  # le plugin de test `auth` n'a pas la version d'xauth (1.0.0) que xflow exige
        (tmp_path / "app" / "xflow" / "plugin.yaml").write_text(yaml.safe_dump(flow, allow_unicode=True, sort_keys=False))
    else:
        install_dev_auth(tmp_path / "app" / "auth", link=True)  # le plugin de TEST, nommé `auth` : les plugins du dépôt le `requires`
    (tmp_path / "app" / "geo").symlink_to(FIXTURES / "geo")  # le référentiel partagé (pays, devises) que `stock` requires
    (tmp_path / "app" / "stock").symlink_to(FIXTURES / "stock")  # le stock : articles, entrepôts, mouvements soumettables
    (tmp_path / "app" / "sales").symlink_to(FIXTURES / "sales")  # le plugin métier : il apporte les DocTypes et le hook du total
    (tmp_path / "app" / "biblio").symlink_to(ROOT / "examples" / "biblio")  # le plugin d'exemple de la documentation
    (tmp_path / "app" / "flux").symlink_to(FIXTURES / "flux")  # un type à circuit d'approbation (l'éditeur du circuit)

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


def _scenario(host: str, script: str, *, grants: list[str] = GRANTS, **extra_env: str) -> subprocess.CompletedProcess:
    missing = _chromium_missing()
    if missing:
        pytest.skip(missing)
    tenant = f"e2e-{uuid.uuid4().hex[:8]}"  # un tenant neuf : le scénario part d'une liste vide quoi qu'il arrive
    token = mint_token(
        SECRET,
        {"sub": "alice", "tenant_id": tenant, "permissions": grants, "user": {"email": "alice@example.com", "tenant_id": tenant}},
        ttl=3600,
    )
    env = {
        **os.environ,
        "DESK_URL": f"{host}/desk/",
        "DESK_API": host,
        "DESK_TOKEN": token,
        "DESK_TENANT": tenant,
        "SHOTS_DIR": os.environ.get("XCORE_DOCTYPE_E2E_SHOTS", ""),
        **extra_env,
    }
    return subprocess.run(["node", script], cwd=UI, env=env, capture_output=True, text=True, timeout=300)


@pytest.mark.skipif(os.environ.get("XCORE_DOCTYPE_SKIP_E2E") == "1", reason="XCORE_DOCTYPE_SKIP_E2E=1")
def test_the_customer_doctype_is_managed_entirely_from_the_desk(host):
    run = _scenario(host, "e2e/desk.e2e.mjs")
    assert run.returncode == 0, f"scénario du Desk en échec :\n{run.stdout}\n{run.stderr}"


@pytest.mark.skipif(os.environ.get("XCORE_DOCTYPE_SKIP_E2E") == "1", reason="XCORE_DOCTYPE_SKIP_E2E=1")
@pytest.mark.parametrize("host", [{"extensions": ["sales"]}], indirect=True)
def test_a_plugins_desk_extension_runs_in_the_browser_under_the_csp(host):
    """D28 : l'extension de `sales` (autorisée par l'opérateur) se charge par l'import map, sous la CSP stricte, et étend le Desk."""
    run = _scenario(host, "e2e/extensions.e2e.mjs")
    assert run.returncode == 0, f"scénario des extensions en échec :\n{run.stdout}\n{run.stderr}"


@pytest.mark.skipif(os.environ.get("XCORE_DOCTYPE_SKIP_E2E") == "1", reason="XCORE_DOCTYPE_SKIP_E2E=1")
@pytest.mark.parametrize("host", [{"extensions": ["biblio"]}], indirect=True)
def test_the_example_plugins_desk_extension_runs_in_the_browser(host):
    """`examples/biblio` : commande, page, lien, panneau et action de son extension, dans Chromium, sous la vraie CSP."""
    run = _scenario(host, "e2e/biblio.e2e.mjs")
    assert run.returncode == 0, f"scénario biblio en échec :\n{run.stdout}\n{run.stderr}"


@pytest.mark.skipif(os.environ.get("XCORE_DOCTYPE_SKIP_E2E") == "1", reason="XCORE_DOCTYPE_SKIP_E2E=1")
def test_the_workflow_builder_draws_the_circuit_and_tightens_it(host):
    """L'éditeur du circuit (Vue Flow) sous la vraie CSP : le graphe, le droit d'une transition, une transition fermée que le serveur n'offre plus."""
    run = _scenario(host, "e2e/workflow.e2e.mjs")
    assert run.returncode == 0, f"scénario du circuit en échec :\n{run.stdout}\n{run.stderr}"


@pytest.mark.skipif(os.environ.get("XCORE_DOCTYPE_SKIP_E2E") == "1", reason="XCORE_DOCTYPE_SKIP_E2E=1")
def test_the_form_builder_rearranges_and_adds_fields_in_the_browser(host):
    """« Personnaliser le formulaire » sous la vraie CSP : un vrai glisser-déposer, un champ ajouté, la fiche qui suit, le retour à l'application."""
    run = _scenario(host, "e2e/formbuilder.e2e.mjs")
    assert run.returncode == 0, f"scénario du Form Builder en échec :\n{run.stdout}\n{run.stderr}"


@pytest.mark.skipif(os.environ.get("XCORE_DOCTYPE_SKIP_E2E") == "1", reason="XCORE_DOCTYPE_SKIP_E2E=1")
@pytest.mark.parametrize("host", [{"developer": True}], indirect=True)
def test_the_developer_builder_makes_a_yaml_that_a_plugin_loads(host, tmp_path):
    """Mode développeur : un type bâti dans le navigateur, téléchargé en YAML, puis CHARGÉ comme un plugin le ferait (`xcore-doctype check`)."""
    from xcore_doctype.cli import check

    plugin = tmp_path / "support" / "doctypes"
    plugin.mkdir(parents=True)
    out = plugin / "support_ticket.yaml"
    run = _scenario(host, "e2e/devbuilder.e2e.mjs", DEV_YAML_OUT=str(out))
    assert run.returncode == 0, f"scénario du constructeur en échec :\n{run.stdout}\n{run.stderr}"
    _, findings = check([plugin], owner="support", needs=[FIXTURES / "geo" / "doctypes", FIXTURES / "stock" / "doctypes", FIXTURES / "sales" / "doctypes"])
    assert not [f for f in findings if f.level == "error"], findings


@pytest.mark.skipif(os.environ.get("XCORE_DOCTYPE_SKIP_E2E") == "1", reason="XCORE_DOCTYPE_SKIP_E2E=1")
@pytest.mark.parametrize("host", [{"xflow": True}], indirect=True)
def test_an_automation_is_built_on_the_canvas_and_runs_on_a_document_event(host):
    """xflow (hors dépôt, saute sans lui) piloté depuis le Desk : une automatisation construite au canevas (déclencheur « Demande change
    d'état », lire, condition, commenter), enregistrée, déclenchée par un vrai événement du moteur, exécutée AU NOM de qui l'a enregistrée,
    et ses étapes allumées sur le canevas."""
    run = _scenario(host, "e2e/automations.e2e.mjs", grants=GRANTS + XFLOW_GRANTS)
    assert run.returncode == 0, f"scénario des automatisations en échec :\n{run.stdout}\n{run.stderr}"
