"""Un orchestrateur (xflow) agit sur les documents AU NOM de quelqu'un : le contrat que le moteur lui offre.

xflow exécute ses runs en tâche de fond, sans requête. Il appelle donc les actions `doc.*` par `ctx.caller(…, tenant_id=, principal=)`
avec l'organisation du run et l'identité de celui qu'il représente, dont il redemande les grants à xauth à chaque exécution
(`xauth.user.permissions`) : `{"sub", "permissions", "user": {"id", "tenant_id"}}`. Le moteur n'a rien de spécial pour lui : c'est
une identité comme une autre, jugée par les mêmes règles (grants, séparation des tâches, organisation couverte par l'identité).

Le plugin `xflow` ci-dessous imite EXACTEMENT l'appel du vrai (`app/xflows/src/runtime/engine.py`, `WorkflowEngine._call`) : il n'est
pas dans le dépôt, ce test n'en dépend pas.
"""

from __future__ import annotations

import yaml

from tests.integration.conftest import boot_host
from tests.support import FIXTURES

XFLOW_MAIN = '''
from xcore.sdk import TrustedBase, ok


class Plugin(TrustedBase):
    async def handle(self, action, payload):
        # Ce que fait `WorkflowEngine._call` : organisation du run et identité résolue, passées explicitement.
        return await self.ctx.caller(
            "xcore-doctype", payload["action"], payload["payload"],
            caller=self.ctx.name, tenant_id=payload["tenant"], principal=payload.get("principal"),
        )
'''
XFLOW_MANIFEST = yaml.safe_dump({
    "name": "xflow", "version": "0.1.0", "framework_version": ">=2.9", "execution_mode": "trusted", "entry_point": "src/main.py",
    "requires": ["xcore-doctype"], "allowed_callers": ["*"],
    "permissions": [{"resource": "run", "actions": ["execute"], "effect": "allow"}],
})
READ = ["flux:demande:read", "flux:demande:write"]


def principal(sub: str, grants: list[str], tenant: str = "acme") -> dict:
    """L'identité telle que xflow la reconstruit (engine.py `_principal`)."""
    return {"sub": sub, "permissions": grants, "user": {"id": sub, "tenant_id": tenant}}


async def _host(tmp_path):
    return boot_host(
        tmp_path, link_plugins={"flux": FIXTURES / "flux"},
        inline_plugins={"xflow": (XFLOW_MAIN, {"plugin.yaml": XFLOW_MANIFEST})},
    )


async def _submitted(h) -> dict:
    """Une demande d'alice, envoyée au responsable (état « À approuver »)."""
    alice = h.token("alice", permissions=[*READ, "flux:demande:workflow_soumettre"])
    doc = (await h.action("doc.save", {"doctype": "Demande", "data": {"objet": "Congés", "jours": 3}}, token=alice))["doc"]
    out = await h.action("doc.transition", {"doctype": "Demande", "name": doc["name"], "action": "Soumettre", "version": doc["version"]}, token=alice)
    return out["doc"]


async def _as_xflow(h, action: str, payload: dict, who: dict | None, tenant: str = "acme") -> dict:
    token = h.token("operator")  # le jeton n'ouvre que l'appel au plugin xflow ; l'identité qui compte est `principal`
    return await h.action("run", {"action": action, "payload": payload, "tenant": tenant, "principal": who}, token=token, plugin="xflow")


async def test_an_orchestrator_moves_a_document_with_the_identity_it_represents(tmp_path):
    async with await _host(tmp_path) as h:
        doc = await _submitted(h)
        bob = principal("bob", [*READ, "flux:demande:workflow_approuver"])
        out = await _as_xflow(h, "doc.transition", {"doctype": "Demande", "name": doc["name"], "action": "Approuver", "version": doc["version"]}, bob)
        assert out["status"] == "ok", out
        assert out["doc"]["workflow_state"] == "Approuvée"
        assert out["doc"]["docstatus"] == 1
        history = (await h.action("doc.history", {"doctype": "Demande", "name": doc["name"]}, token=h.token("alice", permissions=READ)))
        assert history["items"][0]["action"] == "submit" and history["items"][0]["user"] == "bob"  # l'historique nomme la personne représentée, pas l'orchestrateur


async def test_without_an_identity_the_orchestrator_is_refused(tmp_path):
    async with await _host(tmp_path) as h:
        doc = await _submitted(h)
        out = await _as_xflow(h, "doc.get", {"doctype": "Demande", "name": doc["name"]}, None)
        assert (out["status"], out["code"]) == ("error", "unauthenticated")


async def test_the_represented_person_s_rules_apply_grants_and_separation_of_duties(tmp_path):
    async with await _host(tmp_path) as h:
        doc = await _submitted(h)
        move = {"doctype": "Demande", "name": doc["name"], "action": "Approuver", "version": doc["version"]}
        no_grant = await _as_xflow(h, "doc.transition", move, principal("bob", READ))
        assert no_grant["status"] == "error" and no_grant["code"] == "permission_denied", no_grant
        own = await _as_xflow(h, "doc.transition", move, principal("alice", [*READ, "flux:demande:workflow_approuver"]))
        assert own["status"] == "error" and own["code"] == "not_owner", own  # un automate ne contourne pas la séparation des tâches


async def test_the_run_s_organisation_must_be_covered_by_the_identity(tmp_path):
    async with await _host(tmp_path) as h:
        doc = await _submitted(h)
        intruder = principal("mallory", [*READ, "flux:demande:workflow_approuver"], tenant="globex")
        out = await _as_xflow(h, "doc.get", {"doctype": "Demande", "name": doc["name"]}, intruder, tenant="acme")
        assert out["status"] == "error" and out["code"] in ("tenant_mismatch", "forbidden_tenant"), out
