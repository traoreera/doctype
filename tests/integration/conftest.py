"""Harnais d'intégration : démarre un vrai `Xcore` (loader, supervisor, middlewares, routeur IPC).

Part de l'`integration.yaml` du dépôt (il est donc validé au passage) et ne modifie que les chemins
et les options testées. Rien n'est écrit dans le dépôt : tout vit sous `tmp_path`.
"""

from __future__ import annotations

import shutil
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml
from fastapi import FastAPI
from xcore import Xcore

import xcore_doctype
from tests.support import FIXTURES, XAUTH_DIR, hermetic_services, install_dev_auth, mint_token

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_DIR = Path(xcore_doctype.__file__).parent / "plugin_dir"
DEV_SECRET = "integration-test-secret"

# Grants d'un « gestionnaire » de tous les DocTypes de test (voir tests/support/plugins/sales/doctypes/customer.yaml, INVOICE et probe()).
CUSTOMER_MANAGER = ["sales:customer:read", "sales:customer:write", "sales:customer:delete"]
INVOICE_MANAGER = ["sales:invoice:read", "sales:invoice:write", "sales:invoice:delete"]
DEFAULT_GRANTS = [*CUSTOMER_MANAGER, *INVOICE_MANAGER, "sales:probe:manage"]


@dataclass
class Host:
    xcore: Xcore
    app: FastAPI
    client: httpx.AsyncClient
    root: Path
    key: str
    prefix: str = "/app"
    plugin: str = "xcore-doctype"
    rbac_declared: list = None  # type: ignore[assignment]

    # ── Jetons et en-têtes ────────────────────────────────────
    def token(self, sub="alice", tenant: str | None = "acme", **claims: Any) -> str:
        """Jeton de développement. Par défaut : tous les grants des DocTypes de test (`permissions=` pour restreindre)."""
        claims.setdefault("permissions", DEFAULT_GRANTS)
        body = {"sub": sub, **claims}
        if tenant is not None:
            body["tenant_id"] = tenant
        return mint_token(DEV_SECRET, body)

    def headers(self, token: str | None, tenant: str | None, *, key: bool = False) -> dict[str, str]:
        h: dict[str, str] = {}
        if token is not None:
            h["Authorization"] = f"Bearer {token}"
        if tenant is not None:
            h["X-Tenant-ID"] = tenant
        if key:
            h["X-Plugin-Key"] = self.key
        return h

    # ── Appels ────────────────────────────────────────────────
    @property
    def base(self) -> str:
        return f"{self.prefix}/{self.plugin}"

    async def http(self, method: str, path: str, *, token: str | None, tenant: str | None = "acme", **kw) -> httpx.Response:
        return await self.client.request(method, self.base + path, headers=self.headers(token, tenant), **kw)

    async def action(self, name: str, payload: dict, *, token: str | None, tenant: str | None = "acme", plugin: str | None = None) -> dict:
        """Appel par le routeur IPC d'xcore (pipeline complet). Retourne le `result` de l'action. `plugin` : un autre que xcore-doctype."""
        r = await self.client.post(
            f"{self.prefix}/ipc/{plugin or self.plugin}/{name}",
            json={"payload": payload},
            headers=self.headers(token, tenant, key=True),
        )
        assert r.status_code == 200, (r.status_code, r.text)
        return r.json()["result"]

    async def ipc(self, method: str, path: str, **kw) -> httpx.Response:
        return await self.client.request(method, f"{self.prefix}/ipc{path}", headers={"X-Plugin-Key": self.key}, **kw)

    def plugin_routes(self) -> list[str]:
        """Opérations réellement montées, lues dans OpenAPI.

        `app.router.routes` ne convient pas : depuis FastAPI 0.14x, `include_router()` y range des
        `_IncludedRouter` sans `.path`. Le cache OpenAPI est invalidé pour ne jamais lire un état périmé.
        """
        self.app.openapi_schema = None
        paths = self.app.openapi()["paths"]
        return sorted(f"{m.upper()} {p}" for p, ops in paths.items() if p.startswith(self.base) for m in ops)


def _write_plugin(
    dest: Path, *, permissions: bool, binding: bool, doctypes_dir: Path, doctype_config: dict | None = None, extra_permissions: list[dict] | None = None,
) -> None:
    shutil.copytree(PLUGIN_DIR, dest, ignore=shutil.ignore_patterns("__pycache__"))
    manifest_path = dest / "plugin.yaml"
    manifest = yaml.safe_load(manifest_path.read_text())
    if not permissions:
        del manifest["permissions"]
    elif extra_permissions:
        manifest["permissions"] += extra_permissions  # ce qu'un opérateur ajoute à SA copie (une action `ops.*`)
    manifest["doctype"]["enforce_tenant_binding"] = binding
    manifest["doctype"]["doctypes_dirs"] = [str(doctypes_dir)]
    manifest["doctype"].update(doctype_config or {})
    manifest_path.write_text(yaml.safe_dump(manifest, allow_unicode=True))


def _write_dev_auth(dest: Path, members: dict[str, list[dict[str, str]]] | None = None, callers: list[str] | None = None) -> None:
    install_dev_auth(dest)
    manifest = yaml.safe_load((dest / "plugin.yaml").read_text())
    manifest["env"] = {"DEV_AUTH_SECRET": DEV_SECRET}  # le secret de test, en clair : un hôte de test jetable
    manifest["allowed_callers"] = callers if callers is not None else ["xcore-doctype"]  # comme le vrai xauth : le moteur lit l'annuaire
    manifest["permissions"] = [{"resource": "xauth.*", "actions": ["*"], "effect": "allow"}]
    if members is not None:
        manifest["members"] = members
    (dest / "plugin.yaml").write_text(yaml.safe_dump(manifest))


XAUTH_ADMIN = ("admin@example.com", "Test-Passw0rd!42")


def _write_xauth(root: Path):
    """Pose le vrai xauth dans l'hôte de test : une copie de `app/auth` (clés JWT jetables aux chemins absolus), compte administrateur par l'environnement.
    Retourne `None` (rien à remettre : l'environnement du processus n'est pas touché)."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    dest = root / "app" / "auth"
    shutil.copytree(XAUTH_DIR, dest, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    keys = root / "conf"
    keys.mkdir()
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    (keys / "private.pem").write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    (keys / "public.pem").write_bytes(key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
    manifest = yaml.safe_load((dest / "plugin.yaml").read_text())
    manifest["jwt"]["private_key_path"], manifest["jwt"]["public_key_path"] = str(keys / "private.pem"), str(keys / "public.pem")
    manifest["rate_limit"] = {"enabled": False, "routes": {}}  # un test se connecte à répétition : pas de limitation de débit
    # Le compte administrateur est écrit dans le manifeste de la COPIE de test : ni `.env` ni l'environnement du développeur (ni ce que son `plugin.yaml` porte)
    # n'entrent dans un test.
    manifest["env"] = {"ADMIN_EMAIL": XAUTH_ADMIN[0], "ADMIN_PASSWORD": XAUTH_ADMIN[1]}
    (dest / "plugin.yaml").write_text(yaml.safe_dump(manifest, allow_unicode=True))
    return None


SALES_PLUGIN = '''
from xcore.sdk import TrustedBase, error
from xcore_doctype import DocumentValidationError


class Plugin(TrustedBase):
    """Plugin de confiance : enregistre des hooks dans xcore-doctype par le service exporté (D16)."""

    def __init__(self):
        super().__init__()
        self.calls = []
        self.declared = []

    async def on_load(self):
        # Source unique des grants : le YAML des DocTypes. Un SEUL rbac.declare pour tout le plugin (auth désactive ce qui manque).
        self.declared = self.get_service("doctype_grants").for_plugin(self.ctx.name)
        await self.ctx.events.emit("rbac.declare", {"plugin": self.ctx.name, "grants": self.declared}, source=self.ctx.name)
        hooks = self.get_service("doctype_hooks")
        hooks.register("sales.check_amount", self.check_amount, owner=self.ctx.name)
        hooks.register("sales.stamp", self.stamp, owner=self.ctx.name)
        hooks.register("sales.notify", self.notify, owner=self.ctx.name)

    async def on_unload(self):
        self.get_service("doctype_hooks").unregister_owner(self.ctx.name)

    async def check_amount(self, event):
        self.calls.append(("validate", event.name))
        if event.data.get("amount", 0) < 0:
            raise DocumentValidationError("Montant négatif", details={"field": "amount"})
        if event.data.get("customer") == "boom":
            raise RuntimeError("secret=hunter2")

    async def stamp(self, event):
        event.data["stamp"] = f"{event.ctx.tenant_id}:{event.ctx.user_id}"

    async def notify(self, event):
        self.calls.append(("after_save", event.name, event.data.get("amount")))
        if event.data.get("customer") == "notify-fails":
            raise RuntimeError("smtp down")

    async def handle(self, action, payload):
        return error("no actions", "unknown_action")
'''

RULES_PLUGIN = '''
import asyncio
import json


class Plugin:
    """Plugin SANDBOXÉ : un hook ne touche jamais la base, il répond au document reçu (D4)."""

    async def handle(self, action, payload):
        if action == "check_credit":
            doc = dict(payload["doc"])
            if doc.get("amount", 0) > 10000:
                return {"status": "error", "code": "credit_exceeded", "msg": "Crédit dépassé"}
            doc["note"] = "checked-by-sandbox"
            return {"status": "ok", "doc": doc}
        if action == "size":
            return {"status": "ok", "size": len(json.dumps(payload))}
        if action == "slow":
            await asyncio.sleep(3)  # assez pour dépasser un budget de 1 s ; court pour ne pas retarder l'arrêt de l'hôte
        if action == "crash":
            raise RuntimeError("boom password=hunter2")
        if action == "evil":
            return {"status": "ok", "doc": {**payload["doc"], "owner": "mallory"}}
        if action == "tenant":
            return {"status": "ok", "doc": {**payload["doc"], "note": "user=%s roles=%s" % (payload["user"], ",".join(payload["roles"]))}}
        return {"status": "error", "code": "unknown_action", "msg": action}
'''

INVOICE = {
    "schema_version": 1,
    "name": "Invoice",
    "module": "sales",
    "fields": [
        {"fieldname": "customer", "fieldtype": "Text", "required": True},
        {"fieldname": "amount", "fieldtype": "Number", "default": 0},
        {"fieldname": "note", "fieldtype": "Text"},
        {"fieldname": "stamp", "fieldtype": "Text"},
    ],
    # Pas de `permissions` : convention `sales:invoice:read|write|delete`.
    "hooks": {
        "validate": ["sales.check_amount"],
        "before_save": ["sales.stamp", "action:sales-rules.check_credit"],
        "after_save": ["sales.notify"],
    },
}


def probe(action: str) -> dict:
    """Un DocType dont le seul hook est une action donnée du plugin sandboxé."""
    return {
        "schema_version": 1,
        "name": f"Probe {action.title()}",
        "fields": [{"fieldname": "blob", "fieldtype": "Text"}, {"fieldname": "note", "fieldtype": "Text"}],
        "permissions": {a: ["sales:probe:manage"] for a in ("read", "write", "create", "delete")},
        "hooks": {"before_save": [f"action:sales-rules.{action}"]},
    }


def _write_hook_plugins(app_dir: Path) -> None:
    sales = app_dir / "sales"
    (sales / "src").mkdir(parents=True)
    (sales / "src" / "main.py").write_text(SALES_PLUGIN)
    (sales / "plugin.yaml").write_text(
        yaml.safe_dump(
            {"name": "sales", "version": "0.1.0", "framework_version": ">=2.9", "execution_mode": "trusted",
             "entry_point": "src/main.py", "requires": ["xcore-doctype"]}
        )
    )
    rules = app_dir / "sales-rules"
    (rules / "src").mkdir(parents=True)
    (rules / "src" / "main.py").write_text(RULES_PLUGIN)
    (rules / "plugin.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "sales-rules", "version": "0.1.0", "framework_version": ">=2.9",
                "execution_mode": "sandboxed", "entry_point": "src/main.py",
                "allowed_imports": ["json", "asyncio"],
                "allowed_callers": ["xcore-doctype"],
                "permissions": [{"resource": "*", "actions": ["execute"], "effect": "allow"}],
                # 10 s = défaut d'xcore. Ce délai borne AUSSI le démarrage du worker (ping initial) : à 3 s, un
                # `import xcore` ralenti par une machine chargée faisait échouer l'activation du plugin.
                "resources": {"timeout_seconds": 10, "rate_limit": {"calls": 10000, "period_seconds": 60}},
            }
        )
    )


async def _ensure_loaded(host: "Host", name: str, attempts: int = 5) -> None:
    """Le démarrage d'un worker sandbox a un délai FIXE de 5 s (ping initial, non configurable) et peut échouer
    sur une machine chargée : `import xcore` dans le sous-processus est lent quand plusieurs démarrent ensemble.

    Réessayer le chargement est ce que ferait un opérateur. Si ça échoue encore, on le dit clairement plutôt
    que de laisser les tests échouer en 503 sans explication.
    """
    import asyncio

    for _ in range(attempts):
        plugins = (await host.ipc("GET", "/status")).json()["plugins"]
        if any(p["name"] == name and p.get("loaded", True) and p["state"] in ("ready", "running") for p in plugins):
            return
        try:
            await host.ipc("POST", f"/{name}/load")
        except Exception:  # noqa: BLE001 - l'endpoint remonte l'échec de ping en exception : on réessaie
            await asyncio.sleep(0.5)
    raise RuntimeError(f"le plugin sandboxé {name!r} ne démarre pas après {attempts} essais (voir {host.root}/log/app.log)")


@asynccontextmanager
async def boot_host(
    tmp_path: Path,
    *,
    permissions: bool = True,
    binding: bool = True,
    tenancy: bool = True,
    isolate_db: bool = False,
    strict_trusted: bool = False,
    db_type: str = "sqlite+aio",
    db_url: str | None = None,
    with_doctype_plugin: bool = True,
    hook_plugins: bool = False,
    extra_doctypes: tuple[dict, ...] = (),
    doctype_config: dict | None = None,
    plugin_permissions: list[dict] | None = None,
    operator_doctypes: bool = True,
    link_plugins: dict[str, Path] | None = None,
    inline_plugins: dict[str, tuple[str, dict[str, str]]] | None = None,
    xauth: bool = False,
    auth_members: dict[str, list[dict[str, str]]] | None = None,
    auth_callers: list[str] | None = None,
):
    """`operator_doctypes=False` : le dossier de l'opérateur reste vide, seuls les plugins apportent des DocTypes. `plugin_permissions` : des
    entrées ajoutées au bloc `permissions` du manifeste de xcore-doctype (l'ouverture d'une action d'opérateur `ops.*`).
    `link_plugins` : plugins du dépôt (nom -> dossier) liés dans `app/`. `inline_plugins` : nom -> (main.py, fichiers annexes),
    avec un manifeste de confiance qui `requires: [xcore-doctype]`. `auth_callers` : les `allowed_callers` du plugin de test (défaut : le moteur). `auth_members` : l'annuaire que le plugin de test imite (`xauth.users.list.tenant` : organisation -> [{id, email}]). `xauth=True` : le VRAI xauth (`app/auth`) remplace le plugin de test (nommé `auth`, comme xauth : les plugins du dépôt le `requires`)
    (clés JWT et compte administrateur jetables ; voir `XAUTH_ADMIN`) : les jetons viennent de `POST /app/auth/login`. Le vrai xauth
    (`app/auth`) n'est PAS dans le dépôt : sans lui sur le poste, `xauth=True` saute avec cette raison."""
    if xauth and not XAUTH_DIR.is_dir():
        pytest.skip("le vrai xauth (app/auth) est absent de ce poste : test sans objet")
    root = tmp_path / "host"
    (root / "app").mkdir(parents=True)
    doctypes = root / "doctypes"
    doctypes.mkdir()
    sample = FIXTURES / "sales" / "doctypes"
    if operator_doctypes:
        shutil.copy(sample / "customer.yaml", doctypes / "customer.yaml")
        shutil.copy(sample / "country.yaml", doctypes / "country.yaml")  # cible du champ Link `Customer.country`
    for name, source in (link_plugins or {}).items():
        (root / "app" / name).symlink_to(source)
    for name, (main, files) in (inline_plugins or {}).items():
        base = root / "app" / name
        (base / "src").mkdir(parents=True)
        (base / "src" / "main.py").write_text(main)
        for rel, content in files.items():
            (base / rel).parent.mkdir(parents=True, exist_ok=True)
            (base / rel).write_text(content)
        if "plugin.yaml" not in files:  # un plugin qui apporte son manifeste (permissions, appelants…) le garde
            (base / "plugin.yaml").write_text(
                yaml.safe_dump({"name": name, "version": "0.1.0", "framework_version": ">=2.9", "execution_mode": "trusted",
                                "entry_point": "src/main.py", "requires": ["xcore-doctype"]})
            )
    if hook_plugins:
        (doctypes / "invoice.yaml").write_text(yaml.safe_dump(INVOICE))
        _write_hook_plugins(root / "app")
    for extra in extra_doctypes:
        (doctypes / f"{extra['name'].lower().replace(' ', '_')}.yaml").write_text(yaml.safe_dump(extra))
    if with_doctype_plugin:
        _write_plugin(root / "app" / "xcore-doctype", permissions=permissions, binding=binding, doctypes_dir=doctypes,
                      doctype_config=doctype_config, extra_permissions=plugin_permissions)
    restore_env = _write_xauth(root) if xauth else (_write_dev_auth(root / "app" / "auth", auth_members, auth_callers), None)[1]

    config = hermetic_services(yaml.safe_load((ROOT / "integration.yaml").read_text()))  # jamais de Redis dans un test
    config["plugins"]["directory"] = str(root / "app")
    config["app"].pop("dotenv", None)  # le `.env` du développeur n'entre jamais dans un test
    config["plugins"]["strict_trusted"] = strict_trusted
    config["tenancy"] = {"enabled": tenancy, "isolate_db": isolate_db}
    db = config["services"]["databases"]["default"]
    db["type"] = db_type
    db["url"] = db_url or (
        f"sqlite+aiosqlite:///{root}/t.db" if db_type.endswith("aio") else f"sqlite:///{root}/t.db"
    )
    db.pop("echo", None)
    config["observability"]["logging"]["file"] = str(root / "log" / "app.log")
    config_path = root / "integration.yaml"
    config_path.write_text(yaml.safe_dump(config))

    xcore = Xcore(config_path=str(config_path))
    declared: list[dict] = []  # ce que recevrait auth : chaque événement rbac.declare

    async def collect(event):
        declared.append(event.data)

    xcore.events.subscribe("rbac.declare", collect)

    from contextlib import asynccontextmanager as acm

    @acm
    async def lifespan(app: FastAPI):
        await xcore.boot(app)
        yield
        await xcore.shutdown()

    app = FastAPI(lifespan=lifespan)
    xcore.setup(app)  # installe TenantMiddleware : sans lui, request.state.tenant_id n'existe pas

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            host = Host(xcore, app, client, root, key=config["app"]["secret_key"])
            host.rbac_declared = declared
            if hook_plugins:
                await _ensure_loaded(host, "sales-rules")
            try:
                yield host
            finally:
                if restore_env is not None:
                    restore_env()


@pytest.fixture
async def host(tmp_path):
    async with boot_host(tmp_path) as h:
        yield h
