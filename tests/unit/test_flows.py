"""La couture workflow → xflow (`flows.py`) : une transition du YAML nomme le flux qui la SUIT.

Ce que ces tests défendent :
- le flux part APRÈS le commit, une seule fois, avec ce que la transition a fait (type, nom, version, action, états) et au nom de
  l'acteur (son principal, son organisation) ; jamais le contenu du document ;
- une transition sans `flow`, ou refusée, ne lance rien ;
- xflow absent, lent, qui lève ou qui refuse ne fait jamais échouer la transition, et se journalise ;
- seul le NOM se vérifie au chargement (le flux se déploie par organisation chez xflow).
"""

from __future__ import annotations

import asyncio

import pytest

from tests.conftest import make_ctx
from tests.unit.test_workflow import APPROUVER, BASE, SOUMETTRE, STATES, TRANSITIONS, doctype
from xcore_doctype.document import DocumentService
from xcore_doctype.errors import InvalidDefinition, PermissionDenied
from xcore_doctype.flows import FlowLauncher
from xcore_doctype.registry import DocTypeRegistry

FLOWED = [dict(t, flow="commande_fournisseur") if t["action"] == "Approuver" else t for t in TRANSITIONS]


class Log:
    def __init__(self):
        self.warnings = []

    def warning(self, msg, **kw):
        self.warnings.append((msg, kw))

    info = error = warning


class Xflow:
    """Un faux xflow : enregistre ce qu'on lui demande, ou répond ce qu'on veut, ou traîne, ou lève."""

    def __init__(self, answer=None, *, fail=False, slow=False):
        # Par défaut, ce que répond xflow : l'état du RUN qu'il vient de mettre en file.
        self.calls, self.answer, self.fail, self.slow = [], answer or {"status": "pending", "run_id": "r-1"}, fail, slow

    async def __call__(self, plugin, action, payload, tenant_id, principal):
        if self.slow:
            await asyncio.sleep(10)
        if self.fail:
            raise ConnectionError("xflow n'est pas chargé")
        self.calls.append({"plugin": plugin, "action": action, "payload": payload, "tenant": tenant_id, "principal": principal})
        return self.answer


@pytest.fixture
def make(db, tables):
    def build(xflow=None, *, timeout=10.0):
        reg = DocTypeRegistry()
        reg.register(doctype(workflow={"states": STATES, "transitions": FLOWED}))
        log = Log()
        svc = DocumentService(reg, db, tables, log)
        svc.flows = FlowLauncher(xflow or Xflow(), log, timeout=timeout)
        return svc, log

    return build


def who(*grants, user="alice"):
    return make_ctx("acme", user, permissions=[*BASE, *grants])


async def submitted(svc):
    doc = await svc.create(who(), "Leave Request", {"objet": "Congés d’août"})
    return await svc.workflow_transition(who(SOUMETTRE), "Leave Request", doc["name"], "Soumettre", doc["version"])


async def test_the_named_flow_starts_after_the_transition_in_the_actor_s_name(make):
    xflow = Xflow()
    svc, log = make(xflow)
    doc = await submitted(svc)
    await svc.flows.settled()
    assert xflow.calls == []  # « Soumettre » ne nomme pas de flux
    bob = who(APPROUVER, user="bob")
    moved = await svc.workflow_transition(bob, "Leave Request", doc["name"], "Approuver", doc["version"])
    await svc.flows.settled()
    [call] = xflow.calls
    assert (call["plugin"], call["action"], call["tenant"]) == ("xflow", "trigger", "acme")
    assert call["payload"]["workflow_name"] == "commande_fournisseur" and call["payload"]["tenant_id"] == "acme"
    assert call["payload"]["payload"] == {
        "doctype": "Leave Request", "name": doc["name"], "version": moved["version"], "docstatus": moved["docstatus"],
        "action": "Approuver", "from": "À approuver", "to": "Approuvé", "user": "bob",
    }  # ni `objet` ni aucune valeur du document : une étape qui en a besoin le relit avec les droits de l'acteur
    assert call["principal"]["sub"] == "bob" and APPROUVER in call["principal"]["permissions"]
    assert log.warnings == []  # « pending » n'est pas un refus


async def test_a_refused_transition_starts_nothing(make):
    xflow = Xflow()
    svc, _ = make(xflow)
    doc = await submitted(svc)
    with pytest.raises(PermissionDenied):
        await svc.workflow_transition(who(user="bob"), "Leave Request", doc["name"], "Approuver", doc["version"])
    await svc.flows.settled()
    assert xflow.calls == []


@pytest.mark.parametrize("xflow, expected", [
    (Xflow(fail=True), "flow not started"),
    (Xflow(slow=True), "flow not started: timed out"),
    (Xflow({"status": "error", "code": "not_found", "msg": "Workflow inconnu"}), "flow refused"),
])
async def test_an_absent_slow_or_refusing_xflow_never_undoes_the_transition(make, xflow, expected):
    svc, log = make(xflow, timeout=0.05)
    doc = await submitted(svc)
    moved = await svc.workflow_transition(who(APPROUVER, user="bob"), "Leave Request", doc["name"], "Approuver", doc["version"])
    assert moved["workflow_state"] == "Approuvé"
    await svc.flows.settled()
    assert [m for m, _ in log.warnings] == [expected]
    assert (await svc.get(who(), "Leave Request", doc["name"]))["workflow_state"] == "Approuvé"


async def test_after_close_nothing_starts(make):
    xflow = Xflow()
    svc, _ = make(xflow)
    doc = await submitted(svc)
    await svc.flows.close()
    await svc.workflow_transition(who(APPROUVER, user="bob"), "Leave Request", doc["name"], "Approuver", doc["version"])
    await svc.flows.settled()
    assert xflow.calls == []


@pytest.mark.parametrize("name", ["", "-commence-par-un-tiret", "a b", "a/b", "x" * 101])
def test_only_the_flow_s_name_is_checked_at_load(name):
    with pytest.raises(InvalidDefinition):
        doctype(workflow={"states": STATES, "transitions": [dict(TRANSITIONS[0], flow=name), *TRANSITIONS[1:]]})
    assert doctype(workflow={"states": STATES, "transitions": FLOWED}).workflow.transitions[1].flow == "commande_fournisseur"
