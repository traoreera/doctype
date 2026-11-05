"""Webhook entrant signé (HMAC-SHA256 par workflow) — XFlow.

Couvre : absence/fausseté de signature, rejeu, fenêtre d'horodatage, mauvais tenant,
signature correcte, déclenchement interne sans signature, rotation, migration défensive.
Les secrets sont générés à la volée : aucune valeur littérale.
"""
from __future__ import annotations

import json
import secrets
import time
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from xcore_doctype.platform.xflow.src.main import Plugin
from xcore_doctype.platform.xflow.src.repositories.models import Base
from xcore_doctype.platform.xflow.src.schemas import WorkflowDefinition
from xcore_doctype.platform.xflow.src.services.webhook_auth import (
    MAX_BODY_BYTES,
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    WebhookGuard,
    WebhookSecretStore,
    sign,
    verify,
)

TENANT_A = "tenant-a"
TENANT_B = "tenant-b"
NAME = "hook"


def _definition(trigger_type: str = "webhook", **trigger) -> WorkflowDefinition:
    return WorkflowDefinition(
        name=NAME,
        trigger={"type": trigger_type, **trigger},
        steps=[{"id": "s1", "type": "action", "plugin": "p", "action": "a", "payload": {}}],
    )


class FakeDB:
    def __init__(self) -> None:
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        self._sm = async_sessionmaker(self.engine, expire_on_commit=False)

    def session(self):
        return self._sm()


class FakeRegistry:
    def __init__(self) -> None:
        self.defs: dict[tuple[str, str], WorkflowDefinition] = {}

    async def get(self, tenant_id, name):
        return self.defs.get((tenant_id, name))

    async def register(self, tenant_id, definition):
        self.defs[(tenant_id, definition.name)] = definition
        return definition

    async def unregister(self, tenant_id, name):
        return self.defs.pop((tenant_id, name), None)


class FakeEngine:
    def __init__(self) -> None:
        self.runs: list[dict] = []

    async def init_run(self, definition, tenant_id, trigger_payload, trigger_type):
        self.runs.append(
            {"workflow": definition.name, "tenant_id": tenant_id,
             "payload": trigger_payload, "trigger_type": trigger_type}
        )
        return SimpleNamespace(run_id=f"run-{len(self.runs)}", status=SimpleNamespace(value="pending"))


@pytest.fixture
async def env():
    db = FakeDB()
    async with db.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    plugin = Plugin.__new__(Plugin)
    plugin._registry = FakeRegistry()
    plugin._engine = FakeEngine()
    plugin._webhook_secrets = WebhookSecretStore(db)
    plugin._webhook_guard = WebhookGuard(max_per_minute=1000)
    plugin.enqueued = []

    async def _enqueue(run_id):
        plugin.enqueued.append(run_id)

    plugin._enqueue_run_id = _enqueue

    for tenant in (TENANT_A, TENANT_B):
        plugin._registry.defs[(tenant, NAME)] = _definition()
    secret_a = await plugin._webhook_secrets.rotate(TENANT_A, NAME)
    secret_b = await plugin._webhook_secrets.rotate(TENANT_B, NAME)

    app = FastAPI()
    # get_router() selon le SDK récent ; RouterIn() pour le SDK installé dans le venv.
    app.include_router(plugin.get_router() or plugin.RouterIn())
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")
    yield SimpleNamespace(plugin=plugin, client=client, secret_a=secret_a, secret_b=secret_b, db=db)
    await client.aclose()
    await db.engine.dispose()


def _headers(secret: str, body: bytes, ts: int | None = None) -> dict:
    ts = int(time.time()) if ts is None else ts
    return {
        TIMESTAMP_HEADER: str(ts),
        SIGNATURE_HEADER: "sha256=" + sign(secret, str(ts), body),
        "Content-Type": "application/json",
    }


async def _post(env, tenant, body: bytes, headers: dict | None = None, name: str = NAME):
    return await env.client.post(
        f"/webhook/{name}", params={"tenant_id": tenant} if tenant else None,
        content=body, headers=headers or {"Content-Type": "application/json"},
    )


def _d(out: dict) -> dict:
    """Charge utile, quelle que soit la forme du contrat ok() (plate ou sous `data`)."""
    return out.get("data", out)


def _is_ok(out: dict) -> bool:
    return out.get("status") in ("ok", "success")


BODY = json.dumps({"order": 42}).encode()


# --- unitaires ------------------------------------------------------------

def test_verify_accepte_et_refuse():
    key = secrets.token_urlsafe(16)
    now = 1_000_000
    sig = "sha256=" + sign(key, str(now), b"x")
    assert verify(key, str(now), sig, b"x", now=now)
    assert not verify(key, str(now), sig, b"y", now=now)
    assert not verify(secrets.token_urlsafe(16), str(now), sig, b"x", now=now)
    assert not verify(key, str(now), "sha256=zz", b"x", now=now)
    assert not verify(key, "abc", sig, b"x", now=now)
    assert not verify(key, str(now), None, b"x", now=now)
    assert not verify(key, str(now), sig, b"x", now=now + 301)
    assert not verify(key, str(now), sig, b"x", now=now - 301)


# --- refus uniformes --------------------------------------------------------

async def test_sans_signature_refuse(env):
    r = await _post(env, TENANT_A, BODY)
    assert r.status_code == 404
    assert env.plugin._engine.runs == []


async def test_signature_fausse_refusee(env):
    h = _headers(secrets.token_urlsafe(24), BODY)
    r = await _post(env, TENANT_A, BODY, h)
    assert r.status_code == 404
    assert env.plugin._engine.runs == []


async def test_corps_altere_refuse(env):
    h = _headers(env.secret_a, BODY)
    r = await _post(env, TENANT_A, b'{"order": 43}', h)
    assert r.status_code == 404
    assert env.plugin._engine.runs == []


async def test_horodatage_hors_fenetre_refuse(env):
    for delta in (-3600, 3600):
        h = _headers(env.secret_a, BODY, ts=int(time.time()) + delta)
        r = await _post(env, TENANT_A, BODY, h)
        assert r.status_code == 404
    assert env.plugin._engine.runs == []


async def test_rejeu_dans_la_fenetre_refuse(env):
    h = _headers(env.secret_a, BODY)
    assert (await _post(env, TENANT_A, BODY, h)).status_code == 200
    assert (await _post(env, TENANT_A, BODY, h)).status_code == 404
    assert len(env.plugin._engine.runs) == 1


async def test_mauvais_tenant_refuse(env):
    # Signature valide de A présentée pour B (et inversement) : refus.
    assert (await _post(env, TENANT_B, BODY, _headers(env.secret_a, BODY))).status_code == 404
    assert (await _post(env, TENANT_A, BODY, _headers(env.secret_b, BODY))).status_code == 404
    # Tenant inexistant ou absent.
    assert (await _post(env, "inconnu", BODY, _headers(env.secret_a, BODY))).status_code == 404
    assert (await _post(env, None, BODY, _headers(env.secret_a, BODY))).status_code == 404
    assert env.plugin._engine.runs == []


async def test_reponses_uniformes_pas_d_enumeration(env):
    h = _headers(env.secret_a, BODY)
    r_inconnu = await _post(env, TENANT_A, BODY, h, name="n-existe-pas")
    r_fausse = await _post(env, TENANT_A, BODY, _headers("x" * 20, BODY))
    r_absente = await _post(env, TENANT_A, BODY)
    assert {r.status_code for r in (r_inconnu, r_fausse, r_absente)} == {404}
    assert r_inconnu.content == r_fausse.content == r_absente.content


# --- succès -----------------------------------------------------------------

async def test_signature_correcte_declenche_dans_le_tenant_du_secret(env):
    r = await _post(env, TENANT_A, BODY, _headers(env.secret_a, BODY))
    assert r.status_code == 200
    (run,) = env.plugin._engine.runs
    assert run["tenant_id"] == TENANT_A
    assert run["payload"] == {"order": 42}
    assert run["trigger_type"] == "webhook"
    assert env.plugin.enqueued == ["run-1"]


async def test_corps_vide_accepte_si_signe(env):
    r = await _post(env, TENANT_A, b"", _headers(env.secret_a, b""))
    assert r.status_code == 200
    assert env.plugin._engine.runs[0]["payload"] == {}


# --- bornes -----------------------------------------------------------------

async def test_corps_trop_gros_refuse(env):
    big = b"x" * (MAX_BODY_BYTES + 1)
    r = await _post(env, TENANT_A, big, _headers(env.secret_a, big))
    assert r.status_code == 413
    assert env.plugin._engine.runs == []


async def test_limitation_de_debit(env):
    env.plugin._webhook_guard = WebhookGuard(max_per_minute=3)
    codes = [(await _post(env, TENANT_A, BODY)).status_code for _ in range(5)]
    assert codes[:3] == [404, 404, 404]
    assert codes[3:] == [429, 429]


# --- déclenchements internes ---------------------------------------------------

async def test_declenchement_ipc_sans_signature(env):
    out = await env.plugin.ipc_trigger(
        {"tenant_id": TENANT_A, "workflow_name": NAME, "payload": {"a": 1}}
    )
    assert _d(out)["run_id"] == "run-1"
    assert env.plugin._engine.runs[0]["trigger_type"] == "ipc"


# --- cycle de vie du secret ------------------------------------------------------

async def test_workflow_existant_sans_secret_desactive(env):
    env.plugin._registry.defs[(TENANT_A, "legacy")] = WorkflowDefinition(
        name="legacy", trigger={"type": "webhook"},
        steps=[{"id": "s1", "type": "action", "plugin": "p", "action": "a", "payload": {}}],
    )
    r = await _post(env, TENANT_A, BODY, _headers(env.secret_a, BODY), name="legacy")
    assert r.status_code == 404
    assert env.plugin._engine.runs == []


async def test_deploiement_genere_le_secret_une_seule_fois(env):
    d = _definition().model_dump(mode="json")
    d["name"] = "neuf"
    first = await env.plugin.ipc_register({"tenant_id": TENANT_A, "definition": d})
    assert _is_ok(first)
    secret = _d(first)["webhook_secret"]
    assert secret
    again = await env.plugin.ipc_register({"tenant_id": TENANT_A, "definition": d})
    assert not _d(again).get("webhook_secret")
    r = await _post(env, TENANT_A, BODY, _headers(secret, BODY), name="neuf")
    assert r.status_code == 200


async def test_deploiement_non_webhook_ne_genere_pas_de_secret(env):
    d = _definition("event", event_name="x.y").model_dump(mode="json")
    d["name"] = "evt"
    out = await env.plugin.ipc_register({"tenant_id": TENANT_A, "definition": d})
    assert not _d(out).get("webhook_secret")
    assert await env.plugin._webhook_secrets.status(TENANT_A, "evt") is None


async def test_rotation_invalide_l_ancien_secret(env):
    out = await env.plugin.ipc_webhook_rotate({"tenant_id": TENANT_A, "workflow_name": NAME})
    new = _d(out)["webhook_secret"]
    assert new and new != env.secret_a
    assert (await _post(env, TENANT_A, BODY, _headers(env.secret_a, BODY))).status_code == 404
    assert (await _post(env, TENANT_A, BODY, _headers(new, BODY))).status_code == 200


async def test_rotation_workflow_inconnu(env):
    out = await env.plugin.ipc_webhook_rotate({"tenant_id": TENANT_A, "workflow_name": "zzz"})
    assert out["status"] == "error"


async def test_statut_n_expose_pas_le_secret(env):
    out = await env.plugin.ipc_webhook_status({"tenant_id": TENANT_A, "workflow_name": NAME})
    assert _d(out)["configured"] is True
    assert env.secret_a not in json.dumps(out)
    legacy = await env.plugin.ipc_webhook_status({"tenant_id": TENANT_A, "workflow_name": "zzz"})
    assert legacy["status"] == "error"


async def test_suppression_du_workflow_supprime_le_secret(env):
    await env.plugin.ipc_unregister({"tenant_id": TENANT_A, "workflow_name": NAME})
    assert await env.plugin._webhook_secrets.get(TENANT_A, NAME) is None


async def test_secret_chiffre_au_repos_si_cle_fournie(env, monkeypatch):
    monkeypatch.setenv("XFLOW_WEBHOOK_KEK", secrets.token_urlsafe(32))
    store = WebhookSecretStore(env.db)
    s = await store.rotate(TENANT_A, "chiffre")
    from sqlalchemy import text
    async with env.db.session() as session:
        raw = (await session.execute(
            text("select secret_value from xflow_webhook_secrets where workflow_name='chiffre'")
        )).scalar_one()
    assert s not in raw and raw.startswith("enc:")
    assert await store.get(TENANT_A, "chiffre") == s


def test_ancien_champ_webhook_secret_du_trigger_ignore():
    d = _definition(webhook_secret=secrets.token_urlsafe(8))
    assert d.trigger.webhook_secret is None
    assert "webhook_secret" not in json.dumps(d.model_dump(mode="json")["trigger"]) or \
        d.model_dump(mode="json")["trigger"]["webhook_secret"] is None
